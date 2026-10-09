"""Compare a vrptw batch with the local PyVRP fixed-cap baseline."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

SOLVER_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"

COLUMNS = (
    "instance", "ours_vehicles", "pyvrp_vehicles", "vehicle_gap",
    "ours_distance", "pyvrp_distance", "distance_gap", "distance_gap_percent",
    "winner", "budget_seconds", "seeds_compared", "vehicle_cap",
    "ours_feasible_runs", "pyvrp_feasible_runs", "ours_best_seed",
    "pyvrp_best_seed",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _objective(row: dict[str, str], *, pyvrp: bool) -> tuple[int, int] | None:
    if row["status"] != "ok":
        return None
    if not pyvrp and row["feasible"] != "True":
        return None
    if pyvrp and row["own_validator_feasible"] != "True":
        return None
    return int(row["vehicles"]), int(row["distance_ticks"])


def _compare(ours: tuple[int, int] | None,
             pyvrp: tuple[int, int] | None) -> tuple[str, str, str, str]:
    """Return winner, vehicle gap, distance gap, and distance gap percentage.

    Positive gaps mean our solver is worse. Distance is compared only at the
    same vehicle count, as required by the lexicographic VRPTW objective.
    """
    if ours is None and pyvrp is None:
        return "neither_feasible", "", "", ""
    if ours is None:
        return "only_pyvrp_feasible", "", "", ""
    if pyvrp is None:
        return "only_ours_feasible", "", "", ""
    vehicle_gap = ours[0] - pyvrp[0]
    if ours[0] < pyvrp[0]:
        return "ours", str(vehicle_gap), "", ""
    if ours[0] > pyvrp[0]:
        return "pyvrp", str(vehicle_gap), "", ""
    distance_gap = ours[1] - pyvrp[1]
    gap_percent = round(100 * distance_gap / pyvrp[1], 4)
    if ours[1] < pyvrp[1]:
        return "ours", "0", f"{distance_gap / 1000:.3f}", str(gap_percent)
    if ours[1] > pyvrp[1]:
        return "pyvrp", "0", f"{distance_gap / 1000:.3f}", str(gap_percent)
    return "tie", "0", "0.000", "0"


def _family(name: str) -> str:
    return "RC" if name.startswith("RC") else name[0]


def _check_ours_budget(path: Path, rows: list[dict[str, str]], budget: float) -> None:
    """Check CSV budgets, or the M5 sidecar for historical CSVs."""
    sidecar = path.with_suffix(".json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None
    if metadata is not None and metadata.get("time_limit_seconds") != budget:
        raise ValueError("own batch metadata has a different time budget")
    if rows and "time_limit_seconds" in rows[0]:
        if any(not row["time_limit_seconds"] or float(row["time_limit_seconds"]) != budget
               for row in rows):
            raise ValueError("own batch CSV has a different time budget")
    elif metadata is None:
        raise ValueError("own batch has no recorded time budget (CSV or JSON sidecar required)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ours", required=True, type=Path,
                        help="batch_summary.csv from python -m vrptw batch")
    parser.add_argument("--pyvrp", type=Path,
                        help="PyVRP run-level CSV; defaults to results/<budget>s_runs.csv")
    parser.add_argument("--budget", type=float, default=0.5)
    parser.add_argument("--caps", type=Path, default=RESULTS_DIR.parent / "caps.json",
                        help="fixed baseline cap snapshot; smaller new caps require fresh PyVRP runs")
    parser.add_argument("--require-complete", action="store_true",
                        help="require a matching PyVRP row for every own instance/seed")
    parser.add_argument("--out", type=Path, help="optional per-run comparison CSV")
    args = parser.parse_args(argv)
    budget_label = format(args.budget, "g").replace(".", "p")
    pyvrp_path = args.pyvrp or RESULTS_DIR / f"{budget_label}s_runs.csv"

    own_rows = _rows(args.ours)
    _check_ours_budget(args.ours, own_rows, args.budget)
    fixed_caps = json.loads(args.caps.read_text(encoding="utf-8"))
    own_index = {(row["instance"], row["seed"]): row for row in own_rows}
    if len(own_index) != len(own_rows):
        raise ValueError("own batch has duplicate instance/seed rows")
    baseline = [row for row in _rows(pyvrp_path)
                if float(row["budget_seconds"]) == args.budget]
    if not baseline:
        raise ValueError(f"no PyVRP rows for budget {args.budget:g}s")
    py_index = {(row["instance"], row["seed"], int(row["vehicle_cap"])): row
                for row in baseline}
    if len(py_index) != len(baseline):
        raise ValueError("PyVRP baseline has duplicate instance/seed/budget/cap rows")

    # Compare at the best vehicle count achieved by the new batch. When that
    # improves, a new PyVRP run at that cap is required before comparing.
    target_caps: dict[str, int] = {}
    for row in own_rows:
        if (objective := _objective(row, pyvrp=False)) is not None:
            name = row["instance"]
            target_caps[name] = min(target_caps.get(name, objective[0]), objective[0])
    for name in {row["instance"] for row in own_rows} - target_caps.keys():
        available = [key[2] for key in py_index if key[0] == name]
        if available:
            target_caps[name] = max(available)
    # A slower/new run may use more vehicles than the frozen baseline. Keep
    # that original cap; only an improvement below it needs a new baseline.
    for name, cap in target_caps.items():
        if name in fixed_caps:
            target_caps[name] = min(cap, int(fixed_caps[name]["vehicle_cap"]))

    selected: dict[tuple[str, str], dict[str, str]] = {}
    for name, seed in own_index:
        if name not in target_caps:
            continue
        key = (name, seed, target_caps[name])
        if key in py_index:
            selected[(name, seed)] = py_index[key]
        elif any(py_key[:2] == (name, seed) for py_key in py_index):
            raise ValueError(
                f"missing PyVRP baseline for {name} seed={seed} "
                f"at vehicle cap {target_caps[name]}; run "
                "benchmarks/pyvrp/run.py --caps-from-batch <new batch_summary.csv>"
            )

    matched = own_index.keys() & selected.keys()
    if not matched:
        raise ValueError("no matching instance/seed/cap runs")
    if args.require_complete and matched != own_index.keys():
        raise ValueError(f"missing PyVRP runs: {sorted(own_index.keys() - matched)}")
    for key in matched:
        ours, pyvrp = own_index[key], selected[key]
        if ours["input_sha256"] != pyvrp["input_sha256"]:
            raise ValueError(f"input hash differs for {key}")
        if ours["numeric_rule"] != "solomon_exact_1000_v1":
            raise ValueError(f"unexpected numeric rule for {key}")

    # A paper-style table has one row per instance: each solver's best feasible
    # (vehicles, distance) over the same matched seeds. Keep seed IDs visible.
    comparisons: list[dict[str, str | int]] = []
    for name in sorted({key[0] for key in matched}):
        keys = sorted((key for key in matched if key[0] == name),
                      key=lambda key: int(key[1]))
        own_feasible = [(_objective(own_index[key], pyvrp=False), int(key[1]))
                        for key in keys]
        py_feasible = [(_objective(selected[key], pyvrp=True), int(key[1]))
                       for key in keys]
        own_best = min(((obj, seed) for obj, seed in own_feasible if obj is not None),
                       default=None)
        py_best = min(((obj, seed) for obj, seed in py_feasible if obj is not None),
                      default=None)
        own_obj = own_best[0] if own_best else None
        py_obj = py_best[0] if py_best else None
        winner, vehicle_gap, distance_gap, gap_percent = _compare(own_obj, py_obj)
        comparisons.append(dict(
            instance=name,
            budget_seconds=f"{args.budget:g}",
            seeds_compared=";".join(key[1] for key in keys),
            vehicle_cap=target_caps[name],
            ours_feasible_runs=sum(obj is not None for obj, _ in own_feasible),
            pyvrp_feasible_runs=sum(obj is not None for obj, _ in py_feasible),
            ours_vehicles=own_obj[0] if own_obj else "",
            pyvrp_vehicles=py_obj[0] if py_obj else "",
            vehicle_gap=vehicle_gap,
            ours_distance=f"{own_obj[1] / 1000:.3f}" if own_obj else "",
            pyvrp_distance=f"{py_obj[1] / 1000:.3f}" if py_obj else "",
            distance_gap=distance_gap,
            distance_gap_percent=gap_percent,
            winner=winner,
            ours_best_seed=own_best[1] if own_best else "",
            pyvrp_best_seed=py_best[1] if py_best else "",
        ))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(comparisons)

    print(f"Matched runs: {len(matched)}; instances: {len(comparisons)} "
          f"at {args.budget:g}s")
    for family in ("C", "R", "RC"):
        family_rows = [row for row in comparisons if _family(str(row["instance"])) == family]
        counts = Counter(str(row["winner"]) for row in family_rows)
        fewer = sum(row["vehicle_gap"] != "" and int(str(row["vehicle_gap"])) > 0
                    for row in family_rows)
        print(f"{family}: {len(family_rows)} instances; PyVRP fewer vehicles={fewer}; "
              + ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
