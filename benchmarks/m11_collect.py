"""Verify M11 experiments and final CLI artifacts, then archive delivery evidence."""

import argparse
import csv
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks import m8_experiment as m8
from vrptw import Config, read_solomon, validate_solution
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def collect(delivery: Path, out: Path, tests_passed: int | None = None) -> dict:
    """Read and validate all evidence before copying final CLI artifacts."""
    delivery, out = delivery.resolve(), out.resolve()
    out.relative_to(ROOT)
    names = [p.stem for p in sorted((ROOT / "data").glob("*.txt"))]
    expected = {(name, seed) for name in names for seed in (0, 1, 2)}
    if len(names) != 56:
        raise ValueError("M11 delivery requires all 56 Solomon instances")
    experiments, verified = {}, 0
    for label in ("diagnostics", "diagnostics_epochs", "ablation", "confirmation"):
        experiment = out / label
        manifest = json.loads((experiment / "experiment.json").read_text(encoding="utf-8"))
        # Earlier diagnostics have the exact earlier field schema and Config.
        # Load their recorded runner, then use its independent public judge.
        runner = experiment / "runner.py"
        spec = importlib.util.spec_from_file_location(f"m11_verify_{uuid.uuid4().hex}", runner)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._validate_analysis_input(experiment, ROOT / "data", manifest)
        count = manifest["independently_verified"]
        verified += count
        experiments[label] = {"verified_routes": count,
                              "manifest_sha256": m8._sha256(experiment / "experiment.json"),
                              "source_sha256": manifest["sources"]["m11_candidate"]["sha256"]}

    rows = _read_csv(delivery / "batch_summary.csv")
    metadata = json.loads((delivery / "batch_summary.json").read_text(encoding="utf-8"))
    pairs = [(row["instance"], int(row["seed"])) for row in rows]
    fingerprint = code_fingerprint()
    expected_config = json.loads(json.dumps(asdict(Config(
        max_iterations=None, time_limit_seconds=0.5))))
    if (len(pairs) != len(expected) or set(pairs) != expected
            or metadata.get("runs") != 168 or metadata.get("successful") != 168
            or metadata.get("failed") != 0 or metadata.get("seeds") != [0, 1, 2]
            or metadata.get("config") != expected_config
            or metadata.get("code_sha256") != fingerprint
            or metadata.get("numeric_rule") != NUMERIC_RULE_ID):
        raise ValueError("final CLI coverage, config or source differs")
    sources, artifact_hashes, input_hashes = [], {}, {}
    for row in rows:
        name, seed = row["instance"], int(row["seed"])
        source = ROOT / "data" / f"{name}.txt"
        input_hashes[name] = m8._sha256(source)
        directory = delivery / name / f"seed-{seed}"
        payload = json.loads((directory / "solution.json").read_text(encoding="utf-8"))
        verdict = validate_solution(read_solomon(source), payload["routes"])
        if (row.get("status") != "ok" or row.get("feasible") != "True"
                or row.get("code_sha256") != fingerprint
                or row.get("input_sha256") != input_hashes[name]
                or row.get("numeric_rule") != NUMERIC_RULE_ID
                or float(row["time_limit_seconds"]) != 0.5 or row["max_iterations"] != ""
                or not verdict.feasible
                or verdict.objective != (int(row["vehicles"]), int(row["distance_ticks"]))
                or verdict.objective != (payload["vehicles"], payload["distance_ticks"])
                or payload.get("config") != dict(expected_config, seed=seed)
                or payload.get("code_sha256") != fingerprint
                or payload.get("input_sha256") != input_hashes[name]
                or payload.get("numeric_rule") != NUMERIC_RULE_ID
                or payload.get("seed") != seed
                or payload.get("stop", {}).get("time_limit_seconds") != 0.5
                or payload.get("stop", {}).get("max_iterations") is not None):
            raise ValueError(f"final CLI verification failed for {name}/seed-{seed}")
        for filename in ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png"):
            path = directory / filename
            if not path.is_file() or not path.stat().st_size:
                raise ValueError(f"missing final CLI artifact: {path}")
            rel = path.relative_to(delivery).as_posix()
            artifact_hashes[rel] = m8._sha256(path)
            sources.append(path)

    py_root = ROOT / "benchmarks" / "m10" / "pyvrp_distance"
    py_rows = _read_csv(py_root / "0p5s_runs.csv")
    py_verified = 0
    for row in py_rows:
        if row["status"] != "ok" or row["feasible"] != "True":
            continue
        name, seed, cap = row["instance"], int(row["seed"]), int(row["vehicle_cap"])
        if (name, seed) not in expected or float(row["budget_seconds"]) != 0.5:
            raise ValueError("PyVRP reference coverage or budget differs")
        payload = json.loads((py_root / "solutions" / "0p5s" / name /
                              f"seed-{seed}-cap-{cap}.json").read_text(encoding="utf-8"))
        verdict = validate_solution(read_solomon(ROOT / "data" / f"{name}.txt"), payload["routes"])
        if (row["input_sha256"] != input_hashes[name]
                or payload.get("input_sha256") != input_hashes[name]
                or payload.get("numeric_rule") != NUMERIC_RULE_ID
                or payload.get("seed") != seed or payload.get("vehicle_cap") != cap
                or payload.get("budget_seconds") != 0.5
                or not verdict.feasible or verdict.vehicles > cap
                or verdict.objective != (int(row["vehicles"]), int(row["distance_ticks"]))):
            raise ValueError(f"PyVRP saved reference failed validation for {name}/{seed}/{cap}")
        py_verified += 1

    archived = out / "delivery"
    archived.mkdir(parents=True, exist_ok=True)
    for filename in ("batch_summary.csv", "batch_summary.json"):
        shutil.copyfile(delivery / filename, archived / filename)
    for path in sources:
        # Keep every route/trajectory; PNGs remain in raw CLI output except two examples.
        if path.suffix == ".png" and (path.parent.parent.name, path.parent.name) not in {
                ("C103", "seed-0"), ("RC108", "seed-0")}:
            continue
        destination = archived / path.relative_to(delivery)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    _, frozen_hash, _ = m8._freeze_current_source(archived)
    if frozen_hash != fingerprint:
        raise ValueError("delivery frozen source differs")
    tools_dir = out / "tools"
    tools_dir.mkdir(exist_ok=True)
    for path in (Path(__file__), ROOT / "benchmarks" / "m11_experiment.py",
                 ROOT / "benchmarks" / "m8_experiment.py", ROOT / "benchmarks" / "pyvrp" / "compare.py"):
        shutil.copyfile(path, tools_dir / path.name)
    result = {"schema_version": 1, "numeric_rule": NUMERIC_RULE_ID,
              "experiments": experiments, "experiment_routes_verified": verified,
              "cli_routes_verified": len(rows), "self_routes_verified": verified + len(rows),
              "pyvrp_reference_records": len(py_rows), "pyvrp_reference_routes_verified": py_verified,
              "pyvrp_reference_sha256": m8._sha256(py_root / "0p5s_runs.csv"),
              "delivery_source_sha256": fingerprint, "delivery_raw": str(delivery),
              "delivery_artifact_sha256": artifact_hashes,
              "input_sha256": input_hashes, "reported_tests_passed": tests_passed,
              "defaults": expected_config, "infeasible_search_promoted": False}
    m8._write_json(out / "verification.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", type=Path, default=ROOT / "runs" / "m11_delivery")
    parser.add_argument("--out", type=Path, default=ROOT / "benchmarks" / "m11")
    parser.add_argument("--tests-passed", type=int)
    args = parser.parse_args(argv)
    result = collect(args.delivery, args.out, args.tests_passed)
    print(f"verified {result['self_routes_verified']} self routes and "
          f"{result['pyvrp_reference_routes_verified']} saved PyVRP reference routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
