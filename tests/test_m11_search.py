"""M11 penalized fixed-fleet search integration contracts."""

import csv
import importlib
import json
from pathlib import Path
from random import Random
from time import perf_counter

import pytest

from vrptw import Customer, Instance, validate_solution
from vrptw.ils import ILSResult
from vrptw.infeasible_search import improve_soft, perturb_soft, run_penalized_ils
from vrptw.local_search import OPERATORS, Move, apply_move, enumerate_moves
from vrptw.penalties import PenaltyManager, PenaltyParams, SoftEvaluation, evaluate_soft_route, evaluate_soft_solution
from vrptw.solve import Config, solve


ROOT = Path(__file__).resolve().parents[1]
search_module = importlib.import_module("vrptw.infeasible_search")
report_module = importlib.import_module("vrptw.report")
cli_module = importlib.import_module("vrptw.cli")


@pytest.fixture
def small_instance() -> Instance:
    return Instance("m11-small", 4, 4, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        *(Customer(i, i, 0, 1, 0, 100_000, 0) for i in range(1, 9)),
    ))


def _penalized_config(**changes) -> Config:
    values = dict(seed=17, max_iterations=4, fleet_attempts_per_k=0, max_moves=1,
                  remove_min=3, remove_max=3, restart_after=10,
                  infeasible_search=True, penalty_update_interval=2)
    values.update(changes)
    return Config(**values)


def test_fixed_iteration_penalized_solve_is_seed_deterministic(small_instance):
    config = _penalized_config()
    first = solve(small_instance, config)
    second = solve(small_instance, config)

    assert first.routes == second.routes
    assert first.evaluation == second.evaluation
    assert first.iterations == second.iterations == 4
    assert first.stop_reason == second.stop_reason == "max_iterations"
    assert validate_solution(small_instance, first.routes).feasible
    first_states = tuple((row.iteration, row.current_distance, row.candidate_distance,
                          row.candidate_feasible, row.current_feasible, row.accepted,
                          row.load_penalty, row.time_penalty, row.penalty_updated)
                         for row in first.history)
    second_states = tuple((row.iteration, row.current_distance, row.candidate_distance,
                           row.candidate_feasible, row.current_feasible, row.accepted,
                           row.load_penalty, row.time_penalty, row.penalty_updated)
                          for row in second.history)
    assert first_states == second_states


def test_disabled_search_switch_keeps_feasible_m10_path(small_instance):
    default_path = solve(small_instance, Config(
        seed=23, max_iterations=3, fleet_attempts_per_k=0, max_moves=0,
        remove_min=3, remove_max=3,
    ))
    explicit_path = solve(small_instance, Config(
        seed=23, max_iterations=3, fleet_attempts_per_k=0, max_moves=0,
        remove_min=3, remove_max=3, infeasible_search=False,
        adaptive_penalties=False,
    ))

    assert default_path.routes == explicit_path.routes
    assert default_path.evaluation == explicit_path.evaluation
    assert validate_solution(small_instance, default_path.routes).feasible
    assert all(row.load_penalty is None and row.time_penalty is None
               for row in default_path.history)


