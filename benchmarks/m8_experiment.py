"""Run and analyse the sequential M8 evaluation-cache ablation.

Default full run (all 56 top-level Solomon instances, seeds 0/1/2)::

    python benchmarks/m8_experiment.py run --out runs/m8_ablation_seed012_0p5s
    python benchmarks/m8_experiment.py analyse --out runs/m8_ablation_seed012_0p5s

The runner freezes the current ``src/vrptw`` package before solving and imports
both it and the archived M7 baseline under private package aliases. All runs
are single-process and sequential; importing this module never starts a run.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
from types import ModuleType
from typing import Any

SOLVER_DIR = Path(__file__).resolve().parents[1]
SRC_PACKAGE = SOLVER_DIR / "src" / "vrptw"
SRC_DIR = SOLVER_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
M7_SOURCE_HASH = "bcdbfecac731779090e3a398702ae4d335968003d643f03bf90430cd1a9ec707"
M7_PACKAGE = SOLVER_DIR / "benchmarks" / "m7_delivery" / "source" / M7_SOURCE_HASH / "vrptw"
DATA_DIR = SOLVER_DIR / "data"
VARIANTS = ("baseline", "cached", "incremental", "n20", "n40")

# Keep the summary protocol compatible with diagnose.verify_batch and the
# comparison readers used by the M7 experiment.
SUMMARY_FIELDS = (
    "instance", "input_format", "input_sha256", "seed", "threads", "status",
    "feasible", "vehicles", "distance_ticks", "distance", "first_feasible_seconds",
    "runtime_seconds", "iterations", "stop_reason", "numeric_rule", "reference_status",
    "reference_vehicles", "reference_distance", "distance_gap_percent", "error",
    "time_limit_seconds", "max_iterations", "fleet_attempts_per_k", "max_moves",
    "vehicle_limit", "code_sha256", "diagnostics_enabled", "variant", "config_sha256",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_bytes(payload.encode("utf-8"))


def _code_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(files[name])
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, rows: list[dict[str, object]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _environment() -> dict[str, object]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def _freeze_current_source(out: Path) -> tuple[Path, str, dict[str, str]]:
    paths = sorted(SRC_PACKAGE.glob("*.py"))
    if not paths:
        raise ValueError(f"no Python source files found in {SRC_PACKAGE}")
    files = {path.name: path.read_bytes() for path in paths}
    source_hash = _code_hash(files)
    package = out / "source" / source_hash / "vrptw"
    package.mkdir(parents=True, exist_ok=True)
    existing = {path.name: path.read_bytes() for path in package.glob("*.py")}
    if existing and existing != files:
        raise ValueError(f"frozen source directory already contains different files: {package}")
    for name, contents in files.items():
        target = package / name
        if not target.exists():
            target.write_bytes(contents)
        elif target.read_bytes() != contents:
            raise ValueError(f"frozen source file changed while being prepared: {target}")
    file_hashes = {name: _sha256_bytes(contents) for name, contents in sorted(files.items())}
    return package, source_hash, file_hashes


def _load_package(alias: str, package_dir: Path) -> ModuleType:
    init_path = package_dir / "__init__.py"
    if not init_path.is_file():
        raise ValueError(f"solver package is missing {init_path}")
    spec = importlib.util.spec_from_file_location(
        alias, init_path, submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load solver package from {package_dir}")
    module = importlib.util.module_from_spec(spec)
    # Register before execution so dataclasses and relative imports see the
    # private package name throughout initialization.
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def _package_report(package: ModuleType) -> ModuleType:
    return importlib.import_module(f"{package.__name__}.report")


def _config_for(package: ModuleType, variant: str, seed: int, time_limit: float):
    if variant == "baseline":
        # This is the M7 delivery configuration: default due construction and
        # repair, with its 0.95 fleet budget. Spell out the remaining search
        # values so the archived baseline stays reviewable.
        config_type = package.Config
        return config_type(
            seed=seed, max_iterations=None, time_limit_seconds=time_limit,
            fleet_attempts_per_k=100, fleet_time_fraction=0.95, max_moves=2,
            construction_order="due", repair_order="due", repair_strategy="cheapest",
            search_strategy="first",
            operators=importlib.import_module(f"{package.__name__}.local_search").OPERATORS,
            remove_min=3, remove_max=8, restart_after=20, diagnostics=False,
        )
    values: dict[str, object] = {
        "seed": seed,
        "max_iterations": None,
        "time_limit_seconds": time_limit,
        "diagnostics": False,
        "evaluation_mode": "incremental",
        "num_neighbours": None,
    }
    if variant == "cached":
        values["evaluation_mode"] = "cached"
    elif variant == "n20":
        values["num_neighbours"] = 20
    elif variant == "n40":
        values["num_neighbours"] = 40
    elif variant != "incremental":
        raise ValueError(f"unknown variant {variant!r}")
    return package.Config(**values)


def _normalised_config_hash(config: object) -> str:
    values = _jsonable_config(config)
    values["seed"] = 0
    return _canonical_hash(values)


def _jsonable_config(config: object) -> dict[str, Any]:
    """Match list/tuple values after Config is persisted as JSON."""
    return json.loads(json.dumps(asdict(config)))


def _input_paths(data_dir: Path, selected: list[str] | None) -> list[Path]:
    paths = sorted(data_dir.glob("*.txt"), key=lambda path: path.stem)
    if not paths:
        raise ValueError(f"no top-level Solomon .txt files in {data_dir}")
    if selected is None:
        return paths
    by_name = {path.stem: path for path in paths}
    unknown = sorted(set(selected) - by_name.keys())
    if unknown:
        raise ValueError(f"unknown instance(s) in {data_dir}: {', '.join(unknown)}")
    if len(selected) != len(set(selected)):
        raise ValueError("--instances contains duplicates")
    return [by_name[name] for name in sorted(selected)]


def _family(name: str) -> str:
    return "RC" if name.startswith("RC") else name[0]


def _rotated_variants(case_index: int) -> list[str]:
    offset = case_index % len(VARIANTS)
    return [*VARIANTS[offset:], *VARIANTS[:offset]]


def _make_row(instance_name: str, seed: int, variant: str, source_hash: str,
              input_hash: str, config_hash: str, time_limit: float,
              max_iterations: int | None, vehicle_limit: int, numeric_rule: str) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        instance=instance_name, input_format="solomon_txt", input_sha256=input_hash,
        seed=seed, threads=1, status="error", feasible=False, numeric_rule=numeric_rule,
        time_limit_seconds=time_limit, max_iterations="" if max_iterations is None else max_iterations,
        vehicle_limit=vehicle_limit, code_sha256=source_hash, diagnostics_enabled=False,
        variant=variant, config_sha256=config_hash,
    )
    return row


def run_experiment(args: argparse.Namespace) -> int:
    if args.seeds != sorted(set(args.seeds)) or not args.seeds or any(seed < 0 for seed in args.seeds):
        raise ValueError("--seeds must be unique, nonnegative integers in ascending order")
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit must be finite and positive")
    paths = _input_paths(args.data, args.instances)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)

    # Freeze the current M8 package before importing it, so later edits to
    # src/vrptw cannot affect any solve or artifact writer in this process.
    frozen_m8, m8_hash, m8_files = _freeze_current_source(out)
    if not M7_PACKAGE.is_dir():
        raise ValueError(f"archived M7 package is missing: {M7_PACKAGE}")
    m8 = _load_package(f"m8_frozen_{m8_hash[:12]}", frozen_m8)
    m7 = _load_package("m7_frozen_delivery", M7_PACKAGE)
    m8_report, m7_report = _package_report(m8), _package_report(m7)
    m8_fingerprint = m8_report.code_fingerprint()
    m7_fingerprint = m7_report.code_fingerprint()
    if m8_fingerprint != m8_hash:
        raise ValueError(f"frozen M8 fingerprint differs: {m8_fingerprint} != {m8_hash}")
    if m7_fingerprint != M7_SOURCE_HASH:
        raise ValueError(f"archived M7 fingerprint differs from directory hash: {m7_fingerprint}")

    public_api = importlib.import_module("vrptw")
    numeric_rule = m8_report.NUMERIC_RULE_ID
    # Import the public validator module directly so each persisted route is
    # re-evaluated by the currently checked-out public API.
    public_evaluate = importlib.import_module("vrptw.evaluate")
    public_validate = public_evaluate.validate_solution
    public_read = public_api.read_solomon

    input_hashes = {path.stem: _sha256(path) for path in paths}
    names = [path.stem for path in paths]
    config_dicts: dict[str, dict[str, Any]] = {}
    config_hashes: dict[str, str] = {}
    code_hashes = {"baseline": m7_fingerprint, "cached": m8_hash,
                   "incremental": m8_hash, "n20": m8_hash, "n40": m8_hash}
    for variant in VARIANTS:
        package = m7 if variant == "baseline" else m8
        config = _config_for(package, variant, 0, args.time_limit)
        config_dicts[variant] = _jsonable_config(config)
        config_hashes[variant] = _normalised_config_hash(config)

    cases = [
        {"case_index": index, "instance": path.stem, "seed": seed,
         "variants": _rotated_variants(index),
         "results": {
             variant: {"status": "pending", "verified": False,
                       "started_at_utc": None, "finished_at_utc": None,
                       "artifact": f"{variant}/{path.stem}/seed-{seed}/solution.json"}
             for variant in _rotated_variants(index)
         }}
        for index, (path, seed) in enumerate((path, seed) for path in paths for seed in args.seeds)
    ]
    started_at = _utc_now()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": started_at,
        "finished_at_utc": None,
        "data_directory": str(args.data.resolve()),
        "instances": names,
        "seeds": args.seeds,
        "time_limit_seconds": args.time_limit,
        "max_iterations": None,
        "threads": 1,
        "numeric_rule": numeric_rule,
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "input_sha256": input_hashes,
        "environment": _environment(),
        "order": {
            "case_definition": "sorted instance, then ascending seed",
            "variant_rotation": "left rotation by case_index modulo five",
            "cases": cases,
        },
        "sources": {
            "m8": {"sha256": m8_hash, "package": str(frozen_m8.resolve()),
                   "file_sha256": m8_files, "module_alias": m8.__name__},
            "m7_baseline": {"sha256": m7_fingerprint, "package": str(M7_PACKAGE.resolve()),
                            "module_alias": m7.__name__},
        },
        "variants": {
            variant: {
                "config": config_dicts[variant],
                "config_sha256": config_hashes[variant],
                "code_sha256": code_hashes[variant],
                "status": "running", "started_at_utc": None, "finished_at_utc": None,
                "runs": 0, "successful": 0,
                "failed": 0, "independently_verified": 0,
            }
            for variant in VARIANTS
        },
        "total_runs": len(cases) * len(VARIANTS),
        "independently_verified": 0,
    }
    manifest_path = out / "experiment.json"
    _write_json(manifest_path, manifest)
    records: dict[str, list[dict[str, object]]] = {variant: [] for variant in VARIANTS}
    variant_started: dict[str, str | None] = {variant: None for variant in VARIANTS}
    streams: dict[str, Any] = {}
    writers: dict[str, csv.DictWriter] = {}
    public_instances: dict[str, object] = {}

    try:
        with ExitStack() as stack:
            for variant in VARIANTS:
                variant_dir = out / variant
                variant_dir.mkdir(parents=True, exist_ok=True)
                stream = stack.enter_context((variant_dir / "batch_summary.csv").open(
                    "w", encoding="utf-8", newline="",
                ))
                writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
                writer.writeheader()
                stream.flush()
                streams[variant], writers[variant] = stream, writer

            path_by_name = {path.stem: path for path in paths}
            for case in cases:
                instance_name, seed = case["instance"], case["seed"]
                path = path_by_name[instance_name]
                if instance_name not in public_instances:
                    public_instances[instance_name] = public_read(path)
                public_instance = public_instances[instance_name]
                for variant in case["variants"]:
                    run_started = _utc_now()
                    if variant_started[variant] is None:
                        variant_started[variant] = run_started
                        manifest["variants"][variant]["started_at_utc"] = run_started
                    package = m7 if variant == "baseline" else m8
                    report = m7_report if variant == "baseline" else m8_report
                    config = _config_for(package, variant, seed, args.time_limit)
                    row = _make_row(
                        instance_name, seed, variant, code_hashes[variant], input_hashes[instance_name],
                        config_hashes[variant], args.time_limit, None,
                        public_instance.vehicle_count, numeric_rule,
                    )
                    config_values = _jsonable_config(config)
                    row["fleet_attempts_per_k"] = config_values.get("fleet_attempts_per_k", "")
                    row["max_moves"] = config_values.get("max_moves", "")
                    try:
                        solver_instance = package.read_solomon(path)
                        result = package.solve(solver_instance, config)
                        artifact_dir = out / variant / instance_name / f"seed-{seed}"
                        artifact = report.write_run(
                            solver_instance, result, config, artifact_dir,
                            source_path=path, plots=False,
                        )
                        payload = json.loads((artifact / "solution.json").read_text(encoding="utf-8"))
                        verdict = public_validate(public_instance, payload["routes"])
                        if not verdict.feasible:
                            raise ValueError(f"public validator rejected routes: {verdict.first_violation}")
                        objective = (verdict.vehicles, verdict.distance)
                        if objective != result.evaluation.objective:
                            raise ValueError("public validator objective differs from solver result")
                        if objective != (payload.get("vehicles"), payload.get("distance_ticks")):
                            raise ValueError("saved solution objective differs from public validator")
                        if payload.get("input_sha256") != input_hashes[instance_name]:
                            raise ValueError("saved solution input hash differs")
                        if payload.get("code_sha256") != code_hashes[variant]:
                            raise ValueError("saved solution code hash differs")
                        if payload.get("numeric_rule") != numeric_rule or payload.get("seed") != seed:
                            raise ValueError("saved solution numeric rule or seed differs")
                        if payload.get("config") != config_values:
                            raise ValueError("saved solution config differs")
                        row.update(
                            status="ok", feasible=True, vehicles=verdict.vehicles,
                            distance_ticks=verdict.distance, distance=verdict.distance / 1000,
                            first_feasible_seconds=round(result.first_feasible_seconds, 6),
                            runtime_seconds=round(result.runtime_seconds, 6),
                            iterations=result.iterations, stop_reason=result.stop_reason,
                        )
                        manifest["independently_verified"] += 1
                        manifest["variants"][variant]["independently_verified"] += 1
                    except Exception as exc:
                        row["error"] = f"{type(exc).__name__}: {exc}"
                    run_finished = _utc_now()
                    case["results"][variant].update(
                        status=row["status"], verified=row["status"] == "ok",
                        started_at_utc=run_started, finished_at_utc=run_finished,
                        error=row["error"],
                    )
                    manifest["variants"][variant]["finished_at_utc"] = run_finished
                    records[variant].append(row)
                    writers[variant].writerow(row)
                    streams[variant].flush()
                    state = manifest["variants"][variant]
                    state["runs"] += 1
                    if row["status"] == "ok":
                        state["successful"] += 1
                    else:
                        state["failed"] += 1
                    _write_json(manifest_path, manifest)

                print(f"finished {case['case_index'] + 1}/{len(cases)} case(s): "
                      f"{instance_name} seed={seed}; variants={','.join(case['variants'])}", flush=True)
    finally:
        for variant in VARIANTS:
            rows = records[variant]
            successful = sum(row["status"] == "ok" for row in rows)
            config0 = config_dicts[variant]
            summary = {
                "instances": len(names), "instance_names": names,
                "runs": len(rows), "successful": successful,
                "failed": len(rows) - successful, "seeds": args.seeds,
                "time_limit_seconds": args.time_limit, "max_iterations": None,
                "fleet_attempts_per_k": config0.get("fleet_attempts_per_k", ""),
                "max_moves": config0.get("max_moves", ""),
                "diagnostics_enabled": False,
                "config": config0, "config_sha256": config_hashes[variant],
                "variant": variant, "input_format": "solomon_txt", "threads": 1,
                "numeric_rule": numeric_rule, "code_sha256": code_hashes[variant],
                "runner_sha256": manifest["runner_sha256"],
                "input_sha256": input_hashes,
                "started_at_utc": variant_started[variant] or started_at,
                "finished_at_utc": manifest["variants"][variant]["finished_at_utc"] or _utc_now(),
                "environment": manifest["environment"],
                "order": manifest["order"]["variant_rotation"],
            }
            _write_json(out / variant / "batch_summary.json", summary)
            state = manifest["variants"][variant]
            if state["runs"] == len(cases) and state["failed"] == 0:
                state["status"] = "completed"
            else:
                state["status"] = "failed"
            state["batch_summary_sha256"] = _sha256(out / variant / "batch_summary.csv")
        manifest["finished_at_utc"] = _utc_now()
        manifest["status"] = "completed" if all(
            item["status"] == "completed" for item in manifest["variants"].values()
        ) else "failed"
        _write_json(manifest_path, manifest)
    return 0 if manifest["status"] == "completed" else 1


def _best_by_instance(rows: list[dict[str, str]], label: str) -> dict[str, tuple[tuple[int, int], int]]:
    best: dict[str, tuple[tuple[int, int], int]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.get("instance", ""), row.get("seed", ""))
        if key in seen:
            raise ValueError(f"{label} has duplicate instance/seed row: {key}")
        seen.add(key)
        if row.get("status") != "ok" or row.get("feasible") not in ("True", "true", "1"):
            raise ValueError(f"{label} contains a failed or infeasible row: {key}")
        objective = (int(row["vehicles"]), int(row["distance_ticks"]))
        seed = int(row["seed"])
        current = best.get(key[0])
        if current is None or objective < current[0]:
            best[key[0]] = (objective, seed)
    return best


def _comparison(base: tuple[int, int], candidate: tuple[int, int]) -> tuple[str, str, int | None, float | None]:
    if candidate[0] < base[0]:
        return "better", "not_compared", None, None
    if candidate[0] > base[0]:
        return "worse", "not_compared", None, None
    delta = candidate[1] - base[1]
    distance_result = "better" if delta < 0 else "worse" if delta > 0 else "tie"
    percent = 100 * delta / base[1] if base[1] else (0.0 if delta == 0 else None)
    return distance_result, distance_result, delta, percent


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _validate_analysis_input(experiment: Path, data_dir: Path, manifest: dict[str, Any]):
    if manifest.get("status") != "completed":
        raise ValueError(f"experiment status is {manifest.get('status')!r}; only completed runs can be analysed")
    names, seeds = manifest.get("instances", []), manifest.get("seeds", [])
    expected = {(name, str(seed)) for name in names for seed in seeds}
    if not expected:
        raise ValueError("experiment has no instance/seed coverage")
    public_api = importlib.import_module("vrptw")
    public_validate = importlib.import_module("vrptw.evaluate").validate_solution
    public_read = public_api.read_solomon
    numeric_rule = importlib.import_module("vrptw.report").NUMERIC_RULE_ID
    loaded: dict[str, tuple[dict[str, Any], list[dict[str, str]], dict[str, tuple[tuple[int, int], int]]]] = {}
    for variant in manifest["variants"]:
        batch = experiment / variant
        metadata = json.loads((batch / "batch_summary.json").read_text(encoding="utf-8"))
        rows = _read_csv(batch / "batch_summary.csv")
        actual = {(row.get("instance", ""), row.get("seed", "")) for row in rows}
        if len(actual) != len(rows) or actual != expected or metadata.get("runs") != len(rows):
            raise ValueError(f"{variant} batch coverage differs from the manifest")
        if metadata.get("numeric_rule") != numeric_rule or metadata.get("variant") != variant:
            raise ValueError(f"{variant} batch protocol differs")
        if metadata.get("config_sha256") != manifest["variants"][variant].get("config_sha256"):
            raise ValueError(f"{variant} config hash differs")
        config = metadata.get("config")
        if not isinstance(config, dict) or _canonical_hash({**config, "seed": 0}) != metadata["config_sha256"]:
            raise ValueError(f"{variant} configuration differs from its hash")
        if metadata.get("code_sha256") != manifest["variants"][variant].get("code_sha256"):
            raise ValueError(f"{variant} code hash differs")
        for row in rows:
            name, seed = row["instance"], row["seed"]
            source = data_dir / f"{name}.txt"
            input_hash = _sha256(source)
            if input_hash != row.get("input_sha256") or input_hash != manifest["input_sha256"].get(name):
                raise ValueError(f"{variant} input hash differs for {name}")
            if row.get("numeric_rule") != numeric_rule or row.get("code_sha256") != metadata["code_sha256"]:
                raise ValueError(f"{variant} row protocol differs for {name}/seed-{seed}")
            payload_path = batch / name / f"seed-{seed}" / "solution.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            verdict = public_validate(public_read(source), payload.get("routes", []))
            objective = (int(row["vehicles"]), int(row["distance_ticks"]))
            if (row.get("status") != "ok" or row.get("feasible") != "True" or not verdict.feasible
                    or verdict.objective != objective
                    or objective != (payload.get("vehicles"), payload.get("distance_ticks"))):
                raise ValueError(f"{variant} saved solution failed public validation for {name}/seed-{seed}")
            if payload.get("config") != dict(config, seed=int(seed)):
                raise ValueError(f"{variant} saved config differs for {name}/seed-{seed}")
            if payload.get("input_sha256") != input_hash or payload.get("code_sha256") != metadata["code_sha256"]:
                raise ValueError(f"{variant} saved hashes differ for {name}/seed-{seed}")
            if payload.get("numeric_rule") != numeric_rule or payload.get("seed") != int(seed):
                raise ValueError(f"{variant} saved protocol differs for {name}/seed-{seed}")
        loaded[variant] = (metadata, rows, _best_by_instance(rows, variant))
    return loaded


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment = args.out.resolve()
    data_dir = args.data.resolve()
    manifest_path = experiment / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    loaded = _validate_analysis_input(experiment, data_dir, manifest)
    if "baseline" not in loaded:
        raise ValueError("experiment has no baseline variant")
    baseline = loaded["baseline"][2]
    names = manifest["instances"]
    all_rows: list[dict[str, object]] = []
    for variant in manifest["variants"]:
        all_rows.extend({"variant": variant, **row} for row in loaded[variant][1])
    run_fields = SUMMARY_FIELDS
    _write_csv(experiment / "runs.csv", all_rows, run_fields)

    best_rows: list[dict[str, object]] = []
    metrics: dict[str, Any] = {
        "schema_version": 1,
        "experiment": str(experiment),
        "experiment_manifest_sha256": _sha256(manifest_path),
        "independently_verified_runs": sum(len(item[1]) for item in loaded.values()),
        "variants": {},
    }
    for variant in manifest["variants"]:
        metadata, rows, best = loaded[variant]
        counts = {
            "better": 0, "worse": 0, "tie": 0,
            "vehicles_better": 0, "vehicles_worse": 0, "vehicles_same": 0,
            "same_vehicle_distance_better": 0, "same_vehicle_distance_worse": 0,
            "same_vehicle_distance_tie": 0,
        }
        for name in names:
            base_obj, base_seed = baseline[name]
            candidate_obj, candidate_seed = best[name]
            result, distance_result, delta, percent = _comparison(base_obj, candidate_obj)
            counts[result] += 1
            if candidate_obj[0] < base_obj[0]:
                counts["vehicles_better"] += 1
            elif candidate_obj[0] > base_obj[0]:
                counts["vehicles_worse"] += 1
            else:
                counts["vehicles_same"] += 1
                counts[f"same_vehicle_distance_{distance_result}"] += 1
            best_rows.append({
                "variant": variant, "instance": name, "family": _family(name),
                "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
                "baseline_best_seed": base_seed, "variant_vehicles": candidate_obj[0],
                "variant_distance_ticks": candidate_obj[1], "variant_best_seed": candidate_seed,
                "result_vs_baseline": result,
                "same_vehicle_distance_result": distance_result,
                "distance_delta_ticks": "" if delta is None else delta,
                "distance_delta_percent": "" if percent is None else f"{percent:.6f}",
            })

        grouped: dict[str, list[dict[str, str]]] = {"all": rows, "C": [], "R": [], "RC": []}
        for row in rows:
            grouped[_family(row["instance"])].append(row)
        family_stats: dict[str, Any] = {}
        for family, group in grouped.items():
            runtimes = [float(row["runtime_seconds"]) for row in group]
            iterations = [int(row["iterations"]) for row in group]
            family_stats[family] = {
                "runs": len(group),
                "runtime_median_seconds": statistics.median(runtimes) if runtimes else None,
                "runtime_p90_seconds_nearest_rank": _nearest_rank(runtimes, 0.9),
                "runtime_mean_seconds": statistics.fmean(runtimes) if runtimes else None,
                "ils_runs": sum(value > 0 for value in iterations),
                "ils_iterations": sum(iterations),
                "iterations_mean": statistics.fmean(iterations) if iterations else None,
            }
        metrics["variants"][variant] = {
            "config": metadata["config"], "config_sha256": metadata["config_sha256"],
            "code_sha256": metadata["code_sha256"], "runs": len(rows),
            "comparison_vs_baseline": counts,
            "runtime_and_iterations_by_family": family_stats,
        }

    best_fields = (
        "variant", "instance", "family", "baseline_vehicles", "baseline_distance_ticks",
        "baseline_best_seed", "variant_vehicles", "variant_distance_ticks", "variant_best_seed",
        "result_vs_baseline", "same_vehicle_distance_result", "distance_delta_ticks",
        "distance_delta_percent",
    )
    _write_csv(experiment / "best_by_instance.csv", best_rows, best_fields)
    _write_json(experiment / "metrics.json", metrics)
    print(f"analysed {len(all_rows)} verified runs across {len(names)} instance(s); "
          f"wrote runs.csv, best_by_instance.csv, metrics.json in {experiment}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="freeze sources and run the M8 variants sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR, help="directory of top-level Solomon .txt files")
    run.add_argument("--out", type=Path, required=True, help="new or empty experiment directory")
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    run.add_argument("--instances", nargs="+", help="optional instance stems for a short smoke run")

    analyse = commands.add_parser("analyse", help="validate artifacts and write comparison tables")
    analyse.add_argument("--out", type=Path, required=True,
                         help="completed experiment directory; analysis files are written here")
    analyse.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return run_experiment(args)
        return analyse_experiment(args)
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
