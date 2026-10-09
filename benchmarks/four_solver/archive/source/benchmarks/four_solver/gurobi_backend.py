"""Cold-start Gurobi MILP backend for four-solver Solomon comparisons.

The model consumes the integer matrix and scaled time values already stored in
``vrptw.problem.Instance``. Gurobi is an optional, externally installed
dependency; setup and solve failures are returned as data for benchmark rows.
"""

from __future__ import annotations

from math import isfinite
from time import perf_counter
from typing import Any

from vrptw.problem import Instance


def _result(
    *,
    routes: list[list[int]] | None,
    status: str,
    preparation_seconds: float,
    search_seconds: float,
    solver_runtime_seconds: float | None,
    metadata: dict[str, Any],
    first_feasible_seconds: float | None = None,
) -> dict[str, Any]:
    metadata.setdefault("status", status)
    return {
        "routes": routes,
        "status": status,
        "preparation_seconds": preparation_seconds,
        "search_seconds": search_seconds,
        "solver_runtime_seconds": solver_runtime_seconds,
        "first_feasible_seconds": first_feasible_seconds,
        "error": metadata.get("error"),
        "metadata": metadata,
    }


def _extract_routes(x: Any, arcs: list[tuple[int, int]], n: int) -> list[list[int]]:
    """Read the incumbent as depot-rooted paths and reject malformed values."""
    successor: dict[int, int] = {}
    starts: list[int] = []
    for i, j in arcs:
        if x[i, j].X > 0.5:
            if i == 0:
                starts.append(j)
                continue
            if i in successor:
                raise ValueError(f"incumbent has multiple successors for node {i}")
            successor[i] = j

    routes: list[list[int]] = []
    visited: set[int] = set()
    for first in sorted(starts):
        route: list[int] = []
        node = first
        while node != 0:
            if node < 1 or node > n or node in visited:
                raise ValueError("incumbent route repeats or leaves the customer set")
            visited.add(node)
            route.append(node)
            if node not in successor:
                raise ValueError(f"incumbent route ends at customer {node}")
            node = successor[node]
            if len(route) > n:
                raise ValueError("incumbent route traversal exceeded customer count")
        if not route:
            raise ValueError("incumbent contains an empty used vehicle")
        routes.append(route)

    if visited != set(range(1, n + 1)):
        missing = sorted(set(range(1, n + 1)) - visited)
        raise ValueError(f"incumbent routes do not cover all customers: {missing[:8]}")
    return routes


