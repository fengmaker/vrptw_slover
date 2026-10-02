"""Checks for M7 confirmation interleaving and paired report semantics."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "m7_confirm.py"
spec = importlib.util.spec_from_file_location("m7_confirm", MODULE_PATH)
confirm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(confirm)


def test_execution_plan_alternates_configuration_order_for_every_key(tmp_path):
    paths = [tmp_path / "C101.txt", tmp_path / "R101.txt"]
    plan = confirm._execution_plan(paths, [0, 1, 2])
    assert [(row["instance"], row["seed"]) for row in plan] == [
        ("C101", 0), ("C101", 1), ("C101", 2),
        ("R101", 0), ("R101", 1), ("R101", 2),
    ]
    assert [row["order"] for row in plan] == [
        ["baseline", "candidate"], ["candidate", "baseline"],
        ["baseline", "candidate"], ["candidate", "baseline"],
        ["baseline", "candidate"], ["candidate", "baseline"],
    ]


def _row(instance, seed, vehicles, distance, runtime="0.5"):
    return {
        "instance": instance, "seed": str(seed), "status": "ok", "feasible": True,
        "vehicles": str(vehicles), "distance_ticks": str(distance),
        "runtime_seconds": runtime, "iterations": "3",
    }


def test_paired_report_uses_lexicographic_best_per_instance():
    baseline = [_row("C101", 0, 10, 100), _row("C101", 1, 10, 90)]
    candidate = [_row("C101", 0, 9, 200), _row("C101", 1, 10, 80, runtime="0.6")]
    paired, summary = confirm._build_report(baseline, candidate)
    assert [(row["vehicle_result"], row["same_vehicle_distance_result"]) for row in paired] == [
        ("improved", "not_compared"), ("same", "better"),
    ]
    assert summary["per_seed_outcomes"]["vehicle_improved"] == 1
    assert summary["per_seed_outcomes"]["same_count_distance_better"] == 1
    best = summary["best_by_instance"][0]
    assert (best["candidate_vehicles"], best["candidate_distance_ticks"],
            best["vehicle_result"], best["same_vehicle_distance_result"]) == (
        9, 200, "improved", "not_compared"
    )
    assert abs(summary["runtime_seconds"]["paired_median_delta"] - 0.05) < 1e-9