def test_infeasible_candidate_can_be_accepted_while_public_best_stays_valid(monkeypatch):
    instance = Instance("controlled", 2, 2, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        *(Customer(i, i, 0, 1, 0, 100_000, 0) for i in range(1, 5)),
    ))
    start = ((1, 3), (2, 4))
    improved_best = ((1, 2), (3, 4))
    infeasible_candidate = ((1, 2, 3), (4,))
    manager = PenaltyManager(instance, adaptive=False)
    manager.cost = lambda evaluation: 0
    monkeypatch.setattr(search_module, "PenaltyManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(search_module, "perturb_soft", lambda *args, **kwargs: start)
    candidate_value = evaluate_soft_solution(instance, infeasible_candidate)
    best_value = evaluate_soft_solution(instance, improved_best)
    monkeypatch.setattr(
        search_module,
        "improve_soft",
        lambda *args, **kwargs: (infeasible_candidate, candidate_value,
                                 (improved_best, best_value)),
    )

    result = run_penalized_ils(
        instance, start, seed=7, max_iterations=1, deadline=None, max_moves=0,
        operators=("relocate",), search_strategy="first", remove_min=1,
        remove_max=1, restart_after=10, params=PenaltyParams(), adaptive=False,
        started_at=perf_counter(),
    )

    row = result.history[-1]
    assert row.accepted
    assert not row.candidate_feasible
    assert not row.current_feasible
    assert row.current_excess_load == 1
    assert result.routes == improved_best
    assert validate_solution(instance, result.routes).feasible


def test_intermediate_feasible_state_survives_later_infeasible_descent(monkeypatch):
    instance = Instance("time-order", 1, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 5_000, 0),
        Customer(2, 2, 0, 1, 0, 2_500, 0),
        Customer(3, 3, 0, 1, 0, 4_000, 0),
    ))
    start = ((3, 2, 1),)
    feasible = ((1, 2, 3),)
    final_infeasible = ((2, 1, 3),)
    manager = PenaltyManager(instance, adaptive=False)
    manager.cost = lambda value: {1_500: 300, 0: 200, 1_000: 100}[value.time_warp]
    first_move = Move("two_opt", 0, 0, 0, 3)
    second_move = Move("relocate", 0, 1, 0, 0)

    def controlled_moves(routes, operators):
        if routes == start:
            return iter((first_move,))
        if routes == feasible:
            return iter((second_move,))
        return iter(())

    monkeypatch.setattr(search_module, "enumerate_moves", controlled_moves)
    current, evaluation, feasible_best = improve_soft(
        instance, start, manager, operators=("two_opt", "relocate"),
        max_moves=None, deadline=None, operator_schedule="cyclic",
    )

    assert current == final_infeasible
    assert evaluation == evaluate_soft_solution(instance, final_infeasible)
    assert not evaluation.feasible
    assert feasible_best is not None
    assert feasible_best[0] == feasible
    assert feasible_best[1] == evaluate_soft_solution(instance, feasible)
    assert validate_solution(instance, feasible_best[0]).feasible


