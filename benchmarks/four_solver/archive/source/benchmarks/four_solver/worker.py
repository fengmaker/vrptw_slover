"""Persistent JSON-lines worker: imports and process launch are outside timing."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import platform
import sys
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks"))
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", choices=("ours", "pyvrp", "ortools", "gurobi"), required=True)
    parser.add_argument("--pyvrp-root", type=Path)
    parser.add_argument("--pyvrp-site", type=Path)
    args = parser.parse_args()
    if args.solver == "pyvrp":
        if args.pyvrp_site:
            sys.path.insert(2, str(args.pyvrp_site.resolve()))
        if args.pyvrp_root:
            sys.path.insert(2, str(args.pyvrp_root.resolve()))
    from vrptw.solomon import read_solomon
    backend = importlib.import_module(f"four_solver.{args.solver}_backend")
    if args.solver == "ortools":
        importlib.import_module("ortools.constraint_solver.pywrapcp")
        importlib.import_module("ortools.constraint_solver.routing_enums_pb2")
    elif args.solver == "gurobi":
        importlib.import_module("gurobipy")
    elif args.solver == "ours":
        importlib.import_module("vrptw._native")
    print(json.dumps({"ready": True, "solver": args.solver,
                      "python": platform.python_version(), "executable": sys.executable}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        try:
            instance = read_solomon(request["input"])
            started = perf_counter()
            result = backend.solve(instance, request["budget"], request["seed"])
            result["adapter_seconds"] = perf_counter() - started
        except Exception as exc:
            result = {"routes": None, "status": "error", "error": str(exc),
                      "metadata": {"traceback": traceback.format_exc()}}
        print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
