"""M3: structural moves, feasibility, and exact full-recompute deltas."""

from collections import Counter
from pathlib import Path
from random import Random

import pytest

from vrptw import (Customer, Instance, Move, apply_move, construct, improve,
                   move_delta, read_solomon, validate_solution)
from vrptw.local_search import enumerate_moves


@pytest.fixture
def square() -> Instance:
    points = ((0, 0), (1, 0), (2, 0), (0, 1), (0, 2))
    return Instance("square", 2, 5, tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 100_000, 0)
        for i, (x, y) in enumerate(points)
    ))


@pytest.mark.parametrize("routes, move, expected_routes", [
    (((1, 3), (2, 4)), Move("relocate", 0, 1, 1, 1), ((1,), (2, 3, 4))),
    (((1, 4), (3, 2)), Move("swap", 0, 1, 1, 1), ((1, 2), (3, 4))),
    (((1, 3, 2, 4),), Move("two_opt", 0, 1, 0, 3), ((1, 2, 3, 4),)),
    (((1, 4), (3, 2)), Move("two_opt_star", 0, 1, 1, 1), ((1, 2), (3, 4))),
])
def test_each_operator_improves_and_matches_full_delta(square, routes, move, expected_routes):
    before = validate_solution(square, routes)
    after_routes = apply_move(routes, move)
    after = validate_solution(square, after_routes)
    assert after_routes == expected_routes
    assert before.feasible and after.feasible
    assert after.vehicles == before.vehicles
    assert move_delta(square, routes, move) == after.distance - before.distance < 0


def test_all_generated_feasible_moves_match_full_solution_recalculation(square):
    routes = ((1, 4), (3, 2))
    old = validate_solution(square, routes)
    old_customers = Counter(node for route in routes for node in route)
    checked = 0
    for move in enumerate_moves(routes):
        candidate = apply_move(routes, move)
        assert Counter(node for route in candidate for node in route) == old_customers
        delta = move_delta(square, routes, move)
        if delta is not None:
            report = validate_solution(square, candidate)
            assert report.feasible and report.vehicles == old.vehicles
            assert delta == report.distance - old.distance
            checked += 1
    assert checked > 20


def test_infeasible_reversal_and_empty_source_are_not_accepted():
    instance = Instance("tight", 2, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 1_000, 0),
        Customer(2, 2, 0, 1, 0, 5_000, 0),
    ))
    assert move_delta(instance, ((1, 2),), Move("two_opt", 0, 0, 0, 2)) is None
    assert move_delta(instance, ((1,), (2,)), Move("relocate", 0, 0, 1, 0)) is None


def test_invalid_move_positions_are_rejected(square):
    routes = ((1, 4), (3, 2))
    with pytest.raises(ValueError, match="position out of range"):
        apply_move(routes, Move("relocate", 0, 3, 1, 0))
    with pytest.raises(ValueError, match="segment must contain"):
        apply_move(routes, Move("two_opt", 0, 0, 0, 1))


def test_c101_sampled_move_deltas_against_full_verdict():
    path = Path(__file__).resolve().parents[1] / "data" / "C101.txt"
    instance = read_solomon(path)
    routes = construct(instance)
    before = validate_solution(instance, routes)
    moves = list(enumerate_moves(routes))
    sample = Random(0).sample(moves, 100)
    feasible_count = 0
    for move in sample:
        candidate = apply_move(routes, move)
        delta = move_delta(instance, routes, move)
        if delta is not None:
            after = validate_solution(instance, candidate)
            assert after.feasible and after.vehicles == before.vehicles
            assert delta == after.distance - before.distance
            feasible_count += 1
    assert feasible_count > 0


@pytest.mark.parametrize("strategy", ["first", "best"])
def test_improve_preserves_coverage_and_never_increases_distance(square, strategy):
    routes = ((1, 4), (3, 2))
    initial = validate_solution(square, routes)
    result = improve(square, routes, strategy=strategy)
    final = validate_solution(square, result.routes)
    assert result.stop_reason == "local_optimum"
    assert final.feasible and final.vehicles == initial.vehicles
    assert result.distance == final.distance < initial.distance == result.initial_distance
    assert all(move.delta < 0 for move in result.moves)
    assert sum(move.delta for move in result.moves) == result.distance - result.initial_distance
