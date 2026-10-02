"""M8 isolated local-search throughput from saved M7 feasible starting routes."""

import argparse
import csv
from dataclasses import asdict
import importlib.util
import json
from math import isfinite
from pathlib import Path
from statistics import median
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="frozen vrptw package directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=0.5)
    args = parser.parse_args()
    if not isfinite(args.seconds) or args.seconds <= 0:
        parser.error("seconds must be finite and positive")
    spec = importlib.util.spec_from_file_location("m8_rates_frozen", args.source / "__init__.py",
                                                submodule_search_locations=[str(args.source)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    diagnostics = __import__(f"{spec.name}.diagnostics", fromlist=["DiagnosticCollector"])
    search = __import__(f"{spec.name}.local_search", fromlist=["improve"])
    report = __import__(f"{spec.name}.report", fromlist=["code_fingerprint"])
    variants = {"full": ("full", None), "cached": ("cached", None),
                "incremental": ("incremental", None), "n20": ("incremental", 20),
                "n40": ("incremental", 40)}
    args.out.mkdir(parents=True, exist_ok=True)
    rows, saved = [], []
    for case, (name, seed) in enumerate((name, seed) for name in ("C103", "C104", "R101", "RC101")
                                      for seed in (0, 1, 2)):
        instance = package.read_solomon(ROOT / "data" / f"{name}.txt")
        initial_path = ROOT / "runs/m7_verified_delivery_seed012_0p5s" / name / f"seed-{seed}/solution.json"
        routes = tuple(tuple(route) for route in json.loads(initial_path.read_text(encoding="utf-8"))["routes"])
        initial = package.validate_solution(instance, routes)
        assert initial.feasible
        order = list(variants)
        order = order[case % len(order):] + order[:case % len(order)]
        for label in order:
            mode, neighbours = variants[label]
            collector = diagnostics.DiagnosticCollector()
            started = perf_counter()
            result = search.improve(instance, routes, evaluation_mode=mode, num_neighbours=neighbours,
                                    max_moves=None, deadline=started + args.seconds, diagnostics=collector)
            elapsed = perf_counter() - started
            verdict = package.validate_solution(instance, result.routes)
            assert verdict.feasible and verdict.vehicles == initial.vehicles and verdict.distance == result.distance
            phase = next(row for row in collector.snapshot(elapsed).phases if row.phase == "local_search")
            rows.append(dict(variant=label, instance=name, family="RC" if name.startswith("RC") else name[0],
                             seed=seed, initial_distance=initial.distance, distance=verdict.distance,
                             stop_reason=result.stop_reason, **asdict(phase),
                             candidates_per_second=phase.candidates / phase.elapsed_seconds,
                             accepted_per_second=phase.accepted / phase.elapsed_seconds))
            saved.append(dict(variant=label, instance=name, seed=seed, routes=result.routes,
                              vehicles=verdict.vehicles, distance_ticks=verdict.distance))
        print(f"{name} seed={seed}: verified all five throughput variants", flush=True)
    with (args.out / "rates.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = {}
    for label in variants:
        groups[label] = {}
        for family in ("C", "R", "RC"):
            group = [row for row in rows if row["variant"] == label and row["family"] == family]
            seconds = sum(row["elapsed_seconds"] for row in group)
            groups[label][family] = dict(runs=len(group), candidates=sum(row["candidates"] for row in group),
                                        accepted=sum(row["accepted"] for row in group), seconds=seconds,
                                        candidates_per_second=sum(row["candidates"] for row in group) / seconds,
                                        accepted_per_second=sum(row["accepted"] for row in group) / seconds,
                                        median_distance=median(row["distance"] for row in group))
    manifest = dict(code_sha256=report.code_fingerprint(), independent_verified=len(saved),
                    protocol="local search only; fixed M7 saved start; first improvement; unlimited moves",
                    seconds=args.seconds, diagnostics_enabled=True, summary=groups)
    (args.out / "rates.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (args.out / "routes.json").write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
