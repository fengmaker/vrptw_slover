"""M9 experiment runner smoke and artifact contract checks."""

import argparse
import json
from pathlib import Path

import pytest

from benchmarks import m9_experiment as m9


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


def test_rotating_variants_and_selection_contract():
    selected = ("baseline", "uncapped", "relocate_pair", "combined")
    assert m9.VARIANTS == (
        "baseline", "uncapped", "relocate_pair", "exchange_pair_single",
        "exchange_pairs", "combined",
    )
    assert m9.ALL_VARIANTS == (*m9.VARIANTS, "cyclic", "cyclic_single", "cyclic_pairs", "cyclic_combined")
    assert m9._rotated_variants(0, selected) == list(selected)
    assert m9._rotated_variants(1, selected) == ["uncapped", "relocate_pair", "combined", "baseline"]
    assert m9._selected_variants(["baseline", "uncapped", "exchange_pairs"]) == (
        "baseline", "uncapped", "exchange_pairs",
    )
    assert m9._selected_variants(["baseline", "uncapped", "cyclic_pairs"]) == (
        "baseline", "uncapped", "cyclic_pairs",
    )
    assert m9.VARIANT_OPERATORS["cyclic"] == m9.BASE_OPERATORS
    assert m9.VARIANT_OPERATORS["cyclic_pairs"] == ("exchange_pairs", *m9.BASE_OPERATORS)
    assert m9.VARIANT_OPERATORS["cyclic_combined"] == (
        *m9.ADDED_OPERATORS, *m9.BASE_OPERATORS,
    )
    with pytest.raises(ValueError, match="include baseline"):
        m9._selected_variants(["uncapped"])
    with pytest.raises(ValueError, match="include uncapped"):
        m9._selected_variants(["baseline", "relocate_pair"])


def test_short_run_analyse_and_manifest_corruption(tmp_path):
    data_dir = tmp_path / "data"
    _tiny_instance(data_dir)
    out = tmp_path / "m9-smoke"
    args = argparse.Namespace(
        data=data_dir,
        out=out,
        time_limit=0.02,
        seeds=[0],
        instances=None,
        variants=["baseline", "uncapped", "cyclic", "cyclic_pairs", "cyclic_combined"],
        diagnostics=True,
    )

    assert m9.run_experiment(args) == 0
    manifest = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["independently_verified"] == 5
    assert manifest["order"]["cases"][0]["variants"] == args.variants
    for variant in args.variants:
        artifact_dir = out / variant / "C_TINY" / "seed-0"
        assert (artifact_dir / "solution.json").is_file()
        assert (artifact_dir / "routes.sol").is_file()
        assert (artifact_dir / "history.csv").is_file()
        assert (artifact_dir / "diagnostics.csv").is_file()
        assert (out / variant / "batch_diagnostics.csv").is_file()
        assert not (artifact_dir / "routes.png").exists()
    assert (out / "batch_diagnostics.csv").is_file()

    analyse_args = argparse.Namespace(out=out, data=data_dir)
    assert m9.analyse_experiment(analyse_args) == 0
    assert (out / "runs.csv").is_file()
    assert (out / "best_by_instance.csv").is_file()
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics["variants"]) == set(args.variants)
    assert metrics["variants"]["cyclic_pairs"]["config"]["operator_schedule"] == "cyclic"
    assert "cyclic" in metrics["variants"]["cyclic_pairs"]["comparison_vs_reference"]
    assert "comparison_vs_reference" in metrics["variants"]["uncapped"]

    manifest["variants"]["baseline"]["config_sha256"] = "0" * 64
    (out / "experiment.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="config hash differs"):
        m9._validate_analysis_input(out, data_dir, manifest)
