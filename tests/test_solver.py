"""M2/M3 entry point and C101 end-to-end acceptance."""

from pathlib import Path

from vrptw import Config, read_solomon, solve, validate_solution


DATA = Path(__file__).resolve().parents[1] / "data"


def test_c101_solver_reaches_capacity_lower_bound():
    instance = read_solomon(DATA / "C101.txt")
    result = solve(instance, Config(seed=0, max_iterations=0))
    independently_checked = validate_solution(instance, result.routes)
    assert independently_checked.feasible
    assert independently_checked.vehicles == 10
    assert independently_checked.distance == 828_937
    assert sum(len(route) for route in result.routes) == 100
    assert independently_checked.distance == result.evaluation.distance <= result.initial_distance
    assert result.fleet.lower_bound == 10
    assert result.fleet.stop_reason == "lower_bound"
    assert result.stop_reason == "max_iterations"
    assert [row.best_vehicles for row in result.history] == [12, 11, 10]


def test_zero_move_budget_returns_immutable_constructed_result():
    instance = read_solomon(DATA / "C101.txt")
    result = solve(instance, Config(max_moves=0, max_iterations=0,
                                    fleet_attempts_per_k=0))
    assert result.stop_reason == "max_iterations"
    assert result.iterations == 0
    assert result.routes == tuple(tuple(route) for route in result.routes)
    assert result.evaluation.distance == result.initial_distance
