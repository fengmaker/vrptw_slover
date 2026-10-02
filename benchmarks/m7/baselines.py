"""Preserve M6 PyVRP records and append every smaller cap needed by M7."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[2]


def rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def key(row):
    return row["instance"], int(row["seed"]), int(row["vehicle_cap"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", nargs="+", type=Path, required=True)
    parser.add_argument("--origin", type=Path, default=PROJECT / "runs/m6_pyvrp_seed012_0p5s")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.out.resolve().is_relative_to(PROJECT) or args.out.resolve() == args.origin.resolve():
        parser.error("output must be a separate directory in this project")
    caps = json.loads((PROJECT / "benchmarks/pyvrp/caps.json").read_text(encoding="utf-8"))
    required = set()
    batches = []
    for path in args.batches:
        selected = [path] if (path / "batch_summary.json").exists() else [
            item.parent for item in sorted(path.glob("*/batch_summary.json"))]
        if not selected:
            parser.error(f"no complete batch: {path}")
        for batch in selected:
            metadata = json.loads((batch / "batch_summary.json").read_text(encoding="utf-8"))
            if metadata["failed"] or metadata["time_limit_seconds"] != .5 or metadata["seeds"] != [0, 1, 2]:
                raise ValueError(f"batch is not a complete M7 protocol: {batch}")
            best = {}
            for row in rows(batch / "batch_summary.csv"):
                if row["status"] != "ok" or row["feasible"] != "True":
                    raise ValueError(f"failed row: {batch}")
                name = row["instance"]
                best[name] = min(best.get(name, 10**9), int(row["vehicles"]))
            required.update((name, seed, min(count, caps[name]["vehicle_cap"]))
                            for name, count in best.items() for seed in (0, 1, 2))
            batches.append(str(batch))
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "0p5s_runs.csv"
    origin = args.origin / "0p5s_runs.csv"
    original_rows = rows(origin)
    if not target.exists():
        shutil.copy2(origin, target)
        shutil.copytree(args.origin / "solutions", args.out / "solutions", dirs_exist_ok=True)
    existing = rows(target)
    indexed = {key(row): row for row in existing}
    if len(indexed) != len(existing) or any(indexed.get(key(row)) != row for row in original_rows):
        raise ValueError("copied M6 PyVRP records changed or duplicated")
    pending = required - indexed.keys()
    spec = importlib.util.spec_from_file_location("pyvrp_baseline_runner", PROJECT / "benchmarks/pyvrp/run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for cap in sorted({item[2] for item in pending}):
        names = sorted({name for name, _, count in pending if count == cap})
        snapshot = args.out / f"caps-{cap}.json"
        snapshot.write_text(json.dumps({name: dict(vehicle_cap=cap, source="m7_smaller_cap")
                                       for name in names}, indent=2) + "\n", encoding="utf-8")
        runner.main(["--caps", str(snapshot), "--instances", *names, "--budgets", "0.5",
                     "--seeds", "0", "1", "2", "--out-dir", str(args.out)])
    final = {key(row): row for row in rows(target)}
    if not required.issubset(final):
        raise ValueError("missing required PyVRP instance/seed/cap records")
    (args.out / "baseline_origin.json").write_text(json.dumps(dict(
        origin=str(args.origin), original_records=len(original_rows), records=len(final),
        required_keys=[list(item) for item in sorted(required)], batches=batches,
    ), indent=2) + "\n", encoding="utf-8")
    print(f"Preserved {len(original_rows)} M6 rows; {len(final)} total PyVRP records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
