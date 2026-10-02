"""M8: exact route-cache and incremental move evaluation regressions."""

from collections import Counter
from itertools import combinations
from pathlib import Path
from random import Random

import pytest

from vrptw import (Config, Customer, Instance, apply_move, construct,
                   improve, move_delta, read_solomon, validate_solution)
from vrptw.diagnostics import DiagnosticCollector
from vrptw.evaluate import evaluate_route
from vrptw.local_search import OPERATORS, CachedMoveEvaluator, enumerate_moves
from vrptw.neighbourhood import compute_neighbours
from vrptw.route_cache import RouteCache


DATA = Path(__file__).resolve().parents[1] / "data"


def _assert_fast_matches(instance, candidate, actual):
    """Compare all integer route outputs, retaining repeated violation codes."""
    expected = evaluate_route(instance, candidate)
    assert type(actual.load) is int
    assert type(actual.distance) is int
    assert type(actual.return_time) is int
    assert actual.load == expected.load
    assert actual.distance == expected.distance
    assert actual.return_time == expected.return_time
    assert actual.feasible == expected.feasible
    assert isinstance(actual.codes, (tuple, frozenset))
    expected_codes = tuple(issue.code for issue in expected.violations)
    if isinstance(actual.codes, tuple):
        assert Counter(actual.codes) == Counter(expected_codes)
    else:
        assert actual.codes == frozenset(expected_codes)


def _random_scheduled_instance(seed, customer_count=24, route_count=4):
    """Make feasible source routes with deliberate waits and tight due times."""
    rng = Random(seed)
    coordinates = [(0, 0)] + [
        (rng.randrange(1, 45), rng.randrange(1, 45))
        for _ in range(customer_count)
    ]
    demands = [0] + [rng.randrange(1, 5) for _ in range(customer_count)]
    broad = tuple(
        Customer(i, x, y, demands[i], 0, 10**9, 0)
        for i, (x, y) in enumerate(coordinates)
    )
    draft = Instance(f"m8-random-{seed}", route_count, sum(demands), broad)

    ids = list(range(1, customer_count + 1))
    rng.shuffle(ids)
    base, extra = divmod(customer_count, route_count)
    routes = []
    offset = 0
    for route_index in range(route_count):
        size = base + (route_index < extra)
        routes.append(tuple(ids[offset:offset + size]))
        offset += size

    ready = [0] * (customer_count + 1)
    due = [10**9] * (customer_count + 1)
    service = [0] + [rng.choice((0, 0, 400, 1_500, 3_000))
                     for _ in range(customer_count)]
    return_times = []
    for route in routes:
        previous = 0
        departure = 0
        for customer_id in route:
            arrival = departure + draft.distance[previous][customer_id]
            # Half the stops wait deliberately; some windows are exact at service start.
            wait = rng.choice((0, 0, 700, 2_000, 6_000))
            ready[customer_id] = arrival + wait
            start = max(arrival, ready[customer_id])
            due[customer_id] = start + rng.choice((0, 0, 250, 1_500, 5_000))
            departure = start + service[customer_id]
            previous = customer_id
        return_times.append(departure + draft.distance[previous][0])

    # The fleet's most constrained return is exactly at depot close.
    due[0] = max(return_times)
    customers = tuple(
        Customer(i, x, y, demands[i], ready[i], due[i], service[i])
        for i, (x, y) in enumerate(coordinates)
    )
    capacity = max(sum(demands[cid] for cid in route) for route in routes)
    instance = Instance(f"m8-random-{seed}", route_count, capacity, customers)
    assert validate_solution(instance, routes).feasible
    return instance, tuple(routes)


def _boundary_instance():
    """A feasible route with waiting and alternatives that hit each constraint."""
    instance = Instance("m8-boundaries", 2, 2, (
        Customer(0, 0, 0, 0, 0, 15_000, 0),
        Customer(1, 1, 0, 1, 10_000, 10_000, 0),
        Customer(2, 2, 0, 1, 11_000, 12_000, 0),
        Customer(3, 20, 0, 1, 0, 100_000, 0),
    ))
    source = (1, 2)
    assert evaluate_route(instance, source).feasible
    return instance, source


def _square_instance():
    points = ((0, 0), (1, 0), (2, 0), (0, 1), (0, 2))
    instance = Instance("m8-square", 2, 5, tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 100_000, 0)
        for i, (x, y) in enumerate(points)
    ))
    return instance, ((1, 4), (3, 2))


