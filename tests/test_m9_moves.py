"""M9 segment moves: literal structure and independent full-evaluation checks."""

from collections import Counter
from pathlib import Path
from random import Random

import pytest

from vrptw import (Customer, Instance, Move, apply_move, construct, improve,
                   move_delta, read_solomon, validate_solution)
from vrptw.evaluate import evaluate_route
from vrptw.local_search import (OPERATORS, CachedMoveEvaluator,
                                enumerate_moves)
from vrptw.neighbourhood import allows_move


DATA = Path(__file__).resolve().parents[1] / "data"
M9_OPERATORS = ("relocate_pair", "exchange_pair_single", "exchange_pairs")


def _plain_instance(routes):
    """A broad-window instance for structural tests, independent of move code."""
    customer_count = max(customer for route in routes for customer in route)
    customers = (Customer(0, 0, 0, 0, 0, 10**9, 0),) + tuple(
        Customer(i, i, (i * 7) % 13, 1, 0, 10**9, 0)
        for i in range(1, customer_count + 1)
    )
    return Instance("m9-structure", len(routes), customer_count, customers)


@pytest.mark.parametrize("routes, move, expected", [
    # Pair relocation across routes: insert before the target's first customer.
    (((1, 2, 3), (4, 5, 6)), Move("relocate_pair", 0, 1, 1, 0),
     ((1,), (2, 3, 4, 5, 6))),
    # The target slot is measured after source removal, and may be at the end.
    (((1, 2, 3), (4, 5, 6)), Move("relocate_pair", 0, 1, 1, 3),
     ((1,), (4, 5, 6, 2, 3))),
    # Same-route insertion at both ends of the reduced route.
    (((1, 2, 3, 4, 5),), Move("relocate_pair", 0, 1, 0, 0),
     ((2, 3, 1, 4, 5),)),
    (((1, 2, 3, 4, 5),), Move("relocate_pair", 0, 1, 0, 3),
     ((1, 4, 5, 2, 3),)),
    # Cross-route exchange of a two-customer pair with one customer.
    (((1, 2, 3, 4), (5, 6, 7)),
     Move("exchange_pair_single", 0, 1, 1, 2),
     ((1, 7, 4), (5, 6, 2, 3))),
    # Same-route unequal exchanges with the pair before and after the single.
    (((1, 2, 3, 4, 5, 6),),
     Move("exchange_pair_single", 0, 0, 0, 4),
     ((5, 3, 4, 1, 2, 6),)),
    (((1, 2, 3, 4, 5, 6),),
     Move("exchange_pair_single", 0, 3, 0, 0),
     ((4, 5, 2, 3, 1, 6),)),
    # Touching segments are legal in either order.
    (((1, 2, 3, 4),), Move("exchange_pair_single", 0, 0, 0, 2),
     ((3, 1, 2, 4),)),
    (((1, 2, 3, 4, 5),), Move("exchange_pair_single", 0, 2, 0, 0),
     ((3, 4, 2, 1, 5),)),
    # Pair/pair exchange across routes, then in both same-route directions.
    (((1, 2, 3, 4), (5, 6, 7, 8)),
     Move("exchange_pairs", 0, 1, 1, 0),
     ((1, 5, 6, 4), (2, 3, 7, 8))),
    (((1, 2, 3, 4, 5, 6),), Move("exchange_pairs", 0, 0, 0, 4),
     ((5, 6, 3, 4, 1, 2),)),
    (((1, 2, 3, 4, 5, 6),), Move("exchange_pairs", 0, 4, 0, 0),
     ((5, 6, 3, 4, 1, 2),)),
    # Adjacent pair segments may exchange places.
    (((1, 2, 3, 4, 5),), Move("exchange_pairs", 0, 0, 0, 2),
     ((3, 4, 1, 2, 5),)),
])
def test_segment_moves_have_literal_results_and_preserve_customer_coverage(
        routes, move, expected):
    actual = apply_move(routes, move)
    assert actual == expected
    assert Counter(node for route in actual for node in route) == Counter(
        node for route in routes for node in route
    )
    assert validate_solution(_plain_instance(routes), actual).feasible


@pytest.mark.parametrize("routes, move", [
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("relocate_pair", 0, 4, 1, 0)),
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("relocate_pair", 0, 1, 1, 4)),
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("exchange_pair_single", 0, 4, 1, 0)),
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("exchange_pair_single", 0, 1, 1, 3)),
    # A single customer inside the pair overlaps; the endpoint at 3 is legal.
    (((1, 2, 3, 4, 5),), Move("exchange_pair_single", 0, 1, 0, 2)),
    # Pair intervals [1, 3) and [0, 2) overlap by one customer.
    (((1, 2, 3, 4, 5),), Move("exchange_pairs", 0, 1, 0, 0)),
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("exchange_pairs", 0, 4, 1, 0)),
    (((1, 2, 3, 4, 5), (6, 7, 8)), Move("exchange_pairs", 0, 1, 1, 2)),
])
def test_segment_moves_reject_out_of_range_or_overlapping_positions(routes, move):
    with pytest.raises(ValueError, match="out of range|overlap"):
        apply_move(routes, move)


