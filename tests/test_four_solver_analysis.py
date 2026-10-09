"""Synthetic coverage for four-solver analysis edge cases."""

import csv
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "four_solver" / "analyse.py"
spec = importlib.util.spec_from_file_location("four_solver_analyse", SCRIPT)
analyse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyse)


def row(solver, instance, budget, seed, *, feasible=True, vehicles=None, ticks=None,
        prep=0.1, search=0.3, total=0.5, status="ok", metadata=""):
    return {
        "solver": solver,
        "instance": instance,
        "budget_seconds": str(budget),
        "seed": str(seed),
        "status": status,
        "feasible": str(feasible),
        "vehicles": "" if vehicles is None else str(vehicles),
        "distance_ticks": "" if ticks is None else str(ticks),
        "distance": "" if ticks is None else str(ticks / 1000),
        "preparation_seconds": str(prep),
        "search_seconds": str(search),
        "solver_runtime_seconds": str(search),
        "validation_seconds": "0.01",
        "total_seconds": str(total),
        "metadata_json": metadata,
    }


def synthetic_rows():
    rows = [
        # The lexicographic best is the 2-vehicle candidate, despite its longer distance.
        row("ours", "C101", 0.5, 0, vehicles=3, ticks=250_000, prep=.1, search=.3, total=.5),
        row("ours", "C101", 0.5, 1, vehicles=2, ticks=300_000, prep=.3, search=.7, total=1.1),
        row("ours", "C102", 0.5, 0, vehicles=3, ticks=500_000, prep=.2, search=.4, total=.7),
        row("pyvrp", "C101", 0.5, 0, vehicles=2, ticks=310_000),
        row("pyvrp", "C102", 0.5, 0, vehicles=2, ticks=450_000),
        # 5-second results keep K and improve distance by 10% and 20%.
        row("ours", "C101", 5, 0, vehicles=2, ticks=270_000, prep=.2, search=4.3, total=4.6),
        row("ours", "C102", 5, 0, vehicles=3, ticks=400_000, prep=.2, search=4.1, total=4.4),
        row("pyvrp", "C101", 5, 0, vehicles=2, ticks=280_000),
        row("pyvrp", "C102", 5, 0, vehicles=2, ticks=450_000),
        # One infeasible status remains visible and does not enter the result comparison.
        row("ortools", "C101", .5, 0, feasible=False, status="no_solution"),
        row("ortools", "C102", .5, 0, vehicles=3, ticks=490_000),
        row("gurobi", "C101", .5, 0, feasible=False, status="timeout_no_incumbent",
            metadata='{"best_bound": 4.0, "mip_gap": null}'),
    ]
    return rows


def test_best_lex_vehicle_first_common_subset_and_missing_coverage():
    result = analyse.analyze(synthetic_rows())
    ours = next(item for item in result["summary"] if item["solver"] == "ours" and item["budget_seconds"] == "0.5")
    pyvrp = next(item for item in result["summary"] if item["solver"] == "pyvrp" and item["budget_seconds"] == "0.5")
    ortools = next(item for item in result["summary"] if item["solver"] == "ortools" and item["budget_seconds"] == "0.5")

    selected = next(item for item in result["per_instance_wide"] if item["budget_seconds"] == "0.5" and item["instance"] == "C101")
    assert selected["ours_vehicles"] == 2
    assert selected["ours_distance_ticks"] == 300_000
    assert selected["ours_selected_seed"] == "1"
    assert ours["vehicle_sum"] == 5 and ours["vehicle_mean"] == 2.5
    assert ours["common_feasible_with_pyvrp_count"] == 2
    assert (ours["vehicle_better_vs_pyvrp"], ours["vehicle_equal_vs_pyvrp"], ours["vehicle_worse_vs_pyvrp"]) == (0, 1, 1)
    assert ours["same_vehicle_distance_gap_count"] == 1
    assert round(ours["same_vehicle_distance_gap_median_percent"], 3) == -3.226
    assert pyvrp["vehicle_sum"] == 4
    assert ortools["feasible_instance_count"] == 1
    assert ortools["vehicle_sum"] is None
    assert ortools["infeasible_run_count"] == 1