def test_penalty_epoch_change_discards_old_rolling_scores(monkeypatch):
    instance = Instance("epoch", 2, 2, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        *(Customer(i, i, 0, 1, 0, 100_000, 0) for i in range(1, 5)),
    ))
    start = ((1, 3), (2, 4))
    feasible_candidate = ((1, 2), (3, 4))
    first_infeasible = ((1, 2, 3), (4,))
    second_infeasible = ((1, 2, 4), (3,))
    states = (feasible_candidate, first_infeasible, second_infeasible)
    values = {
        start: SoftEvaluation(2, 100, 0, 0),
        feasible_candidate: SoftEvaluation(2, 90, 0, 0),
        first_infeasible: SoftEvaluation(2, 80, 1, 0),
        second_infeasible: SoftEvaluation(2, 70, 1, 1),
    }
    monkeypatch.setattr(
        search_module,
        "evaluate_soft_solution",
        lambda instance, routes: values[tuple(tuple(route) for route in routes)],
    )

    class EpochManager:
        def __init__(self, *args, **kwargs):
            self.load_weight = 1_000
            self.time_weight = 1_000
            self.load_feasible_rate = None
            self.time_feasible_rate = None
            self.feasible_rate = None
            self.epoch = 0
            self.registered = 0

        def cost(self, evaluation):
            key = next(routes for routes, value in values.items() if value == evaluation)
            if self.epoch == 0:
                return {start: 1_000, feasible_candidate: 900,
                        first_infeasible: 800, second_infeasible: 700}[key]
            return {start: 900, feasible_candidate: 800,
                    first_infeasible: 10, second_infeasible: 20}[key]

        def register(self, evaluation):
            self.registered += 1
            if self.registered == 2:
                self.epoch = 1
                self.load_weight = 850
                self.time_weight = 850
                return True
            return False

    manager = EpochManager()
    monkeypatch.setattr(search_module, "PenaltyManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(search_module, "perturb_soft", lambda instance, routes, *args, **kwargs: routes)
    next_state = iter(states)

    def controlled_improve(instance, routes, *args, **kwargs):
        candidate = next(next_state)
        return candidate, values[candidate], None

    monkeypatch.setattr(search_module, "improve_soft", controlled_improve)
    result = run_penalized_ils(
        instance, start, seed=3, max_iterations=3, deadline=None, max_moves=0,
        operators=("relocate",), search_strategy="first", remove_min=1,
        remove_max=1, restart_after=20, params=PenaltyParams(update_interval=2),
        adaptive=True, started_at=perf_counter(),
    )

    assert result.history[2].penalty_updated
    assert result.history[2].accepted
    assert result.history[3].accepted is False
    assert result.history[3].current_feasible is False


def test_incomplete_deadline_repair_returns_original_complete_assignment(monkeypatch, small_instance):
    routes = ((1, 2, 3, 4), (5, 6, 7, 8))
    manager = PenaltyManager(small_instance, adaptive=False)
    checks = iter((False, False, True))
    monkeypatch.setattr(search_module, "_expired", lambda deadline: next(checks))

    result = perturb_soft(small_instance, routes, Random(11), 2, manager, deadline=10.0)

    assert result == routes
    assert sorted(customer for route in result for customer in route) == list(range(1, 9))


def test_fixed_fleet_perturbation_and_descent_never_activate_empty_routes(monkeypatch, small_instance):
    routes = ((1, 2, 3), (), (4, 5, 6, 7, 8))
    manager = PenaltyManager(small_instance, adaptive=False)
    perturbed = perturb_soft(small_instance, routes, Random(8), 3, manager)
    assert len(perturbed) == len(routes)
    assert not perturbed[1]
    assert sum(bool(route) for route in perturbed) == 2

    singleton_instance = Instance("singleton", 2, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 10_000, 0),
        Customer(2, 2, 0, 1, 0, 10_000, 0),
        Customer(3, 3, 0, 1, 0, 10_000, 0),
    ))
    source = ((1,), (2, 3))
    monkeypatch.setattr(
        search_module,
        "enumerate_moves",
        lambda current, operators: iter((Move("relocate", 0, 0, 1, 0),)),
    )
    current, evaluation, _ = improve_soft(
        singleton_instance, source, PenaltyManager(singleton_instance, adaptive=False),
        operators=("relocate",), max_moves=1, deadline=None,
    )
    assert current == source
    assert evaluation.feasible
    assert all(current)


def test_all_seven_operators_apply_complete_moves_and_keep_incremental_totals_exact(monkeypatch):
    rng = Random(9_137)
    customers = [Customer(0, 50, 50, 0, 0, 1_000_000, 0)]
    customers.extend(Customer(i, rng.randint(0, 100), rng.randint(0, 100),
                              1, 0, 1_000_000, 0) for i in range(1, 13))
    instance = Instance("random-operators", 4, 20, tuple(customers))
    customer_ids = list(range(1, 13))
    rng.shuffle(customer_ids)
    routes = (tuple(customer_ids[:4]), tuple(customer_ids[4:8]), tuple(customer_ids[8:]))
    manager = PenaltyManager(instance)

    for operator in OPERATORS:
        best = None
        for move in enumerate_moves(routes, (operator,)):
            candidate = apply_move(routes, move)
            if any(not route for route in candidate):
                continue
            affected = {move.route_a, move.route_b}
            before = sum(manager.cost(evaluate_soft_route(instance, routes[index]))
                         for index in affected)
            after = sum(manager.cost(evaluate_soft_route(instance, candidate[index]))
                        for index in affected)
            if after < before and (best is None or after - before < best[0]):
                best = (after - before, move, candidate)
        assert best is not None, f"test route set has no improving {operator} move"
        _, chosen_move, expected_routes = best

        monkeypatch.setattr(
            search_module,
            "enumerate_moves",
            lambda current, operators, selected=chosen_move: iter((selected,)),
        )
        current, evaluation, feasible_best = improve_soft(
            instance, routes, manager, operators=(operator,), max_moves=1,
            deadline=None, strategy="first", operator_schedule="fixed",
        )
        assert current == expected_routes
        assert all(current)
        assert sorted(customer for route in current for customer in route) == list(range(1, 13))
        assert evaluation == evaluate_soft_solution(instance, current)
        assert feasible_best is not None
        assert feasible_best[1] == evaluation