def test_each_segment_operator_can_be_selected_from_the_enumerator():
    routes = ((1, 2, 3, 4), (5, 6, 7, 8))
    assert set(M9_OPERATORS) <= set(OPERATORS)
    for kind in M9_OPERATORS:
        moves = tuple(enumerate_moves(routes, operators=(kind,)))
        assert moves
        assert {move.kind for move in moves} == {kind}
        assert all(
            Counter(node for route in apply_move(routes, move) for node in route)
            == Counter(node for route in routes for node in route)
            for move in moves
        )


def _neighbours(customer_count, *edges):
    rows = [set() for _ in range(customer_count + 1)]
    for left, right in edges:
        rows[left].add(right)
        rows[right].add(left)
    return tuple(frozenset(row) for row in rows)


@pytest.mark.parametrize("routes, move, new_edge, retained_pair", [
    (((9, 1, 2, 3, 8), (5, 6, 7, 4)),
     Move("relocate_pair", 0, 1, 1, 1), (5, 1), (1, 2)),
    (((9, 1, 2, 3, 8), (5, 6, 7)),
     Move("exchange_pair_single", 0, 1, 1, 1), (9, 6), (1, 2)),
    (((9, 1, 2, 3, 8), (5, 6, 7, 4)),
     Move("exchange_pairs", 0, 1, 1, 1), (9, 6), (1, 2)),
])
def test_segment_neighbour_filter_needs_a_new_customer_connection(
        routes, move, new_edge, retained_pair):
    empty = _neighbours(max(node for route in routes for node in route))
    only_existing_pair = _neighbours(
        max(node for route in routes for node in route), retained_pair
    )
    new_connection = _neighbours(
        max(node for route in routes for node in route), new_edge
    )

    assert not allows_move(routes, move, empty)
    # Keeping an unchanged edge inside a moved pair does not make it a candidate.
    assert not allows_move(routes, move, only_existing_pair)
    assert allows_move(routes, move, new_connection)


@pytest.mark.parametrize("routes, move", [
    # The new route endpoint 2 was internal before the pair relocation.
    (((9, 1, 2, 3, 8), (5, 6, 7, 4)),
     Move("relocate_pair", 0, 1, 1, 4)),
    # Customer 4 becomes the first stop after exchanging it with the leading pair.
    (((1, 2, 3, 4, 5),), Move("exchange_pair_single", 0, 0, 0, 3)),
    # Customer 3 becomes the first stop after exchanging adjacent pairs.
    (((1, 2, 3, 4, 5, 6),), Move("exchange_pairs", 0, 0, 0, 2)),
])
def test_segment_neighbour_filter_allows_new_depot_connections(routes, move):
    empty = _neighbours(max(node for route in routes for node in route))
    assert allows_move(routes, move, empty)


def test_pair_relocation_rejects_empty_source_under_fixed_fleet():
    routes = ((1, 2), (3, 4))
    instance = _plain_instance(routes)
    before = validate_solution(instance, routes)
    move = Move("relocate_pair", 0, 0, 1, 0)
    candidate = apply_move(routes, move)

    assert candidate == ((), (1, 2, 3, 4))
    assert Counter(node for route in candidate for node in route) == Counter(
        node for route in routes for node in route
    )
    # The complete solution is feasible with one route, but M3/M9 local moves
    # keep the original number of used vehicles fixed.
    after = validate_solution(instance, candidate)
    assert after.feasible and after.vehicles == before.vehicles - 1
    assert move_delta(instance, routes, move) is None
    result = improve(instance, routes, operators=("relocate_pair",),
                     evaluation_mode="full")
    assert not result.moves
    assert result.routes == routes
    assert validate_solution(instance, result.routes).vehicles == before.vehicles


