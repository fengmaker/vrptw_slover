"""Independently verify and archive M12 experiments and final CLI delivery."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from benchmarks import m12_experiment as m12
from benchmarks.pyvrp import compare
from vrptw import Config, read_solomon, validate_solution
from vrptw.native_search import native_metadata
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint


def _copy_tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.prof"))


def _comparison_metrics(path):
    rows = m12._read_csv(path)
    groups = {}
    for family in ("all", "C", "R", "RC"):
        own = rows if family == "all" else [r for r in rows if m12.m8._family(r["instance"]) == family]
        gaps = [float(r["distance_gap_percent"]) for r in own if r["distance_gap_percent"] != ""]
        groups[family] = dict(instances=len(own), same_vehicles=len(gaps),
                             more_vehicles=sum(int(r["vehicle_gap"]) > 0 for r in own if r["vehicle_gap"]),
                             fewer_vehicles=sum(int(r["vehicle_gap"]) < 0 for r in own if r["vehicle_gap"]),
                             gap_median_percent=statistics.median(gaps) if gaps else None,
                             gap_p90_percent=m12._nearest_rank(gaps, 0.9))
    return groups


def _best_comparison(path):
    rows = m12._read_csv(path)
    return dict(
        vehicles_better=sum(int(r["native_vehicles"]) < int(r["python_vehicles"]) for r in rows),
        vehicles_worse=sum(int(r["native_vehicles"]) > int(r["python_vehicles"]) for r in rows),
        distance_better=sum(r["same_vehicle_distance_result"] == "better" for r in rows),
        distance_worse=sum(r["same_vehicle_distance_result"] == "worse" for r in rows),
        distance_tie=sum(r["same_vehicle_distance_result"] == "tie" for r in rows),
    )


def collect(args):
    out = args.out.resolve()
    out.relative_to(ROOT)
    out.mkdir(parents=True, exist_ok=True)
    experiments = {}
    total = 0
    comparisons = {}
    for label in ("diagnostics", "ablation", "confirmation"):
        source = args.runs / f"m12_{label}"
        manifest = json.loads((source / "experiment.json").read_text(encoding="utf-8"))
        m12._validate_analysis_input(source, ROOT / "data", manifest)
        expected_cases = 12 if label == "diagnostics" else 168
        if (len(manifest["order"]["cases"]) != expected_cases or manifest["seeds"] != [0, 1, 2]
                or manifest["time_limit_seconds"] != 0.5
                or manifest["diagnostics_enabled"] != (label == "diagnostics")):
            raise ValueError(f"unexpected M12 protocol for {label}")
        # Recreate analysis from verified artifacts; do not trust saved summaries.
        m12.analyse_experiment(argparse.Namespace(out=source, data=ROOT / "data"))
        metrics = json.loads((source / "metrics.json").read_text(encoding="utf-8"))
        count = manifest["independently_verified"]
        total += count
        experiments[label] = dict(verified_routes=count, code_sha256=manifest["source"]["sha256"],
                                  binary_sha256=manifest["source"]["native_binary_sha256"],
                                  best_comparison=_best_comparison(source / "best_by_instance.csv"),
                                  metrics=metrics)
        for variant in m12.VARIANTS:
            destination = source / variant / "vs_pyvrp.csv"
            compare.main(["--ours", str(source / variant / "batch_summary.csv"),
                          "--pyvrp", str(args.pyvrp_root / "0p5s_runs.csv"),
                          "--require-complete", "--out", str(destination)])
            comparisons[f"{label}_{variant}"] = _comparison_metrics(destination)
        _copy_tree(source, out / label)

    work_path = args.runs / "m12_fixed_work_release" / "fixed_work.json"
    work = json.loads(work_path.read_text(encoding="utf-8"))
    for row in work["runs"]:
        instance_path = ROOT / "data" / f"{row['instance']}.txt"
        if row["input_sha256"] != m12._sha256(instance_path):
            raise ValueError("fixed-work input hash differs")
        verdict = validate_solution(read_solomon(instance_path), row["routes"])
        if not verdict.feasible or verdict.distance != row["distance_ticks"]:
            raise ValueError("fixed-work route failed independent validation")
    total += len(work["runs"])
    _copy_tree(work_path.parent, out / "fixed_work")
    _copy_tree(args.runs / "m12_profile", out / "profile")

    delivery = args.delivery.resolve()
    rows = m12._read_csv(delivery / "batch_summary.csv")
    metadata = json.loads((delivery / "batch_summary.json").read_text(encoding="utf-8"))
    names = [p.stem for p in sorted((ROOT / "data").glob("*.txt"))]
    expected = {(name, seed) for name in names for seed in (0, 1, 2)}
    pairs = [(r["instance"], int(r["seed"])) for r in rows]
    fingerprint = code_fingerprint()
    config = json.loads(json.dumps(asdict(Config(max_iterations=None, time_limit_seconds=0.5))))
    if (len(names) != 56 or len(pairs) != 168 or set(pairs) != expected
            or metadata["runs"] != 168 or metadata["seeds"] != [0, 1, 2]
            or metadata["successful"] != 168 or metadata["failed"] != 0
            or metadata["numeric_rule"] != NUMERIC_RULE_ID
            or metadata["config"] != config or metadata["code_sha256"] != fingerprint):
        raise ValueError("final CLI coverage/config/source differs")
    input_hashes, artifact_hashes = {}, {}
    archived = out / "delivery"
    archived.mkdir(exist_ok=True)
    native = native_metadata()
    build_receipt_path = args.runs / "m12_build_receipt.json"
    build_receipt = json.loads(build_receipt_path.read_text(encoding="utf-8"))
    if (build_receipt["native"] != native
            or build_receipt["cpp_sha256"] != m12._sha256(ROOT / "native/moves.cpp")
            or build_receipt["setup_sha256"] != m12._sha256(ROOT / "setup.py")):
        raise ValueError("Windows build receipt differs from the delivered native build")
    shutil.copyfile(build_receipt_path, out / "build_receipt.json")
    for row in rows:
        name, seed = row["instance"], int(row["seed"])
        path = ROOT / "data" / f"{name}.txt"
        input_hashes[name] = m12._sha256(path)
        directory = delivery / name / f"seed-{seed}"
        payload = json.loads((directory / "solution.json").read_text(encoding="utf-8"))
        verdict = validate_solution(read_solomon(path), payload["routes"])
        if (not verdict.feasible or row["status"] != "ok" or row["feasible"] != "True"
                or verdict.objective != (int(row["vehicles"]), int(row["distance_ticks"]))
                or verdict.objective != (payload["vehicles"], payload["distance_ticks"])
                or payload["config"] != dict(config, seed=seed)
                or payload["code_sha256"] != fingerprint or row["code_sha256"] != fingerprint
                or payload["input_sha256"] != input_hashes[name]
                or row["input_sha256"] != input_hashes[name]
                or float(row["time_limit_seconds"]) != 0.5 or row["max_iterations"] != ""
                or payload["numeric_rule"] != NUMERIC_RULE_ID or row["numeric_rule"] != NUMERIC_RULE_ID
                or payload["seed"] != seed or payload["search_backend"] != "native"
                or payload["native"] != native
                or payload["stop"]["time_limit_seconds"] != 0.5
                or payload["stop"]["max_iterations"] is not None):
            raise ValueError(f"final CLI validation differs: {name}/{seed}")
        for filename in ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png"):
            artifact = directory / filename
            if not artifact.is_file() or not artifact.stat().st_size:
                raise ValueError(f"missing final artifact: {artifact}")
            relative = artifact.relative_to(delivery)
            artifact_hashes[relative.as_posix()] = m12._sha256(artifact)
            if artifact.suffix != ".png" or (name, seed) in {("C103", 0), ("RC108", 0)}:
                target = archived / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact, target)
    for filename in ("batch_summary.csv", "batch_summary.json"):
        shutil.copyfile(delivery / filename, archived / filename)
    frozen, frozen_hash, _ = m12._freeze_source(archived)
    if frozen_hash != fingerprint:
        raise ValueError("final source snapshot differs")
    comparison = archived / "vs_pyvrp.csv"
    compare.main(["--ours", str(delivery / "batch_summary.csv"),
                  "--pyvrp", str(args.pyvrp_root / "0p5s_runs.csv"),
                  "--require-complete", "--out", str(comparison)])
    comparisons["delivery"] = _comparison_metrics(comparison)
    total += len(rows)

    py_verified = 0
    for row in m12._read_csv(args.pyvrp_root / "0p5s_runs.csv"):
        if row["status"] != "ok" or row["own_validator_feasible"] != "True":
            continue
        name, seed, cap = row["instance"], int(row["seed"]), int(row["vehicle_cap"])
        path = args.pyvrp_root / "solutions" / "0p5s" / name / f"seed-{seed}-cap-{cap}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        verdict = validate_solution(read_solomon(ROOT / "data" / f"{name}.txt"), payload["routes"])
        if (not verdict.feasible or verdict.vehicles > cap
                or row["input_sha256"] != input_hashes[name]
                or payload["input_sha256"] != input_hashes[name]
                or payload["numeric_rule"] != NUMERIC_RULE_ID
                or float(row["budget_seconds"]) != 0.5
                or payload["seed"] != seed or payload["vehicle_cap"] != cap
                or payload["budget_seconds"] != 0.5
                or verdict.objective != (payload["vehicles"], payload["distance_ticks"])
                or verdict.objective != (int(row["vehicles"]), int(row["distance_ticks"]))):
            raise ValueError(f"PyVRP reference validation differs: {name}/{seed}/{cap}")
        py_verified += 1

    result = dict(schema_version=1, numeric_rule=NUMERIC_RULE_ID,
                  self_routes_verified=total, cli_routes_verified=len(rows),
                  fixed_work_routes_verified=len(work["runs"]), experiments=experiments,
                  pyvrp_reference_routes_verified=py_verified, defaults=config,
                  final_source_sha256=fingerprint, native=native,
                  build_receipt=build_receipt,
                  input_sha256=input_hashes, delivery_artifact_sha256=artifact_hashes,
                  comparisons=comparisons, fixed_work_metrics=work["metrics"],
                  reported_tests_passed=args.tests_passed)
    m12._write_json(out / "verification.json", result)
    tools = out / "tools"
    tools.mkdir(exist_ok=True)
    for path in (Path(__file__), ROOT / "benchmarks/m12_experiment.py",
                 ROOT / "benchmarks/m12_fixed_work.py", ROOT / "benchmarks/m12_profile.py",
                 ROOT / "benchmarks/m8_experiment.py", ROOT / "benchmarks/pyvrp/compare.py"):
        shutil.copyfile(path, tools / path.name)
    files = {p.relative_to(out).as_posix(): m12._sha256(p) for p in out.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.name != "archive_manifest.json"}
    m12._write_json(out / "archive_manifest.json", dict(files=files))
    print(json.dumps(dict(self_routes_verified=total, pyvrp_verified=py_verified,
                         comparisons=comparisons), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=ROOT / "runs")
    parser.add_argument("--delivery", type=Path, default=ROOT / "runs/m12_delivery")
    parser.add_argument("--out", type=Path, default=ROOT / "benchmarks/m12")
    parser.add_argument("--pyvrp-root", type=Path, default=ROOT / "benchmarks/m10/pyvrp_distance")
    parser.add_argument("--tests-passed", type=int, required=True)
    collect(parser.parse_args())


if __name__ == "__main__":
    main()
