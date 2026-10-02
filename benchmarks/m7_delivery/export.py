"""Revalidate and archive this session's M7 measurements without changing defaults."""

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "benchmarks")]
from diagnose import verify_batch, verify_pyvrp
import m7_experiment as experiment
from vrptw.report import code_fingerprint

spec = importlib.util.spec_from_file_location("m7_compare", ROOT / "benchmarks/pyvrp/compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gap_metrics(rows):
    gaps = [float(row["distance_gap_percent"]) for row in rows if row["distance_gap_percent"] != ""]
    compared = [row for row in rows if row["vehicle_gap"] != ""]
    return dict(instances=len(rows), vehicle_worse=sum(int(row["vehicle_gap"]) > 0 for row in compared),
                vehicle_better=sum(int(row["vehicle_gap"]) < 0 for row in compared),
                pyvrp_no_feasible_best=len(rows) - len(compared),
                same_vehicle_count=len(gaps), distance_gap_median_percent=median(gaps) if gaps else None,
                distance_gap_p90_percent=experiment._nearest_rank(gaps, 0.9))


def source_hash(package):
    digest = hashlib.sha256()
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    out = Path(__file__).resolve().parent
    ablation = ROOT / "runs/m7_ablation_seed012_0p5s"
    initial = ROOT / "runs/m7_confirmation_seed012_0p5s"
    confirmation = ROOT / "runs/m7_confirmation_clean_seed012_0p5s"
    pyvrp = ROOT / "runs/m7_pyvrp_seed012_0p5s/0p5s_runs.csv"
    manifest = json.loads((ablation / "experiment.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    batches = {f"ablation_{name}": ablation / name for name in manifest["variants"]}
    batches.update({f"confirmation_{name}": confirmation / name for name in ("baseline", "candidate")})
    batches.update({f"initial_{name}": initial / name for name in ("baseline", "candidate")})
    batches["delivery_default"] = ROOT / "runs/m7_verified_delivery_seed012_0p5s"
    verified, merged_ablation, merged_confirmation, metrics = {}, [], [], {}
    for label, batch in batches.items():
        metadata, phases, rows = verify_batch(batch, ROOT / "data")
        assert len(rows) == 168 and not phases
        if label.startswith("ablation_") or label.startswith("initial_"):
            assert metadata["code_sha256"] == manifest["solver_code_sha256"]
        destination = out / label
        destination.mkdir(exist_ok=True)
        shutil.copyfile(batch / "batch_summary.csv", destination / "runs.csv")
        shutil.copyfile(batch / "batch_summary.json", destination / "summary.json")
        if label != "delivery_default":
            (merged_ablation if label.startswith("ablation") else merged_confirmation).extend(rows)
        else:
            assert metadata["code_sha256"] == code_fingerprint()
            assert metadata["config"]["repair_order"] == "due"
            assert metadata["config"]["fleet_time_fraction"] == 0.95
            for row in rows:
                assert all((batch / row["instance"] / f"seed-{row['seed']}" / name).is_file()
                           for name in ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png"))
        verified[label] = dict(runs=len(rows), independently_verified=len(rows), config=metadata["config"],
                               code_sha256=metadata["code_sha256"],
                               batch_sha256=experiment._sha256(batch / "batch_summary.csv"),
                               runtime_median=median(float(row["runtime_seconds"]) for row in rows),
                               runtime_max=max(float(row["runtime_seconds"]) for row in rows),
                               first_feasible_median=median(float(row["first_feasible_seconds"]) for row in rows),
                               first_feasible_max=max(float(row["first_feasible_seconds"]) for row in rows),
                               by_family=experiment._variant_runtime(rows))
        compare.main(["--ours", str(batch / "batch_summary.csv"), "--pyvrp", str(pyvrp),
                      "--budget", "0.5", "--require-complete", "--out", str(destination / "vs_pyvrp.csv")])
        compared = read_csv(destination / "vs_pyvrp.csv")
        metrics[label] = gap_metrics(compared)
        metrics[label]["families"] = {family: gap_metrics([row for row in compared
                                     if experiment._family(row["instance"]) == family])
                                     for family in ("C", "R", "RC")}
    write_csv(out / "ablation_runs.csv", merged_ablation)
    write_csv(out / "confirmation_runs.csv", merged_confirmation)
    for source, name in ((ablation / "experiment.json", "ablation_manifest.json"),
                         (ablation / "analysis/m7_comparison.csv", "ablation_comparison.csv"),
                         (ablation / "analysis/m7_summary.json", "ablation_summary.json"),
                         (confirmation / "manifest.json", "confirmation_manifest.json"),
                         (confirmation / "paired_runs.csv", "paired_runs.csv"),
                         (confirmation / "paired_best_by_instance.csv", "paired_best_by_instance.csv"),
                         (confirmation / "paired_summary.json", "paired_summary.json"),
                         (initial / "manifest.json", "initial_manifest.json"),
                         (initial / "paired_summary.json", "initial_paired_summary.json"),
                         (pyvrp, "pyvrp_runs.csv")):
        shutil.copyfile(source, out / name)
    m6_best = experiment._best_by_instance(read_csv(ROOT / "benchmarks/m6/control_runs.csv"), "M6")
    historic, historical_rows = {}, []
    for label in ("confirmation_baseline", "confirmation_candidate", "delivery_default"):
        best = experiment._best_by_instance(read_csv(batches[label] / "batch_summary.csv"), label)
        historic[label] = experiment._comparison_summary(best, m6_best, list(best))
        for name in sorted(best):
            base, current = m6_best[name][0], best[name][0]
            vehicle, distance, delta, percent = experiment._comparison(base, current)
            historical_rows.append(dict(label=label, instance=name, m6_vehicles=base[0],
                                        m6_distance_ticks=base[1], m7_vehicles=current[0],
                                        m7_distance_ticks=current[1], vehicle_result=vehicle,
                                        distance_result=distance, distance_delta_ticks=delta,
                                        distance_delta_percent=percent))
    write_csv(out / "confirmation_vs_m6.csv", historical_rows)
    write_json(out / "metrics.json", dict(vs_pyvrp=metrics, vs_historical_m6=historic))
    py_verified = verify_pyvrp(pyvrp, ROOT / "data")
    count = sum(item["runs"] for item in verified.values())
    write_json(out / "verification.json", dict(
        numeric_rule=experiment.NUMERIC_RULE_ID, independently_verified=count,
        batches=verified, pyvrp=py_verified,
        limitation="VS Code Codex launched overlapping exploratory searches, including an old installed-package batch. "
                   "Both confirmation passes are exploratory. Final acceptance uses the separate explicit-src default CLI batch. "
                   "Timing and historical quality comparisons remain sensitive to machine load."))
    for digest in {item["code_sha256"] for item in verified.values()}:
        candidates = [ROOT / "src/vrptw", initial / "source" / digest / "vrptw"]
        package = next((path for path in candidates if path.is_dir() and source_hash(path) == digest), None)
        if package is None:
            raise ValueError(f"measured source snapshot missing: {digest}")
        source_out = out / "source" / digest / "vrptw"
        source_out.mkdir(parents=True, exist_ok=True)
        for source in package.glob("*.py"):
            shutil.copyfile(source, source_out / source.name)
    write_json(out / "archive_manifest.json", dict(
        solver_code_sha256=sorted({item["code_sha256"] for item in verified.values()}),
        files={path.relative_to(out).as_posix(): experiment._sha256(path)
               for path in sorted(out.rglob("*")) if path.is_file()
               and path.name != "archive_manifest.json" and "__pycache__" not in path.parts}))
    print(json.dumps(dict(verified_own_runs=count, pyvrp=py_verified,
                         candidate_vs_pyvrp=metrics["confirmation_candidate"],
                         candidate_vs_m6=historic["confirmation_candidate"]), indent=2))


if __name__ == "__main__":
    main()
