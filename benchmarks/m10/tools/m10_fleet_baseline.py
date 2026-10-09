r"""Run PyVRP's explicit fleet-minimisation pipeline for the M10 baseline.

The initial fleet cap is the route count from this project's deterministic
due-date construction. PyVRP receives one total search budget shared between
``minimise_fleet()`` and a final ``solve()`` at the fleet returned by that
method. Input conversion checks are delegated to the established helper in
``benchmarks/pyvrp/run.py``.

Run from ``vrptw_solver`` with the local PyVRP Python environment, for example::

    $env:PYTHONPATH = (Resolve-Path ..\PyVRP-main\.venv\Lib\site-packages).Path
    & '..\PyVRP-main\.uv-python\cpython-3.13.5-windows-x86_64-none\python.exe' `
      benchmarks\m10_fleet_baseline.py --out-dir runs\m10\pyvrp
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
import platform
import sys
import tomllib
from pathlib import Path
from time import monotonic
from typing import Any, Callable

SOLVER_DIR = Path(__file__).resolve().parents[1]
PYVRP_DIR = SOLVER_DIR.parent / "PyVRP-main"
DATA_DIR = SOLVER_DIR / "data"
CONVERTED_DIR = PYVRP_DIR / "data" / "data_vrp"
sys.path.insert(0, str(SOLVER_DIR / "src"))

from vrptw import construct, read_solomon, validate_solution  # noqa: E402
from vrptw.report import NUMERIC_RULE_ID, code_fingerprint  # noqa: E402


COLUMNS = (
    "instance", "budget_seconds", "seed", "initial_vehicle_cap",
    "cap_source", "reduced_vehicle_cap", "status", "pyvrp_feasible",
    "own_validator_feasible", "own_validator_violation", "vehicles",
    "fleet_time_fraction", "fleet_minimise_budget_seconds",
    "solve_budget_seconds",
    "distance_ticks", "distance", "fleet_minimise_wall_seconds",
    "solve_wall_seconds", "total_solve_wall_seconds",
    "pyvrp_runtime_seconds", "input_sha256", "config_sha256", "error",
)


@dataclass(frozen=True, slots=True)
class PyVRPAPI:
    read: Callable[..., Any]
    minimise_fleet: Callable[..., Any]
    solve: Callable[..., Any]
    max_runtime: Callable[[float], Any]
    version: str
    code_sha256: str
    package_path: str
    check_same_problem: Callable[[Any, Any], None]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pyvrp_code_sha256(package_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".cpp", ".h", ".hpp", ".pyd", ".so"):
            digest.update(path.relative_to(package_dir).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_pyvrp() -> PyVRPAPI:
    """Load PyVRP and the existing conversion check only when running a batch."""
    for path in (str(SOLVER_DIR), str(PYVRP_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    pyvrp = importlib.import_module("pyvrp")
    stop = importlib.import_module("pyvrp.stop")
    # This is the same helper used by the fixed-cap PyVRP benchmark. It checks
    # location and vehicle mappings, plus the exact distance and duration matrices.
    # PyVRP has its own regular `benchmarks` package, which can shadow this
    # project's namespace when this script is invoked directly. Load the
    # established conversion checker by its concrete file instead.
    spec = importlib.util.spec_from_file_location(
        "m10_pyvrp_mapping", SOLVER_DIR / "benchmarks" / "pyvrp" / "run.py")
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)
    version = tomllib.loads((PYVRP_DIR / "pyproject.toml").read_text(
        encoding="utf-8"))["project"]["version"]
    package_path = Path(pyvrp.__file__).resolve().parent
    return PyVRPAPI(
        read=pyvrp.read,
        minimise_fleet=pyvrp.minimise_fleet,
        solve=pyvrp.solve,
        max_runtime=stop.MaxRuntime,
        version=version,
        code_sha256=_pyvrp_code_sha256(package_path),
        package_path=str(package_path),
        check_same_problem=helper_module._check_same_problem,
    )


def _read_caps(path: Path | None, names: list[str], instances: dict[str, Any],
               valid_names: set[str]) -> dict[str, dict[str, Any]]:
    """Derive caps from deterministic construction, or read a frozen override.

    Override JSON accepts either ``{"C101": 10}`` or the existing benchmark
    form ``{"C101": {"vehicle_cap": 10, "source": "..."}}``.
    """
    overrides: dict[str, Any] = {}
    override_hash = ""
    if path is not None:
        overrides = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise ValueError("caps file must contain a JSON object")
        override_hash = _sha256(path)

    caps: dict[str, dict[str, Any]] = {}
    for name in names:
        instance = instances[name]
        routes = construct(instance, order="due")
        derived_cap = len(routes)
        if not 1 <= derived_cap <= instance.vehicle_count:
            raise ValueError(f"invalid deterministic construction cap for {name}: {derived_cap}")
        source = "deterministic_construct_due"
        cap = derived_cap
        if path is not None and name in overrides:
            entry = overrides[name]
            if isinstance(entry, dict):
                cap = entry.get("vehicle_cap", entry.get("initial_vehicle_cap"))
                source = str(entry.get("source", f"caps_file_sha256:{override_hash}"))
            else:
                cap = entry
                source = f"caps_file_sha256:{override_hash}"
            if type(cap) is not int:
                raise ValueError(f"cap for {name} must be an integer")
        if not 1 <= cap <= instance.vehicle_count:
            raise ValueError(f"cap for {name} must be in 1..{instance.vehicle_count}")
        caps[name] = {
            "initial_vehicle_cap": cap,
            "deterministic_construct_due_cap": derived_cap,
            "source": source,
        }
    unknown = set(overrides) - valid_names
    if unknown:
        raise ValueError(f"caps file contains unknown instances: {sorted(unknown)}")
    return caps


def _run_case(
    *, instance: Any, source_path: Path, converted_path: Path,
    cap: int, cap_source: str, seed: int, budget: float, out_dir: Path,
    config_sha256: str, api: PyVRPAPI,
    fleet_time_fraction: float = 0.75,
    clock: Callable[[], float] = monotonic,
) -> tuple[dict[str, Any], Path | None]:
    """Run minimisation plus a final solve under one total wall-clock budget."""
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("budget must be finite and positive")
    if (type(fleet_time_fraction) not in (int, float)
            or not math.isfinite(fleet_time_fraction)
            or not 0 <= fleet_time_fraction <= 1):
        raise ValueError("fleet_time_fraction must be finite and in [0, 1]")
    input_hash = _sha256(source_path)
    row: dict[str, Any] = dict.fromkeys(COLUMNS, "")
    row.update(
        instance=instance.name,
        budget_seconds=format(budget, "g"),
        seed=seed,
        initial_vehicle_cap=cap,
        cap_source=cap_source,
        input_sha256=input_hash,
        config_sha256=config_sha256,
        fleet_time_fraction=fleet_time_fraction,
        fleet_minimise_budget_seconds=format(budget * fleet_time_fraction, ".9g"),
    )

    try:
        data = api.read(str(converted_path), round_func="exact")
        api.check_same_problem(instance, data)
        original_vehicle = data.vehicle_type(0)
        data = data.replace(vehicle_types=[
            original_vehicle.replace(num_available=cap)
        ])
    except Exception as exc:
        row.update(status="error", error=f"{type(exc).__name__}: {exc}")
        return row, None

    started = clock()
    fleet_started = clock()
    try:
        # minimise_fleet() returns a vehicle type, not the route that established
        # that fleet as feasible. Only the following solve's result is scored.
        fleet = api.minimise_fleet(
            data, api.max_runtime(budget * fleet_time_fraction), seed=seed,
        )
        fleet_elapsed = clock() - fleet_started
        elapsed_before_solve = clock() - started
        remaining = max(0.0, budget - elapsed_before_solve)
        row["solve_budget_seconds"] = format(remaining, ".9g")
        reduced_data = data.replace(vehicle_types=[fleet])

        solve_started = clock()
        result = api.solve(
            reduced_data,
            api.max_runtime(remaining),
            seed=seed,
            collect_stats=False,
            display=False,
        )
        solve_elapsed = clock() - solve_started
        total_elapsed = clock() - started

        pyvrp_feasible = bool(result.is_feasible())
        routes = [list(route.visits()) for route in result.best.routes()] if pyvrp_feasible else []
        verdict = validate_solution(instance, routes) if pyvrp_feasible else None
        status = "not_found" if not pyvrp_feasible else "ok"
        if verdict is not None and pyvrp_feasible != verdict.feasible:
            status = "validation_mismatch"

        row.update(
            reduced_vehicle_cap=fleet.num_available,
            status=status,
            pyvrp_feasible=pyvrp_feasible,
            own_validator_feasible=(verdict.feasible if verdict is not None else False),
            own_validator_violation=(
                verdict.first_violation.code
                if verdict is not None and verdict.first_violation else ""
            ),
            vehicles=verdict.vehicles if verdict is not None else "",
            distance_ticks=verdict.distance if verdict is not None and verdict.feasible else "",
            distance=(verdict.distance / 1000
                      if verdict is not None and verdict.feasible else ""),
            fleet_minimise_wall_seconds=round(fleet_elapsed, 6),
            solve_wall_seconds=round(solve_elapsed, 6),
            total_solve_wall_seconds=round(total_elapsed, 6),
            pyvrp_runtime_seconds=getattr(result, "runtime", ""),
        )

        artifact = None
        if status == "ok" and verdict is not None and verdict.feasible:
            artifact = out_dir / "solutions" / instance.name / f"seed-{seed}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps({
                "instance": instance.name,
                "seed": seed,
                "budget_seconds": budget,
                "fleet_time_fraction": fleet_time_fraction,
                "fleet_minimise_budget_seconds": budget * fleet_time_fraction,
                "solve_budget_seconds": remaining,
                "fleet_minimise_wall_seconds": round(fleet_elapsed, 6),
                "solve_wall_seconds": round(solve_elapsed, 6),
                "total_solve_wall_seconds": round(total_elapsed, 6),
                "initial_vehicle_cap": cap,
                "cap_source": cap_source,
                "reduced_vehicle_cap": fleet.num_available,
                "routes": routes,
                "vehicles": verdict.vehicles,
                "distance_ticks": verdict.distance,
                "numeric_rule": NUMERIC_RULE_ID,
                "input_sha256": input_hash,
                "solver_code_sha256": code_fingerprint(),
                "pyvrp_code_sha256": api.code_sha256,
                "pyvrp_package_path": api.package_path,
                "script_sha256": _sha256(Path(__file__).resolve()),
                "config_sha256": config_sha256,
            }, indent=2) + "\n", encoding="utf-8")
        return row, artifact
    except Exception as exc:
        row.update(
            status="error",
            fleet_minimise_wall_seconds=round(clock() - fleet_started, 6),
            total_solve_wall_seconds=round(clock() - started, 6),
            error=f"{type(exc).__name__}: {exc}",
        )
        return row, None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DIR,
                        help="directory containing Solomon TXT instances")
    parser.add_argument("--instances", nargs="*",
                        help="Solomon instance names; default: all 56")
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--budget", type=float, default=0.5,
                        help="one total budget shared by fleet minimisation and solve")
    parser.add_argument("--fleet-time-fraction", type=float, default=0.75,
                        help="fraction of the total budget passed to minimise_fleet")
    parser.add_argument("--caps", type=Path,
                        help="optional JSON cap overrides; defaults come from due-date construction")
    parser.add_argument("--out-dir", type=Path,
                        default=SOLVER_DIR / "runs" / "m10" / "pyvrp")
    return parser


def run_experiment(
    args: argparse.Namespace, *, api: PyVRPAPI | None = None,
    clock: Callable[[], float] = monotonic,
) -> int:
    if not math.isfinite(args.budget) or not 0 < args.budget <= 3600:
        raise ValueError("budget must be in (0, 3600] seconds")
    fleet_time_fraction = getattr(args, "fleet_time_fraction", 0.75)
    if (type(fleet_time_fraction) not in (int, float)
            or not math.isfinite(fleet_time_fraction)
            or not 0 <= fleet_time_fraction <= 1):
        raise ValueError("fleet_time_fraction must be finite and in [0, 1]")
    if (not args.seeds
            or any(type(seed) is not int or seed < 0 for seed in args.seeds)
            or any(left >= right for left, right in zip(args.seeds, args.seeds[1:]))):
        raise ValueError("seeds must be unique, ascending, nonnegative integers")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {args.out_dir}")
    paths = {path.stem: path for path in args.data.glob("*.txt")}
    names = args.instances or sorted(paths)
    if not names:
        raise ValueError(f"no Solomon TXT files found in {args.data}")
    if len(set(names)) != len(names):
        raise ValueError("instance selection contains duplicates")
    unknown = set(names) - paths.keys()
    if unknown:
        raise ValueError(f"unknown instances: {sorted(unknown)}")
    pyvrp = api or _load_pyvrp()
    instances = {name: read_solomon(paths[name]) for name in names}
    caps = _read_caps(args.caps, names, instances, set(paths))
    config = {
        "instances": names,
        "seeds": list(args.seeds),
        "budget_seconds": args.budget,
        "fleet_time_fraction": fleet_time_fraction,
        "initial_cap_policy": "deterministic_construct_due" if args.caps is None else "caps_json_override",
        "caps": caps,
        "pyvrp_pipeline": ["minimise_fleet", "solve_reduced_data"],
        "budget_policy": "remaining_total_budget_for_final_solve",
        "fleet_minimise_result_semantics": (
            "vehicle_type_cap_only; no feasible route is carried into final solve"
        ),
        "conversion_round_func": "exact",
        "numeric_rule": NUMERIC_RULE_ID,
    }
    config_hash = _json_sha256(config)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.json"
    manifest = {
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pyvrp_version": pyvrp.version,
        "numeric_rule": NUMERIC_RULE_ID,
        "config": config,
        "config_sha256": config_hash,
        "source_hashes": {
            "solver_code_sha256": code_fingerprint(),
            "pyvrp_code_sha256": pyvrp.code_sha256,
            "script_sha256": _sha256(Path(__file__).resolve()),
        },
        "pyvrp_package_path": pyvrp.package_path,
        "input_sha256": {name: _sha256(paths[name]) for name in names},
        "conversion_checked_with": "benchmarks/pyvrp/run.py:_check_same_problem",
        "cases": [],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    raw_path = args.out_dir / "runs.csv"
    with raw_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for name in names:
            for seed in args.seeds:
                row, _artifact = _run_case(
                    instance=instances[name],
                    source_path=paths[name],
                    converted_path=CONVERTED_DIR / f"{name}.vrp",
                    cap=caps[name]["initial_vehicle_cap"],
                    cap_source=caps[name]["source"],
                    seed=seed,
                    budget=args.budget,
                    out_dir=args.out_dir,
                    config_sha256=config_hash,
                    api=pyvrp,
                    fleet_time_fraction=fleet_time_fraction,
                    clock=clock,
                )
                writer.writerow(row)
                stream.flush()
                case = {
                    "instance": name,
                    "seed": seed,
                    "status": row["status"],
                    "artifact": None,
                    "artifact_sha256": None,
                }
                if _artifact is not None:
                    case.update(
                        artifact=_artifact.relative_to(args.out_dir).as_posix(),
                        artifact_sha256=_sha256(_artifact),
                    )
                manifest["cases"].append(case)
                print(f"{name} seed={seed}: {row['status']} "
                      f"K={row['vehicles']} wall={row['total_solve_wall_seconds']}s",
                      flush=True)
    status_counts: dict[str, int] = {}
    for case in manifest["cases"]:
        status_counts[case["status"]] = status_counts.get(case["status"], 0) + 1
    manifest["counts"] = {
        "ok": status_counts.get("ok", 0),
        "not_found": status_counts.get("not_found", 0),
        "errors": status_counts.get("error", 0) + status_counts.get("validation_mismatch", 0),
        "validation_mismatch": status_counts.get("validation_mismatch", 0),
        "by_status": status_counts,
    }
    manifest["artifacts"] = {
        "runs_csv": {"path": raw_path.name, "sha256": _sha256(raw_path)},
    }
    manifest["runs_sha256"] = _sha256(raw_path)
    manifest["status"] = (
        "completed" if manifest["counts"]["errors"] == 0 else "completed_with_errors"
    )
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0 if manifest["counts"]["errors"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return run_experiment(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
