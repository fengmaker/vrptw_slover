"""Full route and solution recomputation; the authoritative VRPTW judge."""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .problem import Instance


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    route: int | None = None  # one-based route number in the supplied solution
    customer: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Visit:
    customer: int
    arrival: int
    waiting: int
    start: int
    departure: int
    load: int


@dataclass(frozen=True, slots=True)
class RouteEvaluation:
    visits: tuple[Visit, ...]
    load: int
    distance: int | None
    return_time: int | None
    violations: tuple[Violation, ...]

    @property
    def feasible(self) -> bool:
        return not self.violations


@dataclass(frozen=True, slots=True)
class Evaluation:
    vehicles: int
    distance: int | None
    routes: tuple[RouteEvaluation, ...]
    violations: tuple[Violation, ...]

    @property
    def feasible(self) -> bool:
        return not self.violations

    @property
    def objective(self) -> tuple[int, int] | None:
        """Lexicographic objective, available only for a complete feasible solution."""
        return (self.vehicles, self.distance) if self.feasible else None

    @property
    def first_violation(self) -> Violation | None:
        return self.violations[0] if self.violations else None


def evaluate_route(instance: Instance, route: Iterable[int], *, route_number: int = 1) -> RouteEvaluation:
    """Start at depot ready time; due limits service start and depot return."""
    visits: list[Visit] = []
    issues: list[Violation] = []
    seen: set[int] = set()
    previous = 0
    departure = instance.customers[0].ready
    load = 0
    distance = 0

    for customer_id in route:
        if type(customer_id) is not int or not 1 <= customer_id <= instance.customer_count:
            issues.append(Violation("unknown_customer", route_number, customer_id,
                                    "route entries must be customer IDs 1..N"))
            return RouteEvaluation(tuple(visits), load, None, None, tuple(issues))
        if customer_id in seen:
            issues.append(Violation("duplicate_customer", route_number, customer_id))
        seen.add(customer_id)
        customer = instance.customers[customer_id]
        arrival = departure + instance.distance[previous][customer_id]
        start = max(arrival, customer.ready)
        load += customer.demand
        if start > customer.due:
            issues.append(Violation("time_window", route_number, customer_id,
                                    f"start {start} > due {customer.due}"))
        if load > instance.capacity:
            issues.append(Violation("capacity", route_number, customer_id,
                                    f"load {load} > capacity {instance.capacity}"))
        visits.append(Visit(customer_id, arrival, start - arrival,
                            start, start + customer.service, load))
        distance += instance.distance[previous][customer_id]
        departure = start + customer.service
        previous = customer_id

    if not visits:
        return RouteEvaluation((), 0, 0, instance.customers[0].ready, tuple(issues))
    return_time = departure + instance.distance[previous][0]
    distance += instance.distance[previous][0]
    if return_time > instance.customers[0].due:
        issues.append(Violation("depot_close", route_number, 0,
                                f"return {return_time} > due {instance.customers[0].due}"))
    return RouteEvaluation(tuple(visits), load, distance, return_time, tuple(issues))


def validate_solution(instance: Instance, routes: Iterable[Iterable[int]]) -> Evaluation:
    """Recompute routes and coverage from the supplied visit order, never metadata."""
    route_list = tuple(tuple(route) for route in routes)
    issues: list[Violation] = []
    counts: Counter[int] = Counter()
    route_results: list[RouteEvaluation] = []
    vehicles = sum(bool(route) for route in route_list)
    if vehicles > instance.vehicle_count:
        issues.append(Violation("vehicle_limit", detail=f"{vehicles} > {instance.vehicle_count}"))
    total_distance = 0
    valid_distance = True
    for number, route in enumerate(route_list, start=1):
        for customer_id in route:
            if type(customer_id) is int and 1 <= customer_id <= instance.customer_count:
                counts[customer_id] += 1
        result = evaluate_route(instance, route, route_number=number)
        route_results.append(result)
        issues.extend(result.violations)
        if result.distance is None:
            valid_distance = False
        else:
            total_distance += result.distance
    for customer_id in range(1, instance.customer_count + 1):
        if counts[customer_id] == 0:
            issues.append(Violation("missing_customer", customer=customer_id))
        elif counts[customer_id] > 1:
            issues.append(Violation("duplicate_customer", customer=customer_id,
                                    detail=f"visited {counts[customer_id]} times"))
    return Evaluation(vehicles, total_distance if not issues and valid_distance else None,
                      tuple(route_results), tuple(issues))
