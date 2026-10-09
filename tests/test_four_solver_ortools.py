from __future__ import annotations

from itertools import combinations, permutations

import pytest

pytest.importorskip("ortools")

from benchmarks.four_solver.ortools_backend import solve
from vrptw.evaluate import evaluate_route, validate_solution
from vrptw.problem import Customer, Instance


def _tiny_instance() -> Instance:
    return Instance(
        "tiny-exhaustive",
        4,
        2,
        (
            Customer(0, 0, 0, 0, 0, 30_000, 0),
            Customer(1, 1, 0, 1, 0, 30_000, 0),
            Customer(2, 0, 2, 1, 0, 30_000, 0),
            Customer(3, 3, 1, 1, 0, 30_000, 0),
            Customer(4, 2, 4, 1, 0, 30_000, 0),
        ),
    )


def _exhaustive_objective(instance: Instance) -> tuple[int, int]:
    """Enumerate every ordered partition of customers across nonempty routes."""
    count = instance.customer_count
    best: tuple[int, int] | None = None
    for order in permutations(range(1, count + 1)):
        for vehicle_count in range(1, min(count, instance.vehicle_count) + 1):
            for cuts in combinations(range(1, count), vehicle_count - 1):
                boundaries = (0, *cuts, count)
                routes = [
                    order[boundaries[index]:boundaries[index + 1]]
                    for index in range(vehicle_count)
                ]
                evaluation = validate_solution(instance, routes)
                if evaluation.feasible:
                    assert evaluation.distance is not None
                    candidate = (vehicle_count, evaluation.distance)
                    if best is None or candidate < best:
                        best = candidate
    assert best is not None
    return best


def test_ortools_matches_exhaustive_tiny_optimum_and_routes_revalidate() -> None:
    instance = _tiny_instance()
    expected = _exhaustive_objective(instance)

    result = solve(instance, budget=2.0, seed=0)

    assert result["routes"] is not None
    evaluation = validate_solution(instance, result["routes"])
    assert evaluation.feasible, evaluation.violations
    assert evaluation.objective == expected
    assert result["status"] == "found"
    assert result["metadata"]["vehicle_limit"] == instance.vehicle_count
    assert result["metadata"]["warm_start"] is False
    assert result["metadata"]["cp_solver_reseed_applied"] is True
    assert "no random_seed field" in result["metadata"]["seed_semantics"]
    assert result["metadata"]["routing_status"] in {
        "ROUTING_SUCCESS",
        "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
        "ROUTING_OPTIMAL",
    }
    assert result["metadata"]["objective_value"] == (
        evaluation.vehicles * result["metadata"]["vehicle_fixed_cost"]
        + evaluation.distance
    )
    assert result["preparation_seconds"] >= 0
    assert 0 < result["search_seconds"] < 2.5
    assert result["solver_runtime_seconds"] >= 0
    assert result["metadata"]["first_feasible_seconds"] is not None
    assert result["metadata"]["first_feasible_seconds"] <= result["search_seconds"]


def _tight_windows_instance() -> Instance:
    return Instance(
        "tight-windows-infeasible",
        1,
        2,
        (
            Customer(0, 0, 0, 0, 0, 10_000, 0),
            Customer(1, 1, 0, 1, 1_000, 1_000, 0),
            Customer(2, -1, 0, 1, 1_000, 1_000, 0),
        ),
    )


def test_ortools_reports_no_solution_when_tight_windows_require_two_vehicles() -> None:
    instance = _tight_windows_instance()
    assert evaluate_route(instance, [1]).feasible
    assert evaluate_route(instance, [2]).feasible
    assert not evaluate_route(instance, [1, 2]).feasible
    assert not evaluate_route(instance, [2, 1]).feasible

    result = solve(instance, budget=1.0, seed=0)

    assert result["routes"] is None
    assert result["status"] == "no_solution"
    assert result["metadata"]["routing_status"] in {
        "ROUTING_FAIL",
        "ROUTING_FAIL_TIMEOUT",
        "ROUTING_INFEASIBLE",
    }
    assert result["metadata"]["objective_gap_to_lower_bound"] is None

