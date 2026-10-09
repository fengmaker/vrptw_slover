"""Local PyVRP ILS with the same integer data and dominating vehicle cost."""

import importlib.metadata
from time import perf_counter

import numpy as np
from pyvrp import Client, Depot, ProblemData, VehicleType, solve as solve_pyvrp
from pyvrp.stop import MaxRuntime

from .common import fleet_cost


def make_data(instance):
    depot = instance.customers[0]
    clients = [Client(c.x, c.y, delivery=[c.demand], service_duration=c.service,
                      tw_early=c.ready, tw_late=c.due, required=True, name=str(c.id))
               for c in instance.customers[1:]]
    depots = [Depot(depot.x, depot.y, tw_early=depot.ready, tw_late=depot.due)]
    vehicles = [VehicleType(num_available=instance.vehicle_count,
                            capacity=[instance.capacity], fixed_cost=fleet_cost(instance),
                            tw_early=depot.ready, tw_late=depot.due,
                            unit_distance_cost=1, unit_duration_cost=0)]
    matrix = np.asarray(instance.distance, dtype=np.int64)
    data = ProblemData(clients, depots, vehicles, [matrix], [matrix])
    # Fail immediately if an API conversion silently changes the shared matrix.
    assert np.array_equal(data.distance_matrix(0), matrix)
    assert np.array_equal(data.duration_matrix(0), matrix)
    return data


def solve(instance, budget: float, seed: int) -> dict:
    started = perf_counter()
    data = make_data(instance)
    preparation = perf_counter() - started
    started = perf_counter()
    stop = MaxRuntime(budget)
    # The local criterion starts its clock on first use, normally after initial
    # construction. Start it here so construction/search setup spend the budget.
    stop(0)
    result = solve_pyvrp(data, stop, seed=seed,
                         collect_stats=False, display=False)
    search = perf_counter() - started
    feasible = result.is_feasible()
    return {
        "routes": [list(route.visits()) for route in result.best.routes()] if feasible else None,
        "status": "feasible" if feasible else "no_feasible_solution",
        "preparation_seconds": preparation,
        "search_seconds": search,
        "solver_runtime_seconds": result.runtime,
        "iterations": result.num_iterations,
        "metadata": {
            "version": importlib.metadata.version("pyvrp"),
            "algorithm": "local checkout IteratedLocalSearch",
            "time_limit_clock": "anchored before solve(), including initial solution and search setup",
            "seed_supported": True, "fixed_vehicle_cost": fleet_cost(instance),
            "internal_feasible": feasible,
            "scalar_objective": result.cost() if feasible else None,
            "internal_distance_ticks": result.best.distance() if feasible else None,
            "internal_vehicles": result.best.num_routes() if feasible else None,
        },
    }
