"""Local solve, independent validate, and fault-tolerant batch commands."""

import argparse
import csv
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

from .report import (DIAGNOSTIC_FIELDS, NUMERIC_RULE_ID, code_fingerprint,
                     diagnostic_rows, validate_json, write_run)
from .solomon import read_solomon
from .solve import Config, solve


def _add_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--time-limit", type=float, default=None, metavar="SECONDS")
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--fleet-attempts", type=int, default=100)
    parser.add_argument("--fleet-time-fraction", type=float, default=0.95,
                        help="share of remaining search time allowed for fleet reduction (0..1)")
    parser.add_argument("--max-moves", type=int, default=2)
    parser.add_argument("--construction-order", choices=("due", "id", "slack"), default="due")
    parser.add_argument("--repair-order", choices=("input", "due", "slack"), default="due")
    parser.add_argument("--repair-strategy", choices=("cheapest", "regret2"), default="cheapest")
    parser.add_argument("--remove-min", type=int, default=3)
    parser.add_argument("--remove-max", type=int, default=8)
    parser.add_argument("--restart-after", type=int, default=20)
    parser.add_argument("--evaluation-mode", choices=("full", "cached", "incremental"),
                        default="incremental")
    parser.add_argument("--num-neighbours", type=int, default=None,
                        help="candidate neighbours per customer; omit for exhaustive search")
    parser.add_argument("--diagnostics", action="store_true",
                        help="record exclusive stage times and candidate counters")


def _config(args: argparse.Namespace, seed: int) -> Config:
    if args.time_limit is None and args.max_iterations is None:
        raise ValueError("provide --time-limit or --max-iterations")
    return Config(seed=seed, time_limit_seconds=args.time_limit,
                  max_iterations=args.max_iterations,
                  fleet_attempts_per_k=args.fleet_attempts, max_moves=args.max_moves,
                  fleet_time_fraction=args.fleet_time_fraction,
                  construction_order=args.construction_order, repair_order=args.repair_order,
                  repair_strategy=args.repair_strategy,
                  remove_min=args.remove_min, remove_max=args.remove_max,
                  restart_after=args.restart_after,
                  diagnostics=args.diagnostics, evaluation_mode=args.evaluation_mode,
                  num_neighbours=args.num_neighbours)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vrptw")
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("solve", help="solve one original Solomon TXT")
    single.add_argument("instance", type=Path)
    single.add_argument("--seed", type=int, default=0)
    single.add_argument("--out", type=Path)
    _add_limits(single)
    check = sub.add_parser("validate", help="recompute a solution.json from visit order")
    check.add_argument("instance", type=Path)
    check.add_argument("solution", type=Path)
    batch = sub.add_parser("batch", help="solve sorted top-level *.txt instances")
    batch.add_argument("directory", type=Path)
    batch.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    batch.add_argument("--out", type=Path, default=Path("runs/batch"))
    _add_limits(batch)
    return parser


