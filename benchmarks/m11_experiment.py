"""Run and analyse the M11 infeasible-search penalty ablation.

The default protocol runs the frozen current solver sequentially on all 56
Solomon instances with seeds 0, 1 and 2, giving each variant the same 0.5 s
budget::

    python benchmarks/m11_experiment.py run --out runs/m11_penalties_seed012_0p5s
    python benchmarks/m11_experiment.py analyse --out runs/m11_penalties_seed012_0p5s

Importing this module never starts an experiment.
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

DATA_DIR = SOLVER_DIR / "data"
SRC_PACKAGE = SOLVER_DIR / "src" / "vrptw"
VARIANTS = ("baseline", "fixed_penalties", "adaptive_penalties")
OPTIONAL_VARIANTS = ("adaptive_fast", "adaptive_high")
ALL_VARIANTS = (*VARIANTS, *OPTIONAL_VARIANTS)
SUMMARY_FIELDS = m8.SUMMARY_FIELDS
DIAGNOSTIC_FIELDS = (
    "phase", "elapsed_seconds", "inclusive_seconds", "calls", "candidates",
    "feasible_candidates", "infeasible_candidates", "route_evaluations",
    "validation_calls", "accepted", "capacity_prefilter_skips", "rejected_capacity",
    "rejected_time_window", "rejected_depot_close", "rejected_empty_route", "trials",
    "failures", "removed_customers", "route_cache_builds", "incremental_route_evaluations",
    "reused_prefix_customers", "reused_suffix_customers", "neighbour_filtered",
    "penalized_candidates", "accepted_infeasible",
)
DIAGNOSTIC_BATCH_FIELDS = ("instance", "seed", "variant", *DIAGNOSTIC_FIELDS)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_hash(package_dir: Path) -> tuple[str, dict[str, str]]:
    paths = sorted(package_dir.glob("*.py"))
    if not paths:
        raise ValueError(f"no Python source files found in {package_dir}")
    contents = {path.name: path.read_bytes() for path in paths}
    return m8._code_hash(contents), {
        name: m8._sha256_bytes(value) for name, value in sorted(contents.items())
    }


def _selected_variants(selected: list[str] | None) -> tuple[str, ...]:
    variants = tuple(VARIANTS if selected is None else selected)
    if not variants or len(variants) != len(set(variants)):
        raise ValueError("--variants must contain unique variant names")
    unknown = sorted(set(variants) - set(ALL_VARIANTS))
    if unknown:
        raise ValueError(f"unknown M11 variant(s): {', '.join(unknown)}")
    if "baseline" not in variants:
        raise ValueError("--variants must include baseline")
    return variants


def _config_for(package, variant: str, seed: int, time_limit: float,
                diagnostics: bool = False):
    """Apply only the M11 switches to the current Config defaults."""
    if variant not in ALL_VARIANTS:
        raise ValueError(f"unknown M11 variant {variant!r}")
    fields = set(getattr(package.Config, "__dataclass_fields__", {}))
    required = {"infeasible_search", "adaptive_penalties", "penalty_update_interval",
                "penalty_target_feasible"}
    if required - fields:
        raise ValueError(f"current Config is missing M11 fields: {sorted(required - fields)}")
    values: dict[str, object] = {
        "seed": seed,
        "max_iterations": None,
        "time_limit_seconds": time_limit,
        "diagnostics": diagnostics,
        "infeasible_search": variant != "baseline",
        "adaptive_penalties": variant != "fixed_penalties",
    }
    if variant == "adaptive_fast":
        values["penalty_update_interval"] = 2
    elif variant == "adaptive_high":
        values["penalty_target_feasible"] = 0.8
    return package.Config(**values)


def _jsonable_config(config: object) -> dict[str, Any]:
    return m8._jsonable_config(config)


def _config_hash(config: object) -> str:
    return m8._normalised_config_hash(config)


def _rotated_variants(case_index: int, variants: tuple[str, ...] | list[str]) -> list[str]:
    offset = case_index % len(variants)
    return [*variants[offset:], *variants[:offset]]


def _source_entry(package, source_hash: str, file_hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "sha256": source_hash,
        "package": str(Path(package.__path__[0]).resolve()),
        "file_sha256": file_hashes,
        "module_alias": package.__name__,
    }


def _make_row(instance: str, seed: int, variant: str, code_hash: str,
              input_hash: str, config_hash: str, time_limit: float,
              vehicle_limit: int, numeric_rule: str,
              diagnostics: bool) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        instance=instance, input_format="solomon_txt", input_sha256=input_hash,
        seed=seed, threads=1, status="error", feasible=False,
        time_limit_seconds=time_limit, max_iterations="", vehicle_limit=vehicle_limit,
        code_sha256=code_hash, diagnostics_enabled=diagnostics, variant=variant,
        config_sha256=config_hash, numeric_rule=numeric_rule,
    )
    row["diagnostics_enabled"] = diagnostics
    return row


def run_experiment(args: argparse.Namespace) -> int:
    if (not args.seeds or args.seeds != sorted(set(args.seeds))
            or any(type(seed) is not int or seed < 0 for seed in args.seeds)):
        raise ValueError("--seeds must be unique, nonnegative integers in ascending order")
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit must be finite and positive")
    diagnostics_enabled = bool(getattr(args, "diagnostics", False))
    variants = _selected_variants(args.variants)
    paths = m8._input_paths(args.data, args.instances)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    runner_path = out / "runner.py"
    runner_path.write_bytes(Path(__file__).read_bytes())

    frozen_dir, source_hash, file_hashes = m8._freeze_current_source(out)
    run_id = uuid.uuid4().hex[:12]
    package = m8._load_package(f"m11_candidate_{run_id}", frozen_dir)
    report = m8._package_report(package)
    if report.code_fingerprint() != source_hash:
        raise ValueError("frozen M11 source report fingerprint differs from source hash")
    public_api = __import__("vrptw")
    public_read = public_api.read_solomon
    public_validate = public_api.validate_solution
    numeric_rule = report.NUMERIC_RULE_ID
    names = [path.stem for path in paths]
    input_hashes = {path.stem: m8._sha256(path) for path in paths}
    config_dicts: dict[str, dict[str, Any]] = {}
    config_hashes: dict[str, str] = {}
    for variant in variants:
        config = _config_for(package, variant, 0, args.time_limit, diagnostics_enabled)
        config_dicts[variant] = _jsonable_config(config)
        config_hashes[variant] = _config_hash(config)

    cases = []
    for index, (path, seed) in enumerate((path, seed) for path in paths for seed in args.seeds):
        order = _rotated_variants(index, variants)
        cases.append({
            "case_index": index,
            "instance": path.stem,
            "seed": seed,
            "variants": order,
            "results": {
                variant: {
                    "status": "pending", "verified": False,
                    "artifact": f"{variant}/{path.stem}/seed-{seed}/solution.json",
                    "artifact_sha256": {},
                }
                for variant in order
            },
        })

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "M11 infeasible search adaptive penalties",
        "status": "running",
        "started_at_utc": _utc_now(),
        "finished_at_utc": None,
        "data_directory": str(args.data.resolve()),
        "instances": names,
        "seeds": list(args.seeds),
        "time_limit_seconds": args.time_limit,
        "max_iterations": None,
        "threads": 1,
        "diagnostics_enabled": diagnostics_enabled,
        "numeric_rule": numeric_rule,
        "runner_sha256": m8._sha256(runner_path),
        "input_sha256": input_hashes,
        "environment": m8._environment(),
        "order": {
            "case_definition": "sorted instance, then ascending seed",
            "variant_rotation": "left rotation by case_index modulo selected variants",
            "variants": list(variants),
            "cases": cases,
        },
        "sources": {"m11_candidate": _source_entry(package, source_hash, file_hashes)},
        "variants": {
            variant: {
                "config": config_dicts[variant],
                "config_sha256": config_hashes[variant],
                "code_sha256": source_hash,
                "source": "m11_candidate",
                "status": "running",
                "runs": 0,
                "successful": 0,
                "failed": 0,
                "independently_verified": 0,
            }
            for variant in variants
        },
        "total_runs": len(cases) * len(variants),
        "independently_verified": 0,
    }
    manifest_path = out / "experiment.json"
    m8._write_json(manifest_path, manifest)
    records: dict[str, list[dict[str, object]]] = {variant: [] for variant in variants}
    diagnostic_records: dict[str, list[dict[str, object]]] = {variant: [] for variant in variants}
    streams: dict[str, Any] = {}
    writers: dict[str, csv.DictWriter] = {}
    public_instances: dict[str, object] = {}
    path_by_name = {path.stem: path for path in paths}
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

            for case in cases:
                name, seed = case["instance"], case["seed"]
                path = path_by_name[name]
                if name not in public_instances:
                    public_instances[name] = public_read(path)
                public_instance = public_instances[name]
                for variant in case["variants"]:
                    started = _utc_now()
                    config = _config_for(
                        package, variant, seed, args.time_limit, diagnostics_enabled,
                    )
                    config_values = _jsonable_config(config)
                    row = _make_row(
                        name, seed, variant, source_hash, input_hashes[name],
                        config_hashes[variant], args.time_limit,
                        public_instance.vehicle_count, numeric_rule, diagnostics_enabled,
                    )
                    artifact_dir = out / variant / name / f"seed-{seed}"
                    try:
                        solver_instance = package.read_solomon(path)
                        result = package.solve(solver_instance, config)
                        artifact = report.write_run(
                            solver_instance, result, config, artifact_dir,
                            source_path=path, plots=False,
                        )
                        payload = json.loads((artifact / "solution.json").read_text(encoding="utf-8"))
                        verdict = public_validate(public_instance, payload.get("routes", []))
                        objective = (verdict.vehicles, verdict.distance)
                        if not verdict.feasible or objective != result.evaluation.objective:
                            raise ValueError(f"public validator rejected solver routes: {verdict.first_violation}")
                        if objective != (payload.get("vehicles"), payload.get("distance_ticks")):
                            raise ValueError("saved solution objective differs from public validator")
                        if (payload.get("input_sha256") != input_hashes[name]
                                or payload.get("code_sha256") != source_hash
                                or payload.get("numeric_rule") != numeric_rule
                                or payload.get("seed") != seed
                                or payload.get("config") != config_values):
                            raise ValueError("saved solution hashes or protocol differ")
                        row.update(
                            status="ok", feasible=True, vehicles=verdict.vehicles,
                            distance_ticks=verdict.distance, distance=verdict.distance / 1000,
                            first_feasible_seconds=round(result.first_feasible_seconds, 6),
                            runtime_seconds=round(result.runtime_seconds, 6),
                            iterations=result.iterations, stop_reason=result.stop_reason,
                        )
                        case_result = case["results"][variant]
                        case_result["artifact_sha256"] = {
                            filename: m8._sha256(artifact / filename)
                            for filename in (("solution.json", "routes.sol", "history.csv", "diagnostics.csv")
                                             if diagnostics_enabled else
                                             ("solution.json", "routes.sol", "history.csv"))
                        }
                        if diagnostics_enabled:
                            phases = payload.get("diagnostics", {}).get("phases", [])
                            diagnostic_records[variant].extend(
                                {"instance": name, "seed": seed, "variant": variant, **phase}
                                for phase in phases
                            )
                        manifest["independently_verified"] += 1
                        manifest["variants"][variant]["independently_verified"] += 1
                    except Exception as exc:  # preserve a failed row and make the run unanalyzable
                        row["error"] = f"{type(exc).__name__}: {exc}"
                    finished = _utc_now()
                    state = case["results"][variant]
                    state.update(
                        status=row["status"], verified=row["status"] == "ok",
                        started_at_utc=started, finished_at_utc=finished,
                        error=row["error"],
                    )
                    records[variant].append(row)
                    writers[variant].writerow(row)
                    streams[variant].flush()
                    variant_state = manifest["variants"][variant]
                    variant_state["runs"] += 1
                    if row["status"] == "ok":
                        variant_state["successful"] += 1
                    else:
                        variant_state["failed"] += 1
                    m8._write_json(manifest_path, manifest)
    except Exception:
        manifest["status"] = "failed"
        manifest["finished_at_utc"] = _utc_now()
        m8._write_json(manifest_path, manifest)
        raise

    for variant in variants:
        config = config_dicts[variant]
        summary = {
            "schema_version": 1,
            "variant": variant,
            "runs": len(records[variant]),
            "config": config,
            "config_sha256": config_hashes[variant],
            "code_sha256": source_hash,
            "numeric_rule": numeric_rule,
            "time_limit_seconds": args.time_limit,
            "max_iterations": None,
            "diagnostics_enabled": diagnostics_enabled,
            "input_sha256": input_hashes,
            "environment": manifest["environment"],
            "order": manifest["order"]["variant_rotation"],
        }
        m8._write_json(out / variant / "batch_summary.json", summary)
        if diagnostics_enabled:
            m8._write_csv(out / variant / "batch_diagnostics.csv",
                          diagnostic_records[variant], DIAGNOSTIC_BATCH_FIELDS)
        state = manifest["variants"][variant]
        state["status"] = "completed" if state["failed"] == 0 else "failed"
        state["batch_summary_sha256"] = m8._sha256(out / variant / "batch_summary.csv")
        state["batch_metadata_sha256"] = m8._sha256(out / variant / "batch_summary.json")
        if diagnostics_enabled:
            state["batch_diagnostics_sha256"] = m8._sha256(out / variant / "batch_diagnostics.csv")
    manifest["finished_at_utc"] = _utc_now()
    manifest["status"] = "completed" if all(
        state["status"] == "completed" for state in manifest["variants"].values()
    ) else "failed"
    m8._write_json(manifest_path, manifest)
    return 0 if manifest["status"] == "completed" else 1


def _load_verified_source(experiment: Path, manifest: dict[str, Any]):
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"m11_candidate"}:
        raise ValueError("manifest source records are incomplete")
    entry = sources["m11_candidate"]
    package_dir = Path(entry.get("package", "")).resolve()
    try:
        package_dir.relative_to((experiment / "source").resolve())
    except ValueError as exc:
        raise ValueError("M11 source package is outside its frozen source directory") from exc
    source_hash, file_hashes = _source_hash(package_dir)
    if (package_dir.name != "vrptw" or package_dir.parent.name != entry.get("sha256")
            or source_hash != entry.get("sha256")):
        raise ValueError("M11 source fingerprint differs")
    if file_hashes != entry.get("file_sha256"):
        raise ValueError("M11 source file hashes differ")
    package = m8._load_package(f"m11_analyse_{uuid.uuid4().hex[:12]}", package_dir)
    report = m8._package_report(package)
    if report.code_fingerprint() != source_hash:
        raise ValueError("M11 frozen report fingerprint differs")
    return package, report, source_hash


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
    if (not variants or variants != tuple(manifest.get("variants", {}))
            or "baseline" not in variants or set(variants) - set(ALL_VARIANTS)):
        raise ValueError("manifest has an invalid M11 variant set or order")
    runner = experiment / "runner.py"
    if not runner.is_file() or manifest.get("runner_sha256") != m8._sha256(runner):
        raise ValueError("M11 runner hash differs")
    names, seeds = manifest.get("instances", []), manifest.get("seeds", [])
    time_limit = manifest.get("time_limit_seconds")
    if type(time_limit) not in (int, float) or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("manifest has an invalid time limit")
    diagnostics_enabled = manifest.get("diagnostics_enabled")
    if type(diagnostics_enabled) is not bool:
        raise ValueError("manifest diagnostics setting is invalid")
    if manifest.get("max_iterations") is not None or manifest.get("threads") != 1:
        raise ValueError("manifest search budget or thread count differs")
    if (not names or not seeds or seeds != sorted(set(seeds))
            or any(type(seed) is not int or seed < 0 for seed in seeds)):
        raise ValueError("experiment has invalid instance/seed coverage")
    if names != [path.stem for path in m8._input_paths(data_dir, names)]:
        raise ValueError("manifest instance order differs from the input directory")
    expected_pairs = [(name, seed) for name in names for seed in seeds]
    cases = manifest.get("order", {}).get("cases", [])
    if len(cases) != len(expected_pairs) or manifest.get("total_runs") != len(cases) * len(variants):
        raise ValueError("manifest case count differs from its declared coverage")
    case_by_pair: dict[tuple[str, int], dict[str, Any]] = {}
    for index, (case, pair) in enumerate(zip(cases, expected_pairs)):
        actual = (case.get("instance"), case.get("seed"))
        if case.get("case_index") != index or actual != pair:
            raise ValueError("manifest case order differs from instance/seed order")
        if case.get("variants") != _rotated_variants(index, variants):
            raise ValueError(f"manifest variant rotation differs for case {index}")
        if set(case.get("results", {})) != set(variants):
            raise ValueError(f"case {index} does not record all variants")
        case_by_pair[pair] = case

    package, report, source_hash = _load_verified_source(experiment, manifest)
    if manifest.get("numeric_rule") != report.NUMERIC_RULE_ID:
        raise ValueError("manifest numeric rule differs from frozen solver")
    for variant in variants:
        state = manifest["variants"][variant]
        config = state.get("config")
        if not isinstance(config, dict):
            raise ValueError(f"{variant} manifest config is missing")
        expected_config = _jsonable_config(_config_for(
            package, variant, 0, time_limit, diagnostics_enabled,
        ))
        config_hash = m8._canonical_hash({**config, "seed": 0})
        if (config != expected_config or state.get("config_sha256") != config_hash
                or config_hash != _config_hash(_config_for(
                    package, variant, 0, time_limit, diagnostics_enabled,
                ))):
            raise ValueError(f"{variant} config or config hash differs")
        if state.get("code_sha256") != source_hash or state.get("source") != "m11_candidate":
            raise ValueError(f"{variant} frozen source protocol differs")
        expected_rows = {(name, str(seed)) for name, seed in expected_pairs}
        batch_dir = experiment / variant
        csv_path = batch_dir / "batch_summary.csv"
        metadata_path = batch_dir / "batch_summary.json"
        if (m8._sha256(csv_path) != state.get("batch_summary_sha256")
                or m8._sha256(metadata_path) != state.get("batch_metadata_sha256")):
            raise ValueError(f"{variant} batch summary or metadata hash differs")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        rows = m8._read_csv(csv_path)
        row_map = {(row.get("instance", ""), row.get("seed", "")): row for row in rows}
        if (len(row_map) != len(rows) or set(row_map) != expected_rows
                or metadata.get("runs") != len(rows)):
            raise ValueError(f"{variant} batch coverage differs from the manifest")
        if (metadata.get("variant") != variant or metadata.get("config") != config
                or metadata.get("config_sha256") != config_hash
                or metadata.get("code_sha256") != source_hash
                or metadata.get("numeric_rule") != report.NUMERIC_RULE_ID
                or metadata.get("time_limit_seconds") != time_limit
                or metadata.get("diagnostics_enabled") is not diagnostics_enabled
                or metadata.get("max_iterations") is not None
                or metadata.get("input_sha256") != manifest.get("input_sha256")):
            raise ValueError(f"{variant} batch protocol or budget differs")
        if state.get("status") != "completed" or state.get("runs") != len(expected_pairs) \
                or state.get("successful") != len(expected_pairs) or state.get("failed") != 0:
            raise ValueError(f"{variant} completion counts differ")
        diagnostic_index: dict[tuple[str, str, str], dict[str, str]] = {}
        expected_phases = tuple(package.diagnostics.PHASES)
        if diagnostics_enabled:
            diagnostic_path = batch_dir / "batch_diagnostics.csv"
            if m8._sha256(diagnostic_path) != state.get("batch_diagnostics_sha256"):
                raise ValueError(f"{variant} batch diagnostics hash differs")
            diagnostic_rows = m8._read_csv(diagnostic_path)
            diagnostic_index = {
                (row.get("instance", ""), row.get("seed", ""), row.get("phase", "")): row
                for row in diagnostic_rows
            }
            expected_diag_keys = {
                (name, str(seed), phase)
                for name, seed in expected_pairs for phase in expected_phases
            }
            if (len(diagnostic_index) != len(diagnostic_rows)
                    or set(diagnostic_index) != expected_diag_keys):
                raise ValueError(f"{variant} batch diagnostic phase coverage differs")
        elif "batch_diagnostics_sha256" in state:
            raise ValueError(f"{variant} unexpectedly records batch diagnostics")
        for name, seed in expected_pairs:
            row = row_map[(name, str(seed))]
            source = data_dir / f"{name}.txt"
            input_hash = m8._sha256(source)
            if input_hash != manifest.get("input_sha256", {}).get(name) \
                    or input_hash != row.get("input_sha256"):
                raise ValueError(f"{variant} input hash differs for {name}")
            if (row.get("status") != "ok" or row.get("feasible") != "True"
                    or row.get("numeric_rule") != report.NUMERIC_RULE_ID
                    or row.get("code_sha256") != source_hash
                    or row.get("config_sha256") != config_hash
                    or row.get("variant") != variant
                    or row.get("time_limit_seconds") != str(time_limit)
                    or row.get("max_iterations") != ""
                    or row.get("threads") != "1"
                    or row.get("diagnostics_enabled") != str(diagnostics_enabled)):
                raise ValueError(f"{variant} row protocol differs for {name}/seed-{seed}")
            case_result = case_by_pair[(name, seed)]["results"][variant]
            if case_result.get("status") != "ok" or case_result.get("verified") is not True:
                raise ValueError(f"{variant} run is not independently verified for {name}/seed-{seed}")
            _expected_artifact_hashes(case_result, variant, diagnostics_enabled)
            expected_rel = f"{variant}/{name}/seed-{seed}/solution.json"
            if case_result.get("artifact") != expected_rel:
                raise ValueError(f"{variant} artifact path differs for {name}/seed-{seed}")
            artifact_dir = experiment / variant / name / f"seed-{seed}"
            for filename, digest in case_result["artifact_sha256"].items():
                if m8._sha256(artifact_dir / filename) != digest:
                    raise ValueError(f"{variant} {filename} hash differs for {name}/seed-{seed}")
            payload = json.loads((artifact_dir / "solution.json").read_text(encoding="utf-8"))
            public_instance = __import__("vrptw").read_solomon(source)
            public_validate = __import__("vrptw").validate_solution
            verdict = public_validate(public_instance, payload.get("routes", []))
            objective = (int(row["vehicles"]), int(row["distance_ticks"]))
            if (not verdict.feasible or verdict.objective != objective
                    or objective != (payload.get("vehicles"), payload.get("distance_ticks"))):
                raise ValueError(f"{variant} saved solution failed public validation for {name}/seed-{seed}")
            actual_config = dict(config, seed=seed)
            stop = payload.get("stop")
            if (payload.get("config") != actual_config
                    or not isinstance(stop, dict)
                    or stop.get("time_limit_seconds") != time_limit
                    or stop.get("max_iterations") is not None):
                raise ValueError(f"{variant} saved config or budget differs for {name}/seed-{seed}")
            if (payload.get("input_sha256") != input_hash
                    or payload.get("code_sha256") != source_hash
                    or payload.get("numeric_rule") != report.NUMERIC_RULE_ID
                    or payload.get("seed") != seed):
                raise ValueError(f"{variant} saved solution hashes/protocol differ for {name}/seed-{seed}")
            if (int(row["vehicles"]) != verdict.vehicles
                    or int(row["distance_ticks"]) != verdict.distance
                    or int(row["iterations"]) != payload.get("iterations")
                    or not math.isclose(float(row["runtime_seconds"]), float(payload["runtime_seconds"]),
                                        rel_tol=0.0, abs_tol=1e-6)):
                raise ValueError(f"{variant} batch objective or runtime differs for {name}/seed-{seed}")
            if diagnostics_enabled:
                diag = payload.get("diagnostics")
                phases = diag.get("phases") if isinstance(diag, dict) else None
                if (not isinstance(diag, dict) or diag.get("schema_version") != 1
                        or not isinstance(phases, list)
                        or tuple(value.get("phase") for value in phases) != expected_phases):
                    raise ValueError(f"{variant} saved diagnostics differ for {name}/seed-{seed}")
                if not math.isclose(
                        sum(float(value["elapsed_seconds"]) for value in phases),
                        float(payload["runtime_seconds"]), rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError(f"{variant} phase times do not match runtime for {name}/seed-{seed}")
                run_diagnostic_rows = {
                    item.get("phase", ""): item
                    for item in m8._read_csv(artifact_dir / "diagnostics.csv")
                }
                if set(run_diagnostic_rows) != set(expected_phases):
                    raise ValueError(f"{variant} diagnostics.csv phases differ for {name}/seed-{seed}")
                for phase in phases:
                    if (phase["candidates"] != phase["feasible_candidates"] + phase["infeasible_candidates"]
                            or not 0 <= phase["elapsed_seconds"] <= phase["inclusive_seconds"] + 1e-9):
                        raise ValueError(f"{variant} diagnostic counters/timing invalid for {name}/seed-{seed}")
                    for field in DIAGNOSTIC_FIELDS:
                        if field == "phase":
                            continue
                        expected_value = float(phase[field])
                        for diagnostic_row in (
                                run_diagnostic_rows[phase["phase"]],
                                diagnostic_index[(name, str(seed), phase["phase"])],
                        ):
                            if not math.isclose(float(diagnostic_row[field]), expected_value,
                                                rel_tol=0.0, abs_tol=1e-9):
                                raise ValueError(
                                    f"{variant} JSON/CSV diagnostics differ for {name}/seed-{seed}/"
                                    f"{phase['phase']}/{field}"
                                )
        state["_metadata"] = metadata
        state["_rows"] = rows
    if manifest.get("independently_verified") != len(expected_pairs) * len(variants):
        raise ValueError("manifest independent verification count differs")
    return package, report


def _best_rows(rows: list[dict[str, str]]) -> dict[str, tuple[tuple[int, int], int]]:
    best: dict[str, tuple[tuple[int, int], int]] = {}
    for row in rows:
        name, seed = row["instance"], int(row["seed"])
        objective = int(row["vehicles"]), int(row["distance_ticks"])
        if name not in best or objective < best[name][0]:
            best[name] = objective, seed
    return best


def _compare(base: tuple[int, int], candidate: tuple[int, int]):
    if candidate[0] < base[0]:
        return "fewer_vehicles", "not_compared", None, None
    if candidate[0] > base[0]:
        return "more_vehicles", "not_compared", None, None
    delta = candidate[1] - base[1]
    result = "better" if delta < 0 else "worse" if delta > 0 else "tie"
    percent = 100 * delta / base[1] if base[1] else (0.0 if delta == 0 else None)
    return result, result, delta, percent


def _family(name: str) -> str:
    return m8._family(name)


def _bool(value: str | None) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _trajectory_summary(experiment: Path, variants: tuple[str, ...],
                        cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for variant in variants:
        current_infeasible = candidate_infeasible = penalty_updates = 0
        history_rows = history_runs = runs_with_penalty_fields = 0
        feasible_rates: list[float] = []
        load_rates: list[float] = []
        time_rates: list[float] = []
        final_load: list[float] = []
        final_time: list[float] = []
        for case in cases:
            name, seed = case["instance"], case["seed"]
            path = experiment / variant / name / f"seed-{seed}" / "history.csv"
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if not rows:
                continue
            history_runs += 1
            history_rows += len(rows)
            columns = set(rows[0])
            if "current_feasible" in columns:
                current_infeasible += sum(not _bool(row.get("current_feasible")) for row in rows)
                runs_with_penalty_fields += 1
            if "candidate_feasible" in columns:
                candidate_infeasible += sum(
                    row.get("candidate_vehicles", "") != ""
                    and not _bool(row.get("candidate_feasible")) for row in rows
                )
            elif {"candidate_excess_load", "candidate_time_warp"} <= columns:
                candidate_infeasible += sum(
                    (row.get("candidate_excess_load", "") not in ("", "0")
                     or row.get("candidate_time_warp", "") not in ("", "0"))
                    for row in rows
                )
            if "penalty_updated" in columns:
                penalty_updates += sum(_bool(row.get("penalty_updated")) for row in rows)
            for column, collected in (("feasible_rate", feasible_rates),
                                      ("load_feasible_rate", load_rates),
                                      ("time_feasible_rate", time_rates)):
                if column in columns:
                    collected.extend(float(row[column]) for row in rows if row.get(column, "") != "")
            for column, collected in (("load_penalty", final_load), ("time_penalty", final_time)):
                if column in columns:
                    value = rows[-1].get(column, "")
                    if value != "":
                        collected.append(float(value))
        output[variant] = {
            "history_runs": history_runs,
            "history_rows": history_rows,
            "runs_with_current_feasibility": runs_with_penalty_fields,
            "current_infeasible_rows": current_infeasible,
            "candidate_infeasible_rows": candidate_infeasible,
            "penalty_update_rows": penalty_updates,
            "mean_feasible_rate": statistics.fmean(feasible_rates) if feasible_rates else None,
            "mean_load_feasible_rate": statistics.fmean(load_rates) if load_rates else None,
            "mean_time_feasible_rate": statistics.fmean(time_rates) if time_rates else None,
            "median_final_load_penalty": statistics.median(final_load) if final_load else None,
            "median_final_time_penalty": statistics.median(final_time) if final_time else None,
        }
    return output


def analyse_experiment(args: argparse.Namespace) -> int:
    experiment, data_dir = args.out.resolve(), args.data.resolve()
    manifest_path = experiment / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_analysis_input(experiment, data_dir, manifest)
    variants = tuple(manifest["order"]["variants"])
    names = manifest["instances"]
    rows_for = {
        variant: manifest["variants"][variant]["_rows"] for variant in variants
    }
    best_for = {variant: _best_rows(rows_for[variant]) for variant in variants}
    baseline = best_for["baseline"]
    all_rows = [{"variant": variant, **row} for variant in variants for row in rows_for[variant]]
    m8._write_csv(experiment / "runs.csv", all_rows, SUMMARY_FIELDS)
    best_rows: list[dict[str, object]] = []
    metrics: dict[str, Any] = {
        "schema_version": 1,
        "experiment": str(experiment),
        "experiment_manifest_sha256": m8._sha256(manifest_path),
        "independently_verified_runs": len(all_rows),
        "variants": {},
    }
    for variant in variants:
        rows = rows_for[variant]
        counts = {key: 0 for key in (
            "fewer_vehicles", "more_vehicles", "vehicles_same",
            "same_vehicle_distance_better", "same_vehicle_distance_worse",
            "same_vehicle_distance_tie",
        )}
        family_counts = {
            family: {key: 0 for key in counts}
            for family in ("all", "C", "R", "RC")
        }
        for name in names:
            base_obj, base_seed = baseline[name]
            candidate_obj, candidate_seed = best_for[variant][name]
            result, distance_result, delta, percent = _compare(base_obj, candidate_obj)
            family = _family(name)
            for target in (counts, family_counts["all"], family_counts[family]):
                if candidate_obj[0] < base_obj[0]:
                    target["fewer_vehicles"] += 1
                elif candidate_obj[0] > base_obj[0]:
                    target["more_vehicles"] += 1
                else:
                    target["vehicles_same"] += 1
                    target[f"same_vehicle_distance_{distance_result}"] += 1
            best_rows.append({
                "variant": variant, "instance": name, "family": family,
                "baseline_vehicles": base_obj[0], "baseline_distance_ticks": base_obj[1],
                "baseline_best_seed": base_seed,
                "variant_vehicles": candidate_obj[0], "variant_distance_ticks": candidate_obj[1],
                "variant_best_seed": candidate_seed,
                "result_vs_baseline": result,
                "same_vehicle_distance_result_vs_baseline": distance_result,
                "distance_delta_ticks_vs_baseline": "" if delta is None else delta,
                "distance_delta_percent_vs_baseline": "" if percent is None else f"{percent:.6f}",
            })
        grouped: dict[str, list[dict[str, str]]] = {family: [] for family in ("all", "C", "R", "RC")}
        grouped["all"] = rows
        for row in rows:
            grouped[_family(row["instance"])].append(row)
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
        state = manifest["variants"][variant]
        metrics["variants"][variant] = {
            "config": state["config"], "config_sha256": state["config_sha256"],
            "code_sha256": state["code_sha256"], "source": state["source"],
            "runs": len(rows), "comparison_vs_baseline": counts,
            "comparison_by_family": family_counts,
            "runtime_and_iterations_by_family": family_stats,
        }
    metrics["trajectory_by_variant"] = _trajectory_summary(
        experiment, variants, manifest["order"]["cases"],
    )
    m8._write_csv(experiment / "best_by_instance.csv", best_rows, (
        "variant", "instance", "family", "baseline_vehicles", "baseline_distance_ticks",
        "baseline_best_seed", "variant_vehicles", "variant_distance_ticks", "variant_best_seed",
        "result_vs_baseline", "same_vehicle_distance_result_vs_baseline",
        "distance_delta_ticks_vs_baseline", "distance_delta_percent_vs_baseline",
    ))
    m8._write_json(experiment / "metrics.json", metrics)
    print(f"analysed {len(all_rows)} verified runs across {len(names)} instance(s); "
          f"wrote runs.csv, best_by_instance.csv, metrics.json in {experiment}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="freeze the solver and run the M11 variants sequentially")
    run.add_argument("--data", type=Path, default=DATA_DIR,
                     help="directory of top-level Solomon .txt files")
    run.add_argument("--out", type=Path, required=True,
                     help="new or empty experiment directory")
    run.add_argument("--time-limit", type=float, default=0.5, metavar="SECONDS")
    run.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    run.add_argument("--instances", nargs="+", help="optional instance stems for a diagnostic run")
    run.add_argument("--diagnostics", action="store_true",
                     help="save and verify phase/candidate timing diagnostics")
    run.add_argument("--variants", nargs="+", choices=ALL_VARIANTS, default=list(VARIANTS),
                     help="selected variants; baseline is required")
    analyse = commands.add_parser("analyse", help="verify artifacts and write comparison tables")
    analyse.add_argument("--out", type=Path, required=True,
                         help="completed experiment directory")
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
