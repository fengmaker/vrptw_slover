"""M6: counters have checkable units and diagnostics preserve fixed work."""

from dataclasses import replace
from pathlib import Path

import pytest

from vrptw import Config, Customer, Instance, Move, apply_move, improve, read_solomon, solve
from vrptw.diagnostics import DiagnosticCollector, PHASES
from vrptw.fleet import insert_fixed
from vrptw.local_search import enumerate_moves, move_delta


def test_nested_phase_times_are_exclusive_and_cover_runtime(monkeypatch):
    clock = iter((0.0, 1.0, 3.0, 7.0, 10.0))
    monkeypatch.setattr("vrptw.diagnostics.monotonic", lambda: next(clock))
    collector = DiagnosticCollector()
    with collector.phase("fleet_reduction"):
        with collector.phase("repair"):
            collector.increment("accepted")
    phases = {row.phase: row for row in collector.snapshot(12.0).phases}
    assert phases["fleet_reduction"].elapsed_seconds == 5.0
    assert phases["fleet_reduction"].inclusive_seconds == 9.0
    assert phases["repair"].elapsed_seconds == 4.0
    assert phases["repair"].accepted == 1
    assert phases["other"].elapsed_seconds == 3.0
    assert sum(row.elapsed_seconds for row in phases.values()) == 12.0


def test_repair_capacity_skips_are_separate_from_evaluated_positions():
    instance = Instance("capacity", 1, 1, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        Customer(1, 1, 0, 1, 0, 100_000, 0),
        Customer(2, 2, 0, 1, 0, 100_000, 0),
    ))
    collector = DiagnosticCollector()
    assert insert_fixed(instance, ((1,),), [2], diagnostics=collector) is None
    phase = next(row for row in collector.snapshot(1.0).phases if row.phase == "repair")
    assert (phase.calls, phase.failures, phase.capacity_prefilter_skips) == (1, 1, 1)
    assert phase.candidates == phase.rejected_capacity == 0
    assert phase.route_evaluations == 1


def test_move_counts_match_enumeration_and_route_recomputations():
    instance = Instance("moves", 2, 2, tuple(
        Customer(i, i, 0, 0 if i == 0 else 1, 0, 100_000, 0) for i in range(5)
    ))
    routes = ((1, 2), (3, 4))
    moves = list(enumerate_moves(routes))
    collector = DiagnosticCollector()
    with collector.phase("local_search"):
        expected_feasible = sum(move_delta(instance, routes, move, diagnostics=collector) is not None
                                for move in moves)
        # Empty-route structural rejection has no route recomputation.
        assert move_delta(instance, ((1,), (2, 3, 4)), Move("relocate", 0, 0, 1, 0),
                          diagnostics=collector) is None
    phase = next(row for row in collector.snapshot(1.0).phases if row.phase == "local_search")
    assert phase.candidates == len(moves) + 1
    assert phase.feasible_candidates == expected_feasible
    assert phase.infeasible_candidates == phase.candidates - expected_feasible
    empty_moves = sum(not all(routes_after) for routes_after in (
        apply_move(routes, move) for move in moves))
    expected_evaluations = sum(2 * len({move.route_a, move.route_b})
                               for move in moves
                               if all(apply_move(routes, move)))
    assert phase.route_evaluations == expected_evaluations
    assert phase.rejected_capacity > 0
    assert phase.rejected_empty_route == empty_moves + 1


def test_time_window_rejections_count_each_candidate_once():
    instance = Instance("tight", 1, 10, (
        Customer(0, 0, 0, 0, 0, 5_000, 0),
        Customer(1, 1, 0, 1, 0, 1_000, 0),
        Customer(2, 2, 0, 1, 0, 5_000, 0),
    ))
    collector = DiagnosticCollector()
    with collector.phase("local_search"):
        assert move_delta(instance, ((1, 2),), Move("two_opt", 0, 0, 0, 2),
                          diagnostics=collector) is None
    phase = next(row for row in collector.snapshot(1.0).phases if row.phase == "local_search")
    assert (phase.candidates, phase.infeasible_candidates, phase.rejected_time_window) == (1, 1, 1)


def test_diagnostics_preserve_fixed_work_and_skipped_phases():
    instance = read_solomon(Path(__file__).resolve().parents[1] / "data" / "C103.txt")
    config = Config(seed=2, max_iterations=2, fleet_attempts_per_k=2, max_moves=1)
    plain = solve(instance, config)
    profiled = solve(instance, replace(config, diagnostics=True))
    assert plain.diagnostics is None
    assert plain.routes == profiled.routes
    assert plain.evaluation == profiled.evaluation
    assert plain.fleet.attempts == profiled.fleet.attempts
    assert [(row.event, row.best_distance) for row in plain.history] == [
        (row.event, row.best_distance) for row in profiled.history]
    phases = {row.phase: row for row in profiled.diagnostics.phases}
    assert tuple(phases) == PHASES
    assert phases["construction"].accepted == instance.customer_count
    assert phases["fleet_reduction"].trials == sum(count for _, count in profiled.fleet.attempts)
    assert phases["perturbation"].calls == profiled.iterations == 2
    assert phases["local_search"].accepted <= config.max_moves * profiled.iterations
    assert sum(row.elapsed_seconds for row in phases.values()) == pytest.approx(profiled.runtime_seconds)
    assert all(row.candidates == row.feasible_candidates + row.infeasible_candidates
               for row in phases.values())
    stopped = solve(instance, Config(max_iterations=None, time_limit_seconds=0, diagnostics=True))
    stopped_phases = {row.phase: row for row in stopped.diagnostics.phases}
    assert stopped.iterations == 0
    assert stopped_phases["repair"].calls == stopped_phases["local_search"].candidates == 0


def test_accepted_moves_count_matches_search_result():
    instance = Instance("square", 2, 5, tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 100_000, 0)
        for i, (x, y) in enumerate(((0, 0), (1, 0), (2, 0), (0, 1), (0, 2)))
    ))
    collector = DiagnosticCollector()
    result = improve(instance, ((1, 4), (3, 2)), diagnostics=collector)
    phase = next(row for row in collector.snapshot(1.0).phases if row.phase == "local_search")
    assert phase.accepted == len(result.moves) > 0


def test_diagnostics_config_rejects_non_boolean():
    with pytest.raises(ValueError, match="diagnostics must"):
        Config(diagnostics="yes")