def _references(directory: Path) -> dict:
    path = directory / "solomon_bks.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _batch(args: argparse.Namespace) -> int:
    _config(args, args.seeds[0])
    started_at = datetime.now(timezone.utc)
    paths = sorted(args.directory.glob("*.txt"))
    if not paths:
        raise ValueError(f"no top-level Solomon .txt files in {args.directory}")
    args.out.mkdir(parents=True, exist_ok=True)
    references = _references(args.directory)
    records = []
    fields = ("instance", "input_format", "input_sha256", "seed", "threads", "status",
              "feasible", "vehicles", "distance_ticks", "distance", "first_feasible_seconds",
              "runtime_seconds", "iterations", "stop_reason", "numeric_rule",
              "reference_status", "reference_vehicles", "reference_distance",
              "distance_gap_percent", "error", "time_limit_seconds", "max_iterations",
              "fleet_attempts_per_k", "max_moves", "vehicle_limit", "code_sha256",
              "diagnostics_enabled")
    with ExitStack() as stack:
        stream = stack.enter_context((args.out / "batch_summary.csv").open(
            "w", encoding="utf-8", newline=""))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        diagnostics_writer = None
        if args.diagnostics:
            diagnostic_stream = stack.enter_context((args.out / "batch_diagnostics.csv").open(
                "w", encoding="utf-8", newline=""))
            diagnostics_writer = csv.DictWriter(diagnostic_stream,
                                                fieldnames=("instance", "seed", *DIAGNOSTIC_FIELDS))
            diagnostics_writer.writeheader()
        for path in paths:
            for seed in args.seeds:
                row = {field: "" for field in fields}
                row.update(instance=path.stem, input_format="solomon_txt", seed=seed,
                           threads=1, status="error", feasible=False,
                           numeric_rule=NUMERIC_RULE_ID, time_limit_seconds=args.time_limit,
                           max_iterations=args.max_iterations,
                           fleet_attempts_per_k=args.fleet_attempts, max_moves=args.max_moves,
                           code_sha256=code_fingerprint(), diagnostics_enabled=args.diagnostics)
                try:
                    row["input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    instance = read_solomon(path)
                    row["vehicle_limit"] = instance.vehicle_count
                    config = _config(args, seed)
                    result = solve(instance, config)
                    verdict = validate_json(instance, write_run(
                        instance, result, config, args.out / path.stem / f"seed-{seed}",
                        source_path=path
                    ) / "solution.json")
                    if not verdict.feasible:
                        raise AssertionError(f"reported solution failed validation: {verdict.first_violation}")
                    row.update(status="ok", feasible=True, vehicles=verdict.vehicles,
                               distance_ticks=verdict.distance, distance=verdict.distance / 1000,
                               first_feasible_seconds=round(result.first_feasible_seconds, 6),
                               runtime_seconds=round(result.runtime_seconds, 6),
                               iterations=result.iterations, stop_reason=result.stop_reason)
                    if diagnostics_writer is not None:
                        diagnostics_writer.writerows(dict(instance=instance.name, seed=seed, **phase)
                                                     for phase in diagnostic_rows(result))
                        diagnostic_stream.flush()
                    reference = references.get(instance.name)
                    if reference is None:
                        row["reference_status"] = "missing"
                    else:
                        row.update(reference_vehicles=reference["vehicles"],
                                   reference_distance=reference["distance"])
                        if verdict.vehicles == reference["vehicles"]:
                            row["reference_status"] = "same_vehicles"
                            row["distance_gap_percent"] = round(
                                100 * ((verdict.distance / 1000) / reference["distance"] - 1), 4
                            )
                        else:
                            row["reference_status"] = "different_vehicles"
                except Exception as exc:  # one bad instance must not interrupt the batch
                    row["error"] = f"{type(exc).__name__}: {exc}"
                writer.writerow(row)
                stream.flush()
                records.append(row)
                print(f"{path.stem} seed={seed}: {row['status']} "
                      f"vehicles={row['vehicles']} distance={row['distance']} {row['error']}")
    summary = {
        "instances": len(paths), "runs": len(records),
        "successful": sum(row["status"] == "ok" for row in records),
        "failed": sum(row["status"] != "ok" for row in records),
        "seeds": args.seeds,
        "time_limit_seconds": args.time_limit,
        "max_iterations": args.max_iterations,
        "fleet_attempts_per_k": args.fleet_attempts,
        "max_moves": args.max_moves,
        "diagnostics_enabled": args.diagnostics,
        "config": asdict(_config(args, args.seeds[0])),
        "input_format": "solomon_txt",
        "threads": 1,
        "numeric_rule": NUMERIC_RULE_ID,
        "code_sha256": code_fingerprint(),
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": sys.version.split()[0],
                        "platform": platform.platform(),
                        "processor": platform.processor(),
                        "cpu_count": os.cpu_count()},
    }
    (args.out / "batch_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"batch: {summary['successful']}/{summary['runs']} successful; {args.out}")
    return 0 if summary["failed"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "solve":
            instance = read_solomon(args.instance)
            config = _config(args, args.seed)
            result = solve(instance, config)
            out = args.out or Path("runs") / instance.name
            write_run(instance, result, config, out, source_path=args.instance)
            verdict = validate_json(instance, out / "solution.json")
            print(f"{instance.name}: {verdict.vehicles} vehicles, "
                  f"{verdict.distance / 1000:.3f} distance; "
                  f"{result.iterations} ILS iterations; {result.stop_reason}; {out}")
            return 0
        if args.command == "validate":
            instance = read_solomon(args.instance)
            verdict = validate_json(instance, args.solution)
            if verdict.feasible:
                print(f"feasible: {verdict.vehicles} vehicles, {verdict.distance / 1000:.3f} distance")
                return 0
            issue = verdict.first_violation
            print(f"infeasible: route={issue.route} customer={issue.customer} "
                  f"constraint={issue.code} {issue.detail}")
            return 1
        return _batch(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
        return 2
