"""Run and analyse the sequential M9 fixed-fleet neighbourhood ablation.

Default full run (all 56 Solomon instances, seeds 0/1/2)::

    python benchmarks/m9_experiment.py run --out runs/m9_search_seed012_0p5s
    python benchmarks/m9_experiment.py analyse --out runs/m9_search_seed012_0p5s

Variants are executed in a rotating order. The baseline loads the separately
frozen M8 package; all M9 candidates load a source snapshot made at run start.
Importing this module never starts an experiment.
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
from pathlib import Path
import statistics
import sys
import uuid
from typing import Any

SOLVER_DIR = Path(__file__).resolve().parents[1]
if str(SOLVER_DIR) not in sys.path:
    sys.path.insert(0, str(SOLVER_DIR))
from benchmarks import m8_experiment as m8  # noqa: E402  (shared stable artifact helpers)

SRC_PACKAGE = SOLVER_DIR / "src" / "vrptw"
DATA_DIR = SOLVER_DIR / "data"
BASELINE_ROOT = SOLVER_DIR / "benchmarks" / "m9_baseline" / "source"

BASE_OPERATORS = ("relocate", "swap", "two_opt", "two_opt_star")
ADDED_OPERATORS = ("relocate_pair", "exchange_pair_single", "exchange_pairs")
SINGLE_VARIANTS = ADDED_OPERATORS
VARIANTS = ("baseline", "uncapped", *SINGLE_VARIANTS, "combined")
CYCLIC_VARIANTS = ("cyclic", "cyclic_single", "cyclic_pairs", "cyclic_combined")
ALL_VARIANTS = (*VARIANTS, *CYCLIC_VARIANTS)
OPERATOR_VARIANTS = (*SINGLE_VARIANTS, "combined", *CYCLIC_VARIANTS)
VARIANT_OPERATORS = {
    "baseline": BASE_OPERATORS,
    "uncapped": BASE_OPERATORS,
    "relocate_pair": ("relocate_pair", *BASE_OPERATORS),
    "exchange_pair_single": ("exchange_pair_single", *BASE_OPERATORS),
    "exchange_pairs": ("exchange_pairs", *BASE_OPERATORS),
    "combined": (*ADDED_OPERATORS, *BASE_OPERATORS),
    "cyclic": BASE_OPERATORS,
    "cyclic_single": ("exchange_pair_single", *BASE_OPERATORS),
    "cyclic_pairs": ("exchange_pairs", *BASE_OPERATORS),
    "cyclic_combined": (*ADDED_OPERATORS, *BASE_OPERATORS),
}

SUMMARY_FIELDS = m8.SUMMARY_FIELDS
DIAGNOSTIC_FIELDS = (
    "phase", "elapsed_seconds", "inclusive_seconds", "calls", "candidates",
    "feasible_candidates", "infeasible_candidates", "route_evaluations",
    "validation_calls", "accepted", "capacity_prefilter_skips", "rejected_capacity",
    "rejected_time_window", "rejected_depot_close", "rejected_empty_route", "trials",
    "failures", "removed_customers", "route_cache_builds", "incremental_route_evaluations",
    "reused_prefix_customers", "reused_suffix_customers", "neighbour_filtered",
)
DIAGNOSTIC_BATCH_FIELDS = ("instance", "seed", "variant", *DIAGNOSTIC_FIELDS)
VARIANT_DIAGNOSTIC_FIELDS = ("instance", "seed", *DIAGNOSTIC_FIELDS)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_hash(package_dir: Path) -> tuple[str, dict[str, str]]:
    paths = sorted(package_dir.glob("*.py"))
    if not paths:
        raise ValueError(f"no Python source files found in {package_dir}")
    contents = {path.name: path.read_bytes() for path in paths}
    return m8._code_hash(contents), {
        name: m8._sha256_bytes(value) for name, value in sorted(contents.items())
    }


def _find_m8_baseline() -> tuple[Path, str, dict[str, str]]:
    packages = sorted(BASELINE_ROOT.glob("*/vrptw"))
    if len(packages) != 1:
        raise ValueError(
            f"expected exactly one frozen M8 package under {BASELINE_ROOT}, found {len(packages)}"
        )
    package = packages[0]
    source_hash, file_hashes = _code_hash(package)
    expected_hash = package.parent.name
    if source_hash != expected_hash:
        raise ValueError(f"frozen M8 directory hash differs: {source_hash} != {expected_hash}")
    return package, source_hash, file_hashes


def _load_package(alias: str, package_dir: Path):
    # Use the shared loader, but unique aliases keep repeat runs isolated from
    # any previously imported frozen package in this Python process.
    return m8._load_package(alias, package_dir)


def _config_for(package, variant: str, seed: int, time_limit: float,
                diagnostics: bool):
    if variant not in VARIANT_OPERATORS:
        raise ValueError(f"unknown M9 variant {variant!r}")
    if variant == "baseline":
        operators = tuple(package.local_search.OPERATORS)
        if operators != BASE_OPERATORS:
            raise ValueError(f"frozen M8 operator order differs: {operators!r}")
        max_moves = 2
    else:
        operators = VARIANT_OPERATORS[variant]
        supported = set(package.local_search.OPERATORS)
        missing = set(operators) - supported
        if missing:
            raise ValueError(f"current M9 solver does not support operators: {sorted(missing)}")
        max_moves = None
    values = dict(
        seed=seed,
        max_iterations=None,
        time_limit_seconds=time_limit,
        fleet_attempts_per_k=100,
        fleet_time_fraction=0.95,
        max_moves=max_moves,
        construction_order="due",
        repair_order="due",
        repair_strategy="cheapest",
        search_strategy="first",
        operators=operators,
        remove_min=3,
        remove_max=8,
        restart_after=20,
        diagnostics=diagnostics,
        evaluation_mode="incremental",
        num_neighbours=None,
    )
    config_fields = getattr(package.Config, "__dataclass_fields__", {})
    # M8's frozen Config predates operator scheduling. Older frozen M9
    # packages also need to remain analyzable with their original config.
    if variant != "baseline" and "operator_schedule" in config_fields:
        values["operator_schedule"] = "cyclic" if variant in CYCLIC_VARIANTS else "fixed"
    elif variant in CYCLIC_VARIANTS:
        raise ValueError("cyclic variants require Config.operator_schedule support")
    return package.Config(**values)


def _jsonable_config(config: object) -> dict[str, Any]:
    return m8._jsonable_config(config)


def _normalised_config_hash(config: object) -> str:
    return m8._normalised_config_hash(config)


def _rotated_variants(case_index: int, variants: tuple[str, ...] | list[str]) -> list[str]:
    offset = case_index % len(variants)
    return [*variants[offset:], *variants[:offset]]


def _selected_variants(selected: list[str] | None) -> tuple[str, ...]:
    variants = tuple(VARIANTS if selected is None else selected)
    if not variants or len(variants) != len(set(variants)):
        raise ValueError("--variants must contain unique variant names")
    unknown = sorted(set(variants) - set(ALL_VARIANTS))
    if unknown:
        raise ValueError(f"unknown M9 variant(s): {', '.join(unknown)}")
    if "baseline" not in variants:
        raise ValueError("--variants must include baseline")
    if any(name in variants for name in OPERATOR_VARIANTS) and "uncapped" not in variants:
        raise ValueError("--variants with M9 operators must include uncapped for comparison")
    return variants


def _source_entry(package, source_hash: str, file_hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "sha256": source_hash,
        "package": str(Path(package.__path__[0]).resolve()),
        "file_sha256": file_hashes,
        "module_alias": package.__name__,
    }


def _make_row(instance_name: str, seed: int, variant: str, source_hash: str,
              input_hash: str, config_hash: str, time_limit: float,
              vehicle_limit: int, numeric_rule: str, diagnostics: bool) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        instance=instance_name, input_format="solomon_txt", input_sha256=input_hash,
        seed=seed, threads=1, status="error", feasible=False, numeric_rule=numeric_rule,
        time_limit_seconds=time_limit, max_iterations="", vehicle_limit=vehicle_limit,
        code_sha256=source_hash, diagnostics_enabled=diagnostics,
        variant=variant, config_sha256=config_hash,
    )
    return row


def run_experiment(args: argparse.Namespace) -> int:
    if args.seeds != sorted(set(args.seeds)) or not args.seeds or any(seed < 0 for seed in args.seeds):
        raise ValueError("--seeds must be unique, nonnegative integers in ascending order")
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit must be finite and positive")
    variants = _selected_variants(args.variants)
    paths = m8._input_paths(args.data, args.instances)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)

    baseline_dir, baseline_hash, baseline_files = _find_m8_baseline()
    current_dir, current_hash, current_files = m8._freeze_current_source(out)
    run_id = uuid.uuid4().hex[:12]
    baseline = _load_package(f"m9_m8_{run_id}", baseline_dir)
    current = _load_package(f"m9_current_{run_id}", current_dir)
    baseline_report = m8._package_report(baseline)
    current_report = m8._package_report(current)
    if baseline_report.code_fingerprint() != baseline_hash:
        raise ValueError("frozen M8 report fingerprint differs from its source hash")
    if current_report.code_fingerprint() != current_hash:
        raise ValueError("frozen M9 report fingerprint differs from its source hash")
    if tuple(baseline.local_search.OPERATORS) != BASE_OPERATORS:
        raise ValueError("frozen M8 baseline does not contain the expected original four operators")
    required_current = set(BASE_OPERATORS) | set(ADDED_OPERATORS)
    missing_current = required_current - set(current.local_search.OPERATORS)
    if missing_current:
        raise ValueError(f"current M9 source is missing operators: {sorted(missing_current)}")

    public_api = __import__("vrptw")
    public_read = public_api.read_solomon
    public_validate = __import__("vrptw.evaluate", fromlist=["validate_solution"]).validate_solution
    numeric_rule = current_report.NUMERIC_RULE_ID
    input_hashes = {path.stem: m8._sha256(path) for path in paths}
    names = [path.stem for path in paths]

    package_for = {"baseline": baseline, **{variant: current for variant in variants if variant != "baseline"}}
    report_for = {"baseline": baseline_report, **{variant: current_report for variant in variants if variant != "baseline"}}
    config_dicts: dict[str, dict[str, Any]] = {}
    config_hashes: dict[str, str] = {}
    code_hashes: dict[str, str] = {}
    for variant in variants:
        config = _config_for(package_for[variant], variant, 0, args.time_limit, args.diagnostics)
        config_dicts[variant] = _jsonable_config(config)
        config_hashes[variant] = _normalised_config_hash(config)
        code_hashes[variant] = baseline_hash if variant == "baseline" else current_hash

    cases = []
    for index, (path, seed) in enumerate((path, seed) for path in paths for seed in args.seeds):
        order = _rotated_variants(index, variants)
        cases.append({
            "case_index": index, "instance": path.stem, "seed": seed,
            "variants": order,
            "results": {
                variant: {
                    "status": "pending", "verified": False,
                    "started_at_utc": None, "finished_at_utc": None,
                    "artifact": f"{variant}/{path.stem}/seed-{seed}/solution.json",
                    "artifact_sha256": {},
                }
                for variant in order
            },
        })

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
        "diagnostics_enabled": args.diagnostics,
        "numeric_rule": numeric_rule,
        "runner_sha256": m8._sha256(Path(__file__).resolve()),
        "input_sha256": input_hashes,
        "environment": m8._environment(),
        "order": {
            "case_definition": "sorted instance, then ascending seed",
            "variant_rotation": "left rotation by case_index modulo selected variants",
            "variants": variants,
            "cases": cases,
        },
        "sources": {
            "m8_baseline": _source_entry(baseline, baseline_hash, baseline_files),
            "m9": _source_entry(current, current_hash, current_files),
        },
        "variants": {
            variant: {
                "config": config_dicts[variant],
                "config_sha256": config_hashes[variant],
                "code_sha256": code_hashes[variant],
                "source": "m8_baseline" if variant == "baseline" else "m9",
                "status": "running", "started_at_utc": None, "finished_at_utc": None,
                "runs": 0, "successful": 0, "failed": 0, "independently_verified": 0,
            }
            for variant in variants
        },
        "total_runs": len(cases) * len(variants),
        "independently_verified": 0,
    }
    manifest_path = out / "experiment.json"
    m8._write_json(manifest_path, manifest)
    records: dict[str, list[dict[str, object]]] = {variant: [] for variant in variants}
    diagnostic_records: list[dict[str, object]] = []
    diagnostic_records_by_variant: dict[str, list[dict[str, object]]] = {
        variant: [] for variant in variants
    }
    variant_started: dict[str, str | None] = {variant: None for variant in variants}
    streams: dict[str, Any] = {}
    writers: dict[str, csv.DictWriter] = {}
    public_instances: dict[str, object] = {}

    try:
        with ExitStack() as stack:
            for variant in variants:
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
                    package, report = package_for[variant], report_for[variant]
                    config = _config_for(package, variant, seed, args.time_limit, args.diagnostics)
                    config_values = _jsonable_config(config)
                    row = _make_row(
                        instance_name, seed, variant, code_hashes[variant], input_hashes[instance_name],
                        config_hashes[variant], args.time_limit, public_instance.vehicle_count,
                        numeric_rule, args.diagnostics,
                    )
                    row["fleet_attempts_per_k"] = config_values.get("fleet_attempts_per_k", "")
                    row["max_moves"] = "" if config_values.get("max_moves") is None else config_values["max_moves"]
                    artifact_dir = out / variant / instance_name / f"seed-{seed}"
                    try:
                        solver_instance = package.read_solomon(path)
                        result = package.solve(solver_instance, config)
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
                        result_entry = case["results"][variant]
                        artifacts = ["solution.json", "routes.sol", "history.csv"]
                        if args.diagnostics:
                            artifacts.append("diagnostics.csv")
                            for phase in report.diagnostic_rows(result):
                                diagnostic_row = {
                                    "instance": instance_name, "seed": seed, "variant": variant, **phase,
                                }
                                diagnostic_records.append(diagnostic_row)
                                diagnostic_records_by_variant[variant].append(diagnostic_row)
                        result_entry["artifact_sha256"] = {
                            name: m8._sha256(artifact / name) for name in artifacts
                        }
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
                    m8._write_json(manifest_path, manifest)
                print(
                    f"finished {case['case_index'] + 1}/{len(cases)} case(s): "
                    f"{instance_name} seed={seed}; variants={','.join(case['variants'])}",
                    flush=True,
                )
    finally:
        for variant in variants:
            rows = records[variant]
            config0 = config_dicts[variant]
            summary = {
                "instances": len(names), "instance_names": names,
                "runs": len(rows), "successful": sum(row["status"] == "ok" for row in rows),
                "failed": sum(row["status"] != "ok" for row in rows),
                "seeds": args.seeds, "time_limit_seconds": args.time_limit,
                "max_iterations": None, "fleet_attempts_per_k": config0.get("fleet_attempts_per_k", ""),
                "max_moves": config0.get("max_moves"), "diagnostics_enabled": args.diagnostics,
                "config": config0, "config_sha256": config_hashes[variant],
                "variant": variant, "input_format": "solomon_txt", "threads": 1,
                "numeric_rule": numeric_rule, "code_sha256": code_hashes[variant],
                "runner_sha256": manifest["runner_sha256"], "input_sha256": input_hashes,
                "started_at_utc": variant_started[variant] or started_at,
                "finished_at_utc": manifest["variants"][variant]["finished_at_utc"] or _utc_now(),
                "environment": manifest["environment"],
                "order": manifest["order"]["variant_rotation"],
            }
            batch_dir = out / variant
            summary_path = batch_dir / "batch_summary.json"
            m8._write_json(summary_path, summary)
            state = manifest["variants"][variant]
            state["status"] = (
                "completed" if state["runs"] == len(cases) and state["failed"] == 0 else "failed"
            )
            state["batch_summary_sha256"] = m8._sha256(batch_dir / "batch_summary.csv")
            state["batch_metadata_sha256"] = m8._sha256(summary_path)
        if args.diagnostics:
            for variant in variants:
                variant_rows = [
                    {key: row[key] for key in VARIANT_DIAGNOSTIC_FIELDS}
                    for row in diagnostic_records_by_variant[variant]
                ]
                diagnostic_path = out / variant / "batch_diagnostics.csv"
                m8._write_csv(diagnostic_path, variant_rows, VARIANT_DIAGNOSTIC_FIELDS)
                manifest["variants"][variant]["batch_diagnostics_sha256"] = m8._sha256(
                    diagnostic_path
                )
            m8._write_csv(out / "batch_diagnostics.csv", diagnostic_records, DIAGNOSTIC_BATCH_FIELDS)
            manifest["batch_diagnostics_sha256"] = m8._sha256(out / "batch_diagnostics.csv")
        manifest["finished_at_utc"] = _utc_now()
        manifest["status"] = "completed" if all(
            value["status"] == "completed" for value in manifest["variants"].values()
        ) else "failed"
        m8._write_json(manifest_path, manifest)
    return 0 if manifest["status"] == "completed" else 1


def _load_verified_sources(experiment: Path, manifest: dict[str, Any]):
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"m8_baseline", "m9"}:
        raise ValueError("manifest source records are incomplete")
    loaded = {}
    for key in ("m8_baseline", "m9"):
        entry = sources[key]
        package_dir = Path(entry.get("package", "")).resolve()
        expected_root = BASELINE_ROOT.resolve() if key == "m8_baseline" else (experiment / "source").resolve()
        try:
            package_dir.relative_to(expected_root)
        except ValueError as exc:
            raise ValueError(f"{key} package is outside its frozen source directory") from exc
        if package_dir.name != "vrptw":
            raise ValueError(f"{key} package path does not end in vrptw")
        source_hash, file_hashes = _code_hash(package_dir)
        if source_hash != entry.get("sha256") or source_hash != package_dir.parent.name:
            raise ValueError(f"{key} source fingerprint differs")
        if file_hashes != entry.get("file_sha256"):
            raise ValueError(f"{key} source file hashes differ")
        alias = f"m9_analyse_{key}_{uuid.uuid4().hex[:12]}"
        package = _load_package(alias, package_dir)
        report = m8._package_report(package)
        if report.code_fingerprint() != source_hash:
            raise ValueError(f"{key} report fingerprint differs")
        loaded[key] = (package, report, source_hash)
    return loaded


def _expected_artifact_hashes(case_result: dict[str, Any], variant: str,
                              diagnostics: bool) -> None:
    required = {"solution.json", "routes.sol", "history.csv"}
    if diagnostics:
        required.add("diagnostics.csv")
    hashes = case_result.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != required:
        raise ValueError(f"{variant} artifact hash record is incomplete")


def _validate_analysis_input(experiment: Path, data_dir: Path,
                             manifest: dict[str, Any]):
    if manifest.get("status") != "completed":
        raise ValueError(f"experiment status is {manifest.get('status')!r}; only completed runs can be analysed")
    variants = tuple(manifest.get("order", {}).get("variants", ()))
    if not variants or variants != tuple(manifest.get("variants", {})):
        raise ValueError("variant order differs between manifest sections")
    if "baseline" not in variants:
        raise ValueError("experiment has no baseline variant")
    if any(name in variants for name in OPERATOR_VARIANTS) and "uncapped" not in variants:
        raise ValueError("M9 operator variants require the uncapped comparison")
    if set(variants) - set(ALL_VARIANTS):
        raise ValueError("manifest contains an unknown variant")

    names, seeds = manifest.get("instances", []), manifest.get("seeds", [])
    time_limit = manifest.get("time_limit_seconds")
    if (type(time_limit) not in (int, float) or not math.isfinite(time_limit)
            or time_limit <= 0):
        raise ValueError("manifest has an invalid time limit")
    expected_pairs = [(name, seed) for name in names for seed in seeds]
    if not expected_pairs or len(set(expected_pairs)) != len(expected_pairs):
        raise ValueError("experiment has invalid instance/seed coverage")
    cases = manifest.get("order", {}).get("cases", [])
    if len(cases) != len(expected_pairs) or manifest.get("total_runs") != len(cases) * len(variants):
        raise ValueError("manifest case count differs from its declared coverage")
    case_by_pair = {}
    for index, (case, expected_pair) in enumerate(zip(cases, expected_pairs)):
        pair = (case.get("instance"), case.get("seed"))
        if case.get("case_index") != index or pair != expected_pair:
            raise ValueError("manifest case order differs from instance/seed order")
        if case.get("variants") != _rotated_variants(index, variants):
            raise ValueError(f"variant rotation differs for case {index}")
        if set(case.get("results", {})) != set(variants):
            raise ValueError(f"case {index} does not record all variants")
        case_by_pair[pair] = case

    frozen = _load_verified_sources(experiment, manifest)
    m8_api = frozen["m8_baseline"][0]
    m9_api = frozen["m9"][0]
    if tuple(m8_api.local_search.OPERATORS) != BASE_OPERATORS:
        raise ValueError("frozen baseline operator order is not the M8 order")
    if set(BASE_OPERATORS + ADDED_OPERATORS) - set(m9_api.local_search.OPERATORS):
        raise ValueError("frozen M9 source is missing expected operators")

    public_api = __import__("vrptw")
    public_read = public_api.read_solomon
    public_validate = __import__("vrptw.evaluate", fromlist=["validate_solution"]).validate_solution
    numeric_rule = frozen["m9"][1].NUMERIC_RULE_ID
    expected_phases = tuple(m9_api.diagnostics.PHASES)
    if manifest.get("numeric_rule") != numeric_rule:
        raise ValueError("manifest numeric rule differs from frozen M9 source")

    loaded: dict[str, tuple[dict[str, Any], list[dict[str, str]], dict[str, tuple[tuple[int, int], int]]]] = {}
    independent_count = 0
    for variant in variants:
        state = manifest["variants"].get(variant, {})
        expected_source = "m8_baseline" if variant == "baseline" else "m9"
        if state.get("source") != expected_source:
            raise ValueError(f"{variant} source label differs")
        source_hash = frozen[expected_source][2]
        if state.get("code_sha256") != source_hash:
            raise ValueError(f"{variant} code hash differs")
        metadata_path = experiment / variant / "batch_summary.json"
        csv_path = experiment / variant / "batch_summary.csv"
        if m8._sha256(metadata_path) != state.get("batch_metadata_sha256"):
            raise ValueError(f"{variant} batch metadata hash differs")
        if m8._sha256(csv_path) != state.get("batch_summary_sha256"):
            raise ValueError(f"{variant} batch summary hash differs")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        rows = m8._read_csv(csv_path)
        row_map = {(row.get("instance", ""), row.get("seed", "")): row for row in rows}
        expected_keys = {(name, str(seed)) for name, seed in expected_pairs}
        if len(row_map) != len(rows) or set(row_map) != expected_keys or metadata.get("runs") != len(rows):
            raise ValueError(f"{variant} batch coverage differs from the manifest")
        config = metadata.get("config")
        config_hash = m8._canonical_hash({**config, "seed": 0}) if isinstance(config, dict) else None
        expected_package = m8_api if variant == "baseline" else m9_api
        expected_config = _jsonable_config(_config_for(
            expected_package, variant, 0, time_limit, bool(manifest.get("diagnostics_enabled")),
        ))
        if (metadata.get("variant") != variant or metadata.get("numeric_rule") != numeric_rule
                or metadata.get("code_sha256") != source_hash
                or metadata.get("config_sha256") != state.get("config_sha256")
                or state.get("config_sha256") != config_hash
                or config != state.get("config") or config != expected_config):
            raise ValueError(f"{variant} batch protocol or config hash differs")
        config_values = config
        if config_values.get("max_moves") != (2 if variant == "baseline" else None):
            raise ValueError(f"{variant} has unexpected max_moves setting")
        if tuple(config_values.get("operators", ())) != VARIANT_OPERATORS[variant]:
            raise ValueError(f"{variant} operator set/order differs")
        if bool(config_values.get("diagnostics")) != bool(manifest.get("diagnostics_enabled")):
            raise ValueError(f"{variant} diagnostics setting differs")
        if (metadata.get("diagnostics_enabled") != bool(manifest.get("diagnostics_enabled"))
                or float(metadata.get("time_limit_seconds", -1)) != float(time_limit)
                or metadata.get("max_iterations") is not None):
            raise ValueError(f"{variant} batch budget or diagnostic metadata differs")
        if metadata.get("input_sha256") != manifest.get("input_sha256"):
            raise ValueError(f"{variant} input hash map differs")

        variant_diag_index: dict[tuple[str, str, str], dict[str, str]] = {}
        if manifest.get("diagnostics_enabled"):
            variant_diag_path = experiment / variant / "batch_diagnostics.csv"
            if m8._sha256(variant_diag_path) != state.get("batch_diagnostics_sha256"):
                raise ValueError(f"{variant} batch diagnostics hash differs")
            variant_diag_rows = m8._read_csv(variant_diag_path)
            variant_diag_index = {
                (item.get("instance", ""), item.get("seed", ""), item.get("phase", "")): item
                for item in variant_diag_rows
            }
            expected_diag_keys = {
                (name, str(seed), phase)
                for name, seed in expected_pairs for phase in expected_phases
            }
            if (len(variant_diag_index) != len(variant_diag_rows)
                    or set(variant_diag_index) != expected_diag_keys):
                raise ValueError(f"{variant} batch diagnostics phase coverage differs")

        for name, seed in expected_pairs:
            row = row_map[(name, str(seed))]
            source = data_dir / f"{name}.txt"
            input_hash = m8._sha256(source)
            if (input_hash != row.get("input_sha256")
                    or input_hash != manifest.get("input_sha256", {}).get(name)):
                raise ValueError(f"{variant} input hash differs for {name}")
            if (row.get("status") != "ok" or row.get("feasible") != "True"
                    or row.get("numeric_rule") != numeric_rule
                    or row.get("code_sha256") != source_hash
                    or row.get("config_sha256") != state["config_sha256"]
                    or row.get("variant") != variant
                    or row.get("diagnostics_enabled") not in ("True", "False")):
                raise ValueError(f"{variant} row protocol differs for {name}/seed-{seed}")
            if float(row.get("time_limit_seconds", -1)) != float(time_limit):
                raise ValueError(f"{variant} row budget differs for {name}/seed-{seed}")
            if (row.get("diagnostics_enabled") == "True") != bool(manifest.get("diagnostics_enabled")):
                raise ValueError(f"{variant} row diagnostics differ for {name}/seed-{seed}")
            case_result = case_by_pair[(name, seed)]["results"][variant]
            if case_result.get("status") != "ok" or case_result.get("verified") is not True:
                raise ValueError(f"{variant} manifest run is not verified for {name}/seed-{seed}")
            _expected_artifact_hashes(case_result, variant, bool(manifest.get("diagnostics_enabled")))
            artifact_rel = case_result.get("artifact")
            expected_artifact = f"{variant}/{name}/seed-{seed}/solution.json"
            if artifact_rel != expected_artifact:
                raise ValueError(f"{variant} artifact path differs for {name}/seed-{seed}")
            artifact_dir = experiment / variant / name / f"seed-{seed}"
            for filename, expected_hash in case_result["artifact_sha256"].items():
                if m8._sha256(artifact_dir / filename) != expected_hash:
                    raise ValueError(f"{variant} {filename} hash differs for {name}/seed-{seed}")
            payload = json.loads((artifact_dir / "solution.json").read_text(encoding="utf-8"))
            verdict = public_validate(public_read(source), payload.get("routes", []))
            objective = (int(row["vehicles"]), int(row["distance_ticks"]))
            if (not verdict.feasible or verdict.objective != objective
                    or objective != (payload.get("vehicles"), payload.get("distance_ticks"))):
                raise ValueError(f"{variant} saved solution failed public validation for {name}/seed-{seed}")
            if payload.get("config") != dict(config, seed=int(seed)):
                raise ValueError(f"{variant} saved config differs for {name}/seed-{seed}")
            stop = payload.get("stop")
            if (not isinstance(stop, dict)
                    or float(stop.get("time_limit_seconds", -1)) != float(time_limit)
                    or stop.get("max_iterations") is not None):
                raise ValueError(f"{variant} saved budget differs for {name}/seed-{seed}")
            if (payload.get("input_sha256") != input_hash
                    or payload.get("code_sha256") != source_hash
                    or payload.get("numeric_rule") != numeric_rule
                    or payload.get("seed") != int(seed)):
                raise ValueError(f"{variant} saved solution hashes/protocol differ for {name}/seed-{seed}")
            if manifest.get("diagnostics_enabled"):
                diag = payload.get("diagnostics")
                phases = diag.get("phases") if isinstance(diag, dict) else None
                if (not isinstance(diag, dict) or diag.get("schema_version") != 1
                        or not isinstance(phases, list)
                        or tuple(value.get("phase") for value in phases) != expected_phases):
                    raise ValueError(f"{variant} saved diagnostics differ for {name}/seed-{seed}")
                runtime = float(payload["runtime_seconds"])
                if not math.isclose(
                        sum(float(value["elapsed_seconds"]) for value in phases), runtime,
                        rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError(f"{variant} phase times do not match runtime for {name}/seed-{seed}")
                run_diag_rows = {
                    item.get("phase", ""): item
                    for item in m8._read_csv(artifact_dir / "diagnostics.csv")
                }
                if set(run_diag_rows) != set(expected_phases):
                    raise ValueError(f"{variant} diagnostics.csv phases differ for {name}/seed-{seed}")
                for phase in phases:
                    if (phase["candidates"] != phase["feasible_candidates"] + phase["infeasible_candidates"]
                            or not 0 <= phase["elapsed_seconds"] <= phase["inclusive_seconds"] + 1e-9):
                        raise ValueError(f"{variant} diagnostic counters/timing invalid for {name}/seed-{seed}")
                    for field in DIAGNOSTIC_FIELDS:
                        if field == "phase":
                            continue
                        expected_value = float(phase[field])
                        for csv_row in (run_diag_rows[phase["phase"]],
                                        variant_diag_index[(name, str(seed), phase["phase"])]):
                            if not math.isclose(float(csv_row[field]), expected_value,
                                                rel_tol=0.0, abs_tol=1e-9):
                                raise ValueError(
                                    f"{variant} JSON/CSV diagnostics differ for {name}/seed-{seed}/"
                                    f"{phase['phase']}/{field}"
                                )
            independent_count += 1
        loaded[variant] = (metadata, rows, m8._best_by_instance(rows, variant))

    if manifest.get("independently_verified") != independent_count:
        raise ValueError("manifest independent verification count differs")
    for variant in variants:
        state = manifest["variants"][variant]
        count = sum(row.get("status") == "ok" for row in loaded[variant][1])
        if (state.get("status") != "completed" or state.get("runs") != len(expected_pairs)
                or state.get("successful") != count or state.get("failed") != 0
                or state.get("independently_verified") != count):
            raise ValueError(f"{variant} completion counts differ")

    if manifest.get("diagnostics_enabled"):
        diagnostic_path = experiment / "batch_diagnostics.csv"
        if m8._sha256(diagnostic_path) != manifest.get("batch_diagnostics_sha256"):
            raise ValueError("batch diagnostics hash differs")
        diagnostic_rows = m8._read_csv(diagnostic_path)
        if len(diagnostic_rows) != independent_count * len(expected_phases):
            raise ValueError("batch diagnostics phase coverage differs")
        phases: dict[tuple[str, str, str], set[str]] = {}
        for row in diagnostic_rows:
            key = (row["instance"], row["seed"], row["variant"])
            phases.setdefault(key, set()).add(row["phase"])
        if any(values != set(expected_phases)
               for values in phases.values()) or len(phases) != independent_count:
            raise ValueError("batch diagnostics phases differ")
    return loaded


def _compare(base: tuple[int, int], candidate: tuple[int, int]):
    if candidate[0] < base[0]:
        return "better", "not_compared", None, None
    if candidate[0] > base[0]:
        return "worse", "not_compared", None, None
    delta = candidate[1] - base[1]
    distance = "better" if delta < 0 else "worse" if delta > 0 else "tie"
    percent = 100 * delta / base[1] if base[1] else (0.0 if delta == 0 else None)
    return distance, distance, delta, percent


def _empty_comparison() -> dict[str, int]:
    return {
        "better": 0, "worse": 0, "tie": 0,
        "vehicles_better": 0, "vehicles_worse": 0, "vehicles_same": 0,
        "same_vehicle_distance_better": 0, "same_vehicle_distance_worse": 0,
        "same_vehicle_distance_tie": 0,
    }


def _record_comparison(counts: dict[str, int], base: tuple[int, int], candidate: tuple[int, int]) -> None:
    result, distance_result, _, _ = _compare(base, candidate)
    counts[result] += 1
    if candidate[0] < base[0]:
        counts["vehicles_better"] += 1
    elif candidate[0] > base[0]:
        counts["vehicles_worse"] += 1
    else:
        counts["vehicles_same"] += 1
        counts[f"same_vehicle_distance_{distance_result}"] += 1


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment = args.out.resolve()
    data_dir = args.data.resolve()
    manifest_path = experiment / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    loaded = _validate_analysis_input(experiment, data_dir, manifest)
    variants = tuple(manifest["order"]["variants"])
    baseline = loaded["baseline"][2]
    uncapped = loaded.get("uncapped", (None, None, None))[2]
    cyclic = loaded.get("cyclic", (None, None, None))[2]
    names = manifest["instances"]

    all_rows: list[dict[str, object]] = []
    for variant in variants:
        all_rows.extend({"variant": variant, **row} for row in loaded[variant][1])
    m8._write_csv(experiment / "runs.csv", all_rows, SUMMARY_FIELDS)

    best_rows: list[dict[str, object]] = []
    metrics: dict[str, Any] = {
        "schema_version": 1,
        "experiment": str(experiment),
        "experiment_manifest_sha256": m8._sha256(manifest_path),
        "independently_verified_runs": sum(len(item[1]) for item in loaded.values()),
        "comparison_references": [name for name, value in (("baseline", baseline), ("uncapped", uncapped),
                                                         ("cyclic", cyclic))
                                  if value is not None],
        "variants": {},
    }
    for variant in variants:
        metadata, rows, best = loaded[variant]
        references = {"baseline": baseline}
        if uncapped is not None:
            references["uncapped"] = uncapped
        if cyclic is not None:
            references["cyclic"] = cyclic
        counts_by_reference: dict[str, dict[str, int]] = {}
        family_counts: dict[str, dict[str, dict[str, int]]] = {}
        for reference_name, reference in references.items():
            counts_by_reference[reference_name] = _empty_comparison()
            family_counts[reference_name] = {
                family: _empty_comparison() for family in ("all", "C", "R", "RC")
            }

        for name in names:
            candidate_obj, candidate_seed = best[name]
            base_obj, base_seed = baseline[name]
            uncapped_obj, uncapped_seed = uncapped[name] if uncapped is not None else (None, None)
            family = m8._family(name)
            values: dict[str, object] = {
                "variant": variant, "instance": name, "family": family,
                "variant_vehicles": candidate_obj[0], "variant_distance_ticks": candidate_obj[1],
                "variant_best_seed": candidate_seed,
                "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
                "baseline_best_seed": base_seed,
            }
            for ref_name, reference in references.items():
                ref_obj, ref_seed = reference[name]
                result, distance_result, delta, percent = _compare(ref_obj, candidate_obj)
                _record_comparison(counts_by_reference[ref_name], ref_obj, candidate_obj)
                _record_comparison(family_counts[ref_name]["all"], ref_obj, candidate_obj)
                _record_comparison(family_counts[ref_name][family], ref_obj, candidate_obj)
                values.update({
                    f"{ref_name}_vehicles": ref_obj[0],
                    f"{ref_name}_distance_ticks": ref_obj[1],
                    f"{ref_name}_best_seed": ref_seed,
                    f"result_vs_{ref_name}": result,
                    f"same_vehicle_distance_result_vs_{ref_name}": distance_result,
                    f"distance_delta_ticks_vs_{ref_name}": "" if delta is None else delta,
                    f"distance_delta_percent_vs_{ref_name}": "" if percent is None else f"{percent:.6f}",
                })
            best_rows.append(values)

        grouped: dict[str, list[dict[str, str]]] = {"all": rows, "C": [], "R": [], "RC": []}
        for row in rows:
            grouped[m8._family(row["instance"])].append(row)
        family_runtime: dict[str, Any] = {}
        for family, group in grouped.items():
            runtimes = [float(row["runtime_seconds"]) for row in group]
            iterations = [int(row["iterations"]) for row in group]
            family_runtime[family] = {
                "runs": len(group),
                "runtime_median_seconds": statistics.median(runtimes) if runtimes else None,
                "runtime_p90_seconds_nearest_rank": m8._nearest_rank(runtimes, 0.9),
                "runtime_mean_seconds": statistics.fmean(runtimes) if runtimes else None,
                "ils_runs": sum(value > 0 for value in iterations),
                "ils_iterations": sum(iterations),
                "iterations_mean": statistics.fmean(iterations) if iterations else None,
            }
        metrics["variants"][variant] = {
            "config": metadata["config"], "config_sha256": metadata["config_sha256"],
            "code_sha256": metadata["code_sha256"], "source": manifest["variants"][variant]["source"],
            "runs": len(rows), "comparison_vs_reference": counts_by_reference,
            "comparison_by_family": family_counts,
            "runtime_and_iterations_by_family": family_runtime,
        }

    best_fields = (
        "variant", "instance", "family", "variant_vehicles", "variant_distance_ticks",
        "variant_best_seed", "baseline_vehicles", "baseline_distance_ticks", "baseline_best_seed",
        "uncapped_vehicles", "uncapped_distance_ticks", "uncapped_best_seed",
        "result_vs_baseline", "same_vehicle_distance_result_vs_baseline",
        "distance_delta_ticks_vs_baseline", "distance_delta_percent_vs_baseline",
        "result_vs_uncapped", "same_vehicle_distance_result_vs_uncapped",
        "distance_delta_ticks_vs_uncapped", "distance_delta_percent_vs_uncapped",
        "cyclic_vehicles", "cyclic_distance_ticks", "cyclic_best_seed",
        "result_vs_cyclic", "same_vehicle_distance_result_vs_cyclic",
        "distance_delta_ticks_vs_cyclic", "distance_delta_percent_vs_cyclic",
    )
    m8._write_csv(experiment / "best_by_instance.csv", best_rows, best_fields)
    m8._write_json(experiment / "metrics.json", metrics)
    print(
        f"analysed {len(all_rows)} independently verified runs across {len(names)} instance(s); "
        f"wrote runs.csv, best_by_instance.csv, metrics.json in {experiment}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="freeze sources and run the M9 variants sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR,
                     help="directory of top-level Solomon .txt files")
    run.add_argument("--out", type=Path, required=True, help="new or empty experiment directory")
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    run.add_argument("--instances", nargs="+", help="optional instance stems for a short run")
    run.add_argument("--variants", nargs="+", choices=ALL_VARIANTS, default=list(VARIANTS),
                     help="selected variants; include baseline and uncapped for operator ablations")
    run.add_argument("--diagnostics", action="store_true",
                     help="record per-phase diagnostic CSVs; use for representative cases only")

    analyse = commands.add_parser("analyse", help="verify artifacts and write comparison tables")
    analyse.add_argument("--out", type=Path, required=True,
                         help="completed experiment directory; analysis files are written here")
    analyse.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return run_experiment(args)
        return analyse_experiment(args)
    except (ValueError, OSError, ImportError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
