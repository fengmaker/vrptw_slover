"""OR-Tools RoutingModel backend for the four-solver Solomon comparison."""

from __future__ import annotations

import math
import time
from typing import Any

from vrptw.problem import Instance


def _routing_status_name(status: int, routing_enums_pb2: Any) -> str:
    values = (
        routing_enums_pb2.RoutingSearchStatus.DESCRIPTOR
        .enum_values_by_name.values()
    )
    return next((value.name for value in values if value.number == status), f"UNKNOWN_{status}")


def solve(instance: Instance, budget: float, seed: int) -> dict[str, Any]:
    """Solve a Solomon VRPTW instance with one OR-Tools routing search.

    The model uses the input fleet cap, the source-node service plus travel
    transit convention, and an integer fixed cost that makes vehicle count the
    primary objective. Model construction is outside the OR-Tools search limit.
    """
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("budget must be a finite positive number")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")

    preparation_started = time.perf_counter()
    try:
        import ortools
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError as exc:  # pragma: no cover - comparison venv installs OR-Tools
        raise RuntimeError("OR-Tools is required for the four-solver comparison") from exc

    node_count = instance.customer_count + 1
    manager = pywrapcp.RoutingIndexManager(
        node_count, instance.vehicle_count, 0
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return instance.distance[from_node][to_node]

    distance_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_callback_index)

    # Any complete solution has at most n + min(n, vehicle_cap) traveled arcs.
    # This B is strictly greater than an upper bound on its total distance, so
    # one fewer vehicle always beats every possible distance difference.
    maximum_arc = max(max(row) for row in instance.distance)
    maximum_solution_distance = (
        instance.customer_count + min(instance.customer_count, instance.vehicle_count)
    ) * maximum_arc
    vehicle_fixed_cost = maximum_solution_distance + 1
    for vehicle in range(instance.vehicle_count):
        routing.SetFixedCostOfVehicle(vehicle_fixed_cost, vehicle)

    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        source = instance.customers[from_node]
        return source.service + instance.distance[from_node][to_node]

    time_callback_index = routing.RegisterTransitCallback(time_callback)
    time_horizon = max(
        max(customer.ready for customer in instance.customers),
        max(customer.due for customer in instance.customers),
    )
    routing.AddDimension(
        time_callback_index,
        time_horizon,  # allowed waiting at any node; cumul bounds make this exact
        time_horizon,
        False,  # depot starts at the instance's ready time, not at zero
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    depot = instance.customers[0]
    for vehicle in range(instance.vehicle_count):
        time_dimension.CumulVar(routing.Start(vehicle)).SetRange(depot.ready, depot.ready)
        time_dimension.CumulVar(routing.End(vehicle)).SetRange(depot.ready, depot.due)
    for customer in instance.customers[1:]:
        index = manager.NodeToIndex(customer.id)
        time_dimension.CumulVar(index).SetRange(customer.ready, customer.due)

    def demand_callback(from_index: int) -> int:
        node = manager.IndexToNode(from_index)
        return instance.customers[node].demand

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        [instance.capacity] * instance.vehicle_count,
        True,
        "Capacity",
    )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.FromMilliseconds(max(1, math.ceil(budget * 1000)))

    # OR-Tools Routing CP has no RoutingSearchParameters.random_seed field.
    # Its underlying CP Solver exposes ReSeed(), which is used when available;
    # this does not claim to seed every source of routing-search randomness.
    cp_solver = routing.solver()
    reseed = getattr(cp_solver, "ReSeed", None)
    seed_applied = callable(reseed)
    if seed_applied:
        reseed(seed)

    search_started: float | None = None
    first_feasible_seconds: float | None = None

    def on_solution() -> None:
        nonlocal first_feasible_seconds
        if first_feasible_seconds is None and search_started is not None:
            first_feasible_seconds = time.perf_counter() - search_started

    routing.AddAtSolutionCallback(on_solution)

    # A simple, valid lower bound for interpreting the returned objective.
    minimum_vehicles = max(
        1,
        (instance.total_demand + instance.capacity - 1) // instance.capacity,
    )
    minimum_incoming_distance = sum(
        min(
            instance.distance[predecessor][customer]
            for predecessor in range(node_count)
            if predecessor != customer
        )
        for customer in range(1, node_count)
    )
    objective_lower_bound = (
        minimum_vehicles * vehicle_fixed_cost + minimum_incoming_distance
    )

    preparation_seconds = time.perf_counter() - preparation_started
    solver_wall_before = cp_solver.WallTime()
    search_started = time.perf_counter()
    assignment = routing.SolveWithParameters(search_parameters)
    search_seconds = time.perf_counter() - search_started
    solver_runtime_seconds = max(0, cp_solver.WallTime() - solver_wall_before) / 1000.0

    raw_status = int(routing.status())
    routing_status = _routing_status_name(raw_status, routing_enums_pb2)
    routes: list[list[int]] | None = None
    distance_ticks: int | None = None
    objective_value: int | None = None
    if assignment is not None:
        routes = []
        for vehicle in range(instance.vehicle_count):
            index = routing.Start(vehicle)
            route: list[int] = []
            while not routing.IsEnd(index):
                index = assignment.Value(routing.NextVar(index))
                if not routing.IsEnd(index):
                    route.append(manager.IndexToNode(index))
            if route:
                routes.append(route)
        distance_ticks = sum(
            instance.distance[0][route[0]]
            + sum(instance.distance[left][right] for left, right in zip(route, route[1:]))
            + instance.distance[route[-1]][0]
            for route in routes
        )
        objective_value = len(routes) * vehicle_fixed_cost + distance_ticks

    solver_objective = (
        int(assignment.ObjectiveValue()) if assignment is not None else None
    )
    return {
        "routes": routes,
        "status": "found" if routes is not None else "no_solution",
        "preparation_seconds": preparation_seconds,
        "search_seconds": search_seconds,
        "solver_runtime_seconds": solver_runtime_seconds,
        "first_feasible_seconds": first_feasible_seconds,
        "metadata": {
            "ortools_version": ortools.__version__,
            "requested_seed": seed,
            "cp_solver_reseed_applied": seed_applied,
            "seed_api": "pywrapcp.Solver.ReSeed" if seed_applied else None,
            "seed_semantics": (
                "Applied to the underlying CP Solver RNG; RoutingSearchParameters has no random_seed field. "
                "This does not claim control over every routing-search randomness source."
                if seed_applied else
                "Not applied: this OR-Tools build exposes no CP Solver ReSeed method, and "
                "RoutingSearchParameters has no random_seed field."
            ),
            "warm_start": False,
            "vehicle_limit": instance.vehicle_count,
            "vehicle_fixed_cost": vehicle_fixed_cost,
            "max_solution_distance_bound": maximum_solution_distance,
            "objective_value": objective_value,
            "solver_assignment_objective": solver_objective,
            "scalar_objective": solver_objective,
            "objective_lower_bound": objective_lower_bound,
            "objective_gap_to_lower_bound": (
                objective_value - objective_lower_bound
                if objective_value is not None else None
            ),
            "lower_bound_components": {
                "vehicles": minimum_vehicles,
                "distance_ticks": minimum_incoming_distance,
                "distance_bound_method": "sum of each customer's minimum incoming arc",
            },
            "routing_status_code": raw_status,
            "routing_status": routing_status,
            "optimality_proven": routing_status == "ROUTING_OPTIMAL",
            "first_solution_strategy": "PARALLEL_CHEAPEST_INSERTION",
            "local_search_metaheuristic": "GUIDED_LOCAL_SEARCH",
            "search_workers": 1,
            "search_worker_setting": (
                "Routing CP exposes no worker-count parameter; one CP search was run."
            ),
            "search_time_limit_seconds": search_parameters.time_limit.seconds
            + search_parameters.time_limit.nanos / 1_000_000_000,
            "time_limit_scope": "SolveWithParameters call only; model preparation is separate",
            "first_feasible_seconds": first_feasible_seconds,
            "distance_ticks": distance_ticks,
        },
    }