def test_route_cache_matches_full_evaluation_for_prefix_suffix_and_random_edits():
    instance, routes = _random_scheduled_instance(804)
    route = max(routes, key=len)
    cache = RouteCache(instance, route)

    candidates = set()
    # Exhaust every unchanged-prefix / changed-segment / unchanged-suffix boundary.
    for start, end in combinations(range(len(route) + 1), 2):
        candidates.add(route[:start] + tuple(reversed(route[start:end])) + route[end:])
    rng = Random(805)
    for _ in range(180):
        candidate = list(route)
        rng.shuffle(candidate)
        candidates.add(tuple(candidate))
    # Move-like membership changes exercise cached boundary states around new customers.
    other = next(candidate_route for candidate_route in routes if candidate_route is not route)
    candidates.add(route[:2] + other[:2])
    candidates.add(route[:1] + other[-2:] + route[1:])
    candidates.update(((), route[1:], route[:-1], route[:2], route[-2:]))

    for candidate in candidates:
        assert len(candidate) == len(set(candidate))
        assert all(1 <= customer <= instance.customer_count for customer in candidate)
        _assert_fast_matches(instance, candidate, cache.evaluate(candidate))


def test_route_cache_matches_waiting_time_window_capacity_and_depot_close_boundaries():
    instance, source = _boundary_instance()
    cache = RouteCache(instance, source)
    baseline = cache.evaluate(source)
    assert baseline.feasible
    assert evaluate_route(instance, source).visits[0].waiting == 9_000

    reversed_route = (2, 1)
    late_and_over_capacity = (1, 2, 3)
    time_window = cache.evaluate(reversed_route)
    overloaded_late = cache.evaluate(late_and_over_capacity)
    _assert_fast_matches(instance, reversed_route, time_window)
    _assert_fast_matches(instance, late_and_over_capacity, overloaded_late)
    assert "time_window" in set(time_window.codes)
    assert {"capacity", "depot_close"} <= set(overloaded_late.codes)


def test_route_cache_rejects_empty_or_infeasible_source_routes():
    instance, source = _boundary_instance()
    with pytest.raises(ValueError):
        RouteCache(instance, ())
    with pytest.raises(ValueError):
        RouteCache(instance, (2, 1))


def test_prefix_cache_respects_nonzero_depot_ready_and_unchanged_empty_routes():
    instance = Instance("late-depot", 2, 10, (
        Customer(0, 0, 0, 0, 8_000, 50_000, 0),
        Customer(1, 1, 0, 1, 0, 9_000, 2_000),
        Customer(2, 2, 0, 1, 12_000, 40_000, 0),
    ))
    cache = RouteCache(instance, (1, 2))
    for route in ((1, 2), (2, 1), (1,), (2,), ()):
        _assert_fast_matches(instance, route, cache.evaluate(route))
    for mode in ("full", "cached", "incremental"):
        result = improve(instance, ((1, 2), ()), evaluation_mode=mode)
        assert result.routes == ((1, 2), ())


@pytest.mark.parametrize("seed", [19, 47, 101])
def test_synthetic_random_move_deltas_match_full_recomputation(seed):
    instance, routes = _random_scheduled_instance(seed)
    checked = 0
    evaluators = {
        mode: CachedMoveEvaluator(instance, routes, mode=mode)
        for mode in ("cached", "incremental")
    }
    for move in enumerate_moves(routes):
        candidate = apply_move(routes, move)
        expected = move_delta(instance, routes, move)
        for evaluator in evaluators.values():
            assert evaluator.delta(move) == expected
        if expected is not None:
            full = validate_solution(instance, candidate)
            assert full.feasible
            assert full.distance - validate_solution(instance, routes).distance == expected
        checked += 1
    assert checked > 100
    # Random feasible source schedules include tight windows, waits and an exact depot close.
    assert any(
        visit.waiting > 0
        for route in routes
        for visit in evaluate_route(instance, route).visits
    )
    assert any(
        visit.start == instance.customers[visit.customer].due
        for route in routes
        for visit in evaluate_route(instance, route).visits
    )
    assert max(evaluate_route(instance, route).load for route in routes) == instance.capacity
    assert max(evaluate_route(instance, route).return_time for route in routes) == instance.customers[0].due


def test_cached_move_evaluator_rebuilds_accepted_routes_without_stale_state():
    instance = Instance("m8-cache-update", 2, 5, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        Customer(1, 1, 0, 1, 0, 100_000, 0),
        Customer(2, 2, 0, 1, 0, 100_000, 0),
        Customer(3, 0, 1, 1, 0, 100_000, 0),
        Customer(4, 0, 2, 1, 0, 100_000, 0),
    ))
    initial = ((1, 4), (3, 2))
    for mode in ("cached", "incremental"):
        routes = initial
        evaluator = CachedMoveEvaluator(instance, routes, mode=mode)
        accepted = 0
        for _ in range(8):
            moves = tuple(enumerate_moves(routes))
            for move in moves:
                assert evaluator.delta(move) == move_delta(instance, routes, move)
            improving = next(
                (move for move in moves if (move_delta(instance, routes, move) or 0) < 0),
                None,
            )
            if improving is None:
                break
            candidate = apply_move(routes, improving)
            assert move_delta(instance, routes, improving) is not None
            routes = candidate
            evaluator.update(routes, {improving.route_a, improving.route_b})
            accepted += 1
        assert accepted > 0
        for move in enumerate_moves(routes):
            assert evaluator.delta(move) == move_delta(instance, routes, move)


