"""Deterministic, feasibility-first insertion construction."""

from dataclasses import dataclass

from .evaluate import evaluate_route, validate_solution
from .problem import Instance


@dataclass(frozen=True, slots=True)
class ConstructionError(RuntimeError):
    customer: int
    assigned: int
    vehicles: int
    vehicle_limit: int
    reason: str

    def __str__(self) -> str:
        return (f"cannot insert customer {self.customer}: {self.reason}; "
                f"assigned {self.assigned}; tried all {self.vehicles} used vehicles "
                f"(limit {self.vehicle_limit})")


def construct(instance: Instance, *, order: str = "due") -> tuple[tuple[int, ...], ...]:
    """Insert each customer at the least costly feasible position.

    `due` visits tighter deadlines first, breaking ties by window width and ID.
    `id` follows source-file order. Neither mode changes the numeric rule.
    """
    if order == "due":
        remaining = sorted(range(1, instance.customer_count + 1),
                           key=lambda i: (instance.customers[i].due,
                                          instance.customers[i].due - instance.customers[i].ready,
                                          i))
    elif order == "id":
        remaining = list(range(1, instance.customer_count + 1))
    else:
        raise ValueError("order must be 'due' or 'id'")

    routes: list[tuple[int, ...]] = []
    route_costs: list[int] = []
    for customer in remaining:
        best: tuple[int, int, int, tuple[int, ...]] | None = None
        for route_index, route in enumerate(routes):
            for position in range(len(route) + 1):
                candidate = route[:position] + (customer,) + route[position:]
                evaluation = evaluate_route(instance, candidate)
                if evaluation.feasible:
                    delta = evaluation.distance - route_costs[route_index]
                    choice = (delta, route_index, position, candidate)
                    if best is None or choice[:3] < best[:3]:
                        best = choice
        if best is not None:
            _, route_index, _, candidate = best
            routes[route_index] = candidate
            route_costs[route_index] = evaluate_route(instance, candidate).distance
            continue

        singleton = evaluate_route(instance, (customer,))
        if not singleton.feasible:
            reason = f"singleton route violates {singleton.violations[0].code}"
        elif len(routes) >= instance.vehicle_count:
            reason = "no feasible insertion and vehicle limit reached"
        else:
            routes.append((customer,))
            route_costs.append(singleton.distance)
            continue
        raise ConstructionError(customer, sum(map(len, routes)), len(routes),
                                instance.vehicle_count, reason)

    solution = tuple(routes)
    verdict = validate_solution(instance, solution)
    if not verdict.feasible:
        raise AssertionError(f"construction produced invalid solution: {verdict.first_violation}")
    return solution
