"""Run and analyse the sequential M10 fleet-repair ablation.

The default run evaluates all 56 Solomon instances with seeds 0/1/2 and a
0.5-second wall-clock budget per run::

    python benchmarks/m10_experiment.py run --out runs/m10_fleet_seed012_0p5s
    python benchmarks/m10_experiment.py analyse --out runs/m10_fleet_seed012_0p5s

The baseline is the frozen M9 delivery package. Candidate variants use a
source snapshot made at run start. Importing this module never starts a run.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
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
from benchmarks import m8_experiment as m8  # noqa: E402
from benchmarks import m9_experiment as m9  # noqa: E402

DATA_DIR = SOLVER_DIR / "data"
SRC_PACKAGE = SOLVER_DIR / "src" / "vrptw"
BASELINE_HASH = "f5dbbefc3ae22bb6d00b5d163622c793b753a410a3f9c2988d5f6174cecde83a"
BASELINE_ROOT = SOLVER_DIR / "benchmarks" / "m9" / "delivery" / "source"
BASELINE_PACKAGE = BASELINE_ROOT / BASELINE_HASH / "vrptw"

VARIANTS = ("baseline", "route_removal", "related", "hybrid")
OPTIONAL_VARIANTS = ("reconstruct", "cached_reconstruct", "related_rounds1", "hybrid_half", "hybrid_75")
ALL_VARIANTS = (*VARIANTS, *OPTIONAL_VARIANTS)
SUMMARY_FIELDS = m8.SUMMARY_FIELDS
TARGET_FIELDS = (
    "instance", "seed", "variant", "target", "trials", "repair_rounds",
    "elapsed_seconds", "first_feasible_seconds", "status",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_hash(package_dir: Path) -> tuple[str, dict[str, str]]:
    return m9._code_hash(package_dir)


def _find_baseline() -> tuple[Path, str, dict[str, str]]:
    package = BASELINE_PACKAGE
    if not package.is_dir():
        raise ValueError(f"frozen M9 delivery package is missing: {package}")
    source_hash, file_hashes = _code_hash(package)
    if source_hash != BASELINE_HASH or package.parent.name != BASELINE_HASH:
        raise ValueError(
            f"frozen M9 delivery hash differs: {source_hash} != {BASELINE_HASH}"
        )
    return package, source_hash, file_hashes


def _load_package(alias: str, package_dir: Path):
    return m8._load_package(alias, package_dir)


def _selected_variants(selected: list[str] | None) -> tuple[str, ...]:
    variants = tuple(VARIANTS if selected is None else selected)
    if not variants or len(variants) != len(set(variants)):
        raise ValueError("--variants must contain unique variant names")
    unknown = sorted(set(variants) - set(ALL_VARIANTS))
    if unknown:
        raise ValueError(f"unknown M10 variant(s): {', '.join(unknown)}")
    if "baseline" not in variants:
        raise ValueError("--variants must include baseline")
    return variants


def _config_for(package, variant: str, seed: int, time_limit: float,
                diagnostics: bool = False):
    if variant not in ALL_VARIANTS:
        raise ValueError(f"unknown M10 variant {variant!r}")
    # The M9 delivery uses the unlimited, cyclic four-operator ILS protocol.
    # Ask M9's shared helper for that exact configuration on both code lines;
    # its historical "baseline" branch describes the earlier M8 capped run.
    base = m9._config_for(package, "cyclic", seed, time_limit, diagnostics)
    if variant == "baseline":
        return base

    values = m8._jsonable_config(base)
    values.update(fleet_strategy="reconstruct", fleet_repair_rounds=5,
                  fleet_related_count=8)
    if variant == "cached_reconstruct":
        values["fleet_strategy"] = "cached_reconstruct"
    elif variant == "route_removal":
        values["fleet_strategy"] = "route_removal"
    elif variant in ("related", "related_rounds1"):
        values["fleet_strategy"] = "related"
    elif variant.startswith("hybrid"):
        values["fleet_strategy"] = "hybrid"
    if variant == "related_rounds1":
        values["fleet_repair_rounds"] = 1
    elif variant == "hybrid_half":
        values["fleet_time_fraction"] = 0.5
    elif variant == "hybrid_75":
        values["fleet_time_fraction"] = 0.75
    return package.Config(**values)


def _jsonable_config(config: object) -> dict[str, Any]:
    return m8._jsonable_config(config)


def _normalised_config_hash(config: object) -> str:
    return m8._normalised_config_hash(config)


def _rotated_variants(case_index: int, variants: tuple[str, ...] | list[str]) -> list[str]:
    return m9._rotated_variants(case_index, variants)


def _source_entry(package, source_hash: str, file_hashes: dict[str, str]) -> dict[str, Any]:
    return m9._source_entry(package, source_hash, file_hashes)


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


def _target_stat_rows(instance: str, seed: int, variant: str,
                      payload: dict[str, Any]) -> list[dict[str, object]]:
    fleet = payload.get("fleet")
    if not isinstance(fleet, dict):
        return []
    targets = fleet.get("targets")
    rows: list[dict[str, object]] = []
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict) or "target" not in target:
                continue
            rows.append({
                "instance": instance, "seed": seed, "variant": variant,
                "target": target.get("target", ""), "trials": target.get("trials", ""),
                "repair_rounds": target.get("repair_rounds", ""),
                "elapsed_seconds": target.get("elapsed_seconds", ""),
                "first_feasible_seconds": target.get("first_feasible_seconds", ""),
                "status": target.get("status", ""),
            })
        if rows:
            return rows

    # The archived M9 baseline records only attempts. Preserve its target and
    # trial counts while leaving M10-only timing/repair fields empty.
    milestones = fleet.get("milestones", [])
    reached = {
        int(item["vehicles"] if isinstance(item, dict) else item.vehicles)
        for item in milestones
        if (isinstance(item, dict) and "vehicles" in item)
        or hasattr(item, "vehicles")
    }
    stop_reason = str(fleet.get("stop_reason", ""))
    timed_out = "time" in stop_reason.lower() or "deadline" in stop_reason.lower()
    attempts = fleet.get("attempts", [])
    for attempt in attempts:
        if isinstance(attempt, dict):
            target, trials = attempt.get("target"), attempt.get("trials", "")
        elif isinstance(attempt, (list, tuple)) and len(attempt) >= 2:
            target, trials = attempt[0], attempt[1]
        else:
            continue
        if target is None:
            continue
        status = "found" if int(target) in reached else "time_limit" if timed_out else "not_found"
        rows.append({
            "instance": instance, "seed": seed, "variant": variant,
            "target": target, "trials": trials, "repair_rounds": "",
            "elapsed_seconds": "", "first_feasible_seconds": "", "status": status,
        })
    return rows


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
    (out / "runner.py").write_bytes(Path(__file__).read_bytes())

    baseline_dir, baseline_hash, baseline_files = _find_baseline()
    current_dir, current_hash, current_files = m8._freeze_current_source(out)
    run_id = uuid.uuid4().hex[:12]
    baseline = _load_package(f"m10_m9_baseline_{run_id}", baseline_dir)
    current = _load_package(f"m10_candidate_{run_id}", current_dir)
    baseline_report = m8._package_report(baseline)
    current_report = m8._package_report(current)
    for label, report, source_hash in (
            ("frozen M9 delivery", baseline_report, baseline_hash),
            ("M10 candidate", current_report, current_hash)):
        if report.code_fingerprint() != source_hash:
            raise ValueError(f"{label} report fingerprint differs from its source hash")
    if (tuple(baseline.local_search.DEFAULT_OPERATORS) != m9.BASE_OPERATORS
            or baseline.local_search.DEFAULT_OPERATOR_SCHEDULE != "cyclic"):
        raise ValueError("frozen M9 delivery defaults differ from the M9 search protocol")
    if not hasattr(current.Config, "__dataclass_fields__"):
        raise ValueError("current M10 Config is not a dataclass")
    required_fields = {"fleet_strategy", "fleet_repair_rounds", "fleet_related_count"}
    missing = required_fields - set(current.Config.__dataclass_fields__)
    if missing:
        raise ValueError(f"current solver Config is missing M10 fields: {sorted(missing)}")

    public_api = __import__("vrptw")
    public_read = public_api.read_solomon
    public_validate = __import__("vrptw.evaluate", fromlist=["validate_solution"]).validate_solution
    numeric_rule = current_report.NUMERIC_RULE_ID
    input_hashes = {path.stem: m8._sha256(path) for path in paths}
    names = [path.stem for path in paths]
    package_for = {"baseline": baseline, **{name: current for name in variants if name != "baseline"}}
    report_for = {"baseline": baseline_report, **{name: current_report for name in variants if name != "baseline"}}
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
        "schema_version": 1, "experiment": "M10 fleet repair",
        "status": "running", "started_at_utc": started_at, "finished_at_utc": None,
        "data_directory": str(args.data.resolve()), "instances": names, "seeds": args.seeds,
        "time_limit_seconds": args.time_limit, "max_iterations": None, "threads": 1,
        "diagnostics_enabled": args.diagnostics, "numeric_rule": numeric_rule,
        "runner_sha256": m8._sha256(Path(__file__).resolve()),
        "input_sha256": input_hashes, "environment": m8._environment(),
        "order": {
            "case_definition": "sorted instance, then ascending seed",
            "variant_rotation": "left rotation by case_index modulo selected variants",
            "variants": variants, "cases": cases,
        },
        "sources": {
            "m9_baseline": _source_entry(baseline, baseline_hash, baseline_files),
            "m10_candidate": _source_entry(current, current_hash, current_files),
        },
        "variants": {
            variant: {
                "config": config_dicts[variant], "config_sha256": config_hashes[variant],
                "code_sha256": code_hashes[variant],
                "source": "m9_baseline" if variant == "baseline" else "m10_candidate",
                "status": "running", "started_at_utc": None, "finished_at_utc": None,
                "runs": 0, "successful": 0, "failed": 0, "independently_verified": 0,
            }
            for variant in variants
        },
        "total_runs": len(cases) * len(variants), "independently_verified": 0,
    }
    manifest_path = out / "experiment.json"
    m8._write_json(manifest_path, manifest)
    records: dict[str, list[dict[str, object]]] = {variant: [] for variant in variants}
    target_rows: list[dict[str, object]] = []
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
                        result_entry["artifact_sha256"] = {
                            name: m8._sha256(artifact / name) for name in artifacts
                        }
                        target_rows.extend(_target_stat_rows(instance_name, seed, variant, payload))
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
                "instances": len(names), "instance_names": names, "runs": len(rows),
                "successful": sum(row["status"] == "ok" for row in rows),
                "failed": sum(row["status"] != "ok" for row in rows),
                "seeds": args.seeds, "time_limit_seconds": args.time_limit,
                "max_iterations": None,
                "fleet_attempts_per_k": config0.get("fleet_attempts_per_k", ""),
                "max_moves": config0.get("max_moves"), "diagnostics_enabled": args.diagnostics,
                "config": config0, "config_sha256": config_hashes[variant],
                "variant": variant, "input_format": "solomon_txt", "threads": 1,
                "numeric_rule": numeric_rule, "code_sha256": code_hashes[variant],
                "runner_sha256": manifest["runner_sha256"], "input_sha256": input_hashes,
                "started_at_utc": variant_started[variant] or started_at,
                "finished_at_utc": manifest["variants"][variant]["finished_at_utc"] or _utc_now(),
                "environment": manifest["environment"], "order": manifest["order"]["variant_rotation"],
            }
            batch_dir = out / variant
            metadata_path = batch_dir / "batch_summary.json"
            m8._write_json(metadata_path, summary)
            state = manifest["variants"][variant]
            state["status"] = (
                "completed" if state["runs"] == len(cases) and state["failed"] == 0 else "failed"
            )
            state["batch_summary_sha256"] = m8._sha256(batch_dir / "batch_summary.csv")
            state["batch_metadata_sha256"] = m8._sha256(metadata_path)
        target_path = out / "fleet_targets.csv"
        m8._write_csv(target_path, target_rows, TARGET_FIELDS)
        manifest["fleet_targets_sha256"] = m8._sha256(target_path)
        if args.diagnostics:
            # Keep the conventional per-variant diagnostics files so M9 readers
            # and scripts can consume diagnostic-enabled runs if requested.
            from benchmarks.m9_experiment import DIAGNOSTIC_BATCH_FIELDS, VARIANT_DIAGNOSTIC_FIELDS
            diag_all: list[dict[str, object]] = []
            for variant in variants:
                variant_rows: list[dict[str, object]] = []
                for row in records[variant]:
                    result_entry = next(
                        case["results"][variant]
                        for case in cases
                        if case["instance"] == row["instance"] and case["seed"] == row["seed"]
                    )
                    # Diagnostics are already included in solution.json; a
                    # dedicated CSV is copied only for successful records.
                    if result_entry.get("verified"):
                        artifact_dir = out / variant / row["instance"] / f"seed-{row['seed']}"
                        try:
                            diag_payload = json.loads((artifact_dir / "solution.json").read_text(encoding="utf-8"))
                            phases = diag_payload.get("diagnostics", {}).get("phases", [])
                            for phase in phases:
                                diag_row = {"instance": row["instance"], "seed": row["seed"],
                                            "variant": variant, **phase}
                                variant_rows.append(diag_row)
                                diag_all.append(diag_row)
                        except (OSError, json.JSONDecodeError):
                            continue
                by_phase = [{key: row[key] for key in VARIANT_DIAGNOSTIC_FIELDS}
                            for row in variant_rows]
                diag_path = out / variant / "batch_diagnostics.csv"
                m8._write_csv(diag_path, by_phase, VARIANT_DIAGNOSTIC_FIELDS)
                manifest["variants"][variant]["batch_diagnostics_sha256"] = m8._sha256(diag_path)
            m8._write_csv(out / "batch_diagnostics.csv", diag_all, DIAGNOSTIC_BATCH_FIELDS)
            manifest["batch_diagnostics_sha256"] = m8._sha256(out / "batch_diagnostics.csv")
        manifest["finished_at_utc"] = _utc_now()
        manifest["status"] = "completed" if all(
            value["status"] == "completed" for value in manifest["variants"].values()
        ) else "failed"
        m8._write_json(manifest_path, manifest)
    return 0 if manifest["status"] == "completed" else 1


def _load_verified_sources(experiment: Path, manifest: dict[str, Any]):
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"m9_baseline", "m10_candidate"}:
        raise ValueError("manifest source records are incomplete")
    loaded = {}
    for key in ("m9_baseline", "m10_candidate"):
        entry = sources[key]
        package_dir = Path(entry.get("package", "")).resolve()
        expected_root = BASELINE_ROOT.resolve() if key == "m9_baseline" else (experiment / "source").resolve()
        try:
            package_dir.relative_to(expected_root)
        except ValueError as exc:
            raise ValueError(f"{key} package is outside its frozen source directory") from exc
        if package_dir.name != "vrptw":
            raise ValueError(f"{key} package path does not end in vrptw")
        source_hash, file_hashes = _code_hash(package_dir)
        expected_hash = BASELINE_HASH if key == "m9_baseline" else entry.get("sha256")
        if (source_hash != entry.get("sha256") or source_hash != package_dir.parent.name
                or source_hash != expected_hash):
            raise ValueError(f"{key} source fingerprint differs")
        if file_hashes != entry.get("file_sha256"):
            raise ValueError(f"{key} source file hashes differ")
        alias = f"m10_analyse_{key}_{uuid.uuid4().hex[:12]}"
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
    if "baseline" not in variants or set(variants) - set(ALL_VARIANTS):
        raise ValueError("manifest contains an invalid M10 variant set")
    runner = experiment / "runner.py"
    if manifest.get("runner_sha256") != m8._sha256(runner if runner.exists() else Path(__file__).resolve()):
        raise ValueError("M10 runner hash differs")
    names, seeds = manifest.get("instances", []), manifest.get("seeds", [])
    time_limit = manifest.get("time_limit_seconds")
    if type(time_limit) not in (int, float) or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("manifest has an invalid time limit")
    expected_pairs = [(name, seed) for name in names for seed in seeds]
    if (not expected_pairs or len(set(expected_pairs)) != len(expected_pairs)
            or seeds != sorted(set(seeds)) or any(type(seed) is not int or seed < 0 for seed in seeds)):
        raise ValueError("experiment has invalid instance/seed coverage")
    expected_names = [path.stem for path in m8._input_paths(data_dir, names)]
    if names != expected_names:
        raise ValueError("manifest instance order differs from the input directory")
    cases = manifest.get("order", {}).get("cases", [])
    if len(cases) != len(expected_pairs) or manifest.get("total_runs") != len(cases) * len(variants):
        raise ValueError("manifest case count differs from its declared coverage")
    for index, (case, expected_pair) in enumerate(zip(cases, expected_pairs)):
        pair = (case.get("instance"), case.get("seed"))
        if case.get("case_index") != index or pair != expected_pair:
            raise ValueError("manifest case order differs from instance/seed order")
        if case.get("variants") != _rotated_variants(index, variants):
            raise ValueError(f"variant rotation differs for case {index}")
        if set(case.get("results", {})) != set(variants):
            raise ValueError(f"case {index} does not record all variants")

    frozen = _load_verified_sources(experiment, manifest)
    baseline, candidate = frozen["m9_baseline"][0], frozen["m10_candidate"][0]
    if (tuple(baseline.local_search.DEFAULT_OPERATORS) != m9.BASE_OPERATORS
            or baseline.local_search.DEFAULT_OPERATOR_SCHEDULE != "cyclic"):
        raise ValueError("frozen M9 baseline defaults differ")
    fields = set(candidate.Config.__dataclass_fields__)
    if {"fleet_strategy", "fleet_repair_rounds", "fleet_related_count"} - fields:
        raise ValueError("frozen M10 source is missing fleet Config fields")
    public_api = __import__("vrptw")
    public_read = public_api.read_solomon
    public_validate = __import__("vrptw.evaluate", fromlist=["validate_solution"]).validate_solution
    numeric_rule = frozen["m10_candidate"][1].NUMERIC_RULE_ID
    if manifest.get("numeric_rule") != numeric_rule:
        raise ValueError("manifest numeric rule differs from frozen candidate source")
    if set(manifest.get("input_sha256", {})) != set(names):
        raise ValueError("manifest input hash map differs from instance coverage")
    case_by_pair = {(case["instance"], case["seed"]): case for case in cases}
    loaded: dict[str, tuple[dict[str, Any], list[dict[str, str]], dict[str, tuple[tuple[int, int], int]]]] = {}
    independent_count = 0
    for variant in variants:
        state = manifest["variants"].get(variant, {})
        source_key = "m9_baseline" if variant == "baseline" else "m10_candidate"
        source_hash = frozen[source_key][2]
        if state.get("source") != source_key or state.get("code_sha256") != source_hash:
            raise ValueError(f"{variant} source label or code hash differs")
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
        if (len(row_map) != len(rows) or set(row_map) != expected_keys
                or metadata.get("runs") != len(rows)):
            raise ValueError(f"{variant} batch coverage differs from the manifest")
        expected_package = baseline if variant == "baseline" else candidate
        expected_config = _jsonable_config(_config_for(
            expected_package, variant, 0, time_limit, bool(manifest.get("diagnostics_enabled"))))
        config = metadata.get("config")
        if (config != expected_config or config != state.get("config")
                or metadata.get("config_sha256") != state.get("config_sha256")
                or state.get("config_sha256") != _normalised_config_hash(
                    _config_for(expected_package, variant, 0, time_limit,
                                bool(manifest.get("diagnostics_enabled"))))):
            raise ValueError(f"{variant} configuration or config hash differs")
        if (metadata.get("variant") != variant or metadata.get("numeric_rule") != numeric_rule
                or metadata.get("code_sha256") != source_hash
                or metadata.get("input_sha256") != manifest.get("input_sha256")
                or metadata.get("diagnostics_enabled") != bool(manifest.get("diagnostics_enabled"))
                or float(metadata.get("time_limit_seconds", -1)) != float(time_limit)
                or metadata.get("max_iterations") is not None):
            raise ValueError(f"{variant} batch protocol differs")

        for name, seed in expected_pairs:
            row = row_map[(name, str(seed))]
            source = data_dir / f"{name}.txt"
            input_hash = m8._sha256(source)
            if (input_hash != manifest["input_sha256"].get(name)
                    or input_hash != row.get("input_sha256")):
                raise ValueError(f"{variant} input hash differs for {name}")
            if (row.get("status") != "ok" or row.get("feasible") != "True"
                    or row.get("numeric_rule") != numeric_rule
                    or row.get("code_sha256") != source_hash
                    or row.get("config_sha256") != state.get("config_sha256")
                    or row.get("variant") != variant
                    or float(row.get("time_limit_seconds", -1)) != float(time_limit)):
                raise ValueError(f"{variant} row protocol differs for {name}/seed-{seed}")
            case_result = case_by_pair[(name, seed)]["results"][variant]
            if case_result.get("status") != "ok" or case_result.get("verified") is not True:
                raise ValueError(f"{variant} manifest run is not verified for {name}/seed-{seed}")
            _expected_artifact_hashes(case_result, variant, bool(manifest.get("diagnostics_enabled")))
            artifact_rel = case_result.get("artifact")
            if artifact_rel != f"{variant}/{name}/seed-{seed}/solution.json":
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
                raise ValueError(f"{variant} saved routes failed public validation for {name}/seed-{seed}")
            if (payload.get("config") != dict(config, seed=int(seed))
                    or payload.get("input_sha256") != input_hash
                    or payload.get("code_sha256") != source_hash
                    or payload.get("numeric_rule") != numeric_rule
                    or payload.get("seed") != int(seed)):
                raise ValueError(f"{variant} saved config, hashes, or seed differ for {name}/seed-{seed}")
            stop = payload.get("stop")
            if (not isinstance(stop, dict)
                    or float(stop.get("time_limit_seconds", -1)) != float(time_limit)
                    or stop.get("max_iterations") is not None):
                raise ValueError(f"{variant} saved time budget differs for {name}/seed-{seed}")
            independent_count += 1
        best = m8._best_by_instance(rows, variant)
        loaded[variant] = (metadata, rows, best)

    if manifest.get("independently_verified") != independent_count:
        raise ValueError("manifest independent verification count differs")
    for variant in variants:
        state = manifest["variants"][variant]
        if (state.get("status") != "completed" or state.get("runs") != len(expected_pairs)
                or state.get("successful") != len(expected_pairs) or state.get("failed") != 0
                or state.get("independently_verified") != len(expected_pairs)):
            raise ValueError(f"{variant} completion counts differ")
    target_path = experiment / "fleet_targets.csv"
    if m8._sha256(target_path) != manifest.get("fleet_targets_sha256"):
        raise ValueError("fleet target statistics hash differs")
    target_rows = m8._read_csv(target_path)
    for row in target_rows:
        if (row.get("instance") not in names or row.get("seed") not in {str(x) for x in seeds}
                or row.get("variant") not in variants):
            raise ValueError("fleet target statistics contain an unrequested run")
    return loaded


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment = args.out.resolve()
    data_dir = args.data.resolve()
    manifest_path = experiment / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    loaded = _validate_analysis_input(experiment, data_dir, manifest)
    variants = tuple(manifest["order"]["variants"])
    baseline = loaded["baseline"][2]
    names = manifest["instances"]
    all_rows: list[dict[str, object]] = []
    for variant in variants:
        all_rows.extend({"variant": variant, **row} for row in loaded[variant][1])
    m8._write_csv(experiment / "runs.csv", all_rows, SUMMARY_FIELDS)

    best_rows: list[dict[str, object]] = []
    metrics: dict[str, Any] = {
        "schema_version": 1, "experiment": str(experiment),
        "experiment_manifest_sha256": m8._sha256(manifest_path),
        "independently_verified_runs": sum(len(value[1]) for value in loaded.values()),
        "comparison_reference": "baseline", "variants": {},
    }
    for variant in variants:
        metadata, rows, best = loaded[variant]
        counts = m9._empty_comparison()
        family_counts = {family: m9._empty_comparison() for family in ("all", "C", "R", "RC")}
        for name in names:
            candidate_obj, candidate_seed = best[name]
            base_obj, base_seed = baseline[name]
            result, distance_result, delta, percent = m9._compare(base_obj, candidate_obj)
            m9._record_comparison(counts, base_obj, candidate_obj)
            family = m8._family(name)
            m9._record_comparison(family_counts["all"], base_obj, candidate_obj)
            m9._record_comparison(family_counts[family], base_obj, candidate_obj)
            best_rows.append({
                "variant": variant, "instance": name, "family": family,
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
            grouped[m8._family(row["instance"])].append(row)
        family_stats: dict[str, Any] = {}
        for family, group in grouped.items():
            runtimes = [float(row["runtime_seconds"]) for row in group]
            iterations = [int(row["iterations"]) for row in group]
            family_stats[family] = {
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
            "runs": len(rows), "comparison_vs_baseline": counts,
            "comparison_by_family": family_counts,
            "runtime_and_iterations_by_family": family_stats,
        }

    fields = (
        "variant", "instance", "family", "baseline_vehicles", "baseline_distance_ticks",
        "baseline_best_seed", "variant_vehicles", "variant_distance_ticks", "variant_best_seed",
        "result_vs_baseline", "same_vehicle_distance_result", "distance_delta_ticks",
        "distance_delta_percent",
    )
    m8._write_csv(experiment / "best_by_instance.csv", best_rows, fields)
    m8._write_json(experiment / "metrics.json", metrics)
    print(
        f"analysed {len(all_rows)} verified runs across {len(names)} instance(s); "
        f"wrote runs.csv, best_by_instance.csv, metrics.json in {experiment}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="freeze sources and run the M10 variants sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR,
                     help="directory of top-level Solomon .txt files")
    run.add_argument("--out", type=Path, required=True,
                     help="new or empty experiment directory")
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    run.add_argument("--instances", nargs="+", help="optional instance stems for a short run")
    run.add_argument("--variants", nargs="+", choices=ALL_VARIANTS, default=list(VARIANTS),
                     help="selected variants; baseline is required")
    run.add_argument("--diagnostics", action="store_true",
                     help="save phase diagnostics for each run")
    analyse = commands.add_parser("analyse", help="verify artifacts and write comparison tables")
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
