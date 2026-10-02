"""M6 protocol guards: time budgets and frozen fleet caps cannot drift."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "benchmark_compare", Path(__file__).resolve().parents[1] / "benchmarks" / "pyvrp" / "compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_budget_rejects_mismatch_and_supports_m5_sidecar(tmp_path):
    path = tmp_path / "batch_summary.csv"
    rows = [{"time_limit_seconds": "5"}]
    with pytest.raises(ValueError, match="different time budget"):
        compare._check_ours_budget(path, rows, 0.5)
    with pytest.raises(ValueError, match="no recorded time budget"):
        compare._check_ours_budget(path, [{}], 0.5)
    path.with_suffix(".json").write_text(json.dumps({"time_limit_seconds": 0.5}), encoding="utf-8")
    compare._check_ours_budget(path, [{}], 0.5)


def test_worse_fleet_uses_original_cap_and_smaller_fleet_needs_new_baseline(tmp_path):
    ours, baseline, caps, out = [tmp_path / name for name in ("ours.csv", "baseline.csv", "caps.json", "vs.csv")]
    own_row = dict(instance="C103", seed="0", status="ok", feasible="True", vehicles="11",
                   distance_ticks="900000", input_sha256="same", numeric_rule="solomon_exact_1000_v1",
                   time_limit_seconds="0.5")
    write_csv(ours, [own_row])
    write_csv(baseline, [dict(instance="C103", seed="0", status="ok", own_validator_feasible="True",
                              vehicles="10", distance_ticks="828065", input_sha256="same",
                              budget_seconds="0.5", vehicle_cap="10")])
    caps.write_text(json.dumps({"C103": {"vehicle_cap": 10}}), encoding="utf-8")
    argv = ["--ours", str(ours), "--pyvrp", str(baseline), "--caps", str(caps),
            "--require-complete", "--out", str(out)]
    assert compare.main(argv) == 0
    with out.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["vehicle_cap"] == "10" and row["vehicle_gap"] == "1"
    assert row["distance_gap"] == row["distance_gap_percent"] == ""
    own_row["vehicles"] = "9"
    write_csv(ours, [own_row])
    with pytest.raises(ValueError, match="missing PyVRP baseline"):
        compare.main(argv)
