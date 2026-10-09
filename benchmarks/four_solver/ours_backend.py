"""Unmodified M12 default search, with a time limit instead of iteration cap."""

from dataclasses import asdict
from time import perf_counter

from vrptw import Config, solve as solve_vrptw
from vrptw.native_search import native_metadata


def solve(instance, budget: float, seed: int) -> dict:
    started = perf_counter()
    config = Config(seed=seed, time_limit_seconds=budget, max_iterations=None,
                    search_backend="native")
    preparation = perf_counter() - started
    started = perf_counter()
    result = solve_vrptw(instance, config)
    search = perf_counter() - started
    return {
        "routes": [list(route) for route in result.routes],
        "status": "feasible",
        "preparation_seconds": preparation,
        "search_seconds": search,
        "solver_runtime_seconds": result.runtime_seconds,
        "first_feasible_seconds": result.first_feasible_seconds,
        "iterations": result.iterations,
        "metadata": {
            "config": asdict(config), "backend": result.search_backend,
            "native": native_metadata(), "stop_reason": result.stop_reason,
            "initial_distance_ticks": result.initial_distance,
            "fleet": {"lower_bound": result.fleet.lower_bound,
                      "stop_reason": result.fleet.stop_reason,
                      "targets": [asdict(target) for target in result.fleet.targets]},
            "milestones": [asdict(step) for step in result.fleet.milestones],
        },
    }
