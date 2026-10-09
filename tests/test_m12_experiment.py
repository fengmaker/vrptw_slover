from __future__ import annotations

import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarks import m12_experiment as m12


def test_variant_order_rotates_for_paired_cases() -> None:
    assert m12._rotated_variants(0) == ["python", "native"]
    assert m12._rotated_variants(1) == ["native", "python"]
    assert m12._rotated_variants(2) == ["python", "native"]


def test_m12_config_only_changes_search_backend_and_run_controls() -> None:
    package = importlib.import_module("vrptw")
    python = m12._config_for(package, "python", 7, 0.25, diagnostics=True)
    native = m12._config_for(package, "native", 7, 0.25, diagnostics=True)

    assert python.search_backend == "python"
    assert native.search_backend == "native"
    for config in (python, native):
        assert config.infeasible_search is False
        assert config.evaluation_mode == "incremental"
        assert config.seed == 7
        assert config.time_limit_seconds == 0.25
        assert config.max_iterations is None
        assert config.diagnostics is True


def test_freeze_source_hash_covers_python_and_native_binary(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "solver_source"
    source.mkdir()
    (source / "__init__.py").write_bytes(b"# frozen package\n")
    (source / "core.py").write_bytes(b"VALUE = 12\n")
    (source / "_native.cpython-test.pyd").write_bytes(b"native-binary-bytes")
    monkeypatch.setattr(m12, "SRC_PACKAGE", source)

    frozen, fingerprint, file_hashes = m12._freeze_source(tmp_path / "run")

    assert set(file_hashes) == {"__init__.py", "core.py", "_native.cpython-test.pyd"}
    assert (frozen / "_native.cpython-test.pyd").read_bytes() == b"native-binary-bytes"
    copied = {path.name: path.read_bytes() for path in m12._snapshot_paths(frozen)}
    assert m12._code_hash(copied) == fingerprint


def test_analysis_rejects_incomplete_experiment_before_reading_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only completed"):
        m12._validate_analysis_input(tmp_path, tmp_path, {"status": "running"})


def _native_available() -> bool:
    return any(path.name.startswith("_native") and path.suffix in (".pyd", ".so")
               for path in m12._snapshot_paths(m12.SRC_PACKAGE))


def _run_tiny_pair(tmp_path: Path, *, diagnostics: bool = False) -> tuple[Path, Path]:
    if not _native_available():
        pytest.skip("native extension is not built")
    original = m12.DATA_DIR / "C101.txt"
    if not original.is_file():
        pytest.skip("Solomon C101 input is unavailable")
    data = tmp_path / "data"
    data.mkdir()
    shutil.copyfile(original, data / "C101.txt")
    out = tmp_path / "m12-integrity"
    command = [
        sys.executable, str(Path(m12.__file__).resolve()), "run",
        "--data", str(data), "--out", str(out), "--instances", "C101",
        "--seeds", "0", "--time-limit", "0.001",
    ]
    if diagnostics:
        command.append("--diagnostics")
    completed = subprocess.run(command, cwd=m12.SOLVER_DIR, capture_output=True,
                               text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return out, data


def _read_manifest(out: Path) -> tuple[dict, Path]:
    manifest_path = out / "experiment.json"
    return json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path


def _refresh_solution_hash(out: Path, manifest: dict, variant: str) -> Path:
    entry = manifest["order"]["cases"][0]["results"][variant]
    relative = Path(entry["artifact"])
    solution_path = out / relative
    entry["artifacts_sha256"]["solution.json"] = m12._sha256(solution_path)
    return solution_path


def test_standalone_runner_help_imports_from_repository_root() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(m12.__file__).resolve()), "--help"],
        cwd=m12.SOLVER_DIR, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "run" in completed.stdout and "analyse" in completed.stdout


