"""Run and analyse the frozen M7 solver ablations.

Examples (from ``vrptw_solver``)::

    python benchmarks/m7_experiment.py run --out runs/m7_seed012_0p5s
    python benchmarks/m7_experiment.py run --short --out runs/m7_short
    python benchmarks/m7_experiment.py analyse --experiment runs/m7_seed012_0p5s

Runs are deliberately sequential and single-threaded. The default experiment
uses all ten frozen configurations, all top-level Solomon inputs, seeds 0/1/2,
and a 0.5 second solver budget. It does not run an experiment at import time.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
from typing import Iterable

SOLVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOLVER_DIR / "src"))

from vrptw import Config, read_solomon, solve, validate_solution  # noqa: E402
from vrptw.local_search import OPERATORS  # noqa: E402
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint, validate_json, write_run  # noqa: E402

DATA_DIR = SOLVER_DIR / "data"
SEEDS = (0, 1, 2)
SHORT_INSTANCES = ("C103", "C104", "R101", "RC101")

# Keep these values explicit. Config defaults may evolve after M6, but the
# baseline and each one-factor ablation must continue to mean the same thing.
FROZEN_BASELINE = {
    "fleet_attempts_per_k": 100,
    "max_moves": 2,
    "remove_min": 3,
    "remove_max": 8,
    "restart_after": 20,
    "construction_order": "due",
    "repair_order": "input",
    "repair_strategy": "cheapest",
    "fleet_time_fraction": 1.0,
}

VARIANTS: dict[str, dict[str, object]] = {
    "baseline": {},
    "attempts10": {"fleet_attempts_per_k": 10},
    "reserve20": {"fleet_time_fraction": 0.8},
    "moves8": {"max_moves": 8},
    "removal2": {"remove_min": 2, "remove_max": 4},
    "restart5": {"restart_after": 5},
    "repair_due": {"repair_order": "due"},
    "repair_slack": {"repair_order": "slack"},
    "regret2": {"repair_strategy": "regret2"},
    "construction_slack": {"construction_order": "slack"},
}

SUMMARY_FIELDS = (
    "instance", "input_format", "input_sha256", "seed", "threads", "status",
    "feasible", "vehicles", "distance_ticks", "distance", "first_feasible_seconds",
    "runtime_seconds", "iterations", "stop_reason", "numeric_rule", "reference_status",
    "reference_vehicles", "reference_distance", "distance_gap_percent", "error",
    "time_limit_seconds", "max_iterations", "fleet_attempts_per_k", "max_moves",
    "vehicle_limit", "code_sha256", "diagnostics_enabled", "variant", "config_sha256",
)

COMPARE_FIELDS = (
    "variant", "instance", "family", "baseline_vehicles", "baseline_distance_ticks",
    "baseline_best_seed", "variant_vehicles", "variant_distance_ticks", "variant_best_seed",
    "vehicle_result", "same_vehicle_distance_result", "distance_delta_ticks",
    "distance_delta_percent", "m6_vehicles", "m6_distance_ticks", "m6_best_seed",
    "m6_vehicle_result", "m6_same_vehicle_distance_result", "m6_distance_delta_ticks",
    "m6_distance_delta_percent",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _environment() -> dict[str, object]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def _family(name: str) -> str:
    return "RC" if name.startswith("RC") else name[0]


def _config(seed: int, time_limit: float, changes: dict[str, object] | None = None) -> Config:
    values = dict(FROZEN_BASELINE)
    if changes:
        values.update(changes)
    # Explicitly freeze the remaining M6 search settings used by this runner.
    return Config(
        seed=seed,
        max_iterations=None,
        time_limit_seconds=time_limit,
        fleet_attempts_per_k=int(values["fleet_attempts_per_k"]),
        max_moves=int(values["max_moves"]),
        construction_order=str(values["construction_order"]),
        repair_order=str(values["repair_order"]),
        repair_strategy=str(values["repair_strategy"]),
        fleet_time_fraction=float(values["fleet_time_fraction"]),
        search_strategy="first",
        operators=OPERATORS,
        remove_min=int(values["remove_min"]),
        remove_max=int(values["remove_max"]),
        restart_after=int(values["restart_after"]),
        diagnostics=False,
    )


def _config_hash(config: Config) -> str:
    values = asdict(config)
    values["seed"] = 0
    return _canonical_hash(values)


def _csv_write(path: Path, rows: list[dict], fields: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _input_paths(data: Path, selected: list[str] | None) -> list[Path]:
    paths = sorted(data.glob("*.txt"))
    if not paths:
        raise ValueError(f"no top-level Solomon .txt files in {data}")
    if selected is None:
        return paths
    by_name = {path.stem: path for path in paths}
    unknown = sorted(set(selected) - by_name.keys())
    if unknown:
        raise ValueError(f"unknown instance(s) in {data}: {', '.join(unknown)}")
    if len(selected) != len(set(selected)):
        raise ValueError("instance filter contains duplicates")
    return [by_name[name] for name in selected]


def _custom_changes(items: list[str]) -> dict[str, object]:
    allowed = {
        "fleet_attempts_per_k": int,
        "max_moves": int,
        "remove_min": int,
        "remove_max": int,
        "restart_after": int,
        "fleet_time_fraction": float,
        "construction_order": str,
        "repair_order": str,
        "repair_strategy": str,
    }
    parsed: dict[str, object] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"custom override must be KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        if key not in allowed:
            raise ValueError(f"unsupported custom Config field {key!r}")
        if key in parsed:
            raise ValueError(f"custom override repeats {key!r}")
        try:
            parsed[key] = allowed[key](raw)
        except ValueError as exc:
            raise ValueError(f"invalid value for {key}: {raw!r}") from exc
    return parsed


def _variant_changes(names: list[str], custom_name: str, custom: dict[str, object]) -> dict[str, dict[str, object]]:
    if "baseline" not in names:
        raise ValueError("--variants must include baseline for paired analysis")
    changes = {name: dict(VARIANTS[name]) for name in names}
    if custom:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", custom_name):
            raise ValueError("custom variant name may contain only letters, digits, '_' and '-'")
        if custom_name in changes or custom_name in VARIANTS:
            raise ValueError(f"custom variant name {custom_name!r} is already in use")
        changes[custom_name] = custom
    return changes


def run_experiment(args: argparse.Namespace) -> int:
    paths = _input_paths(args.data, args.instances)
    if args.seeds != sorted(set(args.seeds)):
        raise ValueError("seeds must be unique and in ascending order for reproducible runs")
    if not args.seeds or any(seed < 0 for seed in args.seeds):
        raise ValueError("provide one or more nonnegative seeds")
    if args.time_limit <= 0 or not math.isfinite(args.time_limit):
        raise ValueError("--time-limit must be finite and positive")
    custom = _custom_changes(args.set)
    variant_changes = _variant_changes(args.variants, args.custom_name, custom)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")

    started_at = _utc_now()
    input_hashes = {path.stem: _sha256(path) for path in paths}
    variant_configs = {
        name: asdict(_config(0, args.time_limit, changes))
        for name, changes in variant_changes.items()
    }
    manifest = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": started_at,
        "finished_at_utc": None,
        "data_directory": str(args.data.resolve()),
        "instances": [path.stem for path in paths],
        "seeds": args.seeds,
        "time_limit_seconds": args.time_limit,
        "threads": 1,
        "numeric_rule": NUMERIC_RULE_ID,
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "solver_code_sha256": code_fingerprint(),
        "input_sha256": input_hashes,
        "environment": _environment(),
        "variants": {
            name: {"config": variant_configs[name],
                   "config_sha256": _canonical_hash(variant_configs[name]),
                   "status": "pending", "runs": 0, "successful": 0, "failed": 0}
            for name in variant_changes
        },
    }
    _write_json(out / "experiment.json", manifest)

    references_path = args.data / "solomon_bks.json"
    references = json.loads(references_path.read_text(encoding="utf-8")) if references_path.exists() else {}
    try:
        for variant, changes in variant_changes.items():
            variant_started_at = _utc_now()
            variant_dir = out / variant
            variant_dir.mkdir()
            config0 = _config(args.seeds[0], args.time_limit, changes)
            config_hash = _config_hash(config0)
            records: list[dict] = []
            for path in paths:
                instance = read_solomon(path)
                for seed in args.seeds:
                    config = _config(seed, args.time_limit, changes)
                    row = {field: "" for field in SUMMARY_FIELDS}
                    row.update(
                        instance=instance.name, input_format="solomon_txt",
                        input_sha256=input_hashes[path.stem], seed=seed, threads=1,
                        status="error", feasible=False, numeric_rule=NUMERIC_RULE_ID,
                        time_limit_seconds=args.time_limit, max_iterations="",
                        fleet_attempts_per_k=config.fleet_attempts_per_k,
                        max_moves=config.max_moves, vehicle_limit=instance.vehicle_count,
                        code_sha256=manifest["solver_code_sha256"], diagnostics_enabled=False,
                        variant=variant, config_sha256=config_hash,
                    )
                    try:
                        result = solve(instance, config)
                        artifact = write_run(
                            instance, result, config,
                            variant_dir / instance.name / f"seed-{seed}",
                            source_path=path, plots=False,
                        )
                        # Re-read the persisted visit order for every run. The
                        # in-memory Result is not accepted as validation proof.
                        verdict = validate_json(instance, artifact / "solution.json")
                        if not verdict.feasible:
                            raise ValueError(f"saved route failed validation: {verdict.first_violation}")
                        payload = json.loads((artifact / "solution.json").read_text(encoding="utf-8"))
                        if verdict.objective != result.evaluation.objective:
                            raise ValueError("saved route objective differs from solve result")
                        row.update(
                            status="ok", feasible=True, vehicles=verdict.vehicles,
                            distance_ticks=verdict.distance, distance=verdict.distance / 1000,
                            first_feasible_seconds=round(result.first_feasible_seconds, 6),
                            runtime_seconds=round(result.runtime_seconds, 6),
                            iterations=result.iterations, stop_reason=result.stop_reason,
                        )
                        if payload["input_sha256"] != row["input_sha256"]:
                            raise ValueError("saved artifact input hash differs")
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
                    except Exception as exc:  # retain a complete audit row on a failed solve
                        row.update(status="error", feasible=False,
                                   error=f"{type(exc).__name__}: {exc}")
                    records.append(row)
                    _csv_write(variant_dir / "batch_summary.csv", records, SUMMARY_FIELDS)
                    print(f"{variant}: {instance.name} seed={seed}: {row['status']} "
                          f"vehicles={row['vehicles']} distance={row['distance']} {row['error']}")

            successful = sum(row["status"] == "ok" for row in records)
            summary = {
                "instances": len(paths), "runs": len(records), "successful": successful,
                "failed": len(records) - successful, "seeds": args.seeds,
                "time_limit_seconds": args.time_limit, "max_iterations": None,
                "fleet_attempts_per_k": config0.fleet_attempts_per_k,
                "max_moves": config0.max_moves, "diagnostics_enabled": False,
                "config": asdict(config0), "config_sha256": config_hash,
                "variant": variant, "input_format": "solomon_txt", "threads": 1,
                "numeric_rule": NUMERIC_RULE_ID,
                "code_sha256": manifest["solver_code_sha256"],
                "runner_sha256": manifest["runner_sha256"],
                "input_sha256": input_hashes,
                "started_at_utc": variant_started_at,
                "finished_at_utc": _utc_now(), "environment": manifest["environment"],
            }
            _write_json(variant_dir / "batch_summary.json", summary)
            manifest["variants"][variant].update(
                status="completed" if successful == len(records) else "failed",
                runs=len(records), successful=successful, failed=len(records) - successful,
                batch_summary_sha256=_sha256(variant_dir / "batch_summary.csv"),
            )
            _write_json(out / "experiment.json", manifest)
    finally:
        manifest["finished_at_utc"] = _utc_now()
        all_done = all(item["status"] == "completed" for item in manifest["variants"].values())
        manifest["status"] = "completed" if all_done else "failed"
        _write_json(out / "experiment.json", manifest)
    return 0 if manifest["status"] == "completed" else 1


def _best_by_instance(rows: list[dict[str, str]], label: str) -> dict[str, tuple[tuple[int, int], int]]:
    best: dict[str, tuple[tuple[int, int], int]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.get("instance", ""), row.get("seed", ""))
        if key in seen:
            raise ValueError(f"{label} has duplicate instance/seed row: {key}")
        seen.add(key)
        if row.get("status") != "ok" or row.get("feasible") not in ("True", "true", "1"):
            raise ValueError(f"{label} contains a failed or infeasible row: {key}")
        try:
            objective = (int(row["vehicles"]), int(row["distance_ticks"]))
            seed = int(row["seed"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{label} has an invalid objective at {key}") from exc
        current = best.get(key[0])
        if current is None or objective < current[0]:
            best[key[0]] = (objective, seed)
    return best


def _check_batch_coverage(batch: Path, expected: set[tuple[str, str]], data: Path,
                          label: str, *, validate_artifacts: bool) -> tuple[dict, list[dict[str, str]]]:
    meta_path, csv_path = batch / "batch_summary.json", batch / "batch_summary.csv"
    if not meta_path.exists() or not csv_path.exists():
        raise ValueError(f"{label} is missing batch_summary.json or batch_summary.csv")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = _read_csv(csv_path)
    actual = {(row.get("instance", ""), row.get("seed", "")) for row in rows}
    if len(actual) != len(rows) or actual != expected:
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        raise ValueError(f"{label} coverage differs; missing={missing}, extra={extra}")
    if metadata.get("runs") != len(rows) or metadata.get("numeric_rule") != NUMERIC_RULE_ID:
        raise ValueError(f"{label} metadata run count or numeric rule differs")
    best = _best_by_instance(rows, label)
    if validate_artifacts:
        for row in rows:
            source = data / f"{row['instance']}.txt"
            if not source.exists() or _sha256(source) != row.get("input_sha256"):
                raise ValueError(f"{label} input hash differs for {row['instance']}")
            if row.get("numeric_rule") != NUMERIC_RULE_ID:
                raise ValueError(f"{label} numeric rule differs for {row['instance']}")
            if metadata.get("code_sha256") != row.get("code_sha256"):
                raise ValueError(f"{label} code hash differs for {row['instance']}")
            artifact = batch / row["instance"] / f"seed-{row['seed']}" / "solution.json"
            if not artifact.exists():
                raise ValueError(f"{label} missing saved solution for {row['instance']}/seed-{row['seed']}")
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            verdict = validate_solution(read_solomon(source), payload.get("routes", []))
            objective = (int(row["vehicles"]), int(row["distance_ticks"]))
            if not verdict.feasible or verdict.objective != objective:
                raise ValueError(f"{label} saved solution is invalid for {row['instance']}/seed-{row['seed']}")
            if (payload.get("vehicles"), payload.get("distance_ticks")) != objective:
                raise ValueError(f"{label} saved objective differs for {row['instance']}/seed-{row['seed']}")
            if payload.get("input_sha256") != row.get("input_sha256"):
                raise ValueError(f"{label} saved input hash differs for {row['instance']}/seed-{row['seed']}")
            if payload.get("code_sha256") != row.get("code_sha256"):
                raise ValueError(f"{label} saved code hash differs for {row['instance']}/seed-{row['seed']}")
            if payload.get("numeric_rule") != NUMERIC_RULE_ID or payload.get("seed") != int(row["seed"]):
                raise ValueError(f"{label} saved protocol fields differ for {row['instance']}/seed-{row['seed']}")
            if metadata.get("config") is not None and payload.get("config") != dict(
                metadata["config"], seed=int(row["seed"]),
            ):
                raise ValueError(f"{label} saved configuration differs for {row['instance']}/seed-{row['seed']}")
    return metadata, rows


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _config_hash_from_dict(values: dict) -> str:
    normalized = dict(values)
    normalized["seed"] = 0
    return _canonical_hash(normalized)


def _comparison(base: tuple[int, int], candidate: tuple[int, int]) -> tuple[str, str, int | None, float | None]:
    if candidate[0] < base[0]:
        vehicle_result = "improved"
    elif candidate[0] > base[0]:
        vehicle_result = "worse"
    else:
        vehicle_result = "same"
    if candidate[0] != base[0]:
        return vehicle_result, "not_compared", None, None
    delta = candidate[1] - base[1]
    result = "better" if delta < 0 else "worse" if delta > 0 else "tie"
    percent = 100 * delta / base[1] if base[1] else (0.0 if delta == 0 else None)
    return vehicle_result, result, delta, percent


def _variant_runtime(rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[dict[str, str]]] = {"all": rows, "C": [], "R": [], "RC": []}
    for row in rows:
        grouped[_family(row["instance"])].append(row)
    result: dict[str, dict[str, object]] = {}
    for family, group in grouped.items():
        runtimes = [float(row["runtime_seconds"]) for row in group]
        first = [float(row["first_feasible_seconds"]) for row in group]
        iterations = [int(row["iterations"]) for row in group]
        result[family] = {
            "runs": len(group),
            "ils_runs": sum(value > 0 for value in iterations),
            "ils_iterations": sum(iterations),
            "runtime_median_seconds": statistics.median(runtimes) if runtimes else None,
            "runtime_p90_seconds_nearest_rank": _nearest_rank(runtimes, 0.9),
            "first_feasible_median_seconds": statistics.median(first) if first else None,
            "first_feasible_p90_seconds_nearest_rank": _nearest_rank(first, 0.9),
        }
    return result


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment = args.experiment
    data = args.data
    manifest = json.loads((experiment / "experiment.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise ValueError(f"experiment status is {manifest.get('status')!r}; only completed runs can be analysed")
    instance_names = manifest.get("instances", [])
    seeds = manifest.get("seeds", [])
    expected = {(name, str(seed)) for name in instance_names for seed in seeds}
    if not expected:
        raise ValueError("experiment manifest has no instance/seed coverage")
    variant_names = list(manifest.get("variants", {}))
    if "baseline" not in variant_names:
        raise ValueError("experiment has no baseline variant")
    loaded: dict[str, tuple[dict, list[dict[str, str]], dict[str, tuple[tuple[int, int], int]]]] = {}
    for variant in variant_names:
        batch = experiment / variant
        metadata, rows = _check_batch_coverage(
            batch, expected, data, variant, validate_artifacts=args.validate_artifacts,
        )
        if manifest["variants"][variant].get("status") != "completed":
            raise ValueError(f"variant {variant} is not complete")
        if metadata.get("variant") != variant:
            raise ValueError(f"variant label differs in {variant}/batch_summary.json")
        expected_config_hash = manifest["variants"][variant].get("config_sha256")
        if metadata.get("config_sha256") != expected_config_hash:
            raise ValueError(f"configuration hash differs for variant {variant}")
        metadata_config = metadata.get("config")
        if not isinstance(metadata_config, dict) or _config_hash_from_dict(metadata_config) != expected_config_hash:
            raise ValueError(f"configuration values differ for variant {variant}")
        loaded[variant] = (metadata, rows, _best_by_instance(rows, variant))
    baseline_best = loaded["baseline"][2]
    if set(baseline_best) != set(instance_names):
        raise ValueError("baseline best-instance coverage differs from manifest")

    m6_best = None
    m6_rows = None
    if args.m6_csv is not None:
        m6_rows = _read_csv(args.m6_csv)
        m6_keys = {(row.get("instance", ""), row.get("seed", "")) for row in m6_rows}
        if not expected.issubset(m6_keys):
            raise ValueError(f"M6 CSV is missing required instance/seed rows: {sorted(expected - m6_keys)}")
        if m6_rows and "time_limit_seconds" in m6_rows[0]:
            if any(not row.get("time_limit_seconds")
                   or float(row["time_limit_seconds"]) != float(manifest["time_limit_seconds"])
                   for row in m6_rows if (row.get("instance"), row.get("seed")) in expected):
                raise ValueError("M6 CSV time budget differs from the M7 experiment")
        else:
            raise ValueError("M6 CSV does not record time_limit_seconds")
        m6_best = _best_by_instance(m6_rows, "M6 CSV")
        if not set(instance_names).issubset(m6_best):
            missing = sorted(set(instance_names) - set(m6_best))
            raise ValueError(f"M6 CSV is missing instances: {missing}")
        for row in m6_rows:
            name = row.get("instance", "")
            if name in instance_names and row.get("input_sha256"):
                if row["input_sha256"] != manifest["input_sha256"].get(name):
                    raise ValueError(f"M6 input hash differs for {name}")
            if name in instance_names and row.get("numeric_rule") != NUMERIC_RULE_ID:
                raise ValueError(f"M6 numeric rule differs for {name}")

    comparisons: list[dict[str, object]] = []
    comparison_summaries: dict[str, dict[str, object]] = {}
    for variant in variant_names:
        current_best = loaded[variant][2]
        counts = {
            "vehicle_improved": 0, "vehicle_worse": 0,
            "same_count_distance_better": 0, "same_count_distance_worse": 0,
            "same_count_distance_tie": 0,
        }
        gaps: list[float] = []
        for name in sorted(instance_names):
            base_obj, base_seed = baseline_best[name]
            candidate_obj, candidate_seed = current_best[name]
            vehicle_result, distance_result, delta, percent = _comparison(base_obj, candidate_obj)
            counts["vehicle_improved" if vehicle_result == "improved" else
                   "vehicle_worse" if vehicle_result == "worse" else "same_count_distance_" + distance_result] += 1
            if percent is not None:
                gaps.append(percent)
            record: dict[str, object] = {
                "variant": variant, "instance": name, "family": _family(name),
                "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
                "baseline_best_seed": base_seed, "variant_vehicles": candidate_obj[0],
                "variant_distance_ticks": candidate_obj[1], "variant_best_seed": candidate_seed,
                "vehicle_result": vehicle_result,
                "same_vehicle_distance_result": distance_result,
                "distance_delta_ticks": "" if delta is None else delta,
                "distance_delta_percent": "" if percent is None else f"{percent:.6f}",
            }
            if m6_best is not None:
                m6_obj, m6_seed = m6_best[name]
                m6_vehicle_result, m6_distance_result, m6_delta, m6_percent = _comparison(m6_obj, candidate_obj)
                record.update(
                    m6_vehicles=m6_obj[0], m6_distance_ticks=m6_obj[1], m6_best_seed=m6_seed,
                    m6_vehicle_result=m6_vehicle_result,
                    m6_same_vehicle_distance_result=m6_distance_result,
                    m6_distance_delta_ticks="" if m6_delta is None else m6_delta,
                    m6_distance_delta_percent="" if m6_percent is None else f"{m6_percent:.6f}",
                )
            comparisons.append(record)
        comparison_summaries[variant] = {
            **counts,
            "same_vehicle_distance_p90_percent_nearest_rank": _nearest_rank(gaps, 0.9),
            "same_vehicle_distance_comparisons": len(gaps),
        }

    out = args.out or (experiment / "analysis")
    out.mkdir(parents=True, exist_ok=True)
    _csv_write(out / "m7_comparison.csv", comparisons, COMPARE_FIELDS)
    summary = {
        "schema_version": 1,
        "experiment": str(experiment.resolve()),
        "experiment_manifest_sha256": _sha256(experiment / "experiment.json"),
        "m6_csv": str(args.m6_csv.resolve()) if args.m6_csv else None,
        "m6_csv_sha256": _sha256(args.m6_csv) if args.m6_csv else None,
        "nearest_rank_definition": "sorted values at index ceil(p*n)-1; p=0.90",
        "validation_enabled": args.validate_artifacts,
        "variants": {},
    }
    for variant in variant_names:
        metadata, rows, _ = loaded[variant]
        summary["variants"][variant] = {
            "config": metadata["config"],
            "config_sha256": metadata["config_sha256"],
            "runs": len(rows),
            "runtime_and_ils_by_family": _variant_runtime(rows),
            "comparison_vs_baseline": comparison_summaries[variant],
        }
        if m6_best is not None:
            summary["variants"][variant]["comparison_vs_m6"] = _comparison_summary(
                loaded[variant][2], m6_best, instance_names,
            )
    _write_json(out / "m7_summary.json", summary)
    _print_summary(summary)
    return 0


def _comparison_summary(current: dict[str, tuple[tuple[int, int], int]],
                        baseline: dict[str, tuple[tuple[int, int], int]],
                        names: list[str]) -> dict[str, object]:
    counts = {"vehicle_improved": 0, "vehicle_worse": 0,
              "same_count_distance_better": 0, "same_count_distance_worse": 0,
              "same_count_distance_tie": 0}
    gaps = []
    for name in names:
        vehicle_result, distance_result, _, percent = _comparison(baseline[name][0], current[name][0])
        key = ("vehicle_improved" if vehicle_result == "improved" else
               "vehicle_worse" if vehicle_result == "worse" else "same_count_distance_" + distance_result)
        counts[key] += 1
        if percent is not None:
            gaps.append(percent)
    return {**counts,
            "same_vehicle_distance_p90_percent_nearest_rank": _nearest_rank(gaps, 0.9),
            "same_vehicle_distance_comparisons": len(gaps)}


def _print_summary(summary: dict) -> None:
    print("variant | vehicles better/worse | same-count distance better/worse/tie | distance p90% | runtime median/p90 s | R ILS runs/iterations | RC ILS runs/iterations")
    for variant, item in summary["variants"].items():
        comparison = item["comparison_vs_baseline"]
        families = item["runtime_and_ils_by_family"]
        p90 = comparison["same_vehicle_distance_p90_percent_nearest_rank"]
        r = families["R"]
        rc = families["RC"]
        all_stats = families["all"]
        print(
            f"{variant} | {comparison['vehicle_improved']}/{comparison['vehicle_worse']} | "
            f"{comparison['same_count_distance_better']}/{comparison['same_count_distance_worse']}/"
            f"{comparison['same_count_distance_tie']} | "
            f"{p90 if p90 is not None else 'n/a'} | "
            f"{all_stats['runtime_median_seconds']:.6f}/{all_stats['runtime_p90_seconds_nearest_rank']:.6f} | "
            f"{r['ils_runs']}/{r['ils_iterations']} | {rc['ils_runs']}/{rc['ils_iterations']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run all or selected M7 variants sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    selection = run.add_mutually_exclusive_group()
    selection.add_argument("--instances", nargs="+", help="selected instance stems")
    selection.add_argument("--short", action="store_true",
                           help="use C103, C104, R101, and RC101 as a representative filter")
    run.add_argument("--variants", nargs="+", choices=tuple(VARIANTS), default=list(VARIANTS))
    run.add_argument("--custom-name", default="custom")
    run.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                     help="add a final custom combination; repeat for multiple Config fields")

    analyse = commands.add_parser("analyse", help="compare saved variants with baseline and optional M6")
    analyse.add_argument("--experiment", type=Path, required=True)
    analyse.add_argument("--data", type=Path, default=DATA_DIR)
    analyse.add_argument("--m6-csv", type=Path, help="optional M6 batch_summary.csv")
    analyse.add_argument("--out", type=Path, help="defaults to <experiment>/analysis")
    analyse.add_argument("--no-validate-artifacts", action="store_false", dest="validate_artifacts",
                         help="skip re-reading every saved route during analysis")
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.short:
            args.instances = list(SHORT_INSTANCES)
        try:
            return run_experiment(args)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        return analyse_experiment(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
