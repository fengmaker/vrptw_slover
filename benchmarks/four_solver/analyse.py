#!/usr/bin/env python3
"""Summarize the four-solver Solomon benchmark without hiding missing results.

The input is the centrally validated ``runs.csv`` emitted by the benchmark
runner. Outputs use only the Python standard library so this script can also
analyze archived runs without installing solver dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SOLVERS = ("ours", "pyvrp", "ortools", "gurobi")
DEFAULT_FIELDS = (
    "solver", "instance", "budget_seconds", "seed", "vehicle_cap",
    "fixed_vehicle_cost", "status", "feasible", "vehicles",
    "distance_ticks", "distance", "preparation_seconds", "search_seconds",
    "solver_runtime_seconds", "validation_seconds", "total_seconds",
    "first_feasible_seconds", "iterations", "input_sha256", "source_sha256",
    "error", "metadata_json", "solution_path",
)
PROTOCOL = {
    "start": "cold start from the original Solomon input and frozen vehicle cap",
    "objective": "lexicographic (vehicles, distance); our solver uses direct lexicographic selection and the reference adapters use their recorded dominating fixed vehicle cost",
    "warm_start": "no reference warm start",
    "budget": "search budget excludes external model construction; preparation and search are reported separately",
    "validation": "feasibility and objective values are taken from central validation recorded in runs.csv",
}


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None:
        return None
    return int(parsed)


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _label(value: Any) -> str:
    parsed = _number(value)
    if parsed is not None:
        return str(int(parsed)) if parsed.is_integer() else format(parsed, ".12g")
    return "" if value is None else str(value).strip()


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json", "")
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _walk(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


def _seed_supported(row: Mapping[str, Any]) -> bool | None:
    """Return explicit backend seed support, if the runner recorded it."""
    metadata = _metadata(row)
    candidates = {
        "seed_supported", "backend_seed_supported", "seed_support_supported",
        "supports_seed", "seed_argument_supported", "random_seed_supported",
        "seed_applied", "cp_solver_reseed_applied",
    }
    for key, value in _walk(metadata):
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf in candidates:
            parsed = _bool(value)
            if parsed is not None:
                return parsed
    return None


def _normalize_row(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    result = dict(row)
    solver = str(row.get("solver", "")).strip().lower()
    instance = str(row.get("instance", "")).strip().upper()
    budget = _label(row.get("budget_seconds", ""))
    seed = _label(row.get("seed", ""))
    if not solver or not instance or not budget or not seed:
        raise ValueError(f"runs.csv row {index} is missing solver, instance, budget_seconds, or seed")
    result.update(
        solver=solver,
        instance=instance,
        budget_seconds=budget,
        seed=seed,
        feasible_bool=_bool(row.get("feasible")),
        vehicles_int=_integer(row.get("vehicles")),
        distance_ticks_int=_integer(row.get("distance_ticks")),
        preparation_seconds_num=_number(row.get("preparation_seconds")),
        search_seconds_num=_number(row.get("search_seconds")),
        solver_runtime_seconds_num=_number(row.get("solver_runtime_seconds")),
        validation_seconds_num=_number(row.get("validation_seconds")),
        total_seconds_num=_number(row.get("total_seconds")),
        first_feasible_seconds_num=_number(row.get("first_feasible_seconds")),
        _row_index=index,
    )
    result["seed_supported"] = _seed_supported(row)
    return result


def load_runs(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no CSV header")
        missing = {"solver", "instance", "budget_seconds", "seed", "feasible"} - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")
        rows = [_normalize_row(row, index + 2) for index, row in enumerate(reader)]
    if not rows:
        raise ValueError(f"{path} contains no run rows")
    return rows


def _feasible_result(row: Mapping[str, Any]) -> bool:
    return (
        row.get("feasible_bool") is True
        and row.get("vehicles_int") is not None
        and row.get("distance_ticks_int") is not None
    )


def _best_by_instance(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if _feasible_result(row):
            groups[(str(row["solver"]), str(row["budget_seconds"]), str(row["instance"]))].append(row)
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, candidates in groups.items():
        chosen = min(candidates, key=lambda row: (
            row["vehicles_int"], row["distance_ticks_int"],
            _number(row.get("total_seconds")) or math.inf,
            str(row["seed"]), int(row["_row_index"]),
        ))
        best[key] = dict(chosen)
    return best


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolated percentile, equivalent to common p50/p90 methods."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _instance_family(instance: str) -> str:
    upper = instance.upper()
    return next((prefix for prefix in ("RC", "C", "R") if upper.startswith(prefix)), "Other")


def _run_groups(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["solver"]), str(row["budget_seconds"]))].append(row)
    return groups


def _summary_stats(
    solver: str,
    budget: str,
    run_rows: Sequence[Mapping[str, Any]],
    instances: Sequence[str],
    best: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    selected = [best[(solver, budget, instance)] for instance in instances if (solver, budget, instance) in best]
    feasible_instances = len(selected)
    complete = bool(instances) and feasible_instances == len(instances)
    vehicle_values = [int(row["vehicles_int"]) for row in selected]
    pyvrp_common = [
        instance for instance in instances
        if (solver, budget, instance) in best and ("pyvrp", budget, instance) in best
    ] if solver != "pyvrp" else []
    vehicle_deltas = [
        int(best[(solver, budget, instance)]["vehicles_int"])
        - int(best[("pyvrp", budget, instance)]["vehicles_int"])
        for instance in pyvrp_common
    ]
    same_vehicle = [
        instance for instance in pyvrp_common
        if best[(solver, budget, instance)]["vehicles_int"]
        == best[("pyvrp", budget, instance)]["vehicles_int"]
    ]
    distance_gaps_pct = [
        (best[(solver, budget, instance)]["distance_ticks_int"] /
         best[("pyvrp", budget, instance)]["distance_ticks_int"] - 1.0) * 100.0
        for instance in same_vehicle
        if best[("pyvrp", budget, instance)]["distance_ticks_int"] != 0
    ]
    distance_gaps_ticks = [
        int(best[(solver, budget, instance)]["distance_ticks_int"])
        - int(best[("pyvrp", budget, instance)]["distance_ticks_int"])
        for instance in same_vehicle
    ]
    runtime_fields = {
        "preparation": "preparation_seconds_num",
        "search": "search_seconds_num",
        "solver_runtime": "solver_runtime_seconds_num",
        "validation": "validation_seconds_num",
        "total": "total_seconds_num",
    }
    times = {
        f"median_{name}_seconds": _median([
            float(row[field]) for row in run_rows if row.get(field) is not None
        ])
        for name, field in runtime_fields.items()
    }
    counts = Counter(str(row.get("status", "")).strip() or "(blank)" for row in run_rows)
    feasible_rows = sum(row.get("feasible_bool") is True for row in run_rows)
    seed_flags = {row.get("seed_supported") for row in run_rows if row.get("seed_supported") is not None}
    if seed_flags == {False}:
        seed_support_status = "unsupported"
    elif seed_flags == {True}:
        seed_support_status = "supported"
    elif seed_flags:
        seed_support_status = "mixed"
    else:
        seed_support_status = "not_recorded"
    effective_seeds = {
        "backend-seed-unsupported" if row.get("seed_supported") is False else str(row["seed"])
        for row in run_rows
    }
    return {
        "solver": solver,
        "budget_seconds": budget,
        "instance_count": len(instances),
        "run_count": len(run_rows),
        "effective_seed_count": len(effective_seeds),
        "backend_seed_support": seed_support_status,
        "feasible_run_count": feasible_rows,
        "infeasible_run_count": sum(row.get("feasible_bool") is False for row in run_rows),
        "unknown_feasibility_run_count": sum(row.get("feasible_bool") is None for row in run_rows),
        "feasible_instance_count": feasible_instances,
        "complete_feasible_coverage": complete,
        "vehicle_sum": sum(vehicle_values) if complete else None,
        "vehicle_mean": sum(vehicle_values) / len(vehicle_values) if complete else None,
        "vehicle_mean_on_complete_coverage": complete,
        "common_feasible_with_pyvrp_count": len(pyvrp_common),
        "vehicle_better_vs_pyvrp": sum(delta < 0 for delta in vehicle_deltas),
        "vehicle_equal_vs_pyvrp": sum(delta == 0 for delta in vehicle_deltas),
        "vehicle_worse_vs_pyvrp": sum(delta > 0 for delta in vehicle_deltas),
        "same_vehicle_distance_gap_count": len(distance_gaps_pct),
        "same_vehicle_distance_gap_median_percent": _median(distance_gaps_pct),
        "same_vehicle_distance_gap_p90_percent": _percentile(distance_gaps_pct, 0.90),
        "same_vehicle_distance_gap_median_ticks": _median(distance_gaps_ticks),
        "same_vehicle_distance_gap_p90_ticks": _percentile(distance_gaps_ticks, 0.90),
        **times,
        "status_counts_json": json.dumps(dict(sorted(counts.items())), ensure_ascii=False, sort_keys=True),
    }


def _gurobi_metadata_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if row["solver"] != "gurobi":
            continue
        values = []
        for path, value in _walk(_metadata(row)):
            key = path.rsplit(".", 1)[-1].lower().replace("_", "")
            if "bound" in key or "mipgap" in key or key == "gap":
                if value is None or isinstance(value, (str, int, float, bool)):
                    values.append((path, value))
        if values:
            output.append({
                "solver": row["solver"], "instance": row["instance"],
                "budget_seconds": row["budget_seconds"], "seed": row["seed"],
                "status": row.get("status", ""), "feasible": row.get("feasible", ""),
                "vehicles": row.get("vehicles", ""), "distance_ticks": row.get("distance_ticks", ""),
                "metadata_bounds_json": json.dumps(dict(values), ensure_ascii=False, sort_keys=True),
            })
    return output


def _settings_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for solver in SOLVERS:
        solver_rows = [row for row in rows if row["solver"] == solver]
        fields: dict[str, set[str]] = defaultdict(set)
        for row in solver_rows:
            for path, value in _walk(_metadata(row)):
                leaf = path.rsplit(".", 1)[-1].lower()
                if any(word in leaf for word in ("model", "backend", "engine", "objective", "thread", "worker", "seed", "version", "param", "strategy", "algorithm")):
                    fields[path].add(json.dumps(value, ensure_ascii=False, sort_keys=True))
        result[solver] = {key: sorted(values) for key, values in sorted(fields.items())}
    return result


def _wide_rows(
    rows: Sequence[Mapping[str, Any]],
    instances: Sequence[str],
    budgets: Sequence[str],
    best: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for budget in budgets:
        for instance in instances:
            row: dict[str, Any] = {"budget_seconds": budget, "instance": instance, "family": _instance_family(instance)}
            pyvrp = best.get(("pyvrp", budget, instance))
            for solver in SOLVERS:
                solution = best.get((solver, budget, instance))
                row[f"{solver}_feasible"] = bool(solution)
                row[f"{solver}_vehicles"] = solution["vehicles_int"] if solution else None
                row[f"{solver}_distance_ticks"] = solution["distance_ticks_int"] if solution else None
                row[f"{solver}_distance"] = solution.get("distance", "") if solution else ""
                row[f"{solver}_selected_seed"] = solution.get("seed", "") if solution else ""
                if solver != "pyvrp" and solution and pyvrp:
                    row[f"{solver}_vehicle_delta_vs_pyvrp"] = solution["vehicles_int"] - pyvrp["vehicles_int"]
                    same_k = solution["vehicles_int"] == pyvrp["vehicles_int"]
                    row[f"{solver}_distance_gap_percent_vs_pyvrp"] = (
                        (solution["distance_ticks_int"] / pyvrp["distance_ticks_int"] - 1.0) * 100.0
                        if same_k and pyvrp["distance_ticks_int"] else None
                    )
                elif solver != "pyvrp":
                    row[f"{solver}_vehicle_delta_vs_pyvrp"] = None
                    row[f"{solver}_distance_gap_percent_vs_pyvrp"] = None
            result.append(row)
    return result


def _improvement_rows(
    instances: Sequence[str],
    best: Mapping[tuple[str, str, str], Mapping[str, Any]],
    budgets: Sequence[str],
) -> list[dict[str, Any]]:
    if "0.5" not in budgets or "5" not in budgets:
        return []
    result = []
    for solver in SOLVERS:
        for instance in instances:
            short = best.get((solver, "0.5", instance))
            long = best.get((solver, "5", instance))
            short_k = short["vehicles_int"] if short else None
            long_k = long["vehicles_int"] if long else None
            if short is None and long is None:
                outcome = "no_feasible_solution_at_either_budget"
            elif short is None:
                outcome = "feasible_only_at_5s"
            elif long is None:
                outcome = "lost_feasibility_at_5s"
            elif long_k < short_k:
                outcome = "fewer_vehicles_at_5s"
            elif long_k > short_k:
                outcome = "more_vehicles_at_5s"
            elif long["distance_ticks_int"] < short["distance_ticks_int"]:
                outcome = "same_vehicles_shorter_distance_at_5s"
            elif long["distance_ticks_int"] > short["distance_ticks_int"]:
                outcome = "same_vehicles_longer_distance_at_5s"
            else:
                outcome = "same_lexicographic_result"
            same_k = bool(short and long and short_k == long_k)
            result.append({
                "solver": solver,
                "instance": instance,
                "family": _instance_family(instance),
                "short_feasible": bool(short),
                "long_feasible": bool(long),
                "vehicles_0p5s": short_k,
                "vehicles_5s": long_k,
                "vehicle_change_5s_minus_0p5s": (long_k - short_k) if short and long else None,
                "distance_ticks_0p5s": short["distance_ticks_int"] if short else None,
                "distance_ticks_5s": long["distance_ticks_int"] if long else None,
                "same_vehicle_distance_improvement_percent": (
                    (short["distance_ticks_int"] - long["distance_ticks_int"])
                    / short["distance_ticks_int"] * 100.0
                    if same_k and short["distance_ticks_int"] else None
                ),
                "outcome": outcome,
                "seed_0p5s": short.get("seed", "") if short else "",
                "seed_5s": long.get("seed", "") if long else "",
            })
    return result


def _improvement_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for solver in SOLVERS:
        subset = [row for row in rows if row["solver"] == solver]
        outcomes = Counter(row["outcome"] for row in subset)
        same_k = [
            float(row["same_vehicle_distance_improvement_percent"])
            for row in subset if row.get("same_vehicle_distance_improvement_percent") is not None
        ]
        result.append({
            "solver": solver,
            "instance_count": len(subset),
            "fewer_vehicles_at_5s": outcomes["fewer_vehicles_at_5s"],
            "same_vehicles_shorter_distance_at_5s": outcomes["same_vehicles_shorter_distance_at_5s"],
            "same_vehicles_longer_distance_at_5s": outcomes["same_vehicles_longer_distance_at_5s"],
            "more_vehicles_at_5s": outcomes["more_vehicles_at_5s"],
            "feasible_only_at_5s": outcomes["feasible_only_at_5s"],
            "lost_feasibility_at_5s": outcomes["lost_feasibility_at_5s"],
            "same_lexicographic_result": outcomes["same_lexicographic_result"],
            "no_feasible_solution_at_either_budget": outcomes["no_feasible_solution_at_either_budget"],
            "same_vehicle_distance_improvement_median_percent": _median(same_k),
            "same_vehicle_distance_improvement_p90_percent": _percentile(same_k, .90),
        })
    return result


def _paired_rows(rows: Sequence[Mapping[str, Any]], budgets: Sequence[str]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["solver"] not in {"ours", "pyvrp"}:
            continue
        if row.get("seed_supported") is False:
            continue
        by_key[(str(row["budget_seconds"]), str(row["instance"]), str(row["seed"]), str(row["solver"]))][str(row["solver"])] = row
    keys = sorted({key[:3] for key in by_key}, key=lambda item: (item[0], item[1], item[2]))
    result = []
    for budget, instance, seed in keys:
        ours = by_key.get((budget, instance, seed, "ours"), {}).get("ours")
        pyvrp = by_key.get((budget, instance, seed, "pyvrp"), {}).get("pyvrp")
        if not ours or not pyvrp:
            continue
        ours_feasible = _feasible_result(ours)
        pyvrp_feasible = _feasible_result(pyvrp)
        same_k = ours_feasible and pyvrp_feasible and ours["vehicles_int"] == pyvrp["vehicles_int"]
        result.append({
            "budget_seconds": budget, "instance": instance, "seed": seed,
            "ours_feasible": ours_feasible, "pyvrp_feasible": pyvrp_feasible,
            "ours_vehicles": ours.get("vehicles_int"), "pyvrp_vehicles": pyvrp.get("vehicles_int"),
            "vehicle_delta_ours_minus_pyvrp": (ours["vehicles_int"] - pyvrp["vehicles_int"])
            if ours_feasible and pyvrp_feasible else None,
            "same_vehicle_count": same_k,
            "ours_distance_ticks": ours.get("distance_ticks_int") if ours_feasible else None,
            "pyvrp_distance_ticks": pyvrp.get("distance_ticks_int") if pyvrp_feasible else None,
            "same_vehicle_distance_gap_percent": (
                (ours["distance_ticks_int"] / pyvrp["distance_ticks_int"] - 1.0) * 100.0
                if same_k and pyvrp["distance_ticks_int"] else None
            ),
            "ours_total_seconds": ours.get("total_seconds", ""),
            "pyvrp_total_seconds": pyvrp.get("total_seconds", ""),
        })
    return result


def _paired_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    budgets = sorted({str(row["budget_seconds"]) for row in rows}, key=lambda x: _number(x) or math.inf)
    for budget in budgets:
        subset = [row for row in rows if row["budget_seconds"] == budget]
        comparable = [row for row in subset if row["vehicle_delta_ours_minus_pyvrp"] is not None]
        same_k_gaps = [float(row["same_vehicle_distance_gap_percent"]) for row in comparable if row.get("same_vehicle_distance_gap_percent") is not None]
        result.append({
            "budget_seconds": budget,
            "paired_run_count": len(subset),
            "both_feasible_count": len(comparable),
            "ours_vehicle_better_count": sum(row["vehicle_delta_ours_minus_pyvrp"] < 0 for row in comparable),
            "ours_vehicle_equal_count": sum(row["vehicle_delta_ours_minus_pyvrp"] == 0 for row in comparable),
            "ours_vehicle_worse_count": sum(row["vehicle_delta_ours_minus_pyvrp"] > 0 for row in comparable),
            "same_vehicle_distance_gap_count": len(same_k_gaps),
            "same_vehicle_distance_gap_median_percent": _median(same_k_gaps),
            "same_vehicle_distance_gap_p90_percent": _percentile(same_k_gaps, .90),
        })
    return result


def _tail_rows(rows: Sequence[Mapping[str, Any]], best: Mapping[tuple[str, str, str], Mapping[str, Any]], instances: Sequence[str], budgets: Sequence[str], limit: int = 10) -> list[dict[str, Any]]:
    result = []
    for budget in budgets:
        for solver in SOLVERS:
            pairs = []
            for instance in instances:
                selected = best.get((solver, budget, instance))
                if not selected:
                    continue
                seconds = _number(selected.get("total_seconds"))
                if seconds is not None:
                    pairs.append((seconds, instance, selected))
            for rank, (seconds, instance, selected) in enumerate(sorted(pairs, reverse=True)[:limit], start=1):
                result.append({
                    "solver": solver, "budget_seconds": budget, "rank_slowest": rank,
                    "instance": instance, "total_seconds": seconds,
                    "search_seconds": selected.get("search_seconds", ""),
                    "preparation_seconds": selected.get("preparation_seconds", ""),
                    "feasible": selected.get("feasible", ""),
                    "vehicles": selected.get("vehicles_int"),
                    "distance_ticks": selected.get("distance_ticks_int"),
                })
    return result


def _group_summary(
    run_groups: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    instances: Sequence[str],
    best: Mapping[tuple[str, str, str], Mapping[str, Any]],
    budgets: Sequence[str],
) -> list[dict[str, Any]]:
    result = []
    for family in ("C", "R", "RC"):
        family_instances = [instance for instance in instances if _instance_family(instance) == family]
        for budget in budgets:
            for solver in SOLVERS:
                stats = _summary_stats(solver, budget, run_groups.get((solver, budget), []), family_instances, best)
                stats["family"] = family
                result.append(stats)
    return result


def analyze(
    rows: Sequence[Mapping[str, Any]],
    expected_instances: Sequence[str] | None = None,
    expected_budgets: Sequence[Any] | None = None,
) -> dict[str, Any]:
    normalized = [_normalize_row(row, int(row.get("_row_index", index + 2))) for index, row in enumerate(rows)]
    seen: set[tuple[str, str, str, str]] = set()
    for row in normalized:
        identity = (row["solver"], row["instance"], row["budget_seconds"], row["seed"])
        if identity in seen:
            raise ValueError(f"duplicate run identity: solver={identity[0]}, instance={identity[1]}, budget={identity[2]}, seed={identity[3]}")
        seen.add(identity)
    observed_instances = {str(row["instance"]) for row in normalized}
    expected_instance_set = {str(instance).strip().upper() for instance in expected_instances or []}
    if expected_instance_set and observed_instances - expected_instance_set:
        unexpected = sorted(observed_instances - expected_instance_set)
        raise ValueError(f"runs.csv includes instances absent from manifest: {', '.join(unexpected)}")
    instances = sorted(expected_instance_set or observed_instances)
    missing_instances = sorted(set(instances) - observed_instances)
    observed_budgets = {str(row["budget_seconds"]) for row in normalized}
    expected_budget_set = {_label(budget) for budget in expected_budgets or []}
    if expected_budget_set and observed_budgets - expected_budget_set:
        unexpected = sorted(observed_budgets - expected_budget_set)
        raise ValueError(f"runs.csv includes budgets absent from manifest: {', '.join(unexpected)}")
    budgets = sorted(expected_budget_set or observed_budgets, key=lambda value: _number(value) if _number(value) is not None else math.inf)
    run_groups = _run_groups(normalized)
    best = _best_by_instance(normalized)
    summary = [
        _summary_stats(solver, budget, run_groups.get((solver, budget), []), instances, best)
        for budget in budgets for solver in SOLVERS
    ]
    group_summary = _group_summary(run_groups, instances, best, budgets)
    wide = _wide_rows(normalized, instances, budgets, best)
    improvements = _improvement_rows(instances, best, budgets)
    paired = _paired_rows(normalized, budgets)
    status_counts: list[dict[str, Any]] = []
    for budget in budgets:
        for solver in SOLVERS:
            group = run_groups.get((solver, budget), [])
            counts = Counter(str(row.get("status", "")).strip() or "(blank)" for row in group)
            status_counts.append({
                "solver": solver, "budget_seconds": budget,
                "run_count": len(group),
                "feasible_true": sum(row.get("feasible_bool") is True for row in group),
                "feasible_false": sum(row.get("feasible_bool") is False for row in group),
                "feasible_unknown": sum(row.get("feasible_bool") is None for row in group),
                "status_counts_json": json.dumps(dict(sorted(counts.items())), ensure_ascii=False, sort_keys=True),
            })
    return {
        "protocol": PROTOCOL,
        "instance_count": len(instances),
        "observed_instance_count": len(observed_instances),
        "missing_instances_in_runs": missing_instances,
        "instances": instances,
        "budget_seconds": budgets,
        "solver_order": list(SOLVERS),
        "observed_runs_per_solver_budget": {
            f"{solver}:{budget}": len(run_groups.get((solver, budget), []))
            for budget in budgets for solver in SOLVERS
        },
        "summary": summary,
        "group_summary": group_summary,
        "per_instance_wide": wide,
        "improvement_0p5_to_5": improvements,
        "improvement_summary": _improvement_summary(improvements),
        "paired_run_comparisons": paired,
        "paired_run_summary": _paired_summary(paired),
        "status_counts": status_counts,
        "gurobi_bounds": _gurobi_metadata_rows(normalized),
        "tail_instances": _tail_rows(normalized, best, instances, budgets),
        "settings_from_metadata": _settings_summary(normalized),
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["no_rows"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _md(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_md(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def render_report(analysis: Mapping[str, Any], run_count: int) -> str:
    instances = analysis["instances"]
    budgets = analysis["budget_seconds"]
    maximum_trials = max((row.get("effective_seed_count", 0) for row in analysis["summary"]), default=0)
    all_one = maximum_trials <= 1
    trial_label = "one observed run per solver-instance-budget" if all_one else f"best feasible result across up to {maximum_trials} observed runs per solver-instance-budget"
    lines = [
        "# Four-solver VRPTW comparison",
        "",
        f"The input contains {run_count} rows across {analysis['observed_instance_count']} observed instances and budgets {', '.join(budgets)} seconds. The report checks against {analysis['instance_count']} expected instances from the run manifest when available; without a manifest it uses the instances present in runs.csv. The standard full Solomon comparison contains 56 instances.",
        "",
        f"Per-instance summary values use the lexicographically best feasible result across observed attempts ({trial_label}). A multi-run selection represents the effort of running each attempt, not single-run quality. Vehicle sums and means are populated only when every instance in the table has a feasible selected result.",
        "",
        "## Primary protocol",
        "",
        f"- Start: {PROTOCOL['start']}.",
        f"- Objective: {PROTOCOL['objective']}.",
        f"- Warm start: {PROTOCOL['warm_start']}.",
        f"- Timing: {PROTOCOL['budget']}; reported medians include preparation, search, solver runtime, validation, and total wall time where available.",
        f"- Validation: {PROTOCOL['validation']}.",
        "- Per-solver model and version details are summarized from each row's `metadata_json` in `summary.json`; the raw metadata remains in `raw_runs.csv`.",
        "",
        "## Overall summary by budget",
        "",
    ]
    summary_rows = []
    for row in analysis["summary"]:
        summary_rows.append([
            row["budget_seconds"], row["solver"], f"{row['feasible_run_count']}/{row['run_count']}",
            f"{row['feasible_instance_count']}/{row['instance_count']}", row["vehicle_sum"], row["vehicle_mean"],
            f"{row['vehicle_better_vs_pyvrp']}/{row['vehicle_equal_vs_pyvrp']}/{row['vehicle_worse_vs_pyvrp']} ({row['common_feasible_with_pyvrp_count']} common)",
            row["same_vehicle_distance_gap_median_percent"], row["same_vehicle_distance_gap_p90_percent"],
            row["median_preparation_seconds"], row["median_search_seconds"], row["median_total_seconds"],
        ])
    lines.append(_markdown_table(
        ["Budget (s)", "Solver", "Feasible runs", "Feasible instances", "Vehicle sum*", "Vehicle mean*", "Vehicles better/equal/worse vs PyVRP", "Same-K distance gap median (%)", "Same-K p90 (%)", "Median prep (s)", "Median search (s)", "Median total (s)"],
        summary_rows,
    ))
    lines.extend([
        "`*` Vehicle sum and mean are blank unless feasible coverage is complete. Pairwise vehicle comparisons use the common-feasible per-instance subset. Distance gaps compare only solutions with equal vehicle counts and are relative to PyVRP; positive means longer distance than PyVRP.",
        "Runtime medians use all raw run rows for that solver and budget. `effective_seed_count` in `summary.csv` collapses repeated runs to one seed group when metadata explicitly says the backend does not support seeds.",
        "",
        "## Instance-family summary",
        "",
    ])
    group_rows = []
    for row in analysis["group_summary"]:
        group_rows.append([
            row["family"], row["budget_seconds"], row["solver"],
            f"{row['feasible_instance_count']}/{row['instance_count']}", row["vehicle_sum"], row["vehicle_mean"],
            f"{row['vehicle_better_vs_pyvrp']}/{row['vehicle_equal_vs_pyvrp']}/{row['vehicle_worse_vs_pyvrp']} ({row['common_feasible_with_pyvrp_count']} common)",
            row["same_vehicle_distance_gap_median_percent"], row["same_vehicle_distance_gap_p90_percent"],
        ])
    lines.append(_markdown_table(
        ["Family", "Budget (s)", "Solver", "Feasible instances", "Vehicle sum*", "Vehicle mean*", "Vehicles better/equal/worse vs PyVRP", "Same-K gap median (%)", "Same-K p90 (%)"],
        group_rows,
    ))
    lines.extend([
        "`*` Family vehicle sums and means require feasible coverage of every instance in that family.",
        "",
        "## 0.5 s to 5 s improvement",
        "",
        "Improvement is evaluated lexicographically: fewer vehicles takes precedence; distance is compared only when vehicle counts match. Distance improvement is `(distance at 0.5 s − distance at 5 s) / distance at 0.5 s`, so a positive value is shorter at 5 seconds.",
        "",
    ])
    lines.append(_markdown_table(
        ["Solver", "Fewer vehicles", "Same K, shorter", "Same K, longer", "More vehicles", "Feasible only at 5 s", "Lost feasibility", "No feasible either", "Same-K distance improvement median (%)", "p90 (%)"],
        [[row["solver"], row["fewer_vehicles_at_5s"], row["same_vehicles_shorter_distance_at_5s"],
          row["same_vehicles_longer_distance_at_5s"], row["more_vehicles_at_5s"], row["feasible_only_at_5s"],
          row["lost_feasibility_at_5s"], row["no_feasible_solution_at_either_budget"],
          row["same_vehicle_distance_improvement_median_percent"], row["same_vehicle_distance_improvement_p90_percent"]]
         for row in analysis["improvement_summary"]],
    ))
    lines.extend([
        "",
        "## Seed-matched ours vs PyVRP runs",
        "",
        "These rows compare the same instance, budget, and seed. Rows with an explicit metadata flag saying the backend does not support seeds are excluded from seed-matched comparisons.",
        "",
    ])
    lines.append(_markdown_table(
        ["Budget (s)", "Paired runs", "Both feasible", "Ours fewer K", "Equal K", "Ours more K", "Same-K gap median (%)", "Same-K p90 (%)"],
        [[row["budget_seconds"], row["paired_run_count"], row["both_feasible_count"], row["ours_vehicle_better_count"],
          row["ours_vehicle_equal_count"], row["ours_vehicle_worse_count"], row["same_vehicle_distance_gap_median_percent"],
          row["same_vehicle_distance_gap_p90_percent"]] for row in analysis["paired_run_summary"]],
    ))
    lines.extend(["", "Per-seed detail is in `paired_runs.csv`.", "", "## Status counts", ""])
    lines.append(_markdown_table(
        ["Budget (s)", "Solver", "Runs", "Feasible true", "Feasible false", "Feasibility unknown", "Status counts"],
        [[row["budget_seconds"], row["solver"], row["run_count"], row["feasible_true"], row["feasible_false"],
          row["feasible_unknown"], row["status_counts_json"]] for row in analysis["status_counts"]],
    ))
    lines.extend(["", "## Slowest selected feasible instances", "", "Top ten by selected run total time for each solver and budget. Full rows, including preparation and search time, are in `tail_instances.csv`.", ""])
    tails = analysis["tail_instances"]
    # Keep the Markdown report readable: show the ten slowest per solver and budget.
    lines.append(_markdown_table(
        ["Budget (s)", "Solver", "Rank", "Instance", "Total (s)", "Prep (s)", "Search (s)", "Vehicles"],
        [[row["budget_seconds"], row["solver"], row["rank_slowest"], row["instance"], row["total_seconds"],
          row["preparation_seconds"], row["search_seconds"], row["vehicles"]] for row in tails],
    ))
    lines.extend(["", "## Gurobi bounds", "", "Bound and gap fields recorded under Gurobi `metadata_json` are retained in `gurobi_bounds.csv` and `summary.json`. A missing bound means the runner did not record a matching metadata field.", ""])
    lines.append(_markdown_table(
        ["Budget (s)", "Instance", "Seed", "Status", "Metadata bounds/gap"],
        [[row["budget_seconds"], row["instance"], row["seed"], row["status"], row["metadata_bounds_json"]]
         for row in analysis["gurobi_bounds"]],
    ))
    lines.extend([
        "",
        "## Per-instance results",
        "",
        "`per_instance_wide.csv` contains one row per budget and instance with each solver's selected vehicle count, distance, and selected seed. Distance gaps versus PyVRP are populated only for equal-vehicle comparisons.",
        "",
        "## Files",
        "",
        "- `raw_runs.csv`: unchanged copy of the runner output.",
        "- `summary.csv`, `group_summary.csv`, `status_counts.csv`: aggregate tables.",
        "- `per_instance_wide.csv`, `improvement_0p5_to_5.csv`, `paired_runs.csv`: comparison detail.",
        "- `tail_instances.csv`, `gurobi_bounds.csv`: runtime tail and recorded solver bounds.",
        "- `summary.json`: machine-readable copy of all calculated tables and protocol notes.",
        "",
    ])
    return "\n".join(lines)


def write_outputs(run_csv: Path, out_dir: Path) -> dict[str, Any]:
    rows = load_runs(run_csv)
    manifest_path = run_csv.parent / "manifest.json"
    manifest_protocol: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_protocol = manifest.get("protocol", {}) if isinstance(manifest, dict) else {}
    analysis = analyze(
        rows,
        expected_instances=manifest_protocol.get("instances"),
        expected_budgets=manifest_protocol.get("budgets"),
    )
    analysis["manifest_protocol"] = manifest_protocol
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_target = out_dir / "raw_runs.csv"
    if run_csv.resolve() != raw_target.resolve():
        shutil.copyfile(run_csv, raw_target)
    _write_csv(out_dir / "summary.csv", analysis["summary"])
    _write_csv(out_dir / "group_summary.csv", analysis["group_summary"])
    _write_csv(out_dir / "per_instance_wide.csv", analysis["per_instance_wide"])
    _write_csv(out_dir / "improvement_0p5_to_5.csv", analysis["improvement_0p5_to_5"])
    _write_csv(out_dir / "improvement_summary.csv", analysis["improvement_summary"])
    _write_csv(out_dir / "paired_runs.csv", analysis["paired_run_comparisons"])
    _write_csv(out_dir / "paired_run_summary.csv", analysis["paired_run_summary"])
    _write_csv(out_dir / "status_counts.csv", analysis["status_counts"])
    _write_csv(out_dir / "gurobi_bounds.csv", analysis["gurobi_bounds"])
    _write_csv(out_dir / "tail_instances.csv", analysis["tail_instances"])
    (out_dir / "summary.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(analysis, len(rows)), encoding="utf-8")
    return analysis


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="directory containing runs.csv")
    parser.add_argument("--out-dir", type=Path, help="output directory (default: <run-dir>/tables)")
    args = parser.parse_args(argv)
    run_csv = args.run_dir / "runs.csv"
    out_dir = args.out_dir or args.run_dir / "tables"
    if not run_csv.is_file():
        parser.error(f"run CSV not found: {run_csv}")
    analysis = write_outputs(run_csv, out_dir)
    print(f"Wrote analysis for {analysis['instance_count']} instances to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