@pytest.mark.skipif(not _native_available(), reason="native extension is not built")
def test_tiny_paired_run_detects_tampered_artifact(tmp_path: Path) -> None:
    out, data = _run_tiny_pair(tmp_path)
    assert m12.analyse_experiment(SimpleNamespace(out=out, data=data)) == 0

    routes = out / "native" / "C101" / "seed-0" / "routes.sol"
    routes.write_text(routes.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    manifest, _ = _read_manifest(out)
    with pytest.raises(ValueError, match="artifact hash"):
        m12._validate_analysis_input(out, data, manifest)


@pytest.mark.skipif(not _native_available(), reason="native extension is not built")
@pytest.mark.parametrize("target", ("python", "native"))
def test_tiny_diagnostics_batch_csv_matches_per_run_csv(tmp_path: Path, target: str) -> None:
    out, data = _run_tiny_pair(tmp_path, diagnostics=True)
    assert m12.analyse_experiment(SimpleNamespace(out=out, data=data)) == 0
    batch = m12._read_csv(out / "batch_diagnostics.csv")
    per_run = m12._read_csv(out / target / "C101" / "seed-0" / "diagnostics.csv")
    paired = [{key: row[key] for key in per_run[0]} for row in batch if row["variant"] == target]
    assert paired == per_run

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    totals = metrics["variants"][target]["diagnostics_candidate_move_summary"]["all"]
    expected_candidates = sum(int(row["candidates"]) for row in per_run)
    assert totals["phase_counter_totals"]["candidates"] == expected_candidates


@pytest.mark.skipif(not _native_available(), reason="native extension is not built")
@pytest.mark.parametrize("target", ("source", "binary", "input", "budget", "config", "failed"))
def test_analyse_rejects_tampered_snapshot_or_protocol(tmp_path: Path, target: str) -> None:
    out, data = _run_tiny_pair(tmp_path)
    manifest, manifest_path = _read_manifest(out)

    if target == "source":
        package = out / manifest["source"]["package"]
        source = package / "solve.py"
        source.write_bytes(source.read_bytes() + b"\n# tampered\n")
        error = "source or native binary hash"
    elif target == "binary":
        package = out / manifest["source"]["package"]
        binary = package / manifest["source"]["native_binary"]
        binary.write_bytes(binary.read_bytes() + b"tamper")
        error = "source or native binary hash"
    elif target == "input":
        source = data / "C101.txt"
        source.write_bytes(source.read_bytes() + b"\n")
        error = "input hash differs"
    elif target == "budget":
        solution = out / "python" / "C101" / "seed-0" / "solution.json"
        payload = json.loads(solution.read_text(encoding="utf-8"))
        payload["stop"]["time_limit_seconds"] = 0.002
        solution.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        _refresh_solution_hash(out, manifest, "python")
        m12._write_json(manifest_path, manifest)
        error = "saved solution budget"
    elif target == "config":
        solution = out / "native" / "C101" / "seed-0" / "solution.json"
        payload = json.loads(solution.read_text(encoding="utf-8"))
        payload["config"]["search_backend"] = "python"
        solution.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        _refresh_solution_hash(out, manifest, "native")
        m12._write_json(manifest_path, manifest)
        error = "saved solution config"
    else:
        manifest["variants"]["native"]["status"] = "failed"
        m12._write_json(manifest_path, manifest)
        error = "native has failed or incomplete state"

    with pytest.raises(ValueError, match=error):
        m12._validate_analysis_input(out, data, manifest)


@pytest.mark.skipif(not _native_available(), reason="native extension is not built")
def test_public_validation_rejects_changed_self_report_after_hash_update(tmp_path: Path) -> None:
    out, data = _run_tiny_pair(tmp_path)
    manifest, manifest_path = _read_manifest(out)
    solution = out / "native" / "C101" / "seed-0" / "solution.json"
    payload = json.loads(solution.read_text(encoding="utf-8"))
    payload["vehicles"] += 1
    solution.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _refresh_solution_hash(out, manifest, "native")
    m12._write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="stored vehicles/distance differ from public validation"):
        m12._validate_analysis_input(out, data, manifest)
