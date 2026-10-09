"""Revalidate and archive M9 experiments, CLI delivery and PyVRP comparisons."""

import argparse
import csv
import importlib.util
import json
from pathlib import Path
import shutil
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "benchmarks")]

from diagnose import verify_batch, verify_pyvrp
from m8_experiment import _nearest_rank, _sha256, _write_json
from vrptw import Config
from vrptw.report import code_fingerprint
import m9_experiment


def copy_files(source, destination, names):
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if path.is_file():
            shutil.copyfile(path, destination / name)


def archive_experiment(source, destination):
    m9_experiment.analyse_experiment(argparse.Namespace(out=source, data=ROOT / "data"))
    manifest = json.loads((source / "experiment.json").read_text(encoding="utf-8"))
    copy_files(source, destination,
               ("experiment.json", "metrics.json", "runs.csv", "best_by_instance.csv", "batch_diagnostics.csv"))
    for entry in manifest["sources"].values():
        package = Path(entry["package"])
        copy_files(package, destination / "source" / entry["sha256"] / "vrptw",
                   tuple(path.name for path in package.glob("*.py")))
    for variant in manifest["variants"]:
        copy_files(source / variant, destination / variant,
                   ("batch_summary.csv", "batch_summary.json", "batch_diagnostics.csv"))
    return manifest


def compare_batch(source, destination, pyvrp):
    spec = importlib.util.spec_from_file_location("m9_compare", ROOT / "benchmarks/pyvrp/compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(["--ours", str(source / "batch_summary.csv"), "--pyvrp", str(pyvrp),
                 "--require-complete", "--out", str(destination / "vs_pyvrp.csv")])
    with (destination / "vs_pyvrp.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    groups = {}
    for family in ("all", "C", "R", "RC"):
        selected = [row for row in rows if family == "all" or
                    ("RC" if row["instance"].startswith("RC") else row["instance"][0]) == family]
        gaps = [float(row["distance_gap_percent"]) for row in selected if row["distance_gap_percent"]]
        groups[family] = dict(instances=len(selected), same_vehicle_instances=len(gaps),
                              vehicles_worse=sum(int(row["vehicle_gap"]) > 0 for row in selected
                                                 if row["vehicle_gap"]),
                              gap_median_percent=median(gaps) if gaps else None,
                              gap_p90_percent=_nearest_rank(gaps, 0.9))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--screen-diagnostics", type=Path)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--pyvrp", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "benchmarks/m9")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    verification = {"experiments": {}, "batches": {}}
    for label in ("ablation", "confirmation", "diagnostics", "screen_diagnostics"):
        source = getattr(args, label)
        if source is None:
            continue
        manifest = archive_experiment(source, args.out / label)
        verification["experiments"][label] = dict(runs=manifest["total_runs"],
                                                 verified=manifest["independently_verified"])
        for variant in manifest["variants"]:
            metadata, phases, rows = verify_batch(source / variant, ROOT / "data")
            entry = dict(runs=len(rows), verified=len(rows), diagnostic_phases=len(phases),
                         code_sha256=metadata["code_sha256"], config=metadata["config"])
            if not label.endswith("diagnostics"):
                entry["pyvrp"] = compare_batch(source / variant, args.out / label / variant, args.pyvrp)
            verification["batches"][f"{label}/{variant}"] = entry

    metadata, phases, rows = verify_batch(args.delivery, ROOT / "data")
    if len(rows) != 168 or metadata["code_sha256"] != code_fingerprint():
        raise ValueError("delivery must cover all 168 runs from the final current source")
    from dataclasses import asdict
    expected_config = json.loads(json.dumps(asdict(Config(seed=0, max_iterations=None, time_limit_seconds=0.5))))
    if metadata["config"] != expected_config:
        raise ValueError("delivery configuration differs from final CLI defaults")
    for row in rows:
        directory = args.delivery / row["instance"] / f"seed-{row['seed']}"
        if not all((directory / name).is_file() for name in
                   ("solution.json", "routes.sol", "history.csv", "routes.png", "convergence.png")):
            raise ValueError(f"missing CLI output for {directory}")
    copy_files(args.delivery, args.out / "delivery", ("batch_summary.csv", "batch_summary.json"))
    entry = dict(runs=len(rows), verified=len(rows), code_sha256=metadata["code_sha256"],
                 config=metadata["config"], runtime_median=median(float(row["runtime_seconds"]) for row in rows),
                 runtime_max=max(float(row["runtime_seconds"]) for row in rows),
                 first_feasible_median=median(float(row["first_feasible_seconds"]) for row in rows),
                 first_feasible_max=max(float(row["first_feasible_seconds"]) for row in rows),
                 ils_runs=sum(int(row["iterations"]) > 0 for row in rows),
                 ils_iterations=sum(int(row["iterations"]) for row in rows),
                 pyvrp=compare_batch(args.delivery, args.out / "delivery", args.pyvrp))
    verification["batches"]["delivery"] = entry
    # Save the final source even if promotion changes only its defaults.
    import m8_experiment
    m8_experiment._freeze_current_source(args.out / "delivery")
    verification["pyvrp"] = verify_pyvrp(args.pyvrp, ROOT / "data")
    copy_files(args.pyvrp.parent, args.out / "pyvrp", tuple(
        path.name for path in args.pyvrp.parent.iterdir() if path.suffix in (".csv", ".json")))
    if (args.pyvrp.parent / "solutions").is_dir():
        shutil.copytree(args.pyvrp.parent / "solutions", args.out / "pyvrp/solutions", dirs_exist_ok=True)
    copy_files(ROOT / "benchmarks", args.out / "tools",
               ("m9_experiment.py", "m9_collect.py", "m8_experiment.py", "diagnose.py"))
    copy_files(ROOT / "benchmarks/pyvrp", args.out / "tools/pyvrp", ("compare.py", "run.py", "caps.json"))
    _write_json(args.out / "verification.json", verification)
    hashes = {path.relative_to(args.out).as_posix(): _sha256(path)
              for path in sorted(args.out.rglob("*"))
              if path.is_file() and path.name != "archive_manifest.json"
              and "__pycache__" not in path.parts}
    _write_json(args.out / "archive_manifest.json", dict(files=hashes,
                ablation=str(args.ablation.resolve()), delivery=str(args.delivery.resolve()),
                pyvrp=str(args.pyvrp.resolve())))
    print(json.dumps(entry, indent=2))


if __name__ == "__main__":
    main()
