"""Profile the unchanged Python search before selecting native migration scope."""

import argparse
import cProfile
import json
from pathlib import Path
import pstats
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vrptw import Config, read_solomon, solve, validate_solution
from vrptw.report import code_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ("C103", "C104", "R101", "RC101"):
        instance = read_solomon(ROOT / "data" / f"{name}.txt")
        profiler = cProfile.Profile()
        result = profiler.runcall(solve, instance, Config(
            max_iterations=None, time_limit_seconds=0.5, diagnostics=True, search_backend="python"))
        assert validate_solution(instance, result.routes).feasible
        profiler.dump_stats(str(args.out / f"{name}.prof"))
        stats = pstats.Stats(profiler).sort_stats("cumulative")
        with (args.out / f"{name}.txt").open("w", encoding="utf-8") as stream:
            stats.stream = stream
            stats.print_stats(35)
        functions = []
        for (path, line, function), (primitive, calls, own, cumulative, _) in stats.stats.items():
            if "vrptw" in path and function in ("delta", "evaluate", "apply_move", "enumerate_moves", "improve"):
                functions.append(dict(file=Path(path).name, line=line, function=function,
                                      calls=calls, own_seconds=own, cumulative_seconds=cumulative))
        rows.append(dict(instance=name, runtime_seconds=result.runtime_seconds,
                         iterations=result.iterations, functions=functions,
                         phases=[dict(phase=p.phase, seconds=p.elapsed_seconds,
                                      candidates=p.candidates, accepted=p.accepted)
                                 for p in result.diagnostics.phases]))
    (args.out / "profile.json").write_text(json.dumps(dict(
        code_sha256=code_fingerprint(), note="cProfile and diagnostics change timing; scope diagnosis only",
        cases=rows), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
