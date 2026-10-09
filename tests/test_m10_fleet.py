"""M10 exact insertion, internal partial repairs and complete fleet publication."""

from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from vrptw import Config, Customer, Instance, read_solomon, solve, validate_solution
from vrptw.construct import construct
from vrptw.diagnostics import DiagnosticCollector
from vrptw.evaluate import evaluate_route
from vrptw.fleet import (_insertion_choice, _related_remove, _repair_partial, capacity_lower_bound,
                         _try_route_removal, insert_fixed, minimise_fleet)
from vrptw.route_cache import RouteCache

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.mark.parametrize("name", ["C103", "R109", "RC107"])
@pytest.mark.parametrize("strategy", ["cheapest", "regret2"])
def test_cached_reconstruction_preserves_seeded_full_evaluation_result(name, strategy):
    from vrptw.fleet import try_fleet
    instance = read_solomon(DATA / f"{name}.txt")
    target = max(capacity_lower_bound(instance), len(construct(instance)) - 1)
    for trial in range(4):
        options = dict(repair_order="due", repair_strategy=strategy)
        expected = try_fleet(instance, target, Random(10 + trial), trial, **options)
        actual = try_fleet(instance, target, Random(10 + trial), trial, cached_repair=True, **options)
        assert actual == expected
        if actual is not None:
            assert validate_solution(instance, actual).feasible


@pytest.mark.parametrize("name", ["C103", "R109", "RC107"])
def test_cached_insertion_best_position_matches_all_full_positions(name):
    instance = read_solomon(DATA / f"{name}.txt")
    rng = Random(1010)
    source = construct(instance)
    checked = rejected = 0
    for _ in range(250):
        route = rng.choice(source)
        customer = rng.randrange(1, instance.customer_count + 1)
        reduced = tuple(i for i in route if i != customer)
        original = evaluate_route(instance, reduced)
        cache = RouteCache(instance, reduced) if reduced else None
        options = []
        for position in range(len(reduced) + 1):
            candidate = reduced[:position] + (customer,) + reduced[position:]
            verdict = evaluate_route(instance, candidate)
            if verdict.feasible:
                options.append((verdict.distance - original.distance, position))
        fast = _insertion_choice(instance, reduced, cache, customer,
                                 deadline=None, diagnostics=None)
        assert fast == (min(options) if options else None)
        checked += bool(options)
        rejected += not options
    assert checked and rejected


def _instance():
    return Instance("urgent", 3, 10, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        Customer(1, 10, 0, 1, 0, 20_000, 0),
        Customer(2, 0, 0, 1, 0, 100_000, 10_000),
        Customer(3, 1, 0, 1, 0, 1_500, 1_000),
    ))


def test_fast_regret_matches_existing_complete_repair():
    instance = _instance()
    for order in ("input", "due", "slack"):
        source, pending = ((1,),), [2, 3]
        result, missing = _repair_partial(instance, source, pending, order=order,
                                           deadline=None, diagnostics=None)
        reference = insert_fixed(instance, source, pending, order=order, strategy="regret2")
        if reference is None:
            assert missing
        else:
            assert not missing
            assert result == reference
            assert validate_solution(instance, result).feasible
        assert source == ((1,),) and pending == [2, 3]


def test_partial_repair_keeps_blocked_customers_without_losing_coverage():
    instance = replace(_instance(), capacity=2)
    source, pending = ((1,),), [2, 3]
    routes, missing = _repair_partial(instance, source, pending, order="due",
                                       deadline=None, diagnostics=None)
    assert missing == [2]
    assert routes == ((3, 1),)
    assert sorted([*missing, *(i for route in routes for i in route)]) == [1, 2, 3]
    assert evaluate_route(instance, routes[0]).feasible
    # This internal state must never be mistaken for a complete solution.
    assert not validate_solution(instance, routes).feasible


def test_related_removal_preserves_partition_and_feasible_routes():
    instance = read_solomon(DATA / "R109.txt")
    source = construct(instance)
    pool = list(source[0])
    reduced, missing = _related_remove(instance, source[1:], pool, 12, Random(4))
    assert sorted([*missing, *(i for route in reduced for i in route)]) == list(range(1, 101))
    assert all(evaluate_route(instance, route).feasible for route in reduced)
    assert source == construct(instance)
    assert _related_remove(instance, source[1:], pool, 12, Random(4)) == (reduced, missing)


def test_related_removal_rolls_back_integer_rounding_window_violation():
    instance = Instance("rounding", 2, 10, (
        Customer(0, 0, 0, 0, 0, 20_000, 0),
        Customer(1, 1, 1, 1, 0, 20_000, 0),
        Customer(2, 2, 2, 1, 0, 20_000, 0),
        Customer(3, 3, 3, 1, 0, 4_242, 0),
        Customer(4, 0, 0, 1, 0, 20_000, 0),
    ))
    class Nearest:
        def choice(self, pool):
            return pool[0]
        def random(self):
            return 0.0
        def shuffle(self, pool):
            pass
    assert evaluate_route(instance, (1, 2, 3)).feasible
    assert not evaluate_route(instance, (3,)).feasible  # direct arrival is 4243
    routes, missing = _related_remove(instance, ((1, 2, 3),), [4], 2, Nearest())
    assert routes == ((1, 2, 3),) and missing == [4]