@pytest.mark.parametrize("changes", [
    {"infeasible_search": 1},
    {"adaptive_penalties": 0},
    {"penalty_update_interval": True},
    {"penalty_update_interval": 0},
    {"penalty_target_feasible": True},
    {"penalty_target_feasible": float("nan")},
    {"penalty_target_feasible": 1.01},
    {"penalty_max_moves": True},
    {"penalty_max_moves": -1},
    {"penalty_max_moves": 1.5},
])
def test_config_rejects_invalid_m11_switches_and_penalty_parameters(changes):
    with pytest.raises(ValueError):
        Config(**changes)


def test_cli_writes_m11_config_history_and_penalty_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(report_module, "_draw_routes", lambda *args, **kwargs: None)
    monkeypatch.setattr(report_module, "_draw_convergence", lambda *args, **kwargs: None)
    out = tmp_path / "m11-run"
    code = cli_module.main([
        "solve", str(ROOT / "data" / "C101.txt"), "--seed", "5", "--out", str(out),
        "--max-iterations", "1", "--fleet-attempts", "0", "--max-moves", "0",
        "--infeasible-search", "--fixed-penalties", "--penalty-update-interval", "1",
        "--penalty-target-feasible", "0.5",
    ])

    assert code == 0
    payload = json.loads((out / "solution.json").read_text(encoding="utf-8"))
    assert payload["config"]["infeasible_search"] is True
    assert payload["config"]["adaptive_penalties"] is False
    assert payload["config"]["penalty_update_interval"] == 1
    summary = payload["penalty_search"]
    assert summary["schema_version"] == 1
    assert summary["weight_scale"] == 1000
    assert summary["adaptive"] is False
    assert summary["infeasible_candidates"] >= 0

    with (out / "history.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = set(reader.fieldnames)
        rows = list(reader)
    assert {"current_feasible", "current_excess_load", "current_time_warp",
            "candidate_excess_load", "candidate_time_warp", "load_penalty",
            "time_penalty", "load_feasible_rate", "time_feasible_rate",
            "feasible_rate", "penalty_updated", "penalized_cost"} <= columns
    assert rows
    assert len(rows) == 2  # M11 initial state plus one fixed iteration.
    # The summary's sample count is the number of registered candidates, excluding
    # the initial row, so it stays aligned with PenaltyManager.samples.
    assert summary["samples"] == 1


@pytest.mark.parametrize("explicit, epoch_cap, expected", [
    (None, 8, 8), (3, 8, 3), (0, 8, 0), (None, None, None),
])
def test_soft_epoch_budget_and_explicit_override(monkeypatch, small_instance, explicit, epoch_cap, expected):
    module = importlib.import_module("vrptw.solve")
    observed = []

    def stopped(instance, start, **kwargs):
        observed.append(kwargs["max_moves"])
        return ILSResult(start, (), 0, "max_iterations")

    monkeypatch.setattr(module, "run_penalized_ils", stopped)
    result = solve(small_instance, Config(
        max_iterations=0, fleet_attempts_per_k=0, infeasible_search=True,
        max_moves=explicit, penalty_max_moves=epoch_cap))
    assert observed == [expected]
    assert validate_solution(small_instance, result.routes).feasible
