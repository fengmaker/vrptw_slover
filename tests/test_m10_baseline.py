"""Focused checks for the explicit PyVRP fleet-minimisation baseline."""

import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import m10_fleet_baseline as m10
from vrptw import read_solomon, validate_solution


def _tiny_instance(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True)
    path = data_dir / "C_TINY.txt"
    path.write_text(
        "C_TINY\n\nVEHICLE\nNUMBER CAPACITY\n2 10\n\nCUSTOMER\n"
        "CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME\n"
        "0 0 0 0 0 1000 0\n"
        "1 1 0 1 0 1000 0\n"
        "2 0 1 1 0 1000 0\n",
        encoding="utf-8",
    )
    return path


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeVehicle:
    def __init__(self, num_available):
        self.num_available = num_available

    def replace(self, *, num_available):
        return FakeVehicle(num_available)


class FakeData:
    def __init__(self, vehicle):
        self.vehicle = vehicle

    def vehicle_type(self, index):
        assert index == 0
        return self.vehicle

    def replace(self, *, vehicle_types):
        assert len(vehicle_types) == 1
        return FakeData(vehicle_types[0])


class FakeResult:
    def __init__(self, feasible):
        self.feasible = feasible
        self.runtime = 0.09
        self.best = SimpleNamespace(
            routes=lambda: [SimpleNamespace(visits=lambda: [1, 2])] if feasible else []
        )

    def is_feasible(self):
        return self.feasible


class FakeAPI:
    version = "test"
    code_sha256 = "a" * 64
    package_path = "test/pyvrp"

    def __init__(self, clock, *, feasible=True, minimise_duration=0.2):
        self.clock = clock
        self.feasible = feasible
        self.minimise_duration = minimise_duration
        self.runtime_budgets = []
        self.minimise_budgets = []
        self.check_calls = 0
        self.final_caps = []

    def read(self, path, *, round_func):
        assert round_func == "exact"
        return FakeData(FakeVehicle(9))

    def check_same_problem(self, instance, data):
        self.check_calls += 1
        assert data.vehicle_type(0).num_available == 9

    def max_runtime(self, seconds):
        self.runtime_budgets.append(seconds)
        return SimpleNamespace(seconds=seconds)

    def minimise_fleet(self, data, stop, *, seed):
        assert data.vehicle_type(0).num_available == 1
        assert seed == 2
        self.minimise_budgets.append(stop.seconds)
        self.clock.now += self.minimise_duration
        return FakeVehicle(1)

    def solve(self, data, stop, *, seed, collect_stats, display):
        assert seed == 2
        assert collect_stats is False
        assert display is False
        self.final_caps.append(data.vehicle_type(0).num_available)
        self.clock.now += 0.1
        return FakeResult(self.feasible)


def test_initial_cap_is_deterministic_due_construction(tmp_path):
    source = _tiny_instance(tmp_path / "data")
    instance = read_solomon(source)
    caps = m10._read_caps(None, [instance.name], {instance.name: instance}, {instance.name})

    assert caps[instance.name]["initial_vehicle_cap"] == len(m10.construct(instance, order="due"))
    assert caps[instance.name]["source"] == "deterministic_construct_due"
    assert m10._parser().get_default("fleet_time_fraction") == 0.75


def test_minimisation_and_final_solve_share_one_budget_and_routes_are_verified(tmp_path):
    source = _tiny_instance(tmp_path / "data")
    instance = read_solomon(source)
    clock = FakeClock()
    api = FakeAPI(clock)
    out = tmp_path / "out"

    row, artifact = m10._run_case(
        instance=instance,
        source_path=source,
        converted_path=tmp_path / "C_TINY.vrp",
        cap=1,
        cap_source="deterministic_construct_due",
        seed=2,
        budget=0.5,
        out_dir=out,
        config_sha256="b" * 64,
        api=api,
        clock=clock,
    )

    assert api.runtime_budgets == pytest.approx([0.375, 0.3])
    assert api.minimise_budgets == pytest.approx([0.375])
    assert api.final_caps == [1]
    assert api.check_calls == 1
    assert row["fleet_minimise_wall_seconds"] == pytest.approx(0.2)
    assert row["solve_wall_seconds"] == pytest.approx(0.1)
    assert row["total_solve_wall_seconds"] == pytest.approx(0.3)
    assert row["fleet_time_fraction"] == pytest.approx(0.75)
    assert row["fleet_minimise_budget_seconds"] == "0.375"
    assert row["solve_budget_seconds"] == "0.3"
    assert row["status"] == "ok"
    assert row["own_validator_feasible"] is True
    assert artifact is not None
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved["config_sha256"] == "b" * 64
    assert saved["fleet_time_fraction"] == pytest.approx(0.75)
    assert saved["routes"] == [[1, 2]]
    assert validate_solution(instance, saved["routes"]).distance == saved["distance_ticks"]


