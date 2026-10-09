"""M11 soft evaluation and adaptive penalty behavior."""

from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path

import pytest

from vrptw import Customer, Instance, evaluate_route, read_solomon, validate_solution
from vrptw.penalties import (
    PenaltyManager,
    PenaltyParams,
    SoftEvaluation,
    SoftRoute,
    evaluate_soft_route,
    evaluate_soft_solution,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tiny() -> Instance:
    return Instance("penalty-hand", 2, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 2, 3_000, 3_000, 1_000),
        Customer(2, 2, 0, 3, 0, 10_000, 0),
        Customer(3, 3, 0, 1, 0, 10_000, 0),
    ))


def test_lateness_is_clamped_before_propagating_next_visit():
    instance = Instance("warp", 1, 10, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 500, 0),
        Customer(2, 2, 0, 1, 0, 1_600, 0),
    ))

    soft = evaluate_soft_route(instance, (1, 2))
    hard = evaluate_route(instance, (1, 2))

    # Customer 1 contributes 500 ticks. Clamping its start to due=500 makes
    # customer 2 arrive at 1,500, before its due=1,600.
    assert (soft.distance, soft.excess_load, soft.time_warp) == (4_000, 0, 500)
    assert not hard.feasible
    assert not soft.feasible


def test_soft_route_counts_depot_return_and_capacity_excess(tiny):
    return_late = Instance("return-late", 1, 5, (
        Customer(0, 0, 0, 0, 0, 1_999, 0),
        Customer(1, 1, 0, 1, 0, 10_000, 0),
    ))
    assert evaluate_soft_route(return_late, (1,)) == SoftRoute(2_000, 0, 1)
    assert not evaluate_route(return_late, (1,)).feasible

    overloaded = evaluate_soft_route(tiny, (1, 2, 3))
    assert overloaded.distance == 6_000
    assert overloaded.excess_load == 1
    assert overloaded.time_warp == 0
    assert not overloaded.feasible
    assert not evaluate_route(tiny, (1, 2, 3)).feasible


def test_empty_route_has_zero_soft_cost_even_with_late_depot_ready():
    instance = Instance("empty", 1, 1, (
        Customer(0, 0, 0, 0, 5_000, 6_000, 0),
        Customer(1, 1, 0, 1, 0, 10_000, 0),
    ))
    assert evaluate_soft_route(instance, ()) == SoftRoute(0, 0, 0)


@pytest.mark.parametrize("routes", [
    (((1, 2),),),
    (((1, 2), (2, 3)),),
    (((1, 2, 3, 4),),),
    (((1,), (2,), (3,)),),
])
def test_soft_solution_rejects_missing_duplicate_unknown_and_excess_vehicle_assignments(tiny, routes):
    with pytest.raises(ValueError):
        evaluate_soft_solution(tiny, routes[0])


def test_soft_solution_reports_complete_assignment_metrics(tiny):
    soft = evaluate_soft_solution(tiny, ((), (1, 2), (3,)))
    hard = validate_solution(tiny, ((), (1, 2), (3,)))
    assert soft == SoftEvaluation(2, 10_000, 0, 0)
    assert soft.feasible == hard.feasible


def test_soft_evaluation_matches_authoritative_zero_violation_on_solomon_routes():
    instance = read_solomon(ROOT / "data" / "C101.txt")
    lines = (ROOT / "tests" / "fixtures" / "C101.sol").read_text().splitlines()
    routes = tuple(tuple(map(int, line.split(":", 1)[1].split()))
                   for line in lines if line.startswith("Route #"))

    for route in routes:
        soft = evaluate_soft_route(instance, route)
        hard = evaluate_route(instance, route)
        assert soft.feasible == hard.feasible
        assert (soft.excess_load, soft.time_warp) == (0, 0)
    assert evaluate_soft_solution(instance, routes).feasible


def test_penalty_params_are_frozen_and_validate_numeric_bounds():
    params = PenaltyParams()
    with pytest.raises(FrozenInstanceError):
        params.update_interval = 2

    invalid = (
        {"update_interval": True},
        {"target_feasible": float("nan")},
        {"target_feasible": 1.1},
        {"tolerance": -0.1},
        {"tolerance": float("inf")},
        {"increase": 1.0},
        {"increase": True},
        {"decrease": 0.0},
        {"decrease": float("nan")},
        {"min_weight": True},
        {"min_weight": 0},
        {"max_weight": 99, "min_weight": 100},
    )
    for changes in invalid:
        with pytest.raises(ValueError):
            PenaltyParams(**changes)


