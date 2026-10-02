"""M7: repair ordering and budget limits preserve complete feasible incumbents."""

from dataclasses import replace
from pathlib import Path

import pytest

from vrptw import Config, Customer, Instance, read_solomon, solve, validate_solution
from vrptw.fleet import insert_fixed, minimise_fleet
from vrptw.evaluate import evaluate_route


def _urgent_instance():
    return Instance("urgent", 2, 10, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        Customer(1, 10, 0, 1, 0, 20_000, 0),
        Customer(2, 0, 0, 1, 0, 100_000, 10_000),
        Customer(3, 1, 0, 1, 0, 1_500, 1_000),
    ))


@pytest.mark.parametrize("order", ["due", "slack"])
def test_urgent_repair_inserts_constrained_customer_before_flexible_one(order):
    instance = _urgent_instance()
    pool = [2, 3]
    assert insert_fixed(instance, ((1,),), pool) is None
    repaired = insert_fixed(instance, ((1,),), pool, order=order)
    assert validate_solution(instance, repaired).feasible
    assert repaired == ((3, 1, 2),)
    assert pool == [2, 3]


def test_repair_deadline_discards_partial_work_without_mutating_source(monkeypatch):
    instance = _urgent_instance()
    routes = ((1,),)
    # The first insertion completes; the second expires before its first position.
    ticks = iter((0, 0, 0, 0, 2))
    monkeypatch.setattr("vrptw.fleet.monotonic", lambda: next(ticks))
    assert insert_fixed(instance, routes, [3, 2], deadline=1) is None
    assert routes == ((1,),)


def test_expired_fleet_budget_keeps_incumbent_and_runs_no_trials(monkeypatch):
    instance = _urgent_instance()
    incumbent = ((3, 1), (2,))
    monkeypatch.setattr("vrptw.fleet.monotonic", lambda: 2)
    result = minimise_fleet(instance, incumbent, seed=0, attempts_per_k=100,
                            deadline=1, started_at=0)
    assert result.routes == incumbent
    assert result.attempts == ((1, 0),)
    assert result.stop_reason == "time_limit"


def test_solve_allocates_fraction_of_remaining_time_to_fleet(monkeypatch):
    import importlib
    module = importlib.import_module("vrptw.solve")
    instance = _urgent_instance()
    monkeypatch.setattr(module, "monotonic", lambda: 10)
    original_fleet = module.minimise_fleet
    original_ils = module.run_ils
    deadlines = []

    def fleet(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        kwargs["attempts_per_k"] = 0
        return original_fleet(*args, **kwargs)

    def ils(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        kwargs["max_iterations"] = 0
        return original_ils(*args, **kwargs)

    monkeypatch.setattr(module, "minimise_fleet", fleet)
    monkeypatch.setattr(module, "run_ils", ils)
    result = solve(instance, Config(time_limit_seconds=4, fleet_time_fraction=0.75))
    assert deadlines == [13, 14]
    assert result.evaluation.feasible


def test_capacity_bound_skips_fleet_trials_and_enters_distance_search():
    instance = read_solomon(Path(__file__).resolve().parents[1] / "data" / "C103.txt")
    config = Config(max_iterations=1, max_moves=0, repair_order="due")
    plain = solve(instance, config)
    profiled = solve(instance, replace(config, diagnostics=True))
    assert plain.fleet.attempts == ()
    assert plain.fleet.stop_reason == "lower_bound"
    assert plain.iterations == 1
    assert plain.routes == profiled.routes
    assert plain.fleet.attempts == profiled.fleet.attempts


def _uncached_regret(instance, source, customers):
    """Reference: enumerate every customer's every position again each round."""
    working = list(source)
    pending = list(customers)
    rank = {customer: index for index, customer in enumerate(pending)}
    while pending:
        selected = []
        for customer in pending:
            options = []
            for index, route in enumerate(working):
                original = evaluate_route(instance, route)
                positions = []
                for position in range(len(route) + 1):
                    candidate = route[:position] + (customer,) + route[position:]
                    value = evaluate_route(instance, candidate)
                    if value.feasible:
                        positions.append((value.distance - original.distance, index,
                                          position, candidate, value.distance))
                if positions:
                    options.append(min(positions))
            options.sort()
            if not options:
                return None
            regret = options[1][0] - options[0][0] if len(options) > 1 else 0
            priority = (len(options) == 1, regret, -options[0][0], -rank[customer])
            selected.append((priority, customer, options[0]))
        _, customer, (_, index, _, candidate, _) = max(selected)
        working[index] = candidate
        pending.remove(customer)
    return tuple(working)


def test_regret_choices_match_full_recomputation_after_each_insertion():
    from random import Random
    rng = Random(7)
    succeeded = failed = 0
    for repetition in range(30):
        instance = Instance(f"regret-{repetition}", 3, 6, (
            Customer(0, 0, 0, 0, 0, 100_000, 0),
            *(Customer(i, rng.randrange(1, 10), rng.randrange(1, 10),
                       rng.randrange(1, 4), 0, rng.randrange(15, 70) * 1000, 1000)
              for i in range(1, 9)),
        ))
        source = ((1,), (2,), (3,))
        customers = list(range(4, 9))
        actual = insert_fixed(instance, source, customers, strategy="regret2")
        assert actual == _uncached_regret(instance, source, customers)
        if actual is None:
            failed += 1
        else:
            succeeded += 1
            assert validate_solution(instance, actual).feasible
    assert succeeded > 0 and failed > 0


def test_regret_fixed_work_is_unchanged_by_diagnostics():
    instance = _urgent_instance()
    config = Config(max_iterations=2, fleet_attempts_per_k=2, max_moves=1,
                    repair_strategy="regret2")
    plain = solve(instance, config)
    profiled = solve(instance, replace(config, diagnostics=True))
    assert plain.routes == profiled.routes
    assert plain.fleet.attempts == profiled.fleet.attempts
    assert plain.evaluation == profiled.evaluation


@pytest.mark.parametrize("kwargs", [
    {"fleet_time_fraction": -0.1}, {"fleet_time_fraction": 1.1},
    {"fleet_time_fraction": float("nan")}, {"fleet_time_fraction": True},
    {"repair_order": "random"}, {"construction_order": "random"},
    {"repair_strategy": "random"},
    {"remove_min": 1.5}, {"remove_max": True}, {"restart_after": 1.5},
])
def test_m7_config_rejects_invalid_budget_or_order(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)