def _assert_random_move_matches_full(instance, routes, before, move, evaluators):
    candidate = apply_move(routes, move)
    assert Counter(node for route in candidate for node in route) == Counter(
        node for route in routes for node in route
    )

    # validate_solution is authoritative for feasibility. Sum its independently
    # recomputed route distances too, so infeasible candidates still have a
    # directly checked travel-cost delta.
    full = validate_solution(instance, candidate)
    full_route_distance = sum(
        evaluate_route(instance, route).distance for route in candidate
    )
    assert full.distance == (full_route_distance if full.feasible else None)

    affected = {move.route_a, move.route_b}
    keeps_fleet = all(candidate[index] for index in affected)
    expected_delta = (
        full_route_distance - before.distance
        if full.feasible and keeps_fleet else None
    )
    assert move_delta(instance, routes, move) == expected_delta
    for evaluator in evaluators:
        assert evaluator.delta(move) == expected_delta
    if expected_delta is not None:
        assert full.vehicles == before.vehicles
        assert full.distance - before.distance == expected_delta


@pytest.mark.parametrize("name", ["C103", "R101", "RC101"])
def test_150_random_real_moves_per_operator_match_full_validation(name):
    instance = read_solomon(DATA / f"{name}.txt")
    routes = construct(instance)
    before = validate_solution(instance, routes)
    assert before.feasible
    rng = Random(20261002 + sum(map(ord, name)))
    evaluators = tuple(
        CachedMoveEvaluator(instance, routes, mode=mode)
        for mode in ("cached", "incremental")
    )

    for kind in M9_OPERATORS:
        candidates = tuple(enumerate_moves(routes, operators=(kind,)))
        assert len(candidates) >= 150, f"{name}/{kind} generated only {len(candidates)} moves"
        sample = rng.sample(candidates, 150)
        for move in sample:
            _assert_random_move_matches_full(
                instance, routes, before, move, evaluators
            )


def _operator_search_case():
    points = (
        (0, 0), (9, 43), (39, 22), (10, 35), (45, 42), (38, 11),
        (24, 18), (39, 27), (36, 37), (2, 46), (33, 34),
    )
    customers = tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 10**9, 0)
        for i, (x, y) in enumerate(points)
    )
    instance = Instance("m9-search", 3, 99, customers)
    routes = ((5, 1, 8, 2), (7, 4, 6, 3), (9, 10))
    assert validate_solution(instance, routes).feasible
    return instance, routes


@pytest.mark.parametrize("kind", M9_OPERATORS)
@pytest.mark.parametrize("strategy", ["first", "best"])
def test_full_cached_and_incremental_modes_accept_the_same_nonempty_sequence(kind, strategy):
    instance, routes = _operator_search_case()
    results = {
        mode: improve(instance, routes, operators=(kind,), strategy=strategy,
                      evaluation_mode=mode)
        for mode in ("full", "cached", "incremental")
    }

    baseline = results["full"]
    assert baseline.moves
    assert all(accepted.move.kind == kind for accepted in baseline.moves)
    assert baseline.stop_reason == "local_optimum"
    for result in results.values():
        assert result.moves == baseline.moves
        assert result.routes == baseline.routes
        assert result.distance == baseline.distance < baseline.initial_distance
        assert validate_solution(instance, result.routes).feasible


def test_cyclic_combined_search_has_the_same_trace_in_all_evaluation_modes():
    instance, routes = _operator_search_case()
    operators = (*M9_OPERATORS, "relocate", "swap", "two_opt", "two_opt_star")
    results = [improve(instance, routes, operators=operators, operator_schedule="cyclic",
                       evaluation_mode=mode) for mode in ("full", "cached", "incremental")]
    reference = results[0]
    assert len({accepted.move.kind for accepted in reference.moves}) > 1
    assert reference.stop_reason == "local_optimum"
    for result in results:
        assert result.moves == reference.moves
        assert result.routes == reference.routes
        assert validate_solution(instance, result.routes).distance == result.distance


def test_deadline_stops_and_max_moves_none_searches_past_two_acceptances():
    points = (
        (0, 0), (14, 19), (11, 8), (4, 5), (27, 21),
        (0, 10), (16, 14), (28, 19), (2, 10),
    )
    customers = tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 10**9, 0)
        for i, (x, y) in enumerate(points)
    )
    instance = Instance("m9-unbounded-search", 2, 99, customers)
    routes = ((8, 5, 3, 7), (2, 4, 6, 1))
    initial = validate_solution(instance, routes)
    assert initial.feasible

    expired = improve(instance, routes, deadline=-1, evaluation_mode="full")
    assert expired.stop_reason == "time_limit"
    assert expired.routes == routes
    assert not expired.moves

    unlimited = improve(
        instance, routes,
        operators=("relocate", "swap", "two_opt", "two_opt_star"),
        max_moves=None, evaluation_mode="full",
    )
    assert len(unlimited.moves) > 2
    assert unlimited.stop_reason == "local_optimum"
    assert validate_solution(instance, unlimited.routes).feasible
    assert unlimited.distance < initial.distance
    assert sum(move.delta for move in unlimited.moves) == (
        unlimited.distance - unlimited.initial_distance
    )
