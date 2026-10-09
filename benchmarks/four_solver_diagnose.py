"""Measure M12 phase allocation separately from the quality comparison."""

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vrptw import Config, read_solomon, solve, validate_solution
from vrptw.report import code_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--instances", nargs="+", default=["C103", "C104", "R101", "RC101"])
    parser.add_argument("--budgets", nargs="+", type=float, default=[0.5, 5])
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    protocol = {"instances": [name.upper() for name in args.instances], "budgets": args.budgets,
                "seed": args.seed, "code_sha256": code_fingerprint(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    manifest = args.out / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")) != protocol:
        raise ValueError("diagnostic output protocol changed; use a new output directory")
    manifest.write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    rows = []
    for name in args.instances:
        input_path = ROOT / "data" / f"{name.upper()}.txt"
        instance = read_solomon(input_path)
        for budget in args.budgets:
            config = Config(seed=args.seed, time_limit_seconds=budget, max_iterations=None,
                            search_backend="native", diagnostics=True)
            result = solve(instance, config)
            verdict = validate_solution(instance, result.routes)
            if not verdict.feasible:
                raise AssertionError(verdict.first_violation)
            phases = [asdict(phase) for phase in result.diagnostics.phases]
            payload = {"instance": instance.name, "budget_seconds": budget, "seed": args.seed,
                       "note": "instrumented phase diagnosis, not a quality-ranking run",
                       "config": asdict(config), "code_sha256": code_fingerprint(),
                       "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                       "runtime_seconds": result.runtime_seconds,
                       "first_feasible_seconds": result.first_feasible_seconds,
                       "iterations": result.iterations, "vehicles": verdict.vehicles,
                       "distance_ticks": verdict.distance, "phases": phases,
                       "fleet_targets": [asdict(target) for target in result.fleet.targets],
                       "routes": [list(route) for route in result.routes]}
            (args.out / f"{instance.name}-{budget:g}s-seed-{args.seed}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for phase in phases:
                rows.append({"instance": instance.name, "budget_seconds": budget,
                             "seed": args.seed,
                             "runtime_seconds": result.runtime_seconds,
                             "first_feasible_seconds": result.first_feasible_seconds,
                             "iterations": result.iterations,
                             **phase,
                             "runtime_share_percent": 100 * phase["elapsed_seconds"] / result.runtime_seconds})
            print(f"{instance.name} {budget:g}s K={verdict.vehicles} D={verdict.distance/1000} "
                  f"iterations={result.iterations}", flush=True)
    with (args.out / "phases.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
