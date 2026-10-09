"""Coverage and integrity checks for the M11 penalty ablation runner."""

import argparse
import csv
import json
from pathlib import Path

import pytest

from benchmarks import m11_experiment as m11


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


def _smoke_experiment(tmp_path: Path, *, diagnostics: bool = False,
                      time_limit: float = 0.001):
    data = tmp_path / "data"
    source = _tiny_instance(data)
    out = tmp_path / "m11-smoke"
    args = argparse.Namespace(
        data=data, out=out, time_limit=time_limit, seeds=[0, 1],
        instances=["C_TINY"], variants=list(m11.VARIANTS), diagnostics=diagnostics,
    )
    assert m11.run_experiment(args) == 0
    manifest = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    return data, source, out, manifest


def test_variants_configs_and_vehicle_first_comparison():
    assert m11.VARIANTS == ("baseline", "fixed_penalties", "adaptive_penalties")
    assert m11.OPTIONAL_VARIANTS == ("adaptive_fast", "adaptive_high")
    selected = ("baseline", "fixed_penalties", "adaptive_penalties", "adaptive_fast")
    assert m11._rotated_variants(0, selected) == list(selected)
    assert m11._rotated_variants(1, selected) == [
        "fixed_penalties", "adaptive_penalties", "adaptive_fast", "baseline",
    ]
    assert m11._selected_variants(["baseline", "adaptive_high"]) == (
        "baseline", "adaptive_high",
    )
    with pytest.raises(ValueError, match="include baseline"):
        m11._selected_variants(["adaptive_penalties"])

    import vrptw

    baseline = m11._config_for(vrptw, "baseline", 9, 0.5)
    fixed = m11._config_for(vrptw, "fixed_penalties", 9, 0.5)
    adaptive = m11._config_for(vrptw, "adaptive_penalties", 9, 0.5)
    fast = m11._config_for(vrptw, "adaptive_fast", 9, 0.5)
    high = m11._config_for(vrptw, "adaptive_high", 9, 0.5)
    assert baseline.infeasible_search is False
    assert fixed.infeasible_search is True and fixed.adaptive_penalties is False
    assert adaptive.infeasible_search is True and adaptive.adaptive_penalties is True
    assert fast.penalty_update_interval == 2
    assert high.penalty_target_feasible == 0.8
    assert all(config.max_iterations is None and config.time_limit_seconds == 0.5
               for config in (baseline, fixed, adaptive, fast, high))

    # A vehicle-count difference has priority, so distance is deliberately blank.
    assert m11._compare((2, 1000), (1, 9000)) == (
        "fewer_vehicles", "not_compared", None, None,
    )


def test_tiny_run_analysis_and_same_budget_artifacts(tmp_path):
    data, _, out, manifest = _smoke_experiment(tmp_path)
    assert manifest["status"] == "completed"
    assert manifest["independently_verified"] == 2 * len(m11.VARIANTS)
    assert manifest["order"]["cases"][0]["variants"] == list(m11.VARIANTS)
    assert manifest["order"]["cases"][1]["variants"] == [
        "fixed_penalties", "adaptive_penalties", "baseline",
    ]
    for variant in m11.VARIANTS:
        assert manifest["variants"][variant]["config"]["max_iterations"] is None
        assert manifest["variants"][variant]["config"]["time_limit_seconds"] == 0.001
        batch = out / variant
        assert (batch / "batch_summary.csv").is_file()
        assert (batch / "batch_summary.json").is_file()
        with (batch / "batch_summary.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2
        assert {row["time_limit_seconds"] for row in rows} == {"0.001"}
        for seed in (0, 1):
            artifacts = batch / "C_TINY" / f"seed-{seed}"
            assert all((artifacts / name).is_file()
                       for name in ("solution.json", "routes.sol", "history.csv"))
            assert not (artifacts / "routes.png").exists()

    assert m11.analyse_experiment(argparse.Namespace(out=out, data=data)) == 0
    assert (out / "runs.csv").is_file()
    best_rows = list(csv.DictReader((out / "best_by_instance.csv").open(encoding="utf-8", newline="")))
    assert len(best_rows) == len(m11.VARIANTS)
    assert all(row["result_vs_baseline"] in {"tie", "better", "worse", "fewer_vehicles", "more_vehicles"}
               for row in best_rows)
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics["variants"]) == set(m11.VARIANTS)
    assert set(metrics["variants"]["adaptive_penalties"]["comparison_by_family"]) == {
        "all", "C", "R", "RC",
    }
    assert "trajectory_by_variant" in metrics


def test_optional_diagnostics_are_saved_hashed_and_validated(tmp_path):
    data, _, out, manifest = _smoke_experiment(tmp_path, diagnostics=True, time_limit=0.01)
    assert manifest["diagnostics_enabled"] is True
    for variant in m11.VARIANTS:
        batch = out / variant
        assert (batch / "batch_diagnostics.csv").is_file()
        for seed in (0, 1):
            run = batch / "C_TINY" / f"seed-{seed}"
            assert (run / "diagnostics.csv").is_file()
            result = manifest["order"]["cases"][seed]["results"][variant]
            assert "diagnostics.csv" in result["artifact_sha256"]
    assert m11.analyse_experiment(argparse.Namespace(out=out, data=data)) == 0


@pytest.mark.parametrize("tamper", ["source", "artifact", "input", "protocol"])
def test_analysis_rejects_tampered_source_artifact_input_and_protocol(tmp_path, tamper):
    data, source, out, manifest = _smoke_experiment(tmp_path)
    if tamper == "source":
        package = Path(manifest["sources"]["m11_candidate"]["package"])
        path = package / "solve.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        expected = "source fingerprint differs"
    elif tamper == "artifact":
        path = out / "fixed_penalties" / "C_TINY" / "seed-0" / "routes.sol"
        path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        expected = "routes.sol hash differs"
    elif tamper == "input":
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        expected = "input hash differs"
    else:
        manifest["variants"]["adaptive_penalties"]["config"]["adaptive_penalties"] = False
        (out / "experiment.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        expected = "config or config hash differs"

    with pytest.raises(ValueError, match=expected):
        m11.analyse_experiment(argparse.Namespace(out=out, data=data))


def test_failed_experiment_cannot_be_analysed(tmp_path):
    data, _, out, manifest = _smoke_experiment(tmp_path)
    manifest["status"] = "failed"
    (out / "experiment.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="only completed runs can be analysed"):
        m11.analyse_experiment(argparse.Namespace(out=out, data=data))