def test_initial_load_weight_uses_mean_off_diagonal_distance_and_demand(tiny):
    manager = PenaltyManager(tiny)
    nodes = len(tiny.customers)
    distance_sum = sum(
        tiny.distance[i][j]
        for i in range(nodes)
        for j in range(nodes)
        if i != j
    )
    expected = round(Fraction(distance_sum * 1000, nodes * tiny.total_demand))
    assert manager.load_weight == expected
    assert manager.time_weight == 1_000


def test_adaptation_uses_disjoint_windows_and_adjusts_dimensions_independently(tiny):
    params = PenaltyParams(update_interval=2, target_feasible=0.5, tolerance=0.05)
    manager = PenaltyManager(tiny, params)
    original_load = manager.load_weight

    assert manager.samples == 0
    assert manager.updates == 0
    assert manager.load_feasible_rate is None
    assert manager.time_feasible_rate is None
    assert manager.feasible_rate is None

    load_infeasible = SoftEvaluation(1, 0, 1, 0)
    time_infeasible = SoftEvaluation(1, 0, 0, 1)
    assert not manager.register(load_infeasible)
    assert manager.load_feasible_rate == 0.0
    assert manager.time_feasible_rate == 1.0
    assert manager.feasible_rate == 0.0
    assert manager.register(load_infeasible)
    assert manager.load_weight == round(Fraction(original_load) * Fraction("1.25"))
    assert manager.time_weight == 850
    assert manager.updates == 1
    assert (manager.load_feasible_rate, manager.time_feasible_rate, manager.feasible_rate) == (
        0.0, 1.0, 0.0
    )

    assert not manager.register(time_infeasible)
    assert manager.load_feasible_rate == 1.0
    assert manager.time_feasible_rate == 0.0
    assert manager.feasible_rate == 0.0
    assert manager.register(time_infeasible)
    assert manager.load_weight == round(Fraction(round(Fraction(original_load) * Fraction("1.25")))
                                        * Fraction("0.85"))
    assert manager.time_weight == round(Fraction(850) * Fraction("1.25"))
    assert manager.updates == 2
    assert manager.samples == 4


def test_adaptation_respects_tolerance_bounds_rounding_and_disabled_mode(tiny):
    params = PenaltyParams(
        update_interval=1,
        target_feasible=0.5,
        tolerance=0.5,
        min_weight=100,
        max_weight=150,
    )
    manager = PenaltyManager(tiny, params)
    before = (manager.load_weight, manager.time_weight)
    assert not manager.register(SoftEvaluation(1, 0, 0, 0))
    assert (manager.load_weight, manager.time_weight) == before
    assert manager.updates == 0

    bounded_params = PenaltyParams(update_interval=1, min_weight=100, max_weight=1_250)
    bounded = PenaltyManager(tiny, bounded_params)
    assert bounded.register(SoftEvaluation(1, 0, 1, 1))
    assert bounded.load_weight == 1_250
    assert bounded.time_weight == 1_250
    assert not bounded.register(SoftEvaluation(1, 0, 1, 1))
    assert bounded.load_weight == 1_250

    fixed = PenaltyManager(tiny, PenaltyParams(update_interval=1), adaptive=False)
    before = (fixed.load_weight, fixed.time_weight)
    assert not fixed.register(SoftEvaluation(1, 0, 1, 1))
    assert (fixed.load_weight, fixed.time_weight) == before
    assert (fixed.load_feasible_rate, fixed.time_feasible_rate, fixed.feasible_rate) == (
        0.0, 0.0, 0.0
    )


def test_penalty_cost_uses_integer_1000_scale(tiny):
    manager = PenaltyManager(tiny)
    evaluation = SoftEvaluation(1, 7, 2, 3)
    assert manager.cost(evaluation) == 7_000 + 2 * manager.load_weight + 3 * manager.time_weight
    route = SoftRoute(7, 2, 3)
    assert manager.cost(route) == manager.cost(evaluation)
