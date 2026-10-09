"""The comparison must score actual routes with the frozen Solomon contract."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
from four_solver.common import fleet_cost
from four_solver.run import judge
from vrptw import Customer, Instance, validate_solution


def small_instance():
    return Instance("comparison", 2, 2, (
        Customer(0, 0, 0, 0, 1000, 30000, 0),
        Customer(1, 1, 0, 1, 3000, 5000, 2000),
        Customer(2, 2, 0, 1, 6000, 7000, 1000),
    ))


def test_judge_recomputes_quality_and_ignores_backend_claims():
    instance = small_instance()
    result = judge(instance, {"routes": [[1, 2]], "status": "found",
                              "vehicles": 999, "distance_ticks": 1, "feasible": False})
    assert result["feasible"]
    assert result["vehicles"] == 1
    assert result["distance_ticks"] == 4000
    invalid = judge(instance, {"routes": [[2, 1]], "status": "found", "feasible": True})
    assert invalid["status"] == "invalid_solution"
    assert not invalid["feasible"] and invalid["distance_ticks"] == ""


def test_scalar_cost_agrees_with_independent_lexicographic_cost():
    instance = small_instance()
    cost = fleet_cost(instance)
    good = {"routes": [[1, 2]], "status": "found", "metadata": {
        "scalar_objective": cost + 4000}}
    assert judge(instance, good)["feasible"]
    good["metadata"]["scalar_objective"] += 1
    with pytest.raises(ValueError, match="disagrees"):
        judge(instance, good)
    # Even the largest possible distance gain cannot compensate for one vehicle.
    assert cost > (instance.customer_count + min(instance.customer_count,
                                                instance.vehicle_count)) * max(map(max, instance.distance))


def test_absent_routes_never_become_infeasibility_proof():
    result = judge(small_instance(), {"routes": None, "status": "time_limit"})
    assert result["status"] == "time_limit" and not result["feasible"]
    assert result["vehicles"] == result["distance_ticks"] == ""
