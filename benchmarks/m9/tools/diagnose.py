"""Verify M6 batch artifacts and measure instrumentation on fixed work.

All solving and checking uses the local solver; PyVRP is optional and offline.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from math import isclose
from pathlib import Path
import platform
from statistics import median
import sys

SOLVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOLVER_DIR / "src"))

from vrptw import Config, read_solomon, solve, validate_solution  # noqa: E402
from vrptw.diagnostics import PHASES  # noqa: E402
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint  # noqa: E402


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _check(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def verify_batch(batch: Path, data: Path) -> tuple[dict, list[dict], list[dict[str, str]]]:
    metadata = json.loads((batch / "batch_summary.json").read_text(encoding="utf-8"))
    runs = _csv(batch / "batch_summary.csv")
    available_names = {path.stem for path in data.glob("*.txt")}
    names = metadata.get("instance_names", sorted(available_names))
    _check(names == sorted(set(names)) and not set(names) - available_names,
           "unknown or duplicate selected instance names")
    expected = {(name, str(seed)) for name in names for seed in metadata["seeds"]}
    actual = {(row["instance"], row["seed"]) for row in runs}
    _check(len(runs) == len(actual) == metadata["runs"] and expected == actual,
           "batch coverage or instance/seed uniqueness differs")
    _check(all(row["status"] == "ok" and row["feasible"] == "True" for row in runs),
           "batch includes failed runs")
    _check(metadata["numeric_rule"] == NUMERIC_RULE_ID, "unexpected batch numeric rule")
    long_rows = _csv(batch / "batch_diagnostics.csv") if metadata.get("diagnostics_enabled") else []
    long_index = {(row["instance"], row["seed"], row["phase"]): row for row in long_rows}
    _check(len(long_index) == len(long_rows), "duplicate batch diagnostics rows")
    phase_rows = []
    for row in runs:
        name, seed = row["instance"], row["seed"]
        label = f"{name}/seed-{seed}"
        source = data / f"{name}.txt"
        instance = read_solomon(source)
        payload = json.loads((batch / name / f"seed-{seed}" / "solution.json").read_text(encoding="utf-8"))
        verdict = validate_solution(instance, payload["routes"])
        _check(verdict.feasible, f"{label}: invalid routes: {verdict.first_violation}")
        _check(verdict.objective == (int(row["vehicles"]), int(row["distance_ticks"]))
               == (payload["vehicles"], payload["distance_ticks"]), f"{label}: objective differs")
        _check(payload["input_sha256"] == row["input_sha256"] == _hash(source),
               f"{label}: input hash differs")
        _check(payload["numeric_rule"] == row["numeric_rule"] == NUMERIC_RULE_ID,
               f"{label}: numeric rule differs")
        _check(payload["code_sha256"] == row["code_sha256"] == metadata["code_sha256"],
               f"{label}: code hash differs")
        _check(payload["vehicle_limit"] == instance.vehicle_count == int(row["vehicle_limit"]),
               f"{label}: original vehicle limit differs")
        _check(payload["config"] == dict(metadata["config"], seed=int(seed)),
               f"{label}: configuration differs")
        _check(payload["stop"]["time_limit_seconds"] == metadata["time_limit_seconds"],
               f"{label}: budget differs")
        if not metadata.get("diagnostics_enabled"):
            continue
        phases = payload["diagnostics"]["phases"]
        _check(payload["diagnostics"]["schema_version"] == 1, f"{label}: diagnostic schema differs")
        _check(tuple(phase["phase"] for phase in phases) == PHASES, f"{label}: phases differ")
        _check(isclose(sum(phase["elapsed_seconds"] for phase in phases),
                       payload["runtime_seconds"], abs_tol=1e-9), f"{label}: time accounting differs")
        for phase in phases:
            _check(phase["candidates"] == phase["feasible_candidates"] + phase["infeasible_candidates"],
                   f"{label}/{phase['phase']}: candidate counts differ")
            _check(0 <= phase["elapsed_seconds"] <= phase["inclusive_seconds"] + 1e-9,
                   f"{label}/{phase['phase']}: nested time accounting differs")
            for key in ("rejected_capacity", "rejected_time_window", "rejected_depot_close", "rejected_empty_route"):
                _check(0 <= phase[key] <= phase["infeasible_candidates"], f"{label}: rejection counts differ")
            long = long_index[(name, seed, phase["phase"])]
            _check(all(isclose(float(long[key]), value, abs_tol=1e-9)
                       for key, value in phase.items() if key != "phase"),
                   f"{label}: JSON and batch diagnostics differ")
            phase_rows.append(dict(instance=name, seed=int(seed), runtime_seconds=payload["runtime_seconds"], **phase))
        by_phase = {phase["phase"]: phase for phase in phases}
        _check(by_phase["fleet_reduction"]["trials"] == sum(item["trials"] for item in payload["fleet"]["attempts"]),
               f"{label}: fleet trials differ")
        _check(by_phase["perturbation"]["calls"] == payload["iterations"], f"{label}: perturbation calls differ")
        _check(by_phase["construction"]["accepted"] == instance.customer_count, f"{label}: construction count differs")
    _check(len(long_rows) == len(phase_rows), "extra or missing diagnostics rows")
    return metadata, phase_rows, runs


def verify_pyvrp(path: Path, data: Path) -> dict:
    rows = _csv(path)
    keys = {(row["instance"], row["seed"], row["vehicle_cap"], row["budget_seconds"]) for row in rows}
    _check(len(keys) == len(rows), "duplicate PyVRP runs")
    verified = 0
    for row in rows:
        source = data / f"{row['instance']}.txt"
        _check(_hash(source) == row["input_sha256"], "PyVRP input hash differs")
        if row["status"] != "ok":
            _check(row["status"] == "infeasible", "unexpected PyVRP failure")
            continue
        budget = format(float(row["budget_seconds"]), "g").replace(".", "p") + "s"
        artifact = path.parent / "solutions" / budget / row["instance"] / f"seed-{row['seed']}-cap-{row['vehicle_cap']}.json"
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        verdict = validate_solution(read_solomon(source), payload["routes"])
        _check(verdict.feasible and verdict.vehicles <= int(row["vehicle_cap"]), "invalid PyVRP routes or cap")
        _check(verdict.objective == (int(row["vehicles"]), int(row["distance_ticks"]))
               == (payload["vehicles"], payload["distance_ticks"]), "PyVRP objective differs")
        _check(payload["numeric_rule"] == NUMERIC_RULE_ID and payload["input_sha256"] == row["input_sha256"],
               "PyVRP artifact protocol differs")
        _check(payload["seed"] == int(row["seed"]) and payload["vehicle_cap"] == int(row["vehicle_cap"])
               and payload["budget_seconds"] == float(row["budget_seconds"]), "PyVRP artifact key differs")
        _check(row["own_validator_feasible"] == "True", "PyVRP reported validator disagrees")
        verified += 1
    return dict(runs=len(rows), independently_verified=verified,
                not_found=len(rows) - verified, raw_sha256=_hash(path))


def summarise(args) -> None:
    metadata, phases, runs = verify_batch(args.batch, args.data)
    args.out.mkdir(parents=True, exist_ok=True)
    aggregates = []
    for name in sorted({row["instance"] for row in phases}):
        for phase in PHASES:
            group = [row for row in phases if row["instance"] == name and row["phase"] == phase]
            seconds = sum(row["elapsed_seconds"] for row in group)
            runtime = sum(row["runtime_seconds"] for row in group)
            candidates = sum(row["candidates"] for row in group)
            aggregates.append(dict(
                instance=name, phase=phase, runs=len(group),
                mean_seconds=seconds / len(group), percent_runtime=100 * seconds / runtime,
                mean_inclusive_seconds=sum(row["inclusive_seconds"] for row in group) / len(group),
                candidates=candidates, candidates_per_second=candidates / seconds if seconds else 0,
                route_evaluations=sum(row["route_evaluations"] for row in group),
                accepted=sum(row["accepted"] for row in group),
                infeasible_candidates=sum(row["infeasible_candidates"] for row in group),
                rejected_capacity=sum(row["rejected_capacity"] for row in group),
                rejected_time_window=sum(row["rejected_time_window"] for row in group),
                capacity_prefilter_skips=sum(row["capacity_prefilter_skips"] for row in group),
                trials=sum(row["trials"] for row in group), failures=sum(row["failures"] for row in group),
            ))
    if aggregates:
        _write_csv(args.out / "phase_summary.csv", aggregates)
    summary = dict(
        schema_version=1, batch=str(args.batch), runs=len(runs), independently_verified=len(runs),
        batch_sha256=_hash(args.batch / "batch_summary.csv"), code_sha256=metadata["code_sha256"],
        runtime_median=median(float(row["runtime_seconds"]) for row in runs),
        runtime_max=max(float(row["runtime_seconds"]) for row in runs),
        first_feasible_median=median(float(row["first_feasible_seconds"]) for row in runs),
        first_feasible_max=max(float(row["first_feasible_seconds"]) for row in runs),
        ils_runs=sum(int(row["iterations"]) > 0 for row in runs),
        ils_iterations=sum(int(row["iterations"]) for row in runs),
        environment=metadata["environment"],
    )
    if args.pyvrp:
        summary["pyvrp"] = verify_pyvrp(args.pyvrp, args.data)
    (args.out / "verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def fixed_work(args) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    config = Config(seed=0, max_iterations=1, fleet_attempts_per_k=10, max_moves=1)
    records = []
    for name in args.instances:
        instance = read_solomon(args.data / f"{name}.txt")
        # Warm both paths. Alternate their order to reduce warm-cache bias.
        solve(instance, config)
        solve(instance, replace(config, diagnostics=True))
        for repetition in range(args.repeats):
            results = {}
            for enabled in ((False, True) if repetition % 2 == 0 else (True, False)):
                results[enabled] = solve(instance, replace(config, diagnostics=enabled))
            plain, profiled = results[False], results[True]
            _check(plain.routes == profiled.routes and plain.fleet.attempts == profiled.fleet.attempts,
                   f"{name}: diagnostics changed fixed-work search")
            records.append(dict(instance=name, repetition=repetition,
                                plain_seconds=plain.runtime_seconds, diagnostic_seconds=profiled.runtime_seconds,
                                overhead_percent=100 * (profiled.runtime_seconds / plain.runtime_seconds - 1),
                                vehicles=profiled.evaluation.vehicles, distance_ticks=profiled.evaluation.distance,
                                candidates=sum(row.candidates for row in profiled.diagnostics.phases),
                                route_evaluations=sum(row.route_evaluations for row in profiled.diagnostics.phases)))
            artifact = dict(instance=name, repetition=repetition,
                            config=asdict(replace(config, diagnostics=True)),
                            plain_config=asdict(config),
                            plain_runtime_seconds=plain.runtime_seconds,
                            diagnostic_runtime_seconds=profiled.runtime_seconds,
                            code_sha256=code_fingerprint(),
                            input_sha256=_hash(args.data / f"{name}.txt"),
                            routes=profiled.routes, diagnostics=asdict(profiled.diagnostics))
            (args.out / f"{name}-{repetition}.json").write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    _write_csv(args.out / "overhead.csv", records)
    manifest = dict(code_sha256=code_fingerprint(), config=asdict(config), repeats=args.repeats,
                    numeric_rule=NUMERIC_RULE_ID, python=sys.version, platform=platform.platform(),
                    finished_at_utc=datetime.now(timezone.utc).isoformat())
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"fixed work: {len(records)} pairs; all routes/trials identical; {args.out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("summarise")
    check.add_argument("--batch", type=Path, required=True)
    check.add_argument("--pyvrp", type=Path)
    work = sub.add_parser("fixed-work")
    work.add_argument("--instances", nargs="+", default=["C103", "R101", "RC101"])
    work.add_argument("--repeats", type=int, default=3)
    for item in (check, work):
        item.add_argument("--data", type=Path, default=SOLVER_DIR / "data")
        item.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "fixed-work":
        if args.repeats < 1:
            parser.error("--repeats must be positive")
        fixed_work(args)
    else:
        summarise(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
