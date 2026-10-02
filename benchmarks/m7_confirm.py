"""Run an interleaved, paired confirmation for one final M7 candidate.

Example::

    python benchmarks/m7_confirm.py --out runs/m7_confirm_regret2 \
        --set repair_strategy=regret2

Each instance/seed key runs baseline and candidate consecutively. Their order
alternates at every key. C103/seed 0 warms both configurations before timing;
those two solves are recorded in the manifest and excluded from batch rows.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

SOLVER_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SOLVER_DIR / "src"))
sys.path.insert(0, str(BENCHMARK_DIR))

import m7_experiment as m7  # noqa: E402
from vrptw import read_solomon, solve  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_plan(paths: list[Path], seeds: list[int]) -> list[dict]:
    """Return deterministic keys with alternating config order."""
    plan = []
    for index, (path, seed) in enumerate((path, seed) for path in paths for seed in seeds):
        order = ["baseline", "candidate"] if index % 2 == 0 else ["candidate", "baseline"]
        plan.append({"index": index, "instance": path.stem, "seed": seed, "order": order})
    return plan


def _check_solver_hash(expected: str) -> None:
    actual = m7.code_fingerprint()
    if actual != expected:
        raise RuntimeError(f"solver source changed during confirmation: {expected} -> {actual}")


def _warm_up(configs: dict, data: Path, expected_code_hash: str,
             expected_input_hash: str) -> list[dict]:
    warmup_path = data / "C103.txt"
    if not warmup_path.exists():
        raise ValueError(f"C103.txt is required for the common warm-up: {warmup_path}")
    instance = read_solomon(warmup_path)
    records = []
    for label in ("baseline", "candidate"):
        _check_solver_hash(expected_code_hash)
        if m7._sha256(warmup_path) != expected_input_hash:
            raise RuntimeError(f"warm-up input changed during confirmation: {warmup_path}")
        result = solve(instance, configs[label](0))
        _check_solver_hash(expected_code_hash)
        if m7._sha256(warmup_path) != expected_input_hash:
            raise RuntimeError(f"warm-up input changed during confirmation: {warmup_path}")
        records.append({
            "label": label, "instance": instance.name, "seed": 0,
            "runtime_seconds": result.runtime_seconds,
            "vehicles": result.evaluation.vehicles,
            "distance_ticks": result.evaluation.distance,
            "excluded_from_timed_runs": True,
        })
    return records


def _row_template(path: Path, instance, seed: int, label: str, input_hash: str,
                  config, config_hash: str, solver_hash: str, time_limit: float) -> dict:
    row = {field: "" for field in m7.SUMMARY_FIELDS}
    row.update(
        instance=instance.name, input_format="solomon_txt", input_sha256=input_hash,
        seed=seed, threads=1, status="pending", feasible=False, numeric_rule=m7.NUMERIC_RULE_ID,
        time_limit_seconds=time_limit, max_iterations="",
        fleet_attempts_per_k=config.fleet_attempts_per_k, max_moves=config.max_moves,
        vehicle_limit=instance.vehicle_count, code_sha256=solver_hash,
        diagnostics_enabled=False, variant=label, config_sha256=config_hash,
    )
    return row


def _run_one(*, path: Path, label: str, seed: int, out: Path, config, config_hash: str,
             input_hash: str, solver_hash: str, time_limit: float) -> dict:
    _check_solver_hash(solver_hash)
    if m7._sha256(path) != input_hash:
        raise RuntimeError(f"input changed during confirmation: {path}")
    instance = read_solomon(path)
    result = solve(instance, config)
    _check_solver_hash(solver_hash)
    artifact_dir = out / label / instance.name / f"seed-{seed}"
    artifact = m7.write_run(instance, result, config, artifact_dir,
                            source_path=path, plots=False)
    _check_solver_hash(solver_hash)
    verdict = m7.validate_json(instance, artifact / "solution.json")
    if not verdict.feasible:
        raise ValueError(f"{label}/{instance.name}/seed-{seed}: saved routes failed validation")
    payload = json.loads((artifact / "solution.json").read_text(encoding="utf-8"))
    if verdict.objective != result.evaluation.objective:
        raise ValueError(f"{label}/{instance.name}/seed-{seed}: persisted objective differs")
    if payload.get("input_sha256") != input_hash or payload.get("code_sha256") != solver_hash:
        raise ValueError(f"{label}/{instance.name}/seed-{seed}: persisted hashes differ")
    row = _row_template(path, instance, seed, label, input_hash, config,
                        config_hash, solver_hash, time_limit)
    row.update(
        status="ok", feasible=True, vehicles=verdict.vehicles,
        distance_ticks=verdict.distance, distance=verdict.distance / 1000,
        first_feasible_seconds=round(result.first_feasible_seconds, 6),
        runtime_seconds=round(result.runtime_seconds, 6),
        iterations=result.iterations, stop_reason=result.stop_reason,
        reference_status="missing",
    )
    return row


def _pair_rows(baseline: list[dict[str, str]], candidate: list[dict[str, str]]) -> list[dict]:
    index = {(row["instance"], row["seed"]): row for row in candidate}
    if len(index) != len(candidate):
        raise ValueError("candidate batch contains duplicate instance/seed rows")
    paired = []
    for base in baseline:
        key = (base["instance"], base["seed"])
        other = index.get(key)
        if other is None:
            raise ValueError(f"candidate batch is missing {key}")
        base_obj = (int(base["vehicles"]), int(base["distance_ticks"]))
        other_obj = (int(other["vehicles"]), int(other["distance_ticks"]))
        vehicle_result, distance_result, delta, percent = m7._comparison(base_obj, other_obj)
        paired.append({
            "instance": key[0], "seed": int(key[1]),
            "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
            "candidate_vehicles": other_obj[0], "candidate_distance_ticks": other_obj[1],
            "vehicle_result": vehicle_result,
            "same_vehicle_distance_result": distance_result,
            "distance_delta_ticks": "" if delta is None else delta,
            "distance_delta_percent": "" if percent is None else f"{percent:.6f}",
            "baseline_runtime_seconds": float(base["runtime_seconds"]),
            "candidate_runtime_seconds": float(other["runtime_seconds"]),
            "runtime_delta_seconds": float(other["runtime_seconds"]) - float(base["runtime_seconds"]),
            "baseline_iterations": int(base["iterations"]),
            "candidate_iterations": int(other["iterations"]),
        })
    if set(index) != {(row["instance"], row["seed"]) for row in baseline}:
        raise ValueError("baseline and candidate run coverage differs")
    return paired


def _outcome_counts(rows: list[dict]) -> dict[str, int]:
    counts = {
        "vehicle_improved": 0, "vehicle_worse": 0, "vehicle_tie": 0,
        "same_count_distance_better": 0, "same_count_distance_worse": 0,
        "same_count_distance_tie": 0,
    }
    for row in rows:
        vehicle = row["vehicle_result"]
        if vehicle == "improved":
            counts["vehicle_improved"] += 1
        elif vehicle == "worse":
            counts["vehicle_worse"] += 1
        else:
            counts["vehicle_tie"] += 1
            counts[f"same_count_distance_{row['same_vehicle_distance_result']}"] += 1
    return counts


def _build_report(baseline: list[dict[str, str]], candidate: list[dict[str, str]]) -> tuple[list[dict], dict]:
    paired = _pair_rows(baseline, candidate)
    # Batch rows are kept as booleans in memory and serialized as "True" in
    # CSV. Match the CSV helper's accepted shape for the shared M7 reducer.
    baseline_best = m7._best_by_instance(
        [{**row, "feasible": str(row["feasible"])} for row in baseline], "baseline",
    )
    candidate_best = m7._best_by_instance(
        [{**row, "feasible": str(row["feasible"])} for row in candidate], "candidate",
    )
    best_rows = []
    best_gaps = []
    for name in sorted(baseline_best):
        base_obj, base_seed = baseline_best[name]
        candidate_obj, candidate_seed = candidate_best[name]
        vehicle_result, distance_result, delta, percent = m7._comparison(base_obj, candidate_obj)
        best_rows.append({
            "instance": name,
            "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
            "baseline_best_seed": base_seed,
            "candidate_vehicles": candidate_obj[0], "candidate_distance_ticks": candidate_obj[1],
            "candidate_best_seed": candidate_seed, "vehicle_result": vehicle_result,
            "same_vehicle_distance_result": distance_result,
            "distance_delta_ticks": "" if delta is None else delta,
            "distance_delta_percent": "" if percent is None else f"{percent:.6f}",
        })
        if percent is not None:
            best_gaps.append(percent)
    runtimes_base = [float(row["runtime_seconds"]) for row in baseline]
    runtimes_candidate = [float(row["runtime_seconds"]) for row in candidate]
    summary = {
        "schema_version": 1,
        "paired_runs": len(paired),
        "per_seed_outcomes": _outcome_counts(paired),
        "best_per_instance_outcomes": _outcome_counts([
            {"vehicle_result": row["vehicle_result"],
             "same_vehicle_distance_result": row["same_vehicle_distance_result"]}
            for row in best_rows
        ]),
        "best_same_vehicle_distance_p90_percent_nearest_rank": m7._nearest_rank(best_gaps, 0.9),
        "runtime_seconds": {
            "baseline_median": statistics.median(runtimes_base),
            "baseline_p90_nearest_rank": m7._nearest_rank(runtimes_base, 0.9),
            "candidate_median": statistics.median(runtimes_candidate),
            "candidate_p90_nearest_rank": m7._nearest_rank(runtimes_candidate, 0.9),
            "paired_median_delta": statistics.median(
                row["runtime_delta_seconds"] for row in paired
            ),
        },
        "iterations": {
            "baseline_total": sum(int(row["iterations"]) for row in baseline),
            "candidate_total": sum(int(row["iterations"]) for row in candidate),
            "baseline_ils_runs": sum(int(row["iterations"]) > 0 for row in baseline),
            "candidate_ils_runs": sum(int(row["iterations"]) > 0 for row in candidate),
        },
        "best_by_instance": best_rows,
    }
    return paired, summary


def run(args: argparse.Namespace) -> int:
    paths = sorted(m7._input_paths(args.data, args.instances), key=lambda path: path.stem)
    if args.seeds != sorted(set(args.seeds)) or not args.seeds or any(seed < 0 for seed in args.seeds):
        raise ValueError("seeds must be unique, nonnegative, and in ascending order")
    if args.time_limit <= 0 or not math.isfinite(args.time_limit):
        raise ValueError("--time-limit must be finite and positive")
    changes = m7._custom_changes(args.set)
    if not changes:
        raise ValueError("provide at least one --set KEY=VALUE candidate override")
    configs = {
        "baseline": lambda seed: m7._config(seed, args.time_limit),
        "candidate": lambda seed: m7._config(seed, args.time_limit, changes),
    }
    config0 = {label: configs[label](0) for label in ("baseline", "candidate")}
    config_hashes = {label: m7._config_hash(config0[label]) for label in config0}
    config_values = {label: asdict(config0[label]) for label in config0}
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")

    started_at = _utc_now()
    solver_hash = m7.code_fingerprint()
    input_hashes = {path.stem: m7._sha256(path) for path in paths}
    warmup_input_hash = m7._sha256(args.data / "C103.txt") if (args.data / "C103.txt").exists() else None
    if warmup_input_hash is not None:
        input_hashes.setdefault("C103", warmup_input_hash)
    manifest = {
        "schema_version": 1, "status": "warming_up",
        "started_at_utc": started_at, "timed_started_at_utc": None,
        "finished_at_utc": None, "data_directory": str(args.data.resolve()),
        "instances": [path.stem for path in paths], "seeds": args.seeds,
        "time_limit_seconds": args.time_limit, "threads": 1,
        "numeric_rule": m7.NUMERIC_RULE_ID,
        "runner_sha256": m7._sha256(Path(__file__).resolve()),
        "solver_code_sha256": solver_hash, "input_sha256": input_hashes,
        "warmup_input_sha256": warmup_input_hash,
        "environment": m7._environment(), "configs": {
            label: {"values": config_values[label], "sha256": config_hashes[label]}
            for label in configs
        },
        "warmups": [], "key_execution_order": [],
    }
    m7._write_json(out / "manifest.json", manifest)
    records: dict[str, list[dict]] = {"baseline": [], "candidate": []}
    plan = _execution_plan(paths, args.seeds)
    fields = m7.SUMMARY_FIELDS
    try:
        if warmup_input_hash is None:
            raise ValueError(f"C103.txt is required for the common warm-up: {args.data / 'C103.txt'}")
        manifest["warmups"] = _warm_up(configs, args.data, solver_hash, warmup_input_hash)
        manifest["timed_started_at_utc"] = _utc_now()
        manifest["status"] = "running"
        m7._write_json(out / "manifest.json", manifest)
        for key in plan:
            manifest["key_execution_order"].append(key)
            m7._write_json(out / "manifest.json", manifest)
            for label in key["order"]:
                path = args.data / f"{key['instance']}.txt"
                seed = key["seed"]
                config = configs[label](seed)
                row = _run_one(
                    path=path, label=label, seed=seed, out=out, config=config,
                    config_hash=config_hashes[label], input_hash=input_hashes[path.stem],
                    solver_hash=solver_hash, time_limit=args.time_limit,
                )
                records[label].append(row)
                m7._csv_write(out / label / "batch_summary.csv", records[label], fields)
        finished_at = _utc_now()
        for label in ("baseline", "candidate"):
            summary = {
                "instances": len(paths), "instance_names": sorted(path.stem for path in paths),
                "runs": len(records[label]), "successful": len(records[label]), "failed": 0,
                "seeds": args.seeds, "time_limit_seconds": args.time_limit,
                "max_iterations": None,
                "fleet_attempts_per_k": config0[label].fleet_attempts_per_k,
                "max_moves": config0[label].max_moves,
                "diagnostics_enabled": False, "config": config_values[label],
                "config_sha256": config_hashes[label], "variant": label,
                "input_format": "solomon_txt", "threads": 1,
                "numeric_rule": m7.NUMERIC_RULE_ID, "code_sha256": solver_hash,
                "runner_sha256": manifest["runner_sha256"], "input_sha256": input_hashes,
                "started_at_utc": manifest["timed_started_at_utc"],
                "finished_at_utc": finished_at, "environment": manifest["environment"],
            }
            m7._write_json(out / label / "batch_summary.json", summary)

        paired, report = _build_report(records["baseline"], records["candidate"])
        pair_fields = tuple(paired[0]) if paired else ()
        best_fields = tuple(report["best_by_instance"][0]) if report["best_by_instance"] else ()
        m7._csv_write(out / "paired_runs.csv", paired, pair_fields)
        m7._csv_write(out / "paired_best_by_instance.csv", report["best_by_instance"], best_fields)
        (out / "paired_summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        manifest["status"] = "completed"
        manifest["finished_at_utc"] = finished_at
        manifest["batch_summary_sha256"] = {
            label: m7._sha256(out / label / "batch_summary.csv") for label in records
        }
        m7._write_json(out / "manifest.json", manifest)
        print(json.dumps({
            "runs_per_label": len(records["baseline"]),
            "per_seed_outcomes": report["per_seed_outcomes"],
            "best_per_instance_outcomes": report["best_per_instance_outcomes"],
            "runtime_seconds": report["runtime_seconds"],
        }, indent=2))
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["finished_at_utc"] = _utc_now()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        m7._write_json(out / "manifest.json", manifest)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=m7.DATA_DIR)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(m7.SEEDS))
    parser.add_argument("--instances", type=str, nargs="+", help="optional instance stems")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="candidate Config override; repeat for a combination")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
