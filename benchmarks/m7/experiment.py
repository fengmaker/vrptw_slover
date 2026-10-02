"""Serial M7 ablations: fixed protocols, source snapshots and validated routes."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from math import ceil
import os
from pathlib import Path
import platform
import re
from statistics import median
import sys

SOLVER_DIR = Path(__file__).resolve().parents[2]
_bootstrap = argparse.ArgumentParser(add_help=False)
_bootstrap.add_argument("--solver-src", type=Path, default=SOLVER_DIR / "src")
_source_args, _ = _bootstrap.parse_known_args()
sys.path.insert(0, str(_source_args.solver_src.resolve()))
sys.path.insert(0, str(SOLVER_DIR / "benchmarks"))

import vrptw  # noqa: E402
from vrptw import Config, read_solomon, solve  # noqa: E402
from vrptw.report import (DIAGNOSTIC_FIELDS, NUMERIC_RULE_ID, code_fingerprint,
                         diagnostic_rows, validate_json, write_run)  # noqa: E402
from diagnose import verify_batch  # noqa: E402

SOURCE_DIR = Path(vrptw.__file__).resolve().parent

FIELDS = ("instance", "input_format", "input_sha256", "seed", "threads", "status", "feasible",
          "vehicles", "distance_ticks", "distance", "first_feasible_seconds", "runtime_seconds",
          "iterations", "stop_reason", "numeric_rule", "error", "time_limit_seconds", "max_iterations",
          "fleet_attempts_per_k", "max_moves", "vehicle_limit", "code_sha256", "diagnostics_enabled",
          "fleet_stop_reason", "fleet_trials", "fleet_time_fraction", "construction_order", "repair_order",
          "repair_strategy", "remove_min", "remove_max", "restart_after")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def dump_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def best(rows):
    return {name: min((int(row["vehicles"]), int(row["distance_ticks"])) for row in rows
                      if row["instance"] == name and row["status"] == "ok")
            for name in sorted({row["instance"] for row in rows})}


def compare_ours(previous, current):
    before, after = best(previous), best(current)
    if before.keys() != after.keys():
        raise ValueError("instance sets differ between batches")
    return [dict(instance=name, before_vehicles=before[name][0], after_vehicles=after[name][0],
                 vehicle_gap=after[name][0] - before[name][0], before_distance_ticks=before[name][1],
                 after_distance_ticks=after[name][1],
                 distance_gap_percent=100 * (after[name][1] / before[name][1] - 1)
                 if after[name][0] == before[name][0] else "",
                 outcome="better" if after[name] < before[name] else "worse" if after[name] > before[name] else "tie")
            for name in before]


def run_one(args, label, config, paths, source_hash):
    out = args.out / label
    if (out / "batch_summary.json").exists():
        meta = read_json(out / "batch_summary.json")
        if (meta["config"] != json.loads(json.dumps(asdict(config))) or meta["code_sha256"] != source_hash
                or meta["seeds"] != args.seeds or meta["instance_names"] != [path.stem for path in paths]):
            raise ValueError(f"existing protocol differs: {label}")
        verify_batch(out, args.data)
        print(f"{label}: verified, skipping", flush=True)
        return
    out.mkdir(parents=True, exist_ok=True)
    started, records = datetime.now(timezone.utc), []
    with (out / "batch_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        diag_stream = None
        try:
            if config.diagnostics:
                diag_stream = (out / "batch_diagnostics.csv").open("w", encoding="utf-8", newline="")
                diag_writer = csv.DictWriter(diag_stream, fieldnames=("instance", "seed", *DIAGNOSTIC_FIELDS))
                diag_writer.writeheader()
            for path in paths:
                instance = read_solomon(path)
                for seed in args.seeds:
                    run_config = replace(config, seed=seed)
                    row = {field: "" for field in FIELDS}
                    row.update({key: value for key, value in asdict(run_config).items() if key in FIELDS})
                    row.update(instance=instance.name, input_format="solomon_txt", input_sha256=fingerprint(path),
                               threads=1, status="error", feasible=False, vehicle_limit=instance.vehicle_count,
                               numeric_rule=NUMERIC_RULE_ID, code_sha256=source_hash, diagnostics_enabled=config.diagnostics)
                    try:
                        result = solve(instance, run_config)
                        # Numerical artifacts are written after solving. Normal CLI
                        # runs still draw both images; ablations omit plotting.
                        run_out = write_run(instance, result, run_config, out / instance.name / f"seed-{seed}",
                                            source_path=path, plots=False)
                        verdict = validate_json(instance, run_out / "solution.json")
                        if not verdict.feasible or verdict.objective != result.evaluation.objective:
                            raise AssertionError(f"output validation failed: {verdict.first_violation}")
                        row.update(status="ok", feasible=True, vehicles=verdict.vehicles, distance_ticks=verdict.distance,
                                   distance=verdict.distance / 1000, runtime_seconds=result.runtime_seconds,
                                   first_feasible_seconds=result.first_feasible_seconds, iterations=result.iterations,
                                   stop_reason=result.stop_reason, fleet_stop_reason=result.fleet.stop_reason,
                                   fleet_trials=sum(trials for _, trials in result.fleet.attempts))
                        if diag_stream is not None:
                            diag_writer.writerows(dict(instance=instance.name, seed=seed, **phase)
                                                  for phase in diagnostic_rows(result))
                            diag_stream.flush()
                    except Exception as exc:
                        row["error"] = f"{type(exc).__name__}: {exc}"
                    writer.writerow(row)
                    stream.flush()
                    records.append(row)
                print(f"{label}: {instance.name} {len(records)}/{len(paths) * len(args.seeds)} "
                      f"ok={sum(row['status'] == 'ok' for row in records)}", flush=True)
        finally:
            if diag_stream is not None:
                diag_stream.close()
    if code_fingerprint() != source_hash:
        raise ValueError("solver source changed during the experiment")
    meta = dict(instances=len(paths), instance_names=[path.stem for path in paths], runs=len(records), seeds=args.seeds,
                successful=sum(row["status"] == "ok" for row in records), failed=sum(row["status"] != "ok" for row in records),
                time_limit_seconds=config.time_limit_seconds, max_iterations=config.max_iterations,
                fleet_attempts_per_k=config.fleet_attempts_per_k, max_moves=config.max_moves,
                diagnostics_enabled=config.diagnostics, config=asdict(config), threads=1, input_format="solomon_txt",
                numeric_rule=NUMERIC_RULE_ID, code_sha256=source_hash, started_at_utc=started.isoformat(),
                finished_at_utc=datetime.now(timezone.utc).isoformat(),
                environment=dict(python=platform.python_version(), platform=platform.platform(),
                                 processor=platform.processor(), cpu_count=os.cpu_count()))
    dump_json(out / "batch_summary.json", meta)
    if not meta["failed"]:
        _, phases, verified = verify_batch(out, args.data)
        dump_json(out / "verification.json", dict(independently_verified=len(verified), phase_records=len(phases),
                                                  batch_sha256=fingerprint(out / "batch_summary.csv")))


def summarise(args, labels):
    path = args.out / "control" / "batch_summary.csv"
    if not path.exists():
        return
    previous, metrics = read_csv(path), []
    for label in labels:
        out = args.out / label
        if not (out / "batch_summary.json").exists():
            continue
        meta, rows = read_json(out / "batch_summary.json"), read_csv(out / "batch_summary.csv")
        if meta["failed"]:
            metrics.append(dict(experiment=label, successful=meta["successful"], failed=meta["failed"]))
            continue
        comparison = compare_ours(previous, rows)
        write_csv(out / "vs_control.csv", comparison)
        rrc = [row for row in rows if row["instance"].startswith("R")]
        gaps = sorted(row["distance_gap_percent"] for row in comparison if row["distance_gap_percent"] != "")
        metrics.append(dict(experiment=label, successful=meta["successful"], failed=0,
                            better=sum(row["outcome"] == "better" for row in comparison),
                            worse=sum(row["outcome"] == "worse" for row in comparison),
                            tie=sum(row["outcome"] == "tie" for row in comparison),
                            fewer_vehicles=sum(row["vehicle_gap"] < 0 for row in comparison),
                            more_vehicles=sum(row["vehicle_gap"] > 0 for row in comparison),
                            vehicle_delta=sum(row["vehicle_gap"] for row in comparison),
                            same_fleet_distance_change_median=median(gaps) if gaps else "",
                            same_fleet_distance_change_p90=gaps[ceil(.9 * len(gaps)) - 1] if gaps else "",
                            ils_runs=sum(int(row["iterations"]) > 0 for row in rows),
                            r_rc_ils_runs=sum(int(row["iterations"]) > 0 for row in rrc), r_rc_runs=len(rrc),
                            total_iterations=sum(int(row["iterations"]) for row in rows),
                            runtime_median=median(float(row["runtime_seconds"]) for row in rows),
                            runtime_max=max(float(row["runtime_seconds"]) for row in rows)))
    if metrics:
        write_csv(args.out / "ablation_summary.csv", metrics)
        print("ablation: " + "; ".join(f"{row['experiment']} +{row.get('better', 0)}/-{row.get('worse', 0)}"
                                         for row in metrics), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=Path(__file__).with_name("experiments.json"))
    parser.add_argument("--solver-src", type=Path, default=SOLVER_DIR / "src",
                        help="directory containing vrptw; archived sources can reproduce an old experiment")
    parser.add_argument("--data", type=Path, default=SOLVER_DIR / "data")
    parser.add_argument("--experiments", nargs="+")
    parser.add_argument("--instances", nargs="+")
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--time-limit", type=float, default=0.5)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--summarise-only", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    matrix = read_json(args.matrix)
    labels = args.experiments or list(matrix["experiments"])
    if set(labels) - matrix["experiments"].keys() or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in labels):
        parser.error("unknown or invalid experiment name")
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("seeds must be unique")
    configs = {}
    for name in labels:
        overrides = dict(matrix["base"], **matrix["experiments"][name])
        if {"seed", "max_iterations", "time_limit_seconds", "diagnostics"} & overrides.keys():
            parser.error("matrix cannot override seed, budget, iterations or diagnostics")
        configs[name] = Config(seed=0, max_iterations=None, time_limit_seconds=args.time_limit,
                               diagnostics=args.diagnostics, **overrides)
    paths = sorted(args.data.glob("*.txt"))
    if args.instances:
        if set(args.instances) - {path.stem for path in paths}:
            parser.error("unknown instance name")
        paths = [path for path in paths if path.stem in args.instances]
    if not paths:
        parser.error("no Solomon inputs")
    if args.summarise_only:
        summarise(args, labels)
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    source_hash = code_fingerprint()
    snapshot = args.out / "source" / source_hash / "vrptw"
    snapshot.mkdir(parents=True, exist_ok=True)
    for path in SOURCE_DIR.glob("*.py"):
        (snapshot / path.name).write_bytes(path.read_bytes())
    dump_json(args.out / "protocol.json", dict(matrix=matrix, seeds=args.seeds, budget_seconds=args.time_limit,
                                              experiments=labels,
                                              instances=[path.stem for path in paths], diagnostics=args.diagnostics,
                                              source_sha256=source_hash, script_sha256=fingerprint(Path(__file__))))
    for name in labels:
        run_one(args, name, configs[name], paths, source_hash)
        summarise(args, labels)
    return int(any(read_json(args.out / name / "batch_summary.json")["failed"] for name in labels))


if __name__ == "__main__":
    raise SystemExit(main())
