"""Supplementary PyVRP distance search under each observed M12 vehicle cap.

This answers a different question from the primary fleet-priority experiment:
how much distance improvement can PyVRP find if given the fleet size M12 found?
Only that scalar cap is shared; no route or reference warm start is provided.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r if k != "routes"))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    rank = (len(values) - 1) * fraction
    low = int(rank)
    high = min(len(values) - 1, low + 1)
    return values[low] + (values[high] - values[low]) * (rank - low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--instances", nargs="+")
    pyvrp = ROOT.parent / "PyVRP-main"
    parser.add_argument("--pyvrp-root", type=Path, default=pyvrp)
    parser.add_argument("--pyvrp-site", type=Path, default=pyvrp / ".venv" / "Lib" / "site-packages")
    args = parser.parse_args()
    sys.path.insert(2, str(args.pyvrp_site.resolve()))
    sys.path.insert(2, str(args.pyvrp_root.resolve()))
    from four_solver.common import sha256, tree_hash
    from four_solver.pyvrp_backend import make_data
    from vrptw import read_solomon, validate_solution
    from pyvrp import solve
    from pyvrp.stop import MaxRuntime

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.primary_run / "runs.csv").open(encoding="utf-8-sig", newline="") as stream:
        primary = [r for r in csv.DictReader(stream) if r["solver"] == "ours" and r["feasible"] == "True"]
    if args.instances:
        selected = {name.upper() for name in args.instances}
        primary = [r for r in primary if r["instance"] in selected]
    if not primary:
        raise ValueError("no feasible M12 primary results")
    manifest = {
        "schema_version": 1, "protocol": "conditioned_on_observed_M12_cap_distance_only",
        "objective": "distance only, fixed_vehicle_cost=0",
        "warm_start": False, "seed": "same as each primary M12 run",
        "cap_source_sha256": sha256(args.primary_run / "runs.csv"),
        "primary_run": str(args.primary_run.resolve()),
        "script_sha256": sha256(Path(__file__)),
        "adapter_sha256": sha256(ROOT / "benchmarks" / "four_solver" / "pyvrp_backend.py"),
        "pyvrp_version": importlib.metadata.version("pyvrp"),
        "pyvrp_code_sha256": tree_hash(sorted(p for p in (args.pyvrp_root / "pyvrp").rglob("*")
                                              if p.suffix in {".py", ".cpp", ".h", ".hpp", ".pyd", ".so"}),
                                       args.pyvrp_root),
        "python": sys.version, "numeric_rule": "solomon_exact_1000_v1",
        "timing": "input and imports excluded; data preparation separate; MaxRuntime anchored before solve",
        "cases": [(r["instance"], r["budget_seconds"], r["seed"], r["vehicles"], r["input_sha256"])
                  for r in primary],
    }
    # Normalize tuples so an interrupted run can compare its persisted manifest.
    manifest = json.loads(json.dumps(manifest))
    manifest_path = args.out / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("resume refused: primary results, code, or protocol changed")
    else:
        write_json(manifest_path, manifest)
    rows = []
    for index, own in enumerate(primary, 1):
        name, budget, seed, cap = own["instance"], float(own["budget_seconds"]), int(own["seed"]), int(own["vehicles"])
        instance_path = args.primary_run / "inputs" / f"{name}.txt"
        if sha256(instance_path) != own["input_sha256"]:
            raise ValueError(f"primary input changed: {name}")
        instance = read_solomon(instance_path)
        path = args.out / "solutions" / f"{budget:g}s" / f"{name}-seed-{seed}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            identity = (row["instance"], row["budget_seconds"], row["seed"], row["vehicle_cap"],
                        row["ours_vehicles"], row["ours_distance_ticks"], row["input_sha256"])
            expected = (name, budget, seed, cap, cap, int(own["distance_ticks"]), own["input_sha256"])
            if identity != expected:
                raise ValueError(f"saved record identity/cap/primary score changed: {path}")
        else:
            started = perf_counter()
            data = make_data(instance)
            data = data.replace(vehicle_types=[data.vehicle_type(0).replace(num_available=cap, fixed_cost=0)])
            preparation = perf_counter() - started
            started = perf_counter()
            stop = MaxRuntime(budget)
            stop(0)
            result = solve(data, stop, seed=seed, collect_stats=False, display=False)
            search = perf_counter() - started
            routes = [list(r.visits()) for r in result.best.routes()] if result.is_feasible() else None
            verdict = validate_solution(instance, routes) if routes is not None else None
            feasible = verdict is not None and verdict.feasible and verdict.vehicles <= cap
            if feasible and result.cost() != verdict.distance:
                raise ValueError("PyVRP distance disagrees with independent recomputation")
            row = {
                "instance": name, "budget_seconds": budget, "seed": seed, "vehicle_cap": cap,
                "status": "feasible" if feasible else "no_feasible_solution",
                "feasible": feasible, "vehicles": verdict.vehicles if feasible else None,
                "distance_ticks": verdict.distance if feasible else None,
                "distance": verdict.distance / 1000 if feasible else None,
                "ours_vehicles": int(own["vehicles"]), "ours_distance_ticks": int(own["distance_ticks"]),
                "preparation_seconds": preparation, "search_seconds": search,
                "solver_runtime_seconds": result.runtime, "iterations": result.num_iterations,
                "input_sha256": own["input_sha256"], "routes": routes,
                "solution_path": path.relative_to(args.out).as_posix(),
            }
            write_json(path, row)
        if row["routes"] is not None:
            verdict = validate_solution(instance, row["routes"])
            if (not verdict.feasible or verdict.vehicles > cap or verdict.distance != row["distance_ticks"]
                    or verdict.vehicles != row["vehicles"] or row["feasible"] is not True):
                raise ValueError(f"saved route failed revalidation: {path}")
        elif row["feasible"] is not False or row["vehicles"] is not None or row["distance_ticks"] is not None:
            raise ValueError(f"missing routes disagree with saved feasibility: {path}")
        row["vehicle_gap_ours_minus_pyvrp"] = row["ours_vehicles"] - row["vehicles"] if row["feasible"] else None
        row["distance_gap_percent"] = (
            100 * (row["ours_distance_ticks"] / row["distance_ticks"] - 1)
            if row["feasible"] and row["vehicles"] == row["ours_vehicles"] and row["distance_ticks"] else None)
        rows.append(row)
        write_csv(args.out / "comparison.csv", rows)
        print(f"{index}/{len(primary)} {name} {budget:g}s cap={cap} {row['status']} "
              f"K={row['vehicles']} D={row['distance']} gap={row['distance_gap_percent']}", flush=True)
    summaries = []
    for budget in sorted({r["budget_seconds"] for r in rows}):
        for family in ("all", "C", "R", "RC"):
            group = [r for r in rows if r["budget_seconds"] == budget and (family == "all" or
                     ("RC" if r["instance"].startswith("RC") else r["instance"][0]) == family)]
            gaps = [r["distance_gap_percent"] for r in group if r["distance_gap_percent"] is not None]
            summaries.append({"budget_seconds": budget, "family": family, "runs": len(group),
                              "feasible": sum(r["feasible"] for r in group),
                              "pyvrp_fewer_vehicles": sum(r["feasible"] and r["vehicles"] < r["ours_vehicles"] for r in group),
                              "same_vehicles": len(gaps),
                              "distance_gap_median_percent": statistics.median(gaps) if gaps else None,
                              "distance_gap_p90_percent": percentile(gaps, .9)})
    write_csv(args.out / "summary.csv", summaries)
    write_json(args.out / "verification.json", {"records": len(rows), "feasible": sum(r["feasible"] for r in rows),
                                               "all_returned_routes_revalidated": True,
                                               "completed_at": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    main()