@pytest.mark.parametrize("strategy", ["route_removal", "related", "hybrid"])
def test_fleet_success_and_target_records_are_independently_validated(strategy):
    instance = _instance()
    source = ((3,), (1,), (2,))
    result = minimise_fleet(instance, source, seed=0, attempts_per_k=10, strategy=strategy)
    assert validate_solution(instance, result.routes).feasible
    assert len(result.routes) == 1
    assert result.stop_reason == "lower_bound"
    assert [target.target for target in result.targets] == [2, 1]
    assert result.attempts == tuple((value.target, value.trials) for value in result.targets)
    assert all(value.status == "found" and value.first_feasible_seconds is not None
               and value.repair_rounds >= value.trials for value in result.targets)


def test_expired_trial_keeps_complete_incumbent(monkeypatch):
    instance = _instance()
    incumbent = ((3, 1), (2,))
    monkeypatch.setattr("vrptw.fleet.monotonic", lambda: 2)
    result = minimise_fleet(instance, incumbent, seed=0, attempts_per_k=100,
                            strategy="related", deadline=1, started_at=0)
    assert result.routes == incumbent
    assert result.attempts == ((1, 0),)
    assert result.targets[0].status == "time_limit"
    assert result.targets[0].first_feasible_seconds is None


def test_later_related_round_can_repair_blocked_route(monkeypatch):
    import vrptw.fleet as fleet
    instance = _instance()
    calls = []
    original = fleet._repair_partial

    def first_blocked(instance, routes, pending, **kwargs):
        calls.append((routes, pending))
        if len(calls) == 1:
            return routes, pending
        return original(instance, routes, pending, **kwargs)

    monkeypatch.setattr(fleet, "_repair_partial", first_blocked)
    candidate, rounds = _try_route_removal(instance, ((3, 1), (2,)), Random(2), 0,
                                           related_count=1, repair_rounds=3, repair_order="due",
                                           deadline=None, diagnostics=None)
    assert rounds == 2
    assert validate_solution(instance, candidate).feasible
    assert len(candidate) == 1


@pytest.mark.parametrize("name", ["R101", "R109", "R112"])
def test_related_ruin_repairs_real_route_that_direct_insertion_cannot(name):
    instance = read_solomon(DATA / f"{name}.txt")
    routes = construct(instance)
    seed = (len(routes) - 1 - capacity_lower_bound(instance)) * 9973 + 1_000_003
    options = dict(related_count=8, repair_order="due", deadline=None, diagnostics=None)
    direct, rounds = _try_route_removal(instance, routes, Random(seed), 0,
                                       repair_rounds=1, **options)
    assert direct is None and rounds == 1
    candidate, rounds = _try_route_removal(instance, routes, Random(seed), 0,
                                          repair_rounds=5, **options)
    assert rounds > 1
    assert validate_solution(instance, candidate).feasible
    assert len(candidate) == len(routes) - 1


@pytest.mark.parametrize("due, capacity, expected", [
    (2_000, 1, (4_000, 0)), (1_999, 1, None), (2_000, 2, (4_000, 0)),
])
def test_empty_slot_insertion_uses_exact_due_boundary(due, capacity, expected):
    instance = Instance("boundary", 1, capacity, (
        Customer(0, 0, 0, 0, 0, 5_000, 0),
        Customer(1, 2, 0, 1, 0, due, 1_000),
    ))
    assert _insertion_choice(instance, (), None, 1, deadline=None, diagnostics=None) == expected
    assert evaluate_route(instance, (1,)).feasible == (expected is not None)


def test_failed_target_retries_and_reports_not_found_without_infeasibility_claim():
    instance = replace(_instance(), capacity=2)
    source = ((3, 1), (2,))
    # Capacity lower bound is already two, so no unsound K=1 trial is run.
    result = minimise_fleet(instance, source, seed=0, attempts_per_k=4, strategy="related")
    assert result.targets == () and result.stop_reason == "lower_bound"
    tight = Instance("separate", 2, 10, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        Customer(1, 10, 0, 1, 10_000, 10_000, 0),
        Customer(2, -10, 0, 1, 10_000, 10_000, 0),
    ))
    result = minimise_fleet(tight, ((1,), (2,)), seed=0, attempts_per_k=4, strategy="related")
    assert result.routes == ((1,), (2,))
    assert result.attempts == ((1, 4),)
    assert result.targets[0].status == "not_found"
    assert result.targets[0].first_feasible_seconds is None
    assert result.stop_reason == "target_not_found"


def test_fixed_work_is_reproducible_with_or_without_diagnostics():
    instance = read_solomon(DATA / "R109.txt")
    config = Config(seed=7, max_iterations=1, max_moves=1, fleet_attempts_per_k=5,
                    fleet_strategy="related")
    first = solve(instance, config)
    second = solve(instance, replace(config, diagnostics=True))
    assert first.routes == second.routes
    assert first.fleet.attempts == second.fleet.attempts
    assert [v.repair_rounds for v in first.fleet.targets] == [v.repair_rounds for v in second.fleet.targets]
    assert validate_solution(instance, first.routes).feasible


@pytest.mark.parametrize("settings", [
    {"fleet_strategy": "wrong"}, {"fleet_repair_rounds": 0},
    {"fleet_repair_rounds": True}, {"fleet_related_count": 0},
    {"fleet_related_count": 1.5},
])
def test_config_rejects_invalid_fleet_search(settings):
    with pytest.raises(ValueError):
        Config(**settings)
