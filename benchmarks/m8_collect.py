"""Independently revalidate and archive completed M8 evidence."""

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from statistics import median
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "benchmarks")]
from diagnose import verify_batch, verify_pyvrp
from vrptw import read_solomon, validate_solution
from vrptw.report import code_fingerprint
import m8_experiment


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation", type=Path, default=ROOT / "runs/m8_ablation_corrected_seed012_0p5s")
    parser.add_argument("--delivery", type=Path, default=ROOT / "runs/m8_verified_delivery_seed012_0p5s")
    parser.add_argument("--rates", type=Path, default=ROOT / "runs/m8_rates")
    parser.add_argument("--pyvrp", type=Path, default=ROOT / "runs/m8_pyvrp_seed012_0p5s/0p5s_runs.csv")
    parser.add_argument("--fresh-pyvrp", type=Path, default=ROOT / "runs/m8_pyvrp_fresh_seed012_0p5s/0p5s_runs.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "benchmarks/m8")
    args = parser.parse_args()
    manifest = json.loads((args.ablation / "experiment.json").read_text(encoding="utf-8"))
    metrics = json.loads((args.ablation / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["experiment_manifest_sha256"] == m8_experiment._sha256(args.ablation / "experiment.json")
    assert manifest["status"] == "completed" and manifest["total_runs"] == 840
    args.out.mkdir(parents=True, exist_ok=True)
    verified = {}
    for label, batch in [(label, args.ablation / label) for label in manifest["variants"]] + [("delivery", args.delivery)]:
        metadata, phases, rows = verify_batch(batch, ROOT / "data")
        assert len(rows) == 168 and not phases
        if label == "delivery":
            assert metadata["code_sha256"] == code_fingerprint()
            for row in rows:
                run = batch / row["instance"] / f"seed-{row['seed']}"
                assert all((run / name).is_file() for name in
                           ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png"))
        destination = args.out / label
        destination.mkdir(exist_ok=True)
        for name in ("batch_summary.csv", "batch_summary.json"):
            shutil.copyfile(batch / name, destination / name)
        verified[label] = dict(runs=len(rows), independently_verified=len(rows), code_sha256=metadata["code_sha256"],
                               config=metadata["config"], runtime_median=median(float(row["runtime_seconds"]) for row in rows),
                               runtime_max=max(float(row["runtime_seconds"]) for row in rows),
                               first_feasible_median=median(float(row["first_feasible_seconds"]) for row in rows),
                               first_feasible_max=max(float(row["first_feasible_seconds"]) for row in rows),
                               ils_runs=sum(int(row["iterations"]) > 0 for row in rows),
                               ils_iterations=sum(int(row["iterations"]) for row in rows))
    for name in ("experiment.json", "original_experiment.json", "recheck_experiment.json",
                 "metrics.json", "best_by_instance.csv", "runs.csv"):
        shutil.copyfile(args.ablation / name, args.out / name)
    for source in manifest["sources"].values():
        package = Path(source["package"])
        files = {path.name: path.read_bytes() for path in package.glob("*.py")}
        assert m8_experiment._code_hash(files) == source["sha256"]
        destination = args.out / "source" / source["sha256"] / "vrptw"
        destination.mkdir(parents=True, exist_ok=True)
        for name, contents in files.items():
            (destination / name).write_bytes(contents)
    rates = json.loads((args.rates / "rates.json").read_text(encoding="utf-8"))
    saved_rates = json.loads((args.rates / "routes.json").read_text(encoding="utf-8"))
    assert len(saved_rates) == rates["independent_verified"] == 60
    assert rates["code_sha256"] == manifest["sources"]["m8"]["sha256"]
    for row in saved_rates:
        checked = validate_solution(read_solomon(ROOT / "data" / f"{row['instance']}.txt"), row["routes"])
        assert checked.feasible and checked.objective == (row["vehicles"], row["distance_ticks"])
    for name in ("rates.csv", "rates.json", "routes.json"):
        shutil.copyfile(args.rates / name, args.out / name)
    pyvrp = verify_pyvrp(args.pyvrp, ROOT / "data")
    shutil.copyfile(args.pyvrp, args.out / "pyvrp_runs.csv")
    fresh_pyvrp = verify_pyvrp(args.fresh_pyvrp, ROOT / "data")
    shutil.copyfile(args.fresh_pyvrp, args.out / "fresh_pyvrp_runs.csv")
    for kind, raw in (("pyvrp_history", args.pyvrp), ("pyvrp_fresh", args.fresh_pyvrp)):
        destination = args.out / kind
        destination.mkdir(exist_ok=True)
        for path in raw.parent.glob("*.json"):
            shutil.copyfile(path, destination / path.name)
    spec = importlib.util.spec_from_file_location("m8_compare", ROOT / "benchmarks/pyvrp/compare.py")
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    for label in ("baseline", "cached", "incremental", "n20", "n40", "delivery"):
        compare.main(["--ours", str(args.out / label / "batch_summary.csv"), "--pyvrp", str(args.pyvrp),
                      "--require-complete", "--out", str(args.out / label / "vs_pyvrp.csv")])
        rows = read_csv(args.out / label / "vs_pyvrp.csv")
        gaps = [float(row["distance_gap_percent"]) for row in rows if row["distance_gap_percent"]]
        verified[label]["pyvrp"] = dict(same_vehicle_instances=len(gaps),
                                         vehicles_worse=sum(int(row["vehicle_gap"]) > 0 for row in rows if row["vehicle_gap"]),
                                         distance_gap_median_percent=median(gaps),
                                         distance_gap_p90_percent=m8_experiment._nearest_rank(gaps, 0.9))
        compare.main(["--ours", str(args.out / label / "batch_summary.csv"), "--pyvrp", str(args.fresh_pyvrp),
                      "--require-complete", "--out", str(args.out / label / "vs_pyvrp_fresh.csv")])
        rows = read_csv(args.out / label / "vs_pyvrp_fresh.csv")
        gaps = [float(row["distance_gap_percent"]) for row in rows if row["distance_gap_percent"]]
        verified[label]["fresh_pyvrp"] = dict(same_vehicle_instances=len(gaps),
                                               vehicles_worse=sum(int(row["vehicle_gap"]) > 0 for row in rows if row["vehicle_gap"]),
                                               distance_gap_median_percent=median(gaps),
                                               distance_gap_p90_percent=m8_experiment._nearest_rank(gaps, 0.9))
    write_json(args.out / "verification.json", dict(solver=verified, throughput_routes_verified=len(saved_rates),
                                                    pyvrp=pyvrp, fresh_pyvrp=fresh_pyvrp))
    hashes = {str(path.relative_to(args.out)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(args.out.rglob("*")) if path.is_file() and path.name != "archive_manifest.json"}
    write_json(args.out / "archive_manifest.json", dict(files=hashes, ablation=str(args.ablation.resolve()),
                                                       delivery=str(args.delivery.resolve()), pyvrp=str(args.pyvrp.resolve())))
    print(json.dumps(verified["delivery"], indent=2))


if __name__ == "__main__":
    main()
