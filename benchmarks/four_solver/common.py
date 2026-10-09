"""Shared benchmark contract; public costs always come from our validator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vrptw.problem import Instance


SOLVERS = ("ours", "pyvrp", "ortools", "gurobi")
NUMERIC_RULE = "solomon_exact_1000_v1"
FIELDS = (
    "solver", "instance", "budget_seconds", "seed", "vehicle_cap",
    "fixed_vehicle_cost", "status", "feasible", "vehicles", "distance_ticks",
    "distance", "preparation_seconds", "search_seconds", "solver_runtime_seconds",
    "validation_seconds", "total_seconds", "first_feasible_seconds", "iterations",
    "adapter_seconds", "postprocessing_seconds",
    "input_sha256", "source_sha256", "error", "metadata_json", "solution_path",
)


def fleet_cost(instance: Instance) -> int:
    """Dominate any feasible distance, including rounded nonmetric arcs.

    A complete solution with K nonempty routes has exactly N+K arcs. Thus its
    distance is at most (N+min(N,cap))*max_arc, and adding one vehicle costs
    strictly more than the distance difference between any two feasible plans.
    """
    n = instance.customer_count
    return (n + min(n, instance.vehicle_count)) * max(map(max, instance.distance)) + 1


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
