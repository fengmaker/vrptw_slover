"""Revalidate M7 saved routes and export compact, versioned evidence."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import io
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "benchmarks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose import verify_batch, verify_pyvrp  # noqa: E402
from experiment import compare_ours, read_csv, write_csv  # noqa: E402
from vrptw import Config  # noqa: E402


def hash_file(path):
    return sha256(path.read_bytes()).hexdigest()


def metrics(group, name, rows, comparisons):
    gaps = sorted(row["distance_gap_percent"] for row in comparisons
                  if row["distance_gap_percent"] != "")
    times = sorted(float(row["runtime_seconds"]) for row in rows)
    result = dict(group=group, experiment=name, runs=len(rows),
                  better=sum(row["outcome"] == "better" for row in comparisons),
                  worse=sum(row["outcome"] == "worse" for row in comparisons),
                  tie=sum(row["outcome"] == "tie" for row in comparisons),
                  fewer_vehicles=sum(row["vehicle_gap"] < 0 for row in comparisons),
                  more_vehicles=sum(row["vehicle_gap"] > 0 for row in comparisons),
                  vehicle_delta=sum(row["vehicle_gap"] for row in comparisons),
                  same_fleet_cases=len(gaps),
                  distance_change_median=median(gaps) if gaps else "",
                  distance_change_p90=gaps[ceil(.9 * len(gaps)) - 1] if gaps else "",
                  runtime_median=median(times), runtime_p90=times[ceil(.9 * len(times)) - 1],
                  runtime_max=times[-1])
    for family in ("C", "R", "RC"):
        selected = [row for row in rows if
                    ("RC" if row["instance"].startswith("RC") else row["instance"][0]) == family]
        result[f"{family}_runs"] = len(selected)
        result[f"{family}_ils_runs"] = sum(int(row["iterations"]) > 0 for row in selected)
        result[f"{family}_iterations"] = sum(int(row["iterations"]) for row in selected)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", required=True, metavar="LABEL=DIRECTORY")
    parser.add_argument("--data", type=Path, default=PROJECT / "data")
    parser.add_argument("--m6", type=Path, default=PROJECT / "runs/m6_control_seed012_0p5s")
    parser.add_argument("--pyvrp", type=Path)
    parser.add_argument("--delivery", type=Path, help="normal CLI batch using the adopted defaults")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    _, _, previous = verify_batch(args.m6, args.data)
    evidence, summaries, comparisons, batches = {}, [], [], []
    for item in args.group:
        label, raw_path = item.split("=", 1)
        if not label.isidentifier() or label in evidence:
            parser.error("group labels must be unique identifiers")
        root = Path(raw_path)
        paths = sorted(root.glob("*/batch_summary.json"))
        source_hash = None
        if (root / "protocol.json").exists():
            protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
            allowed = protocol.get("experiments", list(protocol["matrix"]["experiments"]))
            paths = [path for path in paths if path.parent.name in allowed]
            source_hash = protocol["source_sha256"]
        elif (root / "experiment.json").exists():
            protocol = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
            paths = [path for path in paths if path.parent.name in protocol["variants"]]
            source_hash = protocol["solver_code_sha256"]
        if not paths:
            parser.error(f"no completed batches: {root}")
        baseline = root / ("control" if (root / "control").exists() else "baseline")
        _, _, control = verify_batch(baseline, args.data)
        group = {}
        for meta_path in paths:
            batch, name = meta_path.parent, meta_path.parent.name
            meta, phases, rows = verify_batch(batch, args.data)
            if source_hash is not None and meta["code_sha256"] != source_hash:
                raise ValueError(f"batch source differs from experiment protocol: {batch}")
            if meta["time_limit_seconds"] != .5 or meta["seeds"] != [0, 1, 2]:
                raise ValueError(f"not the M7 0.5s/seed012 protocol: {batch}")
            prefix = args.out / f"{label}_{name}"
            exports = {"runs.csv": batch / "batch_summary.csv", "summary.json": meta_path}
            if phases:
                exports["phases.csv"] = batch / "batch_diagnostics.csv"
            hashes = {}
            for suffix, source in exports.items():
                target = Path(f"{prefix}_{suffix}")
                target.write_bytes(source.read_bytes())
                hashes[target.name] = hash_file(target)
            for reference, before in (("control", control), ("m6", previous)):
                # Representative diagnosis groups have a smaller instance set.
                names = {row["instance"] for row in rows}
                subset = [row for row in before if row["instance"] in names]
                difference = compare_ours(subset, rows)
                for row in difference:
                    comparisons.append(dict(group=label, experiment=name, reference=reference, **row))
                if reference == "control":
                    summaries.append(metrics(label, name, rows, difference))
            group[name] = dict(batch=str(batch), independently_verified=len(rows), phase_records=len(phases),
                               code_sha256=meta["code_sha256"], config=meta["config"], exports_sha256=hashes)
            batches.append((f"{label}_{name}", batch))
        evidence[label] = group
    write_csv(args.out / "m7_summary.csv", summaries)
    write_csv(args.out / "m7_own_comparison.csv", comparisons)
    payload = dict(numeric_rule="solomon_exact_1000_v1", budget_seconds=.5, seeds=[0, 1, 2],
                   percentile_definition="nearest rank: sorted values[ceil(0.90*n)-1]",
                   groups=evidence, independently_verified=sum(item["independently_verified"]
                   for group in evidence.values() for item in group.values()))
    if args.delivery:
        meta, phases, rows = verify_batch(args.delivery, args.data)
        default = Config(max_iterations=None, time_limit_seconds=.5)
        if meta["config"] != json.loads(json.dumps(asdict(default))) or meta["seeds"] != [0, 1, 2]:
            raise ValueError("delivery batch does not use the adopted CLI defaults")
        hashes = {}
        for suffix in ("csv", "json"):
            target = args.out / f"delivery_summary.{suffix}"
            target.write_bytes((args.delivery / f"batch_summary.{suffix}").read_bytes())
            hashes[target.name] = hash_file(target)
        payload["delivery"] = dict(batch=str(args.delivery), independently_verified=len(rows),
                                   code_sha256=meta["code_sha256"], exports_sha256=hashes)
        payload["independently_verified"] += len(rows)
        write_csv(args.out / "delivery_vs_m6.csv", compare_ours(previous, rows))
        batches.append(("delivery", args.delivery))
    if args.pyvrp:
        payload["pyvrp"] = verify_pyvrp(args.pyvrp, args.data)
        target = args.out / "pyvrp_runs.csv"
        target.write_bytes(args.pyvrp.read_bytes())
        spec = importlib.util.spec_from_file_location("m7_pyvrp_compare", PROJECT / "benchmarks/pyvrp/compare.py")
        compare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compare)
        metrics_rows = []
        for label, batch in batches:
            target = args.out / f"{label}_vs_pyvrp.csv"
            with redirect_stdout(io.StringIO()):
                compare.main(["--ours", str(batch / "batch_summary.csv"), "--pyvrp", str(args.pyvrp),
                              "--budget", "0.5", "--require-complete", "--out", str(target)])
            compared = read_csv(target)
            for family in ("all", "C", "R", "RC"):
                subset = [row for row in compared if family == "all" or
                          ("RC" if row["instance"].startswith("RC") else row["instance"][0]) == family]
                gaps = sorted(float(row["distance_gap_percent"]) for row in subset if row["distance_gap_percent"])
                metrics_rows.append(dict(experiment=label, family=family, instances=len(subset),
                    ours_fewer_vehicles=sum(row["vehicle_gap"] != "" and int(row["vehicle_gap"]) < 0 for row in subset),
                    ours_more_vehicles=sum(row["vehicle_gap"] != "" and int(row["vehicle_gap"]) > 0 for row in subset),
                    same_fleet_cases=len(gaps), distance_gap_median=median(gaps) if gaps else "",
                    distance_gap_p90=gaps[ceil(.9 * len(gaps)) - 1] if gaps else ""))
        write_csv(args.out / "m7_vs_pyvrp_summary.csv", metrics_rows)
    (args.out / "verification.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Revalidated {payload['independently_verified']} saved M7 solutions", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