@pytest.mark.parametrize("strategy", ["first", "best"])
def test_full_cached_and_incremental_search_modes_have_identical_fixed_work(strategy):
    instance, routes = _square_instance()
    results = {}
    for mode in ("full", "cached", "incremental"):
        plain = improve(instance, routes, strategy=strategy, max_moves=5,
                        num_neighbours=None, evaluation_mode=mode)
        assert plain.moves
        for count in (instance.customer_count - 1,
                      instance.customer_count + 1):
            complete_neighbours = improve(
                instance, routes, strategy=strategy, max_moves=5,
                num_neighbours=count, evaluation_mode=mode,
            )
            assert complete_neighbours.routes == plain.routes
            assert complete_neighbours.distance == plain.distance
            assert complete_neighbours.moves == plain.moves
        results[mode] = plain

    baseline = results["full"]
    assert validate_solution(instance, baseline.routes).feasible
    for result in results.values():
        assert result.routes == baseline.routes
        assert result.distance == baseline.distance
        assert result.moves == baseline.moves
        assert result.stop_reason == baseline.stop_reason


def test_complete_neighbourhood_matches_none_at_and_above_full_degree():
    instance, _ = _square_instance()
    all_neighbours = compute_neighbours(instance, None)
    complete_sets = [
        compute_neighbours(instance, count)
        for count in (instance.customer_count - 1, instance.customer_count + 1)
    ]
    assert all(neighbours == all_neighbours for neighbours in complete_sets)


def test_incremental_rejection_diagnostics_match_full_violation_codes():
    instance, routes = _random_scheduled_instance(616)
    full_diagnostics = DiagnosticCollector()
    cached_diagnostics = DiagnosticCollector()
    incremental_diagnostics = DiagnosticCollector()
    evaluators = (
        CachedMoveEvaluator(instance, routes, mode="cached",
                            diagnostics=cached_diagnostics),
        CachedMoveEvaluator(instance, routes, mode="incremental",
                            diagnostics=incremental_diagnostics),
    )
    checked = 0
    for move in enumerate_moves(routes):
        expected = move_delta(instance, routes, move, diagnostics=full_diagnostics)
        for evaluator in evaluators:
            assert evaluator.delta(move) == expected
        checked += 1
    assert checked > 100

    def candidate_stats(collector):
        phase = next(value for value in collector.snapshot(0).phases
                     if value.phase == "other")
        return (
            phase.candidates,
            phase.feasible_candidates,
            phase.infeasible_candidates,
            phase.rejected_capacity,
            phase.rejected_time_window,
            phase.rejected_depot_close,
            phase.rejected_empty_route,
        )

    expected_stats = candidate_stats(full_diagnostics)
    assert expected_stats[2] > 0
    assert candidate_stats(cached_diagnostics) == expected_stats
    assert candidate_stats(incremental_diagnostics) == expected_stats


def test_random_moves_on_c103_r101_and_rc101_match_both_cached_modes():
    rng = Random(20261002)
    checked = 0
    operator_counts = Counter()
    for name in ("C103", "R101", "RC101"):
        instance = read_solomon(DATA / f"{name}.txt")
        routes = construct(instance)
        assert validate_solution(instance, routes).feasible
        moves = tuple(enumerate_moves(routes))
        assert {move.kind for move in moves} == set(OPERATORS)
        sample = rng.sample(moves, min(800, len(moves)))
        evaluators = {
            mode: CachedMoveEvaluator(instance, routes, mode=mode)
            for mode in ("cached", "incremental")
        }
        before = validate_solution(instance, routes)
        for move in sample:
            operator_counts[move.kind] += 1
            expected = move_delta(instance, routes, move)
            for evaluator in evaluators.values():
                assert evaluator.delta(move) == expected
            if expected is not None:
                after = validate_solution(instance, apply_move(routes, move))
                assert after.feasible
                assert after.distance - before.distance == expected
            checked += 1
    assert checked >= 2_000
    assert set(operator_counts) == set(OPERATORS)


@pytest.mark.parametrize("kwargs", [
    {"evaluation_mode": "approx"},
    {"evaluation_mode": None},
    {"num_neighbours": 0},
    {"num_neighbours": -1},
    {"num_neighbours": 1.5},
    {"num_neighbours": True},
])
def test_config_rejects_invalid_m8_evaluation_settings(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)
