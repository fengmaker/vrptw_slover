"""M5 artifacts, independent validation, and per-instance batch errors."""

import csv
import json
from pathlib import Path

from vrptw import Config, read_solomon, solve
from vrptw.cli import main
from vrptw.report import validate_json, write_run


C101 = Path(__file__).resolve().parents[1] / "data" / "C101.txt"


def test_report_contains_all_outputs_and_validator_ignores_claims(tmp_path):
    instance = read_solomon(C101)
    config = Config(seed=0, max_iterations=0)
    result = solve(instance, config)
    out = write_run(instance, result, config, tmp_path / "run")
    for name in ("solution.json", "routes.sol", "history.csv",
                 "routes.png", "convergence.png"):
        assert (out / name).stat().st_size > 0
    payload = json.loads((out / "solution.json").read_text(encoding="utf-8"))
    assert (payload["vehicles"], payload["distance_ticks"], payload["numeric_rule"]) == (
        10, 828_937, "solomon_exact_1000_v1"
    )
    assert payload["input_format"] == "solomon_txt" and payload["threads"] == 1
    assert 0 <= payload["first_feasible_seconds"] <= payload["runtime_seconds"]
    payload.update(feasible=False, vehicles=25, distance_ticks=-1, distance=-1)
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_json(instance, tampered).objective == (10, 828_937)
    payload["routes"][0].pop()
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    verdict = validate_json(instance, tampered)
    assert not verdict.feasible
    assert any(issue.code == "missing_customer" for issue in verdict.violations)
    assert main(["validate", str(C101), str(tampered)]) == 1


def test_batch_continues_after_bad_instance_and_writes_all_statuses(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "A01.txt").write_text(
        "A01\nVEHICLE\nNUMBER CAPACITY\n2 2\nCUSTOMER\n"
        "CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME\n"
        "0 0 0 0 0 100 0\n1 1 0 1 0 100 0\n2 2 0 1 0 100 0\n",
        encoding="utf-8",
    )
    (data / "B01.txt").write_text("broken\n", encoding="utf-8")
    out = tmp_path / "batch"
    status = main(["batch", str(data), "--seeds", "0", "1", "2",
                   "--max-iterations", "0", "--fleet-attempts", "0",
                   "--out", str(out)])
    assert status == 1
    with (out / "batch_summary.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["instance"] for row in rows] == ["A01"] * 3 + ["B01"] * 3
    assert [row["status"] for row in rows] == ["ok"] * 3 + ["error"] * 3
    assert all(row["reference_status"] == "missing" for row in rows[:3])
    summary = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    assert (summary["instances"], summary["runs"], summary["successful"],
            summary["failed"]) == (2, 6, 3, 3)
