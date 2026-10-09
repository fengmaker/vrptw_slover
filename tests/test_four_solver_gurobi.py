"""Focused correctness checks for the optional Gurobi comparison backend."""

from itertools import permutations

import pytest

from vrptw import Customer, Instance, validate_solution
from benchmarks.four_solver.gurobi_backend import solve


def _small_instance() -> Instance:
    points = ((0, 0), (1, 0), (0, 1), (2, 0), (0, 2))
    customers = [Customer(0, *points[0], 0, 0, 100_000, 0)]
    customers.extend(
        Customer(i, *point, 1, 0, 100_000, 0)
        for i, point in enumerate(points[1:], start=1)
    )
    return Instance("tiny-four-customer", vehicle_count=2, capacity=2, customers=tuple(customers))


def _exact_pair_route_distance(instance: Instance) -> int:
    """Enumerate every two-route split and order for this capacity-tight case."""
    best = None
    for ordering in permutations(range(1, instance.customer_count + 1)):
        total = 0
        for route in (ordering[:2], ordering[2:]):
            nodes = (0, *route, 0)
            total += sum(instance.distance[a][b] for a, b in zip(nodes, nodes[1:]))
        best = total if best is None else min(best, total)
    assert best is not None
    return best


def _skip_if_no_working_license(result):
    if result["status"] == "license_error":
        pytest.skip(result["metadata"].get("error", "Gurobi license unavailable"))
    if result["status"] == "dependency_error":
        pytest.skip("gurobipy is not installed in this Python environment")


def test_gurobi_matches_exact_tiny_lexicographic_optimum():
    pytest.importorskip("gurobipy")
    instance = _small_instance()
    expected_distance = _exact_pair_route_distance(instance)
    result = solve(instance, budget=5.0, seed=17)
    _skip_if_no_working_license(result)

    assert result["status"] == "optimal", result
    evaluation = validate_solution(instance, result["routes"])
    assert evaluation.feasible
    assert evaluation.objective == (2, expected_distance)
    assert result["metadata"]["threads"] == 1
    assert result["metadata"]["cold_start"] is True
    assert result["metadata"]["seed_supported"] is True
    assert result["metadata"]["scalar_objective"] == result["metadata"]["objective"]
    assert result["first_feasible_seconds"] is not None
    assert result["metadata"]["node_count"] >= 0
    assert result["metadata"]["iteration_count"] >= 0
    assert result["metadata"]["gurobi_version"]


def test_gurobi_mtz_handles_zero_demand_and_zero_time_cycles():
    pytest.importorskip("gurobipy")
    nodes = (Customer(0, 0, 0, 0, 0, 0, 0),) + tuple(
        Customer(i, 0, 0, 0, 0, 0, 0) for i in range(1, 4)
    )
    instance = Instance("all-zero-three-customer", vehicle_count=1, capacity=1, customers=nodes)
    result = solve(instance, budget=5.0, seed=3)
    _skip_if_no_working_license(result)

    assert result["status"] == "optimal", result
    evaluation = validate_solution(instance, result["routes"])
    assert evaluation.feasible
    assert evaluation.objective == (1, 0)


def test_proven_capacity_impossibility_is_returned_without_a_solver_run():
    instance = Instance(
        "impossible-demand",
        vehicle_count=1,
        capacity=4,
        customers=(
            Customer(0, 0, 0, 0, 0, 10_000, 0),
            Customer(1, 1, 0, 5, 0, 10_000, 0),
        ),
    )
    result = solve(instance, budget=0.5, seed=0)

    assert result["routes"] is None
    assert result["status"] == "infeasible"
    assert result["search_seconds"] == 0.0
    assert "exceeds vehicle capacity" in result["metadata"]["proof"]
