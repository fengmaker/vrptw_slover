"""Serial, rotating, resumable four-solver benchmark with independent judging."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks"))
sys.path.insert(0, str(ROOT / "src"))

from four_solver.common import FIELDS, NUMERIC_RULE, SOLVERS, fleet_cost, sha256, tree_hash, write_json
from vrptw import read_solomon, validate_solution
from vrptw.report import code_fingerprint


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def key(solver, instance, budget, seed):
    return f"{solver}/{instance}/{budget:g}s/seed-{seed}"


def source_files():
    return sorted([*Path(__file__).parent.glob("*.py"),
                   * (ROOT / "src" / "vrptw").glob("*.py"),
                   * (ROOT / "src" / "vrptw").glob("_native*.pyd"),
                   * (ROOT / "src" / "vrptw").glob("_native*.so")])


class Worker:
    def __init__(self, solver, python, args, log_dir):
        command = [str(python), "-u", str(Path(__file__).with_name("worker.py")),
                   "--solver", solver]
        if solver == "pyvrp":
            command += ["--pyvrp-root", str(args.pyvrp_root.resolve()),
                        "--pyvrp-site", str(args.pyvrp_site.resolve())]
        env = dict(os.environ)
        # Avoid numerical helper libraries silently launching worker pools.
        env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                   PYTHONIOENCODING="utf-8")
        self.log = (log_dir / f"{solver}.stderr.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, encoding="utf-8", env=env, cwd=ROOT)
        line = self.process.stdout.readline()
        if not line:
            self.close()
            raise RuntimeError(f"{solver} worker could not import; see {self.log.name}")
        self.environment = json.loads(line)
        if not self.environment.get("ready"):
            raise RuntimeError(f"{solver} worker not ready: {line}")

    def solve(self, path, budget, seed):
        self.process.stdin.write(json.dumps({"input": str(path), "budget": budget, "seed": seed}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"worker exited ({self.process.poll()}); see {self.log.name}")
        return json.loads(line)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        self.log.close()


def judge(instance, response):
    """Ignore self-reported quality and recompute the full returned sequence."""
    started = perf_counter()
    routes = response.get("routes")
    verdict = validate_solution(instance, routes) if routes is not None else None
    feasible = verdict is not None and verdict.feasible
    status = response.get("status", "error")
    error = response.get("error", "")
    if verdict is not None and not feasible:
        status = "invalid_solution"
        error = str(verdict.first_violation)
    metadata = response.get("metadata", {})
    if feasible:
        expected = fleet_cost(instance) * verdict.vehicles + verdict.distance
        internal = metadata.get("scalar_objective")
        # All backend objectives are exact integers; Gurobi returns doubles.
        if internal is not None and abs(internal - expected) > 0.01:
            raise ValueError(f"backend objective {internal} disagrees with validated {expected}")
    return {
        "status": status, "feasible": feasible,
        "vehicles": verdict.vehicles if feasible else "",
        "distance_ticks": verdict.distance if feasible else "",
        "distance": verdict.distance / 1000 if feasible else "",
        "validation_seconds": perf_counter() - started, "error": error,
    }


def write_csv(path, records):
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(path)


def verify(args):
    manifest = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
    checked = feasible = 0
    for rel, expected_hash in manifest["frozen_files"].items():
        if sha256(args.out / rel) != expected_hash:
            raise ValueError(f"changed frozen file: {rel}")
    records = []
    for path in sorted((args.out / "results").rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        input_path = args.out / "inputs" / f"{record['instance']}.txt"
        if sha256(input_path) != record["input_sha256"]:
            raise ValueError(f"input hash mismatch: {input_path}")
        instance = read_solomon(input_path)
        verdict = judge(instance, record["response"])
        for field in ("feasible", "vehicles", "distance_ticks", "status"):
            if verdict[field] != record[field]:
                raise ValueError(f"revalidation mismatch {field}: {path}")
        records.append(record)
        checked += 1
        feasible += verdict["feasible"]
    write_csv(args.out / "runs.csv", records)
    receipt = {"checked_at": utc_now(), "records": checked,
               "independently_feasible": feasible, "invalid_solutions": sum(
                   r["status"] == "invalid_solution" for r in records)}
    write_json(args.out / "verification.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0


def run(args):
    if any(budget <= 0 for budget in args.budgets):
        raise ValueError("budgets must be positive")
    if len(set(args.seeds)) != len(args.seeds) or len(set(args.budgets)) != len(args.budgets):
        raise ValueError("duplicate seeds or budgets")
    inputs = sorted(args.data.resolve().glob("*.txt"))
    if args.instances:
        requested = {name.upper() for name in args.instances}
        inputs = [p for p in inputs if p.stem.upper() in requested]
        if requested != {p.stem.upper() for p in inputs}:
            raise ValueError("unknown instance name")
    if not inputs:
        raise ValueError("no Solomon inputs")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "logs").mkdir(exist_ok=True)
    paths = source_files()
    source_hash = tree_hash(paths, ROOT)
    protocol = {
        "schema_version": 1, "numeric_rule": NUMERIC_RULE,
        "objective": "lexicographic (nonempty vehicles, integer distance)",
        "fixed_vehicle_cost": "(N + min(N, original_vehicle_cap)) * max_arc + 1",
        "vehicle_cap": "original Solomon TXT", "warm_start": False,
        "budget_boundary": "backend search call, including algorithm initial construction; external model build excluded and reported",
        "import_and_process_startup": "excluded; persistent workers",
        "serial_execution": True, "order": "rotate solver order within each instance/budget/seed",
        "instances": [p.stem for p in inputs], "seeds": args.seeds,
        "budgets": args.budgets, "solvers": args.solvers,
        "source_sha256": source_hash, "solver_code_sha256": code_fingerprint(),
        "input_hashes": {p.stem: sha256(p) for p in inputs},
    }
    manifest_path = args.out / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["protocol"] != protocol:
            raise ValueError("resume refused: inputs, protocol, or source changed; use a new output directory")
    else:
        frozen = {}
        for path in paths:
            target = args.out / "source" / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            frozen[target.relative_to(args.out).as_posix()] = sha256(target)
        for path in inputs:
            target = args.out / "inputs" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            frozen[target.relative_to(args.out).as_posix()] = sha256(target)
        if "pyvrp" in args.solvers:
            pyvrp_files = sorted(p for p in (args.pyvrp_root / "pyvrp").rglob("*")
                                 if p.suffix in {".py", ".cpp", ".h", ".hpp", ".pyd", ".so"})
            protocol["pyvrp_code_sha256"] = tree_hash(pyvrp_files, args.pyvrp_root)
            # Store this separately: resume hashes it again below.
            pyvrp_hash = protocol.pop("pyvrp_code_sha256")
        else:
            pyvrp_hash = None
        manifest = {"created_at": utc_now(), "protocol": protocol,
                    "pyvrp_code_sha256": pyvrp_hash,
                    "machine": {"platform": platform.platform(),
                                "processor": platform.processor(), "logical_cpus": os.cpu_count()},
                    "frozen_files": frozen, "workers": {}, "sessions": []}
        write_json(manifest_path, manifest)
    if "pyvrp" in args.solvers:
        files = sorted(p for p in (args.pyvrp_root / "pyvrp").rglob("*")
                       if p.suffix in {".py", ".cpp", ".h", ".hpp", ".pyd", ".so"})
        if tree_hash(files, args.pyvrp_root) != manifest["pyvrp_code_sha256"]:
            raise ValueError("PyVRP checkout changed since experiment creation")
    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((args.out / "results").rglob("*.json"))]
    completed = {key(r["solver"], r["instance"], r["budget_seconds"], r["seed"]) for r in records}
    total = len(inputs) * len(args.seeds) * len(args.budgets) * len(args.solvers)
    workers = {}
    session = {"started_at": utc_now(), "previous_records": len(records)}
    manifest["sessions"].append(session)
    try:
        for solver in args.solvers:
            python = args.pyvrp_python if solver == "pyvrp" else args.python
            workers[solver] = Worker(solver, python.resolve(), args, args.out / "logs")
            previous = manifest["workers"].get(solver)
            if previous and previous != workers[solver].environment:
                raise ValueError(f"{solver} interpreter changed during resume")
            manifest["workers"][solver] = workers[solver].environment
        write_json(manifest_path, manifest)
        case_index = 0
        for input_path in inputs:
            instance = read_solomon(input_path)
            for budget in args.budgets:
                for seed in args.seeds:
                    offset = case_index % len(args.solvers)
                    order = args.solvers[offset:] + args.solvers[:offset]
                    case_index += 1
                    for solver in order:
                        run_key = key(solver, instance.name, budget, seed)
                        if run_key in completed:
                            continue
                        response = workers[solver].solve(input_path, budget, seed)
                        verdict = judge(instance, response)
                        solution_rel = Path("results") / f"{budget:g}s" / instance.name / f"{solver}-seed-{seed}.json"
                        record = {field: response.get(field, "") for field in FIELDS}
                        record.update(verdict)
                        record.update(solver=solver, instance=instance.name, budget_seconds=budget,
                                      seed=seed, vehicle_cap=instance.vehicle_count,
                                      fixed_vehicle_cost=fleet_cost(instance),
                                      input_sha256=sha256(input_path), source_sha256=source_hash,
                                      metadata_json=json.dumps(response.get("metadata", {}), ensure_ascii=False),
                                      solution_path=solution_rel.as_posix(),
                                      response=response, completed_at=utc_now())
                        phases = sum(record.get(f, 0) or 0 for f in
                                     ("preparation_seconds", "search_seconds"))
                        adapter = record.get("adapter_seconds") or phases
                        record["postprocessing_seconds"] = max(0, adapter - phases)
                        record["total_seconds"] = adapter + record["validation_seconds"]
                        write_json(args.out / solution_rel, record)
                        records.append(record)
                        completed.add(run_key)
                        write_csv(args.out / "runs.csv", records)
                        print(f"{len(records)}/{total} {run_key} {record['status']} "
                              f"K={record['vehicles']} D={record['distance']} "
                              f"search={record['search_seconds']} prep={record['preparation_seconds']}", flush=True)
        session["completed_at"] = utc_now()
        manifest["completed_at"] = utc_now()
    finally:
        for worker in workers.values():
            worker.close()
        write_json(manifest_path, manifest)
    return verify(args)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--instances", nargs="+")
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--budgets", nargs="+", type=float, default=[0.5, 5.0])
    parser.add_argument("--solvers", nargs="+", choices=SOLVERS, default=list(SOLVERS))
    parser.add_argument("--python", type=Path, default=ROOT / ".venv-comparison" / "Scripts" / "python.exe")
    pyvrp = ROOT.parent / "PyVRP-main"
    parser.add_argument("--pyvrp-root", type=Path, default=pyvrp)
    parser.add_argument("--pyvrp-site", type=Path, default=pyvrp / ".venv" / "Lib" / "site-packages")
    parser.add_argument("--pyvrp-python", type=Path,
                        default=pyvrp / ".uv-python" / "cpython-3.13.5-windows-x86_64-none" / "python.exe")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(run(arguments) if arguments.action == "run" else verify(arguments))