def solve(instance: Instance, budget: float, seed: int) -> dict[str, Any]:
    """Solve ``instance`` for at most ``budget`` seconds with Gurobi.

    The scalar objective exactly prioritizes used vehicles, then integer
    distance. No incumbent or external route is used as a warm start.
    """
    started = perf_counter()
    prep_seconds = 0.0
    search_seconds = 0.0
    solver_runtime: float | None = None
    first_feasible: list[float | None] = [None]
    metadata: dict[str, Any] = {"backend": "gurobi", "gurobi_version": None}
    env = None
    model = None
    search_started: float | None = None
    try:
        if not isinstance(instance, Instance):
            raise TypeError("instance must be a vrptw.problem.Instance")
        if not isinstance(budget, (int, float)) or not isfinite(float(budget)) or budget < 0:
            return _result(
                routes=None,
                status="invalid_budget",
                preparation_seconds=0.0,
                search_seconds=0.0,
                solver_runtime_seconds=None,
                metadata={**metadata, "error": "budget must be a finite nonnegative number"},
            )
        if type(seed) is not int:
            return _result(
                routes=None,
                status="invalid_seed",
                preparation_seconds=0.0,
                search_seconds=0.0,
                solver_runtime_seconds=None,
                metadata={**metadata, "error": "seed must be an integer"},
            )

        n = instance.customer_count
        max_vehicles = min(n, instance.vehicle_count)
        max_arc_distance = max(max(row) for row in instance.distance)
        vehicle_weight = (n + max_vehicles) * max_arc_distance + 1
        distance_upper_bound = (n + max_vehicles) * max_arc_distance
        objective_upper_bound = max_vehicles * vehicle_weight + distance_upper_bound
        metadata.update(
            {
                "vehicle_weight": vehicle_weight,
                "max_vehicles": max_vehicles,
                "distance_upper_bound": distance_upper_bound,
                "objective_upper_bound": objective_upper_bound,
                "cold_start": True,
                "threads": 1,
                "seed": seed,
                "seed_supported": True,
                "mip_gap_target": 0.0,
            }
        )

        # Gurobi takes binary64 coefficients. Refuse a range where distinct
        # integer objective values cannot be represented exactly at input.
        if objective_upper_bound > 2**53:
            return _result(
                routes=None,
                status="unsupported_numeric_range",
                preparation_seconds=perf_counter() - started,
                search_seconds=0.0,
                solver_runtime_seconds=None,
                metadata={**metadata, "error": "integer objective exceeds exact binary64 range"},
            )

        # Necessary conditions prove these infeasibilities without a timed run.
        if any(c.demand > instance.capacity for c in instance.customers[1:]):
            metadata["proof"] = "a customer demand exceeds vehicle capacity"
            return _result(
                routes=None,
                status="infeasible",
                preparation_seconds=perf_counter() - started,
                search_seconds=0.0,
                solver_runtime_seconds=0.0,
                metadata=metadata,
            )
        if instance.total_demand > instance.vehicle_count * instance.capacity:
            metadata["proof"] = "total demand exceeds fleet capacity"
            return _result(
                routes=None,
                status="infeasible",
                preparation_seconds=perf_counter() - started,
                search_seconds=0.0,
                solver_runtime_seconds=0.0,
                metadata=metadata,
            )

        import gurobipy as gp
        from gurobipy import GRB

        metadata["gurobi_version"] = list(gp.gurobi.version())

        # Suppress startup/license output before starting the environment.
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 0)
        env.start()
        model = gp.Model("solomon_vrptw", env=env)
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.Seed = seed
        model.Params.TimeLimit = float(budget)
        model.Params.MIPGap = 0.0
        model.Params.MIPGapAbs = 0.0

        customers = instance.customers
        distance = instance.distance
        capacity = instance.capacity
        depot = customers[0]

        # Keep arcs only if earliest service times and endpoint demands do not
        # already prove that the arc is impossible.
        arcs: list[tuple[int, int]] = []
        for j in range(1, n + 1):
            if depot.ready + distance[0][j] <= customers[j].due:
                arcs.append((0, j))
        for i in range(1, n + 1):
            if customers[i].ready + customers[i].service + distance[i][0] <= depot.due:
                arcs.append((i, 0))
            for j in range(1, n + 1):
                if i == j or customers[i].demand + customers[j].demand > capacity:
                    continue
                if (
                    customers[i].ready
                    + customers[i].service
                    + distance[i][j]
                    <= customers[j].due
                ):
                    arcs.append((i, j))

        x = model.addVars(arcs, vtype=GRB.BINARY, name="x")
        starts = model.addVars(
            range(1, n + 1),
            lb={i: customers[i].ready for i in range(1, n + 1)},
            ub={i: customers[i].due for i in range(1, n + 1)},
            vtype=GRB.CONTINUOUS,
            name="start",
        )
        loads = model.addVars(
            range(1, n + 1),
            lb={i: customers[i].demand for i in range(1, n + 1)},
            ub=capacity,
            vtype=GRB.CONTINUOUS,
            name="load",
        )
        order = model.addVars(
            range(1, n + 1), lb=1.0, ub=float(n), vtype=GRB.CONTINUOUS, name="order"
        )

        out_by_node: dict[int, list[tuple[int, int]]] = {i: [] for i in range(n + 1)}
        in_by_node: dict[int, list[tuple[int, int]]] = {i: [] for i in range(n + 1)}
        for arc in arcs:
            out_by_node[arc[0]].append(arc)
            in_by_node[arc[1]].append(arc)

        for i in range(1, n + 1):
            model.addConstr(gp.quicksum(x[a] for a in in_by_node[i]) == 1, name=f"in_{i}")
            model.addConstr(gp.quicksum(x[a] for a in out_by_node[i]) == 1, name=f"out_{i}")
        model.addConstr(
            gp.quicksum(x[a] for a in out_by_node[0])
            == gp.quicksum(x[a] for a in in_by_node[0]),
            name="depot_flow",
        )
        model.addConstr(
            gp.quicksum(x[a] for a in out_by_node[0]) <= instance.vehicle_count,
            name="vehicle_cap",
        )

        for i, j in arcs:
            if i == 0:
                continue
            if j == 0:
                # Selected return arcs must reach the depot before its due time.
                big_m = max(
                    0,
                    customers[i].due
                    + customers[i].service
                    + distance[i][0]
                    - depot.due,
                )
                model.addConstr(
                    starts[i] + customers[i].service + distance[i][0]
                    <= depot.due + big_m * (1 - x[i, 0]),
                    name=f"return_time_{i}",
                )
                continue

            # With x=0 this relaxes the destination to its ready-time lower
            # bound; with x=1 it propagates arrival, service and waiting.
            time_m = max(
                0,
                customers[i].due
                + customers[i].service
                + distance[i][j]
                - customers[j].ready,
            )
            model.addConstr(
                starts[j]
                >= starts[i]
                + customers[i].service
                + distance[i][j]
                - time_m * (1 - x[i, j]),
                name=f"time_{i}_{j}",
            )
            model.addConstr(
                loads[j] >= loads[i] + customers[j].demand - capacity * (1 - x[i, j]),
                name=f"load_{i}_{j}",
            )
            # This independent order variable prevents subtours even when
            # travel, service, or demand values are all zero.
            model.addConstr(
                order[j] >= order[i] + 1 - n * (1 - x[i, j]),
                name=f"subtour_{i}_{j}",
            )

        for _, j in out_by_node[0]:
            time_m = max(0, depot.ready + distance[0][j] - customers[j].ready)
            model.addConstr(
                starts[j] >= depot.ready + distance[0][j] - time_m * (1 - x[0, j]),
                name=f"depot_time_{j}",
            )

        model.setObjective(
            vehicle_weight * gp.quicksum(x[a] for a in out_by_node[0])
            + gp.quicksum(distance[i][j] * x[i, j] for i, j in arcs),
            GRB.MINIMIZE,
        )
        model.update()
        prep_seconds = perf_counter() - started
        metadata.update(
            {
                "variable_count": model.NumVars,
                "constraint_count": model.NumConstrs,
                "arc_count": len(arcs),
            }
        )

        search_started = perf_counter()

        def record_first_incumbent(active_model, where):
            if where == GRB.Callback.MIPSOL and first_feasible[0] is None:
                first_feasible[0] = perf_counter() - started

        model.optimize(record_first_incumbent)
        search_seconds = perf_counter() - search_started
        solver_runtime = float(model.Runtime)

        code_to_status = {
            GRB.OPTIMAL: "optimal",
            GRB.INFEASIBLE: "infeasible",
            GRB.TIME_LIMIT: "time_limit",
            GRB.INTERRUPTED: "interrupted",
            GRB.INF_OR_UNBD: "infeasible_or_unbounded",
            GRB.UNBOUNDED: "unbounded",
            GRB.NUMERIC: "numeric_error",
            GRB.SUBOPTIMAL: "suboptimal",
        }
        status = code_to_status.get(model.Status, f"gurobi_status_{model.Status}")
        metadata.update(
            {
                "gurobi_status_code": int(model.Status),
                "status": status,
                "incumbent_count": int(model.SolCount),
                "node_count": float(model.NodeCount),
                "iteration_count": float(model.IterCount),
            }
        )
        try:
            bound = float(model.ObjBound)
            if isfinite(bound):
                metadata["objective_bound"] = bound
        except Exception:
            # Some terminal statuses do not expose a meaningful MIP bound.
            pass
        try:
            continuous_bound = float(model.ObjBoundC)
            if isfinite(continuous_bound):
                metadata["objective_bound_continuous"] = continuous_bound
        except Exception:
            pass
        if model.SolCount:
            routes = _extract_routes(x, arcs, n)
            metadata["objective"] = float(model.ObjVal)
            # Binary MIP values may lie inside IntFeasTol rather than at exact
            # integers. Preserve that raw floating objective, and score the
            # extracted integral route separately (central validation repeats it).
            route_distance = sum(
                distance[0][route[0]]
                + sum(distance[i][j] for i, j in zip(route, route[1:]))
                + distance[route[-1]][0] for route in routes
            )
            metadata["scalar_objective"] = vehicle_weight * len(routes) + route_distance
            metadata["raw_objective_minus_integral_route_objective"] = (
                float(model.ObjVal) - metadata["scalar_objective"]
            )
            metadata["mip_gap"] = float(model.MIPGap)
            return _result(
                routes=routes,
                status=status,
                preparation_seconds=prep_seconds,
                search_seconds=search_seconds,
                solver_runtime_seconds=solver_runtime,
                metadata=metadata,
                first_feasible_seconds=first_feasible[0],
            )
        return _result(
            routes=None,
            status=status,
            preparation_seconds=prep_seconds,
            search_seconds=search_seconds,
            solver_runtime_seconds=solver_runtime,
            metadata=metadata,
            first_feasible_seconds=first_feasible[0],
        )
    except Exception as exc:  # benchmark output must preserve failed attempts
        message = f"{type(exc).__name__}: {exc}"
        low_message = message.lower()
        if "license" in low_message or "licence" in low_message:
            status = "license_error"
        elif isinstance(exc, ImportError):
            status = "dependency_error"
        elif "gurobi" in low_message or type(exc).__module__.startswith("gurobipy"):
            status = "solver_error"
        else:
            status = "model_error"
        metadata.update({"error": message, "status": status})
        if search_started is not None:
            search_seconds = max(0.0, perf_counter() - search_started)
        if model is not None:
            try:
                solver_runtime = float(model.Runtime)
            except Exception:
                pass
        return _result(
            routes=None,
            status=status,
            preparation_seconds=prep_seconds or max(0.0, perf_counter() - started - search_seconds),
            search_seconds=search_seconds,
            solver_runtime_seconds=solver_runtime,
            metadata=metadata,
            first_feasible_seconds=first_feasible[0],
        )
    finally:
        if model is not None:
            try:
                model.dispose()
            except Exception:
                pass
        if env is not None:
            try:
                env.dispose()
            except Exception:
                pass
