"""M4 fleet lower bound, reproducibility, and stop conditions."""

from pathlib import Path

import pytest

from vrptw import Config, capacity_lower_bound, read_solomon, solve, validate_solution


C101 = Path(__file__).resolve().parents[1] / "data" / "C101.txt"


def test_fixed_iterations_and_seed_reproduce_routes_and_distance():
    instance = read_solomon(C101)
    config = Config(seed=2, max_iterations=3, max_moves=0,
                    fleet_attempts_per_k=30)
    first = solve(instance, config)
    second = solve(instance, config)
    assert first.routes == second.routes
    assert first.evaluation.objective == second.evaluation.objective
    assert first.fleet.attempts == second.fleet.attempts
    assert first.iterations == second.iterations == 3
    assert len(first.history) >= 4
    assert validate_solution(instance, first.routes).feasible


def test_zero_time_limit_returns_valid_initial_solution():
    instance = read_solomon(C101)
    result = solve(instance, Config(max_iterations=None, time_limit_seconds=0))
    assert result.stop_reason == "time_limit"
    assert result.iterations == 0
    assert validate_solution(instance, result.routes).feasible


def test_lower_bound_and_invalid_stop_config():
    instance = read_solomon(C101)
    assert capacity_lower_bound(instance) == 10
    with pytest.raises(ValueError, match="provide max_iterations"):
        Config(max_iterations=None, time_limit_seconds=None)
    with pytest.raises(ValueError, match="max_iterations"):
        Config(max_iterations=-1)