def test_no_solution_is_recorded_as_not_found_without_route_artifact(tmp_path):
    source = _tiny_instance(tmp_path / "data")
    instance = read_solomon(source)
    clock = FakeClock()
    api = FakeAPI(clock, feasible=False)

    row, artifact = m10._run_case(
        instance=instance,
        source_path=source,
        converted_path=tmp_path / "C_TINY.vrp",
        cap=1,
        cap_source="deterministic_construct_due",
        seed=2,
        budget=0.5,
        out_dir=tmp_path / "out",
        config_sha256="c" * 64,
        api=api,
        clock=clock,
    )

    assert row["status"] == "not_found"
    assert row["vehicles"] == ""
    assert row["distance_ticks"] == ""
    assert artifact is None
    assert api.runtime_budgets == pytest.approx([0.375, 0.3])


def test_custom_fraction_and_minimisation_overrun_leave_zero_solve_budget(tmp_path):
    source = _tiny_instance(tmp_path / "data")
    instance = read_solomon(source)
    clock = FakeClock()
    api = FakeAPI(clock, minimise_duration=0.7)

    row, artifact = m10._run_case(
        instance=instance,
        source_path=source,
        converted_path=tmp_path / "C_TINY.vrp",
        cap=1,
        cap_source="deterministic_construct_due",
        seed=2,
        budget=0.5,
        out_dir=tmp_path / "out",
        config_sha256="d" * 64,
        api=api,
        fleet_time_fraction=0.4,
        clock=clock,
    )

    assert api.minimise_budgets == pytest.approx([0.2])
    assert api.runtime_budgets == [pytest.approx(0.2), 0.0]
    assert row["solve_budget_seconds"] == "0"
    assert row["total_solve_wall_seconds"] == pytest.approx(0.8)
    assert artifact is not None
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved["fleet_time_fraction"] == pytest.approx(0.4)
    assert saved["solve_budget_seconds"] == 0


def test_batch_writes_hashes_rows_and_routes_and_uses_existing_mapping_check(tmp_path):
    data_dir = tmp_path / "data"
    source = _tiny_instance(data_dir)
    out = tmp_path / "m10"
    clock = FakeClock()
    api = FakeAPI(clock)
    args = argparse.Namespace(
        data=data_dir,
        instances=["C_TINY"],
        seeds=[2],
        budget=0.5,
        caps=None,
        out_dir=out,
    )

    assert m10.run_experiment(args, api=api, clock=clock) == 0

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["pyvrp_package_path"] == api.package_path
    assert manifest["conversion_checked_with"] == "benchmarks/pyvrp/run.py:_check_same_problem"
    assert manifest["input_sha256"]["C_TINY"] == m10._sha256(source)
    assert manifest["config_sha256"] == json.loads(
        (out / "solutions" / "C_TINY" / "seed-2.json").read_text(encoding="utf-8")
    )["config_sha256"]
    with (out / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["config_sha256"] == manifest["config_sha256"]
    assert rows[0]["fleet_time_fraction"] == "0.75"
    assert api.check_calls == 1
    case = manifest["cases"][0]
    route_path = out / case["artifact"]
    assert case["artifact_sha256"] == m10._sha256(route_path)
    assert case["artifact"] == "solutions/C_TINY/seed-2.json"
    assert manifest["artifacts"]["runs_csv"]["sha256"] == m10._sha256(out / "runs.csv")
    assert manifest["runs_sha256"] == m10._sha256(out / "runs.csv")
    assert manifest["config"]["fleet_minimise_result_semantics"].startswith("vehicle_type_cap_only")
    assert manifest["counts"] == {
        "ok": 1,
        "not_found": 0,
        "errors": 0,
        "validation_mismatch": 0,
        "by_status": {"ok": 1},
    }
    with pytest.raises(FileExistsError, match="not empty"):
        m10.run_experiment(args, api=api, clock=clock)


def test_batch_rejects_nonfinite_budget_fraction_and_duplicate_or_unsorted_seeds(tmp_path):
    data_dir = tmp_path / "data"
    _tiny_instance(data_dir)
    out = tmp_path / "unused-out"
    args = argparse.Namespace(
        data=data_dir,
        instances=["C_TINY"],
        seeds=[2, 2],
        budget=0.5,
        fleet_time_fraction=0.75,
        caps=None,
        out_dir=out,
    )
    with pytest.raises(ValueError, match="unique, ascending"):
        m10.run_experiment(args, api=object())

    args.seeds = [1, 0]
    with pytest.raises(ValueError, match="unique, ascending"):
        m10.run_experiment(args, api=object())
    args.seeds = [-1, 2]
    with pytest.raises(ValueError, match="unique, ascending"):
        m10.run_experiment(args, api=object())

    args.seeds = [0, 1]
    args.budget = float("nan")
    with pytest.raises(ValueError, match="budget"):
        m10.run_experiment(args, api=object())

    args.budget = 0.5
    args.fleet_time_fraction = float("inf")
    with pytest.raises(ValueError, match="fleet_time_fraction"):
        m10.run_experiment(args, api=object())
