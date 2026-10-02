"""M7 experiment protocol and analysis guards."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from vrptw import read_solomon, validate_solution


MODULE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "m7_experiment.py"
spec = importlib.util.spec_from_file_location("m7_experiment", MODULE_PATH)
m7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m7)


def _write_csv(path, rows, fields=None):
    fields = fields or tuple(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _input_text():
    return (
        "C101\nVEHICLE\nNUMBER CAPACITY\n1 3\nCUSTOMER\n"
        "CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME\n"
        "0 0 0 0 0 100 0\n"
        "1 1 0 1 0 100 0\n"
        "2 2 0 1 0 100 0\n"
        "3 1 1 1 0 100 0\n"
    )


def _make_batch(experiment, data, variant, route, seed=0):
    batch = experiment / variant
    batch.mkdir(parents=True)
    source = data / "C101.txt"
    instance = read_solomon(source)
    verdict = validate_solution(instance, [route])
    assert verdict.feasible
    input_hash = m7._sha256(source)
    config = m7._config(0, 0.5, m7.VARIANTS.get(variant, {}))
    config_dict = m7.asdict(config)
    config_hash = m7._config_hash(config)
    code_hash = "solver-source-hash"
    row = {
        "instance": "C101", "seed": str(seed), "status": "ok", "feasible": "True",
        "vehicles": str(verdict.vehicles), "distance_ticks": str(verdict.distance),
        "input_sha256": input_hash, "numeric_rule": m7.NUMERIC_RULE_ID,
        "runtime_seconds": "0.25", "first_feasible_seconds": "0.01", "iterations": "4",
        "code_sha256": code_hash, "time_limit_seconds": "0.5",
    }
    _write_csv(batch / "batch_summary.csv", [row])
    payload = {
        "routes": [route], "vehicles": verdict.vehicles,
        "distance_ticks": verdict.distance, "input_sha256": input_hash,
        "numeric_rule": m7.NUMERIC_RULE_ID, "seed": seed,
        "code_sha256": code_hash, "config": config_dict,
    }
    artifact = batch / "C101" / f"seed-{seed}"
    artifact.mkdir(parents=True)
    (artifact / "solution.json").write_text(json.dumps(payload), encoding="utf-8")
    metadata = {
        "runs": 1, "numeric_rule": m7.NUMERIC_RULE_ID, "code_sha256": code_hash,
        "variant": variant, "config": config_dict, "config_sha256": config_hash,
    }
    (batch / "batch_summary.json").write_text(json.dumps(metadata), encoding="utf-8")
    return row


def test_frozen_m7_variants_do_not_follow_config_defaults():
    config = m7._config(2, 0.5)
    assert (config.fleet_attempts_per_k, config.max_moves, config.remove_min,
            config.remove_max, config.restart_after, config.construction_order,
            config.repair_order, config.repair_strategy, config.fleet_time_fraction) == (
        100, 2, 3, 8, 20, "due", "input", "cheapest", 1.0
    )
    assert m7.VARIANTS["attempts10"] == {"fleet_attempts_per_k": 10}
    assert m7.VARIANTS["reserve20"] == {"fleet_time_fraction": 0.8}
    assert m7.VARIANTS["regret2"] == {"repair_strategy": "regret2"}
    assert m7._config(0, 0.5, {"repair_strategy": "regret2"}).repair_strategy == "regret2"


def test_analysis_checks_coverage_and_reports_nearest_rank_and_m6(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    source = data / "C101.txt"
    source.write_text(_input_text(), encoding="utf-8")
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    base_row = _make_batch(experiment, data, "baseline", [1, 3, 2])
    improved_row = _make_batch(experiment, data, "moves8", [1, 2, 3])
    manifest = {
        "status": "completed", "instances": ["C101"], "seeds": [0],
        "time_limit_seconds": 0.5, "input_sha256": {"C101": m7._sha256(source)},
        "variants": {
            "baseline": {"status": "completed", "config_sha256": json.loads(
                (experiment / "baseline" / "batch_summary.json").read_text())["config_sha256"]},
            "moves8": {"status": "completed", "config_sha256": json.loads(
                (experiment / "moves8" / "batch_summary.json").read_text())["config_sha256"]},
        },
    }
    (experiment / "experiment.json").write_text(json.dumps(manifest), encoding="utf-8")
    m6_csv = tmp_path / "m6.csv"
    _write_csv(m6_csv, [{
        "instance": "C101", "seed": "0", "status": "ok", "feasible": "True",
        "vehicles": base_row["vehicles"], "distance_ticks": base_row["distance_ticks"],
        "input_sha256": base_row["input_sha256"], "numeric_rule": m7.NUMERIC_RULE_ID,
        "time_limit_seconds": "0.5",
    }])
    out = tmp_path / "analysis"
    args = type("Args", (), {
        "experiment": experiment, "data": data, "m6_csv": m6_csv,
        "out": out, "validate_artifacts": True,
    })()
    assert m7.analyse_experiment(args) == 0
    summary = json.loads((out / "m7_summary.json").read_text(encoding="utf-8"))
    comparison = summary["variants"]["moves8"]["comparison_vs_baseline"]
    assert comparison["vehicle_improved"] == 0
    assert comparison["same_count_distance_better"] == 1
    assert comparison["same_vehicle_distance_p90_percent_nearest_rank"] < 0
    assert summary["variants"]["moves8"]["comparison_vs_m6"]["same_count_distance_better"] == 1


def test_analysis_rejects_missing_instance_seed_coverage(tmp_path):
    batch = tmp_path / "baseline"
    batch.mkdir()
    (batch / "batch_summary.json").write_text(json.dumps({
        "runs": 1, "numeric_rule": m7.NUMERIC_RULE_ID,
    }), encoding="utf-8")
    _write_csv(batch / "batch_summary.csv", [{"instance": "C101", "seed": "0"}])
    with pytest.raises(ValueError, match="coverage differs"):
        m7._check_batch_coverage(batch, {("C101", "0"), ("C102", "0")}, tmp_path,
                                 "baseline", validate_artifacts=False)
