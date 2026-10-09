"""Exact integer prefix/suffix state for feasible, unique-customer routes.

This internal evaluator assumes structural moves preserve known customer IDs.
The public judge remains evaluate_route/validate_solution.
"""

from dataclasses import dataclass

from .evaluate import RouteEvaluation, evaluate_route
from .problem import Instance


@dataclass(frozen=True, slots=True)
class FastEvaluation:
    load: int
    distance: int
    return_time: int
    codes: frozenset[str]
    reused_prefix: int = 0
    reused_suffix: int = 0

    @property
    def feasible(self) -> bool:
        return not self.codes


class RouteCache:
    """Cache prefixes and suffix transfer functions; rebuild on acceptance only."""

    def __init__(self, instance: Instance, route: tuple[int, ...], *,
                 _evaluation: RouteEvaluation | None = None) -> None:
        self.instance = instance
        self.route = tuple(route)
        checked = evaluate_route(instance, self.route) if _evaluation is None else _evaluation
        if not self.route or not checked.feasible:
            raise ValueError("RouteCache requires a nonempty feasible route")
        self.distance = checked.distance
        self.load = checked.load
        self.prefix_departure = (instance.customers[0].ready,) + tuple(
            visit.departure for visit in checked.visits)
        self.prefix_load = (0,) + tuple(visit.load for visit in checked.visits)
        matrix, customers = instance.distance, instance.customers
        prefix_distance = [0]
        previous = 0
        for node in self.route:
            prefix_distance.append(prefix_distance[-1] + matrix[previous][node])
            previous = node
        self.prefix_distance = tuple(prefix_distance)
        n = len(self.route)
        self.suffix_distance = [0] * (n + 1)
        self.suffix_load = [0] * (n + 1)
        self.duration = [0] * n
        self.release = [0] * n
        self.latest = [0] * n
        for i in range(n - 1, -1, -1):
            node = self.route[i]
            customer = customers[node]
            nxt = self.route[i + 1] if i + 1 < n else 0
            step = customer.service + matrix[node][nxt]
            self.suffix_distance[i] = matrix[node][nxt] + self.suffix_distance[i + 1]
            self.suffix_load[i] = customer.demand + self.suffix_load[i + 1]
            self.duration[i] = step + (self.duration[i + 1] if i + 1 < n else 0)
            self.release[i] = max(customer.ready + self.duration[i],
                                  self.release[i + 1] if i + 1 < n else 0)
            self.latest[i] = min(customer.due, self.latest[i + 1] - step) if i + 1 < n else customer.due

    def evaluate(self, candidate: tuple[int, ...]) -> FastEvaluation:
        """Scan changed middle; compose untouched suffix without visiting it.

        A suffix maps its first arrival t to return max(t + duration, release).
        Its customer windows hold iff t <= latest, since the cached suffix was
        feasible. Depot closing is checked separately against exact return.
        """
        candidate = tuple(candidate)
        original = self.route
        size = min(len(original), len(candidate))
        prefix = 0
        while prefix < size and original[prefix] == candidate[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < size - prefix
               and original[len(original) - 1 - suffix] == candidate[len(candidate) - 1 - suffix]):
            suffix += 1
        customers, matrix = self.instance.customers, self.instance.distance
        departure = self.prefix_departure[prefix]
        distance = self.prefix_distance[prefix]
        load = self.prefix_load[prefix]
        previous = candidate[prefix - 1] if prefix else 0
        codes: set[str] = set()
        for i in range(prefix, len(candidate) - suffix):
            node = candidate[i]
            customer = customers[node]
            arc = matrix[previous][node]
            start = max(departure + arc, customer.ready)
            if start > customer.due:
                codes.add("time_window")
            distance += arc
            load += customer.demand
            departure = start + customer.service
            previous = node
        if suffix:
            index = len(original) - suffix
            first = original[index]
            arc = matrix[previous][first]
            arrival = departure + arc
            if arrival > self.latest[index]:
                codes.add("time_window")
            return_time = max(arrival + self.duration[index], self.release[index])
            distance += arc + self.suffix_distance[index]
            load += self.suffix_load[index]
        elif candidate:
            distance += matrix[previous][0]
            return_time = departure + matrix[previous][0]
        else:
            return_time = customers[0].ready
        if load > self.instance.capacity:
            codes.add("capacity")
        if candidate and return_time > customers[0].due:
            codes.add("depot_close")
        return FastEvaluation(load, distance, return_time, frozenset(codes), prefix, suffix)
