"""Alternate backends on identical deterministic descents, including validation."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vrptw import construct, improve, read_solomon, validate_solution
from vrptw.diagnostics import DiagnosticCollector
from vrptw.native_search import native_metadata
from vrptw.report import code_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output directory must be empty")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    metrics = []
    for name in ("C103", "C104", "R101", "RC101"):
        path = ROOT / "data" / f"{name}.txt"
        instance = read_solomon(path)
        start = construct(instance)
        expected = None
        for enabled in (False, True):
            for repeat in range(args.repeats):
                for backend in (("python", "native") if repeat % 2 == 0 else ("native", "python")):
                    diagnostics = DiagnosticCollector() if enabled else None
                    begin = perf_counter()
                    result = improve(instance, start, max_moves=30, diagnostics=diagnostics,
                                     search_backend=backend)
                    elapsed = perf_counter() - begin
                    assert validate_solution(instance, result.routes).feasible
                    signature = (result.routes, result.moves, result.distance, result.stop_reason)
                    if expected is None:
                        expected = signature
                    assert signature == expected, (name, repeat, backend)
                    phase = (next(p for p in diagnostics.snapshot(elapsed).phases
                                  if p.phase == "local_search") if enabled else None)
                    rows.append(dict(instance=name, repeat=repeat, backend=backend, diagnostics=enabled,
                                     seconds=elapsed,
                                     moves=[asdict(m) for m in result.moves], routes=result.routes,
                                     distance_ticks=result.distance,
                                     counters=asdict(phase) if phase else None,
                                     input_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        own = [r for r in rows if r["instance"] == name]
        timings = {b: statistics.median(r["seconds"] for r in own
                                       if r["backend"] == b and not r["diagnostics"])
                   for b in ("python", "native")}
        candidates = {r["counters"]["candidates"] for r in own if r["diagnostics"]}
        assert len(candidates) == 1, (name, candidates)
        counter_fields = ("candidates", "feasible_candidates", "infeasible_candidates",
                          "rejected_capacity", "rejected_time_window", "rejected_depot_close",
                          "rejected_empty_route", "accepted", "neighbour_filtered")
        assert len({tuple(r["counters"][f] for f in counter_fields) for r in own if r["diagnostics"]}) == 1
        metrics.append(dict(instance=name, timings=timings,
                            speedup=timings["python"] / timings["native"],
                            candidates=candidates.pop(), moves=len(expected[1])))
    payload = dict(code_sha256=code_fingerprint(), native=native_metadata(),
                   protocol=dict(repeats=args.repeats, max_moves=30, diagnostics=[False, True],
                                 deadline=None, includes_python_validation=True),
                   metrics=metrics, runs=rows)
    (args.out / "fixed_work.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