def test_percentile_time_medians_improvement_and_gurobi_bounds():
    assert analyse._percentile([1, 2, 3, 4, 5], .9) == 4.6
    result = analyse.analyze(synthetic_rows())
    ours_short = next(item for item in result["summary"] if item["solver"] == "ours" and item["budget_seconds"] == "0.5")
    assert ours_short["median_preparation_seconds"] == .2
    assert ours_short["median_search_seconds"] == .4
    assert ours_short["median_total_seconds"] == .7

    ours_improvements = next(item for item in result["improvement_summary"] if item["solver"] == "ours")
    assert ours_improvements["same_vehicles_shorter_distance_at_5s"] == 2
    assert ours_improvements["same_vehicle_distance_improvement_median_percent"] == 15.0
    assert round(ours_improvements["same_vehicle_distance_improvement_p90_percent"], 3) == 19.0

    assert result["gurobi_bounds"] == [{
        "solver": "gurobi", "instance": "C101", "budget_seconds": "0.5", "seed": "0",
        "status": "timeout_no_incumbent", "feasible": "False", "vehicles": "", "distance_ticks": "",
        "metadata_bounds_json": '{"best_bound": 4.0, "mip_gap": null}',
    }]


def test_same_vehicle_gap_is_blank_when_fleet_sizes_differ_and_report_mentions_dynamic_effort():
    result = analyse.analyze(synthetic_rows())
    c102 = next(item for item in result["per_instance_wide"] if item["budget_seconds"] == "0.5" and item["instance"] == "C102")
    assert c102["ours_vehicle_delta_vs_pyvrp"] == 1
    assert c102["ours_distance_gap_percent_vs_pyvrp"] is None
    report = analyse.render_report(result, run_count=12)
    assert "best feasible result across observed attempts" in report
    assert "Distance gaps compare only solutions with equal vehicle counts" in report


def test_repeated_unseeded_ortools_attempts_count_as_one_effective_seed():
    rows = [
        row("ortools", "C101", .5, 0, vehicles=3, ticks=100, metadata='{"cp_solver_reseed_applied": false}'),
        row("ortools", "C101", .5, 1, vehicles=2, ticks=110, metadata='{"cp_solver_reseed_applied": false}'),
    ]
    result = analyse.analyze(rows)
    summary = next(item for item in result["summary"] if item["solver"] == "ortools")
    assert summary["run_count"] == 2
    assert summary["effective_seed_count"] == 1
    assert summary["backend_seed_support"] == "unsupported"


def test_manifest_exposes_missing_instances_and_writes_expected_outputs(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runs = [row("ours", "C101", .5, 0, vehicles=2, ticks=100)]
    with (run_dir / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(runs[0]))
        writer.writeheader()
        writer.writerows(runs)
    (run_dir / "manifest.json").write_text(json.dumps({
        "protocol": {"instances": ["C101", "C102"], "budgets": [0.5, 5]},
    }), encoding="utf-8")

    out_dir = tmp_path / "tables"
    result = analyse.write_outputs(run_dir / "runs.csv", out_dir)
    assert result["instance_count"] == 2
    assert result["observed_instance_count"] == 1
    assert result["missing_instances_in_runs"] == ["C102"]
    ours_short = next(item for item in result["summary"] if item["solver"] == "ours" and item["budget_seconds"] == "0.5")
    assert ours_short["vehicle_sum"] is None
    assert len(result["status_counts"]) == 8
    assert {path.name for path in out_dir.iterdir()} == {
        "raw_runs.csv", "summary.csv", "group_summary.csv", "per_instance_wide.csv",
        "improvement_0p5_to_5.csv", "improvement_summary.csv", "paired_runs.csv",
        "paired_run_summary.csv", "status_counts.csv", "gurobi_bounds.csv",
        "tail_instances.csv", "summary.json", "report.md",
    }
    assert "missing_instances_in_runs" in json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert "checks against 2 expected instances" in (out_dir / "report.md").read_text(encoding="utf-8")
