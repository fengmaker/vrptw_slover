"""Revalidate and archive M10 trials, default CLI runs and both PyVRP protocols."""

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "benchmarks")]

from diagnose import verify_batch, verify_pyvrp
from m8_experiment import _nearest_rank, _sha256, _write_csv, _write_json
from m9_collect import compare_batch, copy_files
import m10_experiment
from vrptw import Config, construct, read_solomon, validate_solution
from vrptw.report import code_fingerprint


def _rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_fleet_baseline(directory, own_batch, destination):
    """Check the explicit fleet protocol separately from fixed-cap solve rows."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    if manifest["status"] != "completed" or config["budget_seconds"] != 0.5:
        raise ValueError("PyVRP fleet experiment is incomplete or has a different budget")
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if (hashlib.sha256(encoded).hexdigest() != manifest["config_sha256"]
            or config["fleet_time_fraction"] != 0.75
            or config["initial_cap_policy"] != "deterministic_construct_due"):
        raise ValueError("PyVRP fleet configuration hash or protocol differs")
    if manifest["source_hashes"]["script_sha256"] != _sha256(ROOT / "benchmarks/m10_fleet_baseline.py"):
        raise ValueError("PyVRP fleet runner differs from the archived script")
    rows = _rows(directory / "runs.csv")
    if _sha256(directory / "runs.csv") != manifest["runs_sha256"]:
        raise ValueError("PyVRP fleet CSV hash differs")
    own_rows = _rows(own_batch / "batch_summary.csv")
    expected = {(row["instance"], int(row["seed"])) for row in own_rows}
    index = {(row["instance"], int(row["seed"])): row for row in rows}
    if len(index) != len(rows) or set(index) != expected:
        raise ValueError("PyVRP fleet and ours have different instance/seed coverage")
    verified = 0
    instances = {name: read_solomon(ROOT / "data" / f"{name}.txt") for name, _ in expected}
    caps = {name: len(construct(instance, order="due")) for name, instance in instances.items()}
    cases = {(case["instance"], int(case["seed"])): case for case in manifest["cases"]}
    if set(cases) != expected or len(cases) != len(manifest["cases"]):
        raise ValueError("PyVRP fleet manifest coverage differs")
    for pair, row in index.items():
        name, seed = pair
        instance = instances[name]
        cap = caps[name]
        if (int(row["initial_vehicle_cap"]) != cap
                or config["caps"][name]["initial_vehicle_cap"] != cap
                or row["input_sha256"] != _sha256(ROOT / "data" / f"{name}.txt")
                or float(row["budget_seconds"]) != 0.5
                or float(row["fleet_time_fraction"]) != 0.75
                or row["cap_source"] != "deterministic_construct_due"
                or row["config_sha256"] != manifest["config_sha256"]):
            raise ValueError(f"PyVRP initial cap, input or budget differs for {pair}")
        case = cases[pair]
        if case["status"] != row["status"] or row["status"] not in ("ok", "not_found"):
            raise ValueError(f"PyVRP fleet run has an error for {pair}")
        if row["status"] == "not_found":
            if case.get("artifact") or row["own_validator_feasible"] != "False":
                raise ValueError("not_found run declares a feasible artifact")
            continue
        artifact = directory / case["artifact"]
        expected_path = directory / "solutions" / name / f"seed-{seed}.json"
        if artifact.resolve() != expected_path.resolve() or _sha256(artifact) != case["artifact_sha256"]:
            raise ValueError(f"PyVRP fleet artifact path/hash differs for {pair}")
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        verdict = validate_solution(instance, payload["routes"])
        if (not verdict.feasible or verdict.objective != (int(row["vehicles"]), int(row["distance_ticks"]))
                or verdict.objective != (payload["vehicles"], payload["distance_ticks"])
                or payload["seed"] != seed or payload["budget_seconds"] != 0.5
                or payload["initial_vehicle_cap"] != cap
                or verdict.vehicles > int(row["reduced_vehicle_cap"])
                or payload["config_sha256"] != row["config_sha256"]
                or payload["input_sha256"] != row["input_sha256"]
                or payload["solver_code_sha256"] != manifest["source_hashes"]["solver_code_sha256"]
                or payload["pyvrp_code_sha256"] != manifest["source_hashes"]["pyvrp_code_sha256"]
                or payload["script_sha256"] != manifest["source_hashes"]["script_sha256"]):
            raise ValueError(f"PyVRP fleet saved routes failed validation for {pair}")
        verified += 1
    comparison = []
    own_index = {(row["instance"], int(row["seed"])): row for row in own_rows}
    for name in sorted({pair[0] for pair in expected}):
        seeds = sorted(seed for instance_name, seed in expected if instance_name == name)
        own_best = min((int(own_index[name, seed]["vehicles"]),
                        int(own_index[name, seed]["distance_ticks"]), seed) for seed in seeds)
        py_best = min(((int(index[name, seed]["vehicles"]), int(index[name, seed]["distance_ticks"]), seed)
                       for seed in seeds if index[name, seed]["status"] == "ok"), default=None)
        comparison.append(dict(instance=name, initial_vehicle_cap=int(index[name, seeds[0]]["initial_vehicle_cap"]),
                               ours_vehicles=own_best[0], pyvrp_vehicles=py_best[0] if py_best else "",
                               vehicle_gap=own_best[0] - py_best[0] if py_best else "",
                               ours_distance_ticks=own_best[1], pyvrp_distance_ticks=py_best[1] if py_best else "",
                               distance_gap_percent=(100 * (own_best[1] / py_best[1] - 1)
                                                     if py_best and own_best[0] == py_best[0] else ""),
                               ours_best_seed=own_best[2], pyvrp_best_seed=py_best[2] if py_best else "",
                               pyvrp_feasible_runs=sum(index[name, seed]["status"] == "ok" for seed in seeds)))
    _write_csv(destination / "vs_pyvrp_fleet.csv", comparison, tuple(comparison[0]))
    gaps = [row["distance_gap_percent"] for row in comparison if row["distance_gap_percent"] != ""]
    return dict(runs=len(rows), verified=verified, not_found=len(rows) - verified,
                initial_caps_match=True, vehicles_worse=sum(row["vehicle_gap"] != "" and row["vehicle_gap"] > 0
                                                           for row in comparison),
                vehicles_better=sum(row["vehicle_gap"] != "" and row["vehicle_gap"] < 0 for row in comparison),
                same_vehicle_instances=len(gaps), gap_median_percent=median(gaps) if gaps else None,
                gap_p90_percent=_nearest_rank(gaps, 0.9),
                runtime_median=median(float(row["total_solve_wall_seconds"]) for row in rows),
                runtime_max=max(float(row["total_solve_wall_seconds"]) for row in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--pyvrp", type=Path, required=True, help="fixed-cap distance CSV")
    parser.add_argument("--pyvrp-fleet", type=Path, required=True, help="explicit fleet experiment directory")
    parser.add_argument("--out", type=Path, default=ROOT / "benchmarks/m10")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    verification = {"experiments": {}, "batches": {}}
    for label in ("ablation", "confirmation", "diagnostics"):
        source = getattr(args, label)
        if source is None:
            continue
        m10_experiment.analyse_experiment(argparse.Namespace(out=source, data=ROOT / "data"))
        manifest = json.loads((source / "experiment.json").read_text(encoding="utf-8"))
        destination = args.out / label
        copy_files(source, destination, ("experiment.json", "metrics.json", "runs.csv",
                                        "best_by_instance.csv", "fleet_targets.csv", "batch_diagnostics.csv", "runner.py"))
        for entry in manifest["sources"].values():
            package = Path(entry["package"])
            copy_files(package, destination / "source" / entry["sha256"] / "vrptw",
                       tuple(path.name for path in package.glob("*.py")))
        verification["experiments"][label] = dict(runs=manifest["total_runs"], verified=manifest["independently_verified"])
        for variant in manifest["variants"]:
            copy_files(source / variant, destination / variant, ("batch_summary.csv", "batch_summary.json", "batch_diagnostics.csv"))
            metadata, phases, rows = verify_batch(source / variant, ROOT / "data")
            entry = dict(runs=len(rows), verified=len(rows), diagnostic_phases=len(phases),
                         code_sha256=metadata["code_sha256"], config=metadata["config"])
            if label != "diagnostics":
                entry["pyvrp_distance"] = compare_batch(source / variant, destination / variant, args.pyvrp)
            verification["batches"][f"{label}/{variant}"] = entry
    metadata, phases, rows = verify_batch(args.delivery, ROOT / "data")
    defaults = json.loads(json.dumps(asdict(Config(seed=0, max_iterations=None, time_limit_seconds=0.5))))
    if len(rows) != 168 or metadata["code_sha256"] != code_fingerprint() or metadata["config"] != defaults:
        raise ValueError("CLI delivery must contain 168 final-default runs with current source")
    final_targets = []
    for row in rows:
        directory = args.delivery / row["instance"] / f"seed-{row['seed']}"
        if not all((directory / name).is_file() for name in
                   ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png")):
            raise ValueError(f"missing CLI artifacts in {directory}")
        payload = json.loads((directory / "solution.json").read_text(encoding="utf-8"))
        final_targets.extend(m10_experiment._target_stat_rows(row["instance"], int(row["seed"]), "default", payload))
    copy_files(args.delivery, args.out / "delivery", ("batch_summary.csv", "batch_summary.json"))
    _write_csv(args.out / "delivery/fleet_targets.csv", final_targets, m10_experiment.TARGET_FIELDS)
    from m8_experiment import _freeze_current_source
    _freeze_current_source(args.out / "delivery")
    verification["batches"]["delivery"] = dict(
        runs=len(rows), verified=len(rows), code_sha256=metadata["code_sha256"], config=metadata["config"],
        runtime_median=median(float(row["runtime_seconds"]) for row in rows),
        runtime_max=max(float(row["runtime_seconds"]) for row in rows),
        first_feasible_median=median(float(row["first_feasible_seconds"]) for row in rows),
        first_feasible_max=max(float(row["first_feasible_seconds"]) for row in rows),
        ils_runs=sum(int(row["iterations"]) > 0 for row in rows),
        pyvrp_distance=compare_batch(args.delivery, args.out / "delivery", args.pyvrp))
    verification["pyvrp_distance"] = verify_pyvrp(args.pyvrp, ROOT / "data")
    verification["pyvrp_fleet"] = verify_fleet_baseline(args.pyvrp_fleet, args.delivery, args.out / "delivery")
    for label, source in (("pyvrp_distance", args.pyvrp.parent), ("pyvrp_fleet", args.pyvrp_fleet)):
        copy_files(source, args.out / label, tuple(path.name for path in source.iterdir() if path.suffix in (".csv", ".json")))
        if (source / "solutions").is_dir():
            shutil.copytree(source / "solutions", args.out / label / "solutions", dirs_exist_ok=True)
    copy_files(ROOT / "benchmarks", args.out / "tools", ("m10_experiment.py", "m10_collect.py", "m10_fleet_baseline.py",
                                                        "m8_experiment.py", "m9_experiment.py", "m9_collect.py", "diagnose.py"))
    copy_files(ROOT / "benchmarks/pyvrp", args.out / "tools/pyvrp", ("run.py", "compare.py", "caps.json"))
    _write_json(args.out / "verification.json", verification)
    hashes = {path.relative_to(args.out).as_posix(): _sha256(path) for path in sorted(args.out.rglob("*"))
              if path.is_file() and path.name != "archive_manifest.json" and "__pycache__" not in path.parts}
    _write_json(args.out / "archive_manifest.json", {"files": hashes})
    print(json.dumps(verification["batches"]["delivery"], indent=2))


if __name__ == "__main__":
    main()
