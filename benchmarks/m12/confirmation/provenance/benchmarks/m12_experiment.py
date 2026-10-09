"""Run and analyse the paired M12 Python/native search-backend experiment.

The default protocol runs all 56 Solomon instances with seeds 0, 1 and 2,
giving each backend the same 0.5 second budget. Runs are sequential and the
backend order rotates for every instance/seed pair::

    python benchmarks/m12_experiment.py run --out runs/m12_native_seed012_0p5s
    python benchmarks/m12_experiment.py analyse --out runs/m12_native_seed012_0p5s

Importing this module never starts an experiment.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import statistics
import sys
import sysconfig
import uuid
from typing import Any

SOLVER_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = SOLVER_DIR / "src"
SRC_PACKAGE = SRC_DIR / "vrptw"
DATA_DIR = SOLVER_DIR / "data"
VARIANTS = ("python", "native")
ARCHIVE_INPUTS = (Path("native/moves.cpp"), Path("setup.py"), Path("pyproject.toml"))

for directory in (SOLVER_DIR, SRC_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from benchmarks import m8_experiment as m8  # noqa: E402

SUMMARY_FIELDS = (*m8.SUMMARY_FIELDS, "binary_sha256", "paired_baseline_vehicles",
                 "paired_baseline_distance_ticks", "vehicles_delta",
                 "distance_delta_ticks", "result_vs_baseline",
                 "same_vehicle_distance_result", "is_regression")
DIAGNOSTIC_FIELDS = tuple(importlib.import_module("vrptw.diagnostics").PhaseStats.__dataclass_fields__)
DIAGNOSTIC_BATCH_FIELDS = ("instance", "seed", "variant", *DIAGNOSTIC_FIELDS)
ARTIFACT_NAMES = ("solution.json", "routes.sol", "history.csv")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _code_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(files[name])
    return digest.hexdigest()


def _jsonable_config(config: object) -> dict[str, Any]:
    return m8._jsonable_config(config)


def _config_hash(config: object) -> str:
    return m8._normalised_config_hash(config)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]], fields: tuple[str, ...]) -> None:
    import csv

    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _snapshot_paths(package_dir: Path) -> list[Path]:
    return sorted([*package_dir.glob("*.py"), *package_dir.glob("_native*.pyd"),
                   *package_dir.glob("_native*.so")])


def _freeze_source(out: Path) -> tuple[Path, str, dict[str, str]]:
    """Freeze Python sources and the extension binary under a fingerprint path."""
    paths = _snapshot_paths(SRC_PACKAGE)
    if not paths:
        raise ValueError(f"no solver source files found in {SRC_PACKAGE}")
    files = {path.name: path.read_bytes() for path in paths}
    source_hash = _code_hash(files)
    package = out / "source" / source_hash / "vrptw"
    package.mkdir(parents=True, exist_ok=True)
    expected = {name: data for name, data in files.items()}
    existing = {path.name: path.read_bytes() for path in _snapshot_paths(package)}
    if existing and existing != expected:
        raise ValueError(f"frozen source directory contains different files: {package}")
    for name, data in expected.items():
        target = package / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f"frozen source file changed while being copied: {target}")
        if not target.exists():
            target.write_bytes(data)
    return package, source_hash, {name: _sha256_bytes(data) for name, data in sorted(files.items())}


def _archive_provenance(out: Path) -> dict[str, str]:
    """Copy build inputs and this runner so a run can be audited later."""
    archived: dict[str, str] = {}
    runner_target = out / "provenance" / "benchmarks" / "m12_experiment.py"
    runner_target.parent.mkdir(parents=True, exist_ok=True)
    runner_target.write_bytes(Path(__file__).resolve().read_bytes())
    archived[runner_target.relative_to(out).as_posix()] = _sha256(runner_target)
    for relative in ARCHIVE_INPUTS:
        source = SOLVER_DIR / relative
        if not source.is_file():
            raise ValueError(f"required native build input is missing: {source}")
        target = out / "provenance" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        archived[target.relative_to(out).as_posix()] = _sha256(target)
    return archived


def _load_package(alias: str, package_dir: Path):
    return m8._load_package(alias, package_dir)


def _package_report(package):
    return m8._package_report(package)


def _config_for(package, variant: str, seed: int, time_limit: float,
                diagnostics: bool = False):
    if variant not in VARIANTS:
        raise ValueError(f"unknown M12 backend {variant!r}")
    return package.Config(
        seed=seed,
        max_iterations=None,
        time_limit_seconds=time_limit,
        diagnostics=diagnostics,
        search_backend=variant,
        infeasible_search=False,
        evaluation_mode="incremental",
    )


def _rotated_variants(case_index: int) -> list[str]:
    offset = case_index % len(VARIANTS)
    return [*VARIANTS[offset:], *VARIANTS[:offset]]


def _make_row(instance: str, seed: int, variant: str, code_hash: str,
              input_hash: str, config_hash: str, time_limit: float,
              vehicle_limit: int, numeric_rule: str, diagnostics: bool,
              binary_hash: str) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        instance=instance, input_format="solomon_txt", input_sha256=input_hash,
        seed=seed, threads=1, status="error", feasible=False,
        time_limit_seconds=time_limit, max_iterations="", vehicle_limit=vehicle_limit,
        code_sha256=code_hash, diagnostics_enabled=diagnostics, variant=variant,
        config_sha256=config_hash, numeric_rule=numeric_rule, binary_sha256=binary_hash,
    )
    return row


def _environment() -> dict[str, object]:
    return m8._environment()


def _native_build_metadata(package, binary_hash: str) -> dict[str, Any]:
    native = importlib.import_module(f"{package.__name__}.native_search")
    metadata = native.native_metadata()
    if metadata["binary_sha256"] != binary_hash:
        raise ValueError("native extension metadata hash differs from frozen binary")
    try:
        pybind_version = importlib.metadata.version("pybind11")
    except importlib.metadata.PackageNotFoundError:
        pybind_version = None
    return {
        **metadata,
        "compiler": {
            "cc": sysconfig.get_config_var("CC"),
            "cxx": sysconfig.get_config_var("CXX"),
            "platform": sysconfig.get_platform(),
            "python": sys.version.split()[0],
            "pybind11": pybind_version,
        },
    }


def _artifacts(artifact_dir: Path, diagnostics: bool) -> dict[str, str]:
    names = [*ARTIFACT_NAMES, *( ["diagnostics.csv"] if diagnostics else [])]
    missing = [name for name in names if not (artifact_dir / name).is_file()]
    if missing:
        raise ValueError(f"run artifact(s) missing: {', '.join(missing)}")
    return {name: _sha256(artifact_dir / name) for name in names}


def run_experiment(args: argparse.Namespace) -> int:
    if (not args.seeds or args.seeds != sorted(set(args.seeds))
            or any(type(seed) is not int or seed < 0 for seed in args.seeds)):
        raise ValueError("--seeds must be unique, nonnegative integers in ascending order")
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit must be finite and positive")
    paths = m8._input_paths(args.data, args.instances)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    archive_hashes = _archive_provenance(out)
    frozen_dir, source_hash, source_hashes = _freeze_source(out)
    run_id = uuid.uuid4().hex[:12]
    package = _load_package(f"m12_frozen_{run_id}", frozen_dir)
    report = _package_report(package)
    if report.code_fingerprint() != source_hash:
        raise ValueError("frozen package fingerprint differs from copied sources and binary")
    binary_files = {name: digest for name, digest in source_hashes.items()
                    if name.startswith("_native") and name.endswith((".pyd", ".so"))}
    if len(binary_files) != 1:
        raise ValueError(f"M12 requires exactly one frozen native extension, found {len(binary_files)}")
    binary_name, binary_hash = next(iter(binary_files.items()))
    build_metadata = _native_build_metadata(package, binary_hash)
    public_api = importlib.import_module("vrptw")
    public_validate = importlib.import_module("vrptw.evaluate").validate_solution
    numeric_rule = report.NUMERIC_RULE_ID
    names = [path.stem for path in paths]
    input_hashes = {path.stem: _sha256(path) for path in paths}
    config_dicts: dict[str, dict[str, Any]] = {}
    config_hashes: dict[str, str] = {}
    for variant in VARIANTS:
        values = _config_for(package, variant, 0, args.time_limit, args.diagnostics)
        config_dicts[variant] = _jsonable_config(values)
        config_hashes[variant] = _config_hash(values)
    cases = []
    for index, (path, seed) in enumerate((path, seed) for path in paths for seed in args.seeds):
        order = _rotated_variants(index)
        cases.append({
            "case_index": index, "instance": path.stem, "seed": seed,
            "variants": order,
            "results": {variant: {
                "status": "pending", "verified": False,
                "artifact": f"{variant}/{path.stem}/seed-{seed}/solution.json",
                "artifacts_sha256": {},
            } for variant in order},
        })

    started_at = _utc_now()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "M12 paired Python/native search backend",
        "status": "running", "started_at_utc": started_at, "finished_at_utc": None,
        "data_directory": str(args.data.resolve()), "instances": names,
        "seeds": list(args.seeds), "time_limit_seconds": args.time_limit,
        "max_iterations": None, "threads": 1,
        "diagnostics_enabled": bool(args.diagnostics), "numeric_rule": numeric_rule,
        "runner_sha256": archive_hashes["provenance/benchmarks/m12_experiment.py"],
        "input_sha256": input_hashes, "environment": _environment(),
        "provenance_files": archive_hashes,
        "source": {
            "sha256": source_hash,
            "package": frozen_dir.relative_to(out).as_posix(),
            "file_sha256": source_hashes,
            "module_alias": package.__name__,
            "native_binary": binary_name,
            "native_binary_sha256": binary_hash,
            "native_build": build_metadata,
        },
        "order": {
            "case_definition": "sorted instance, then ascending seed",
            "variant_rotation": "alternate Python/native first by case_index parity",
            "variants": list(VARIANTS), "cases": cases,
        },
        "variants": {
            variant: {
                "config": config_dicts[variant], "config_sha256": config_hashes[variant],
                "code_sha256": source_hash, "binary_sha256": binary_hash,
                "status": "running", "runs": 0, "successful": 0, "failed": 0,
                "independently_verified": 0,
            } for variant in VARIANTS
        },
        "total_runs": len(cases) * len(VARIANTS), "independently_verified": 0,
    }
    manifest_path = out / "experiment.json"
    _write_json(manifest_path, manifest)
    records: dict[str, list[dict[str, object]]] = {variant: [] for variant in VARIANTS}
    diagnostics_records: list[dict[str, object]] = []
    public_instances: dict[str, object] = {}
    path_by_name = {path.stem: path for path in paths}
    batch_paths: dict[str, Path] = {}
    for variant in VARIANTS:
        variant_dir = out / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        batch_path = variant_dir / "batch_summary.csv"
        _write_csv(batch_path, [], SUMMARY_FIELDS)
        batch_paths[variant] = batch_path

    try:
        for case in cases:
            name, seed = case["instance"], case["seed"]
            path = path_by_name[name]
            if name not in public_instances:
                public_instances[name] = public_api.read_solomon(path)
            public_instance = public_instances[name]
            for variant in case["variants"]:
                config = _config_for(package, variant, seed, args.time_limit, args.diagnostics)
                config_values = _jsonable_config(config)
                row = _make_row(name, seed, variant, source_hash, input_hashes[name],
                                config_hashes[variant], args.time_limit,
                                public_instance.vehicle_count, numeric_rule,
                                args.diagnostics, binary_hash)
                artifact_dir = out / variant / name / f"seed-{seed}"
                try:
                    solver_instance = package.read_solomon(path)
                    result = package.solve(solver_instance, config)
                    report.write_run(solver_instance, result, config, artifact_dir,
                                     source_path=path, plots=False)
                    payload = json.loads((artifact_dir / "solution.json").read_text(encoding="utf-8"))
                    verdict = public_validate(public_instance, payload.get("routes", []))
                    objective = (verdict.vehicles, verdict.distance)
                    if not verdict.feasible or objective != result.evaluation.objective:
                        raise ValueError(f"public validator rejected solver routes: {verdict.first_violation}")
                    if objective != (payload.get("vehicles"), payload.get("distance_ticks")):
                        raise ValueError("stored integer objective differs from public validation")
                    if (payload.get("input_sha256") != input_hashes[name]
                            or payload.get("code_sha256") != source_hash
                            or payload.get("numeric_rule") != numeric_rule
                            or payload.get("seed") != seed
                            or payload.get("config") != config_values
                            or payload.get("search_backend") != variant):
                        raise ValueError("saved solution metadata differs from the paired run")
                    if variant == "native" and payload.get("native", {}).get("binary_sha256") != binary_hash:
                        raise ValueError("saved native solution metadata has a different binary hash")
                    row.update(
                        status="ok", feasible=True, vehicles=verdict.vehicles,
                        distance_ticks=verdict.distance, distance=verdict.distance / 1000,
                        first_feasible_seconds=round(result.first_feasible_seconds, 6),
                        runtime_seconds=round(result.runtime_seconds, 6),
                        iterations=result.iterations, stop_reason=result.stop_reason,
                    )
                    case["results"][variant]["artifacts_sha256"] = _artifacts(artifact_dir, args.diagnostics)
                    if result.diagnostics is not None:
                        for phase in result.diagnostics.phases:
                            diagnostics_records.append({
                                "instance": name, "seed": seed, "variant": variant,
                                **asdict(phase),
                            })
                    manifest["independently_verified"] += 1
                    manifest["variants"][variant]["independently_verified"] += 1
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                case["results"][variant].update(
                    status=row["status"], verified=row["status"] == "ok",
                    error=row["error"],
                )
                records[variant].append(row)
                _append_csv(batch_paths[variant], row, SUMMARY_FIELDS)
                state = manifest["variants"][variant]
                state["runs"] += 1
                if row["status"] == "ok":
                    state["successful"] += 1
                else:
                    state["failed"] += 1
                _write_json(manifest_path, manifest)
            print(f"finished {case['case_index'] + 1}/{len(cases)} case(s): "
                  f"{name} seed={seed}; variants={','.join(case['variants'])}", flush=True)
    finally:
        for variant in VARIANTS:
            rows = records[variant]
            summary = {
                "schema_version": 1, "variant": variant,
                "instances": names, "runs": len(rows),
                "successful": sum(row["status"] == "ok" for row in rows),
                "failed": sum(row["status"] != "ok" for row in rows),
                "seeds": list(args.seeds), "time_limit_seconds": args.time_limit,
                "max_iterations": None, "threads": 1,
                "diagnostics_enabled": bool(args.diagnostics),
                "config": config_dicts[variant], "config_sha256": config_hashes[variant],
                "code_sha256": source_hash, "binary_sha256": binary_hash,
                "numeric_rule": numeric_rule, "input_sha256": input_hashes,
                "runner_sha256": manifest["runner_sha256"],
            }
            _write_json(out / variant / "batch_summary.json", summary)
            variant_state = manifest["variants"][variant]
            variant_state["status"] = (
                "completed" if len(rows) == len(cases)
                and all(row["status"] == "ok" for row in rows) else "failed"
            )
            variant_state["batch_summary_csv_sha256"] = _sha256(batch_paths[variant])
            variant_state["batch_summary_json_sha256"] = _sha256(out / variant / "batch_summary.json")
        if args.diagnostics:
            _write_csv(out / "batch_diagnostics.csv", diagnostics_records, DIAGNOSTIC_BATCH_FIELDS)
            manifest["diagnostics_sha256"] = _sha256(out / "batch_diagnostics.csv")
        manifest["finished_at_utc"] = _utc_now()
        manifest["status"] = "completed" if all(
            state["status"] == "completed" for state in manifest["variants"].values()
        ) else "failed"
        _write_json(manifest_path, manifest)
    return 0 if manifest["status"] == "completed" else 1


def _append_csv(path: Path, row: dict[str, object], fields: tuple[str, ...]) -> None:
    import csv

    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writerow(row)
        stream.flush()


def _strict_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _comparison(base: tuple[int, int], candidate: tuple[int, int]) -> tuple[str, str, int | None]:
    if candidate[0] < base[0]:
        return "better", "not_compared", None
    if candidate[0] > base[0]:
        return "worse", "not_compared", None
    delta = candidate[1] - base[1]
    result = "better" if delta < 0 else "worse" if delta > 0 else "tie"
    return result, result, delta


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_source_and_provenance(experiment: Path, manifest: dict[str, Any]):
    source = manifest.get("source", {})
    package_rel = source.get("package")
    _require(isinstance(package_rel, str), "manifest frozen package path is missing")
    package_dir = experiment / package_rel
    _require(package_dir.resolve().is_relative_to(experiment.resolve()), "frozen package path escapes experiment")
    actual_files = {path.name: path.read_bytes() for path in _snapshot_paths(package_dir)}
    expected_hashes = source.get("file_sha256")
    _require(isinstance(expected_hashes, dict), "manifest frozen source hashes are missing")
    _require(set(actual_files) == set(expected_hashes), "frozen source/binary file coverage differs")
    actual_hashes = {name: _sha256_bytes(data) for name, data in actual_files.items()}
    _require(actual_hashes == expected_hashes, "frozen source or native binary hash differs")
    code_hash = _code_hash(actual_files)
    _require(code_hash == source.get("sha256"), "frozen source fingerprint differs")
    _require(source.get("native_binary") in actual_files, "frozen native binary is missing")
    _require(actual_hashes[source["native_binary"]] == source.get("native_binary_sha256"),
             "frozen native binary hash differs")
    provenance = manifest.get("provenance_files")
    _require(isinstance(provenance, dict) and provenance, "archived provenance hashes are missing")
    for relative, expected in provenance.items():
        target = experiment / relative
        _require(target.resolve().is_relative_to(experiment.resolve()) and target.is_file(),
                 f"archived provenance file missing: {relative}")
        _require(_sha256(target) == expected, f"archived provenance hash differs: {relative}")
    _require(manifest.get("runner_sha256") == provenance.get(
        "provenance/benchmarks/m12_experiment.py"), "archived runner hash differs")
    module_alias = source.get("module_alias")
    _require(isinstance(module_alias, str) and module_alias,
             "frozen module alias is missing from source metadata")
    # Reuse the run's package when analyse is called in the same process. This
    # also avoids registering the same pybind11 C++ type under a second alias.
    package = sys.modules.get(module_alias)
    if package is None:
        package = _load_package(module_alias, package_dir)
    report = _package_report(package)
    _require(report.code_fingerprint() == code_hash, "frozen report fingerprint differs")
    binary = importlib.import_module(f"{package.__name__}.native_search").native_metadata()
    _require(binary.get("binary_sha256") == source.get("native_binary_sha256"),
             "loaded native extension differs from archived binary")
    return package, code_hash


def _expected_config(package, variant: str, seed: int, manifest: dict[str, Any]) -> dict[str, Any]:
    return _jsonable_config(_config_for(
        package, variant, seed, manifest["time_limit_seconds"],
        manifest["diagnostics_enabled"],
    ))


def _validate_analysis_input(experiment: Path, data_dir: Path, manifest: dict[str, Any]):
    _require(manifest.get("status") == "completed", "only completed experiments can be analysed")
    _require(manifest.get("experiment") == "M12 paired Python/native search backend",
             "manifest is not an M12 experiment")
    _require(manifest.get("variants") is not None and set(manifest["variants"]) == set(VARIANTS),
             "M12 variant coverage differs")
    package, code_hash = _validate_source_and_provenance(experiment, manifest)
    names, seeds = manifest.get("instances", []), manifest.get("seeds", [])
    _require(names == sorted(set(names)) and bool(names), "manifest instance coverage is invalid")
    _require(seeds == sorted(set(seeds)) and bool(seeds), "manifest seed coverage is invalid")
    expected_cases = [(name, seed) for name in names for seed in seeds]
    cases = manifest.get("order", {}).get("cases", [])
    _require(len(cases) == len(expected_cases), "manifest paired case coverage differs")
    _require([(case.get("instance"), case.get("seed")) for case in cases] == expected_cases,
             "manifest paired case order or coverage differs")
    _require([case.get("case_index") for case in cases] == list(range(len(cases))),
             "manifest case indices differ")
    input_hashes = manifest.get("input_sha256", {})
    _require(set(input_hashes) == set(names), "manifest input hash coverage differs")
    for name in names:
        path = data_dir / f"{name}.txt"
        _require(path.is_file() and _sha256(path) == input_hashes[name], f"input hash differs for {name}")
    public_api = importlib.import_module("vrptw")
    public_validate = importlib.import_module("vrptw.evaluate").validate_solution
    public_report = importlib.import_module("vrptw.report")
    _require(public_report.NUMERIC_RULE_ID == manifest.get("numeric_rule"), "numeric rule differs")
    public_instances = {name: public_api.read_solomon(data_dir / f"{name}.txt") for name in names}
    expected_keys = {(name, str(seed)) for name, seed in expected_cases}
    loaded: dict[str, tuple[dict[str, Any], list[dict[str, str]]]] = {}
    for variant in VARIANTS:
        state = manifest["variants"][variant]
        _require(state.get("status") == "completed", f"{variant} has failed or incomplete state")
        _require(state.get("runs") == len(expected_cases)
                 and state.get("successful") == len(expected_cases)
                 and state.get("failed") == 0
                 and state.get("independently_verified") == len(expected_cases),
                 f"{variant} run state or coverage differs")
        batch_dir = experiment / variant
        csv_path, json_path = batch_dir / "batch_summary.csv", batch_dir / "batch_summary.json"
        _require(_sha256(csv_path) == state.get("batch_summary_csv_sha256"),
                 f"{variant} batch CSV hash differs")
        _require(_sha256(json_path) == state.get("batch_summary_json_sha256"),
                 f"{variant} batch JSON hash differs")
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        rows = _read_csv(csv_path)
        actual_keys = {(row.get("instance", ""), row.get("seed", "")) for row in rows}
        _require(len(rows) == len(expected_keys) and actual_keys == expected_keys,
                 f"{variant} batch summary coverage differs")
        expected_config = _expected_config(package, variant, 0, manifest)
        expected_config_hash = _config_hash(package.Config(**expected_config))
        _require(state.get("config") == expected_config
                 and state.get("config_sha256") == expected_config_hash,
                 f"{variant} manifest config differs from M12 baseline")
        _require(metadata.get("config") == expected_config
                 and metadata.get("config_sha256") == expected_config_hash,
                 f"{variant} batch config differs")
        _require(metadata.get("variant") == variant and metadata.get("runs") == len(rows)
                 and metadata.get("successful") == len(rows) and metadata.get("failed") == 0,
                 f"{variant} batch state differs")
        _require(metadata.get("code_sha256") == code_hash == state.get("code_sha256"),
                 f"{variant} source fingerprint differs")
        _require(metadata.get("binary_sha256") == manifest["source"]["native_binary_sha256"]
                 == state.get("binary_sha256"), f"{variant} binary hash differs")
        _require(metadata.get("input_sha256") == input_hashes,
                 f"{variant} batch input hashes differ")
        case_by_key = {(case.get("instance"), case.get("seed")): case for case in cases}
        for row in rows:
            name, seed_text = row["instance"], row["seed"]
            seed = int(seed_text)
            key = (name, seed)
            _require(row.get("status") == "ok" and row.get("feasible", "").lower() == "true",
                     f"{variant} contains a failed or infeasible run: {key}")
            _require(row.get("variant") == variant and row.get("threads") == "1",
                     f"{variant} row protocol differs for {key}")
            _require(row.get("input_sha256") == input_hashes[name]
                     and row.get("code_sha256") == code_hash
                     and row.get("config_sha256") == expected_config_hash
                     and row.get("binary_sha256") == manifest["source"]["native_binary_sha256"]
                     and row.get("numeric_rule") == manifest["numeric_rule"],
                     f"{variant} row hashes/config differ for {key}")
            _require(float(row["time_limit_seconds"]) == manifest["time_limit_seconds"],
                     f"{variant} budget differs for {key}")
            case = case_by_key.get(key)
            _require(case is not None and case.get("variants") == _rotated_variants(case["case_index"]),
                     f"paired order differs for {key}")
            entry = case["results"][variant]
            _require(entry.get("status") == "ok" and entry.get("verified") is True,
                     f"manifest has a failed run for {variant} {key}")
            artifact_dir = experiment / variant / name / f"seed-{seed}"
            artifact_dir_resolved = artifact_dir.resolve()
            _require(artifact_dir_resolved.is_relative_to(experiment.resolve()),
                     f"artifact path escapes experiment for {key}")
            expected_artifacts = entry.get("artifacts_sha256", {})
            required_artifacts = {*ARTIFACT_NAMES,
                                  *( ["diagnostics.csv"] if manifest["diagnostics_enabled"] else [])}
            _require(set(expected_artifacts) == required_artifacts,
                     f"{variant} artifact coverage differs for {key}")
            actual_artifacts = _artifacts(artifact_dir, manifest["diagnostics_enabled"])
            _require(actual_artifacts == expected_artifacts,
                     f"{variant} artifact hash differs for {key}")
            solution = json.loads((artifact_dir / "solution.json").read_text(encoding="utf-8"))
            verdict = public_validate(public_instances[name], solution.get("routes", []))
            _require(verdict.feasible, f"public validation failed for {variant} {key}: {verdict.first_violation}")
            stored_obj = (verdict.vehicles, verdict.distance)
            row_obj = (int(row["vehicles"]), int(row["distance_ticks"]))
            payload_obj = (solution.get("vehicles"), solution.get("distance_ticks"))
            _require(payload_obj == row_obj == stored_obj,
                     f"stored vehicles/distance differ from public validation for {variant} {key}")
            expected_run_config = _expected_config(package, variant, seed, manifest)
            _require(solution.get("config") == expected_run_config,
                     f"saved solution config differs for {variant} {key}")
            _require(solution.get("input_sha256") == input_hashes[name]
                     and solution.get("code_sha256") == code_hash
                     and solution.get("numeric_rule") == manifest["numeric_rule"]
                     and solution.get("seed") == seed
                     and solution.get("search_backend") == variant,
                     f"saved solution protocol differs for {variant} {key}")
            stop = solution.get("stop", {})
            _require(stop.get("time_limit_seconds") == manifest["time_limit_seconds"]
                     and stop.get("max_iterations") is None,
                     f"saved solution budget differs for {variant} {key}")
            _require(int(row["vehicle_limit"]) == public_instances[name].vehicle_count,
                     f"vehicle limit differs for {variant} {key}")
            if variant == "native":
                _require(solution.get("native", {}).get("binary_sha256")
                         == manifest["source"]["native_binary_sha256"],
                         f"native solution binary hash differs for {key}")
        loaded[variant] = (metadata, rows)
    _require(manifest.get("total_runs") == len(expected_cases) * len(VARIANTS)
             and manifest.get("independently_verified") == len(expected_cases) * len(VARIANTS),
             "manifest total run count differs")
    if manifest["diagnostics_enabled"]:
        batch_diag = experiment / "batch_diagnostics.csv"
        _require(batch_diag.is_file() and _sha256(batch_diag) == manifest.get("diagnostics_sha256"),
                 "batch diagnostics hash differs")
        diagnostic_rows = _read_csv(batch_diag)
        expected_diag_keys = {(name, str(seed), variant) for name, seed in expected_cases
                              for variant in VARIANTS}
        observed = {(row.get("instance"), row.get("seed"), row.get("variant"))
                    for row in diagnostic_rows if row.get("phase") == "other"}
        _require(observed == expected_diag_keys, "batch diagnostics coverage differs")
    return loaded


def _best_by_instance(rows: list[dict[str, str]], label: str):
    best: dict[str, tuple[tuple[int, int], int]] = {}
    for row in rows:
        objective = (int(row["vehicles"]), int(row["distance_ticks"]))
        name, seed = row["instance"], int(row["seed"])
        if name not in best or objective < best[name][0]:
            best[name] = (objective, seed)
    return best


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": statistics.median(values) if values else None,
        "p90_nearest_rank": _nearest_rank(values, 0.9),
        "mean": statistics.fmean(values) if values else None,
    }


def _metric_rows(loaded) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, Any]]:
    by_variant = {variant: { (row["instance"], int(row["seed"])): dict(row)
                            for row in loaded[variant][1] } for variant in VARIANTS}
    enriched: list[dict[str, object]] = []
    pair_results: dict[str, list[dict[str, object]]] = {"all": [], "C": [], "R": [], "RC": []}
    regressions: list[dict[str, object]] = []
    families: dict[tuple[str, int], str] = {}
    for (name, seed), py_row in sorted(by_variant["python"].items()):
        native_row = by_variant["native"][(name, seed)]
        families[(name, seed)] = m8._family(name)
        base = (int(py_row["vehicles"]), int(py_row["distance_ticks"]))
        candidate = (int(native_row["vehicles"]), int(native_row["distance_ticks"]))
        result, distance_result, distance_delta = _comparison(base, candidate)
        vehicles_delta = candidate[0] - base[0]
        is_regression = result == "worse"
        pair = {
            "instance": name, "seed": seed, "family": m8._family(name),
            "python_vehicles": base[0], "python_distance_ticks": base[1],
            "native_vehicles": candidate[0], "native_distance_ticks": candidate[1],
            "vehicles_delta": vehicles_delta,
            "distance_delta_ticks": "" if distance_delta is None else distance_delta,
            "result_vs_python": result,
            "same_vehicle_distance_result": distance_result,
            "is_regression": is_regression,
        }
        pair_results["all"].append(pair)
        pair_results[m8._family(name)].append(pair)
        if is_regression:
            regressions.append(pair)
        for variant, own, other in (("python", py_row, base), ("native", native_row, base)):
            comparison = ("tie", "tie", 0) if variant == "python" else (result, distance_result, distance_delta)
            own.update(
                paired_baseline_vehicles=other[0],
                paired_baseline_distance_ticks=other[1],
                vehicles_delta=(int(own["vehicles"]) - other[0]),
                distance_delta_ticks=("" if comparison[2] is None else comparison[2]),
                result_vs_baseline=comparison[0],
                same_vehicle_distance_result=comparison[1],
                is_regression=(variant == "native" and is_regression),
            )
            enriched.append(own)

    best_rows: list[dict[str, object]] = []
    bests = {variant: _best_by_instance(loaded[variant][1], variant) for variant in VARIANTS}
    best_regressions: list[dict[str, object]] = []
    for name in sorted(bests["python"]):
        py_obj, py_seed = bests["python"][name]
        native_obj, native_seed = bests["native"][name]
        result, distance_result, delta = _comparison(py_obj, native_obj)
        record = {
            "instance": name, "family": m8._family(name),
            "python_vehicles": py_obj[0], "python_distance_ticks": py_obj[1],
            "python_best_seed": py_seed, "native_vehicles": native_obj[0],
            "native_distance_ticks": native_obj[1], "native_best_seed": native_seed,
            "result_vs_python": result, "same_vehicle_distance_result": distance_result,
            "distance_delta_ticks": "" if delta is None else delta,
        }
        best_rows.append(record)
        if result == "worse":
            best_regressions.append(record)

    metrics: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "M12 paired Python/native search backend",
        "independently_verified_runs": sum(len(item[1]) for item in loaded.values()),
        "paired_cases": len(pair_results["all"]),
        "comparison_vs_python": {},
        "all_native_regressions": regressions,
        "best_by_instance_native_regressions": best_regressions,
        "variants": {},
    }
    for variant in VARIANTS:
        metadata, rows = loaded[variant]
        variant_families: dict[str, list[dict[str, str]]] = {"all": rows, "C": [], "R": [], "RC": []}
        for row in rows:
            variant_families[m8._family(row["instance"])].append(row)
        timing: dict[str, Any] = {}
        for family, group in variant_families.items():
            timing[family] = {
                "runs": len(group),
                "runtime_seconds": _stats([float(row["runtime_seconds"]) for row in group]),
                "first_feasible_seconds": _stats([float(row["first_feasible_seconds"]) for row in group]),
                "iterations": _stats([float(row["iterations"]) for row in group]),
                "total_iterations": sum(int(row["iterations"]) for row in group),
            }
        metrics["variants"][variant] = {
            "config": metadata["config"], "config_sha256": metadata["config_sha256"],
            "code_sha256": metadata["code_sha256"], "binary_sha256": metadata["binary_sha256"],
            "runs": len(rows), "runtime_first_feasible_iterations_by_family": timing,
            "diagnostics_candidate_move_summary": None,
        }
    for family, pairs in pair_results.items():
        results = [pair["result_vs_python"] for pair in pairs]
        same_vehicle_deltas = [int(pair["distance_delta_ticks"]) for pair in pairs
                               if pair["distance_delta_ticks"] != ""]
        metrics["comparison_vs_python"][family] = {
            "cases": len(pairs), "better": results.count("better"),
            "worse": results.count("worse"), "tie": results.count("tie"),
            "vehicles_better": sum(int(pair["vehicles_delta"]) < 0 for pair in pairs),
            "vehicles_worse": sum(int(pair["vehicles_delta"]) > 0 for pair in pairs),
            "vehicles_same": sum(int(pair["vehicles_delta"]) == 0 for pair in pairs),
            "same_vehicle_distance_delta_ticks": _stats([float(value) for value in same_vehicle_deltas]),
        }
    return enriched, best_rows, metrics


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment = args.out.resolve()
    manifest_path = experiment / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    loaded = _validate_analysis_input(experiment, args.data.resolve(), manifest)
    runs, best, metrics = _metric_rows(loaded)
    if manifest["diagnostics_enabled"]:
        _fill_diagnostic_metrics(experiment, loaded, metrics)
    metrics["experiment_manifest_sha256"] = _sha256(manifest_path)
    _write_csv(experiment / "runs.csv", runs, SUMMARY_FIELDS)
    _write_csv(experiment / "best_by_instance.csv", best, (
        "instance", "family", "python_vehicles", "python_distance_ticks", "python_best_seed",
        "native_vehicles", "native_distance_ticks", "native_best_seed", "result_vs_python",
        "same_vehicle_distance_result", "distance_delta_ticks",
    ))
    _write_json(experiment / "metrics.json", metrics)
    print(f"analysed {len(runs)} verified backend runs; wrote runs.csv, best_by_instance.csv, metrics.json")
    return 0


def _fill_diagnostic_metrics(experiment: Path, loaded, metrics: dict[str, Any]) -> None:
    rows = _read_csv(experiment / "batch_diagnostics.csv")
    counters = ("candidates", "feasible_candidates", "infeasible_candidates", "accepted",
                "route_evaluations", "calls", "trials", "failures")
    events: dict[tuple[str, str, str], int] = {}
    for variant in VARIANTS:
        summary: dict[str, Any] = {}
        batch_rows = [row for row in rows if row["variant"] == variant]
        for family in ("all", "C", "R", "RC"):
            group = batch_rows if family == "all" else [row for row in batch_rows
                                                         if m8._family(row["instance"]) == family]
            totals = {field: 0 for field in counters}
            for row in group:
                for field in counters:
                    totals[field] += int(row[field])
            history_events: dict[str, int] = {}
            for row in loaded[variant][1]:
                if family != "all" and m8._family(row["instance"]) != family:
                    continue
                history_path = experiment / variant / row["instance"] / f"seed-{row['seed']}" / "history.csv"
                for history in _read_csv(history_path):
                    event = history.get("event", "")
                    history_events[event] = history_events.get(event, 0) + 1
            summary[family] = {"runs": len({(row["instance"], row["seed"]) for row in group}),
                               "phase_counter_totals": totals,
                               "history_event_counts": history_events}
        metrics["variants"][variant]["diagnostics_candidate_move_summary"] = summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="freeze the solver and run paired backends sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR,
                     help="directory of top-level Solomon .txt files")
    run.add_argument("--out", type=Path, required=True, help="new or empty experiment directory")
    run.add_argument("--instances", nargs="+", help="optional instance stems for a short run")
    run.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--diagnostics", action="store_true", help="record stage and candidate counters")
    analyse = commands.add_parser("analyse", help="verify artifacts and compare paired results")
    analyse.add_argument("--out", type=Path, required=True, help="completed experiment directory")
    analyse.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    try:
        return run_experiment(args) if args.command == "run" else analyse_experiment(args)
    except (ValueError, OSError, ImportError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
