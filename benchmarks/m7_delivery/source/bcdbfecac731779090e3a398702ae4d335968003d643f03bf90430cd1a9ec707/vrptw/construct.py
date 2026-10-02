"""Deterministic, feasibility-first insertion construction."""

from dataclasses import dataclass

from .diagnostics import DiagnosticCollector, in_phase
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


@in_phase("construction")
def construct(instance: Instance, *, order: str = "due",
              diagnostics: DiagnosticCollector | None = None) -> tuple[tuple[int, ...], ...]:
    """Insert each customer at the least costly feasible position.

    `due` visits tighter deadlines first, breaking ties by window width and ID.
    `slack` visits narrow windows first, breaking ties by deadline and ID.
    `id` follows source-file order. No mode changes the numeric rule.
    """
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    if order == "due":
        remaining = sorted(range(1, instance.customer_count + 1),
                           key=lambda i: (instance.customers[i].due,
                                          instance.customers[i].due - instance.customers[i].ready,
                                          i))
    elif order == "id":
        remaining = list(range(1, instance.customer_count + 1))
    elif order == "slack":
        remaining = sorted(range(1, instance.customer_count + 1),
                           key=lambda i: (instance.customers[i].due - instance.customers[i].ready,
                                          instance.customers[i].due, i))
    else:
        raise ValueError("order must be 'due', 'id' or 'slack'")

    routes: list[tuple[int, ...]] = []
    route_costs: list[int] = []
    for customer in remaining:
        best: tuple[int, int, int, tuple[int, ...]] | None = None
        for route_index, route in enumerate(routes):
            for position in range(len(route) + 1):
                candidate = route[:position] + (customer,) + route[position:]
                evaluation = evaluate(instance, candidate)
                if diagnostics is not None:
                    diagnostics.candidate(evaluation)
                if evaluation.feasible:
                    delta = evaluation.distance - route_costs[route_index]
                    choice = (delta, route_index, position, candidate)
                    if best is None or choice[:3] < best[:3]:
                        best = choice
        if best is not None:
            _, route_index, _, candidate = best
            routes[route_index] = candidate
            route_costs[route_index] = evaluate(instance, candidate).distance
            if diagnostics is not None:
                diagnostics.increment("accepted")
            continue

        singleton = evaluate(instance, (customer,))
        if diagnostics is not None:
            diagnostics.candidate(singleton)
        if not singleton.feasible:
            reason = f"singleton route violates {singleton.violations[0].code}"
        elif len(routes) >= instance.vehicle_count:
            reason = "no feasible insertion and vehicle limit reached"
        else:
            routes.append((customer,))
            route_costs.append(singleton.distance)
            if diagnostics is not None:
                diagnostics.increment("accepted")
            continue
        raise ConstructionError(customer, sum(map(len, routes)), len(routes),
                                instance.vehicle_count, reason)

    solution = tuple(routes)
    verdict = validate(instance, solution)
    if not verdict.feasible:
        raise AssertionError(f"construction produced invalid solution: {verdict.first_violation}")
    return solution
