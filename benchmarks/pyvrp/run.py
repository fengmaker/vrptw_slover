"""Run the local PyVRP checkout against this project's Solomon instances.

Run with PyVRP-main's Python environment. This is an offline benchmark helper;
the vrptw package never imports PyVRP while solving.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import platform
import sys
import tomllib
from pathlib import Path
from time import monotonic

import numpy as np

SOLVER_DIR = Path(__file__).resolve().parents[2]
PYVRP_DIR = SOLVER_DIR.parent / "PyVRP-main"
sys.path.insert(0, str(SOLVER_DIR / "src"))
sys.path.insert(0, str(PYVRP_DIR))

from pyvrp import read, solve  # noqa: E402
from pyvrp.stop import MaxRuntime  # noqa: E402
from vrptw import read_solomon, validate_solution  # noqa: E402
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint  # noqa: E402


COLUMNS = (
    "instance", "budget_seconds", "seed", "vehicle_cap", "cap_source",
    "input_sha256", "status", "feasible", "vehicles", "distance_ticks",
    "distance", "pyvrp_runtime_seconds", "solve_wall_seconds",
    "own_validator_feasible", "own_validator_violation", "python_version",
    "pyvrp_version", "error",
)

SUMMARY_COLUMNS = (
    "instance", "budget_seconds", "vehicle_cap", "runs", "feasible_runs",
    "best_vehicles", "best_distance", "best_distance_ticks", "best_seed",
    "status", "seeds_run", "cap_source", "input_sha256",
)


def _check_same_problem(instance, data) -> None:
    """Reject converted input if it differs from our Solomon contract."""
    locations = [*data.depots(), *data.clients()]
    vehicle = data.vehicle_type(0)
    if len(locations) != len(instance.customers) or data.num_vehicle_types != 1:
        raise ValueError("different depot, customer, or vehicle-type count")
    depot = instance.customers[0]
    py_depot = locations[0]
    if (
        py_depot.x != depot.x * 1000
        or py_depot.y != depot.y * 1000
        or vehicle.tw_early != depot.ready
        or vehicle.tw_late != depot.due
        or vehicle.capacity != [instance.capacity * 1000]
        or vehicle.num_available != instance.vehicle_count
    ):
        raise ValueError("converted depot or vehicle differs from Solomon TXT")
    for customer, py_client in zip(instance.customers[1:], locations[1:]):
        if (
            py_client.x != customer.x * 1000
            or py_client.y != customer.y * 1000
            or py_client.delivery != [customer.demand * 1000]
            or py_client.tw_early != customer.ready
            or py_client.tw_late != customer.due
            or py_client.service_duration != customer.service
        ):
            raise ValueError(f"converted customer {customer.id} differs from Solomon TXT")
    matrix = np.asarray(instance.distance)
    if not (
        np.array_equal(data.distance_matrix(0), matrix)
        and np.array_equal(data.duration_matrix(0), matrix)
    ):
        raise ValueError("converted distance or time matrix differs from Solomon TXT")


def _read_existing(path: Path) -> set[tuple[str, str, str, str]]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"unexpected existing CSV columns: {path}")
        return {
            (row["instance"], row["budget_seconds"], row["seed"], row["vehicle_cap"])
            for row in reader
        }


def _write_budget_summary(path: Path, budget: float) -> None:
    """Summarise all available seeds at one budget and each vehicle cap."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    budget_text = format(budget, "g")
    groups: dict[tuple[str, int], list[dict[str, str]]] = {}
    for row in rows:
        if row["budget_seconds"] != budget_text:
            raise ValueError(f"unexpected budget in {path}: {row['budget_seconds']}")
        groups.setdefault((row["instance"], int(row["vehicle_cap"])), []).append(row)
    summary_path = path.with_name(path.name.removesuffix("_runs.csv") + ".csv")
    with summary_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for (name, cap), group in sorted(groups.items()):
            feasible = [row for row in group
                        if row["status"] == "ok"
                        and row["own_validator_feasible"] == "True"]
            best = min(feasible,
                       key=lambda row: (int(row["vehicles"]),
                                        int(row["distance_ticks"]), int(row["seed"])),
                       default=None)
            hashes = {row["input_sha256"] for row in group}
            if len(hashes) != 1:
                raise ValueError(f"mixed input hashes for {name} at {budget_text}s")
            writer.writerow(dict(
                instance=name, budget_seconds=budget_text, vehicle_cap=cap,
                runs=len(group), feasible_runs=len(feasible),
                best_vehicles=best["vehicles"] if best else "",
                best_distance=(f"{int(best['distance_ticks']) / 1000:.3f}"
                               if best else ""),
                best_distance_ticks=best["distance_ticks"] if best else "",
                best_seed=best["seed"] if best else "",
                status="ok" if best else "not_found",
                seeds_run=";".join(sorted({row["seed"] for row in group},
                                          key=int)),
                cap_source=group[0]["cap_source"],
                input_sha256=next(iter(hashes)),
            ))
    print(f"Saved {len(groups)} instance scores to {summary_path}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", nargs="*", help="Solomon names; default: all 56")
    parser.add_argument("--budgets", nargs="+", type=float, default=[0.5])
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--caps", type=Path,
                        default=Path(__file__).resolve().parent / "caps.json")
    parser.add_argument("--caps-from-batch", type=Path,
                        help="override caps with the best vehicle count in this batch")
    parser.add_argument("--out-dir", type=Path,
                        default=Path(__file__).resolve().parent / "results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if any(not 0 < budget <= 3600 for budget in args.budgets):
        raise ValueError("budgets must be in (0, 3600] seconds")
    caps = json.loads(args.caps.read_text(encoding="utf-8"))
    if args.caps_from_batch:
        batch_hash = hashlib.sha256(args.caps_from_batch.read_bytes()).hexdigest()[:12]
        with args.caps_from_batch.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            if row["status"] == "ok" and row["feasible"] == "True":
                name = row["instance"]
                if name not in caps:
                    raise ValueError(f"unknown batch instance: {name}")
                candidate = int(row["vehicles"])
                current = caps[name].get("batch_vehicle_cap", 10**9)
                if candidate < current:
                    caps[name]["batch_vehicle_cap"] = candidate
        for name in caps:
            if "batch_vehicle_cap" in caps[name]:
                caps[name]["vehicle_cap"] = caps[name].pop("batch_vehicle_cap")
                caps[name]["source"] = f"batch_sha256:{batch_hash}"
    names = args.instances or sorted(caps)
    unknown = set(names) - set(caps)
    if unknown:
        raise ValueError(f"unknown instances: {sorted(unknown)}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pyvrp_version = tomllib.loads(
        (PYVRP_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    digest = hashlib.sha256()
    for path in sorted((PYVRP_DIR / "pyvrp").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".cpp", ".h", ".hpp", ".pyd", ".so"):
            digest.update(path.relative_to(PYVRP_DIR).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    manifest = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(), "platform": platform.platform(),
        "pyvrp_version": pyvrp_version, "pyvrp_code_sha256": digest.hexdigest(),
        "validator_code_sha256": code_fingerprint(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numeric_rule": NUMERIC_RULE_ID, "budgets": args.budgets, "seeds": args.seeds,
        "caps": {name: caps[name] for name in names},
    }
    # Preserve earlier invocation manifests when resuming a long benchmark.
    manifest_path = args.out_dir / ("manifest-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%f") + ".json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for budget in dict.fromkeys(args.budgets):
        budget_text = format(budget, "g")
        label = budget_text.replace(".", "p")
        raw_path = args.out_dir / f"{label}s_runs.csv"
        completed = _read_existing(raw_path)
        pending = [(name, seed) for name in names for seed in args.seeds
                   if (name, budget_text, str(seed), str(caps[name]["vehicle_cap"]))
                   not in completed]
        if not pending:
            print(f"{budget_text}s: all requested runs already recorded", flush=True)
            _write_budget_summary(raw_path, budget)
            continue
        new_file = not raw_path.exists()
        with raw_path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            for name, seed in pending:
                source = SOLVER_DIR / "data" / f"{name}.txt"
                converted = PYVRP_DIR / "data" / "data_vrp" / f"{name}.vrp"
                instance = read_solomon(source)
                input_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
                data = read(str(converted), round_func="exact")
                _check_same_problem(instance, data)
                cap = caps[name]["vehicle_cap"]
                data = data.replace(vehicle_types=[
                    data.vehicle_type(0).replace(num_available=cap)
                ])
                row = dict.fromkeys(COLUMNS, "")
                row.update(instance=name, budget_seconds=budget_text, seed=seed,
                           vehicle_cap=cap, cap_source=caps[name]["source"],
                           input_sha256=input_sha256,
                           python_version=platform.python_version(),
                           pyvrp_version=pyvrp_version)
                started = monotonic()
                try:
                    result = solve(data, MaxRuntime(budget), seed=seed,
                                   collect_stats=False)
                    wall = monotonic() - started
                    routes = [route.visits() for route in result.best.routes()]
                    verdict = validate_solution(instance, routes)
                    row.update(
                        status="ok" if verdict.feasible else "infeasible",
                        feasible=result.is_feasible(),
                        vehicles=verdict.vehicles,
                        distance_ticks=verdict.distance if verdict.feasible else "",
                        distance=(verdict.distance / 1000 if verdict.feasible else ""),
                        pyvrp_runtime_seconds=round(result.runtime, 6),
                        solve_wall_seconds=round(wall, 6),
                        own_validator_feasible=verdict.feasible,
                        own_validator_violation=(
                            verdict.first_violation.code if verdict.first_violation else ""
                        ),
                    )
                    if result.is_feasible() != verdict.feasible:
                        row["status"] = "validation_mismatch"
                    if row["status"] == "ok":
                        artifact = args.out_dir / "solutions" / f"{label}s" / name / f"seed-{seed}-cap-{cap}.json"
                        artifact.parent.mkdir(parents=True, exist_ok=True)
                        artifact.write_text(json.dumps({
                            "instance": name, "seed": seed, "vehicle_cap": cap,
                            "budget_seconds": budget, "input_sha256": input_sha256,
                            "numeric_rule": NUMERIC_RULE_ID, "routes": routes,
                            "vehicles": verdict.vehicles, "distance_ticks": verdict.distance,
                            "pyvrp_code_sha256": manifest["pyvrp_code_sha256"],
                            "validator_code_sha256": manifest["validator_code_sha256"],
                        }, indent=2) + "\n", encoding="utf-8")
                except Exception as exc:
                    row.update(status="error", solve_wall_seconds=round(monotonic() - started, 6),
                               error=f"{type(exc).__name__}: {exc}")
                writer.writerow(row)
                stream.flush()  # A long 60-second batch can resume safely.
                print(f"{name} {budget:g}s seed={seed}: {row['status']} "
                      f"K={row['vehicles']} d={row['distance']} "
                      f"wall={row['solve_wall_seconds']}s", flush=True)
        _write_budget_summary(raw_path, budget)
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
