"""M10 runner coverage, config, and artifact-integrity checks."""

import argparse
import json
from pathlib import Path

import pytest

from benchmarks import m10_experiment as m10


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


def test_variant_order_selection_and_configs():
    selected = ("baseline", "reconstruct", "route_removal", "related", "hybrid")
    assert m10.VARIANTS == ("baseline", "route_removal", "related", "hybrid")
    assert m10.OPTIONAL_VARIANTS == (
        "reconstruct", "cached_reconstruct", "related_rounds1", "hybrid_half", "hybrid_75",
    )
    assert m10._rotated_variants(0, selected) == list(selected)
    assert m10._rotated_variants(1, selected) == [
        "reconstruct", "route_removal", "related", "hybrid", "baseline",
    ]
    assert m10._selected_variants(["baseline", "related_rounds1", "hybrid_half"]) == (
        "baseline", "related_rounds1", "hybrid_half",
    )
    with pytest.raises(ValueError, match="include baseline"):
        m10._selected_variants(["related"])

    import vrptw

    reconstruct = m10._config_for(vrptw, "reconstruct", 4, 0.5)
    route_removal = m10._config_for(vrptw, "route_removal", 4, 0.5)
    related = m10._config_for(vrptw, "related", 4, 0.5)
    hybrid = m10._config_for(vrptw, "hybrid", 4, 0.5)
    related_short = m10._config_for(vrptw, "related_rounds1", 4, 0.5)
    hybrid_half = m10._config_for(vrptw, "hybrid_half", 4, 0.5)
    hybrid_75 = m10._config_for(vrptw, "hybrid_75", 4, 0.5)
    assert reconstruct.fleet_strategy == "reconstruct"
    assert reconstruct.max_moves is None
    assert reconstruct.operator_schedule == "cyclic"
    assert tuple(reconstruct.operators) == m10.m9.BASE_OPERATORS
    assert route_removal.fleet_strategy == "route_removal"
    assert related.fleet_strategy == "related"
    assert hybrid.fleet_strategy == "hybrid"
    assert related.fleet_repair_rounds == 5
    assert related_short.fleet_repair_rounds == 1
    assert hybrid_half.fleet_time_fraction == 0.5
    assert hybrid_75.fleet_time_fraction == 0.75
    assert route_removal.fleet_related_count == 8


def test_tiny_run_analysis_and_artifact_corruption(tmp_path):
    data_dir = tmp_path / "data"
    _tiny_instance(data_dir)
    out = tmp_path / "m10-smoke"
    variants = ["baseline", "reconstruct", "route_removal"]
    args = argparse.Namespace(
        data=data_dir, out=out, time_limit=0.05, seeds=[0], instances=None,
        variants=variants, diagnostics=False,
    )

    assert m10.run_experiment(args) == 0
    manifest = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["independently_verified"] == len(variants)
    assert manifest["order"]["cases"][0]["variants"] == variants
    baseline_config = manifest["variants"]["baseline"]["config"]
    assert baseline_config["max_moves"] is None
    assert baseline_config["operator_schedule"] == "cyclic"
    for variant in variants:
        artifact_dir = out / variant / "C_TINY" / "seed-0"
        assert (artifact_dir / "solution.json").is_file()
        assert (artifact_dir / "routes.sol").is_file()
        assert (artifact_dir / "history.csv").is_file()
        assert not (artifact_dir / "routes.png").exists()
        assert (out / variant / "batch_summary.csv").is_file()
    assert (out / "fleet_targets.csv").is_file()

    assert m10.analyse_experiment(argparse.Namespace(out=out, data=data_dir)) == 0
    assert (out / "runs.csv").is_file()
    assert (out / "best_by_instance.csv").is_file()
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics["variants"]) == set(variants)
    assert set(metrics["variants"]["route_removal"]["comparison_by_family"]) == {
        "all", "C", "R", "RC",
    }

    manifest["status"] = "completed"
    (out / "experiment.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    damaged = out / "route_removal" / "C_TINY" / "seed-0" / "routes.sol"
    damaged.write_text(damaged.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="routes.sol hash differs"):
        m10._validate_analysis_input(out, data_dir, manifest)
