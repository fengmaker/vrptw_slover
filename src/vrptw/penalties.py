"""Independent soft evaluation and adaptive penalties for infeasible VRPTW routes."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Real
from typing import Iterable

from .problem import Instance


@dataclass(frozen=True, slots=True)
class SoftRoute:
    """Integer route metrics, including excess capacity and time warp."""

    distance: int
    excess_load: int
    time_warp: int

    def __post_init__(self) -> None:
        for name in ("distance", "excess_load", "time_warp"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def feasible(self) -> bool:
        return self.excess_load == 0 and self.time_warp == 0


@dataclass(frozen=True, slots=True)
class SoftEvaluation:
    """Integer metrics for a complete customer assignment."""

    vehicles: int
    distance: int
    excess_load: int
    time_warp: int

    def __post_init__(self) -> None:
        for name in ("vehicles", "distance", "excess_load", "time_warp"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def feasible(self) -> bool:
        return self.excess_load == 0 and self.time_warp == 0


def evaluate_soft_route(instance: Instance, route: Iterable[int]) -> SoftRoute:
    """Measure a route without discarding candidates that miss hard constraints.

    A late service start contributes its excess over the due time to time warp,
    then is clamped to that due time before propagating the schedule. This keeps
    one late visit from making every later visit appear late as well.
    """

    customer_ids = tuple(route)
    seen: set[int] = set()
    for customer_id in customer_ids:
        if type(customer_id) is not int or not 1 <= customer_id <= instance.customer_count:
            raise ValueError(f"route entries must be customer IDs 1..{instance.customer_count}")
        if customer_id in seen:
            raise ValueError(f"route contains duplicate customer {customer_id}")
        seen.add(customer_id)

    if not customer_ids:
        return SoftRoute(0, 0, 0)

    depot = instance.customers[0]
    previous = 0
    departure = depot.ready
    load = 0
    distance = 0
    time_warp = 0

    for customer_id in customer_ids:
        customer = instance.customers[customer_id]
        arc = instance.distance[previous][customer_id]
        arrival = departure + arc
        start = max(arrival, customer.ready)
        if start > customer.due:
            time_warp += start - customer.due
            start = customer.due

        load += customer.demand
        distance += arc
        departure = start + customer.service
        previous = customer_id

    return_time = departure + instance.distance[previous][0]
    distance += instance.distance[previous][0]
    time_warp += max(return_time - depot.due, 0)

    return SoftRoute(distance, max(load - instance.capacity, 0), time_warp)


def evaluate_soft_solution(instance: Instance, routes: Iterable[Iterable[int]]) -> SoftEvaluation:
    """Measure a complete assignment, rejecting structurally invalid solutions."""

    route_list = tuple(tuple(route) for route in routes)
    counts = [0] * (instance.customer_count + 1)
    vehicles = sum(bool(route) for route in route_list)
    if vehicles > instance.vehicle_count:
        raise ValueError(f"solution uses {vehicles} vehicles; limit is {instance.vehicle_count}")

    distance = 0
    excess_load = 0
    time_warp = 0
    for route in route_list:
        route_evaluation = evaluate_soft_route(instance, route)
        distance += route_evaluation.distance
        excess_load += route_evaluation.excess_load
        time_warp += route_evaluation.time_warp
        for customer_id in route:
            counts[customer_id] += 1

    missing = [customer_id for customer_id in range(1, instance.customer_count + 1)
               if counts[customer_id] == 0]
    if missing:
        raise ValueError(f"solution is missing customer IDs {missing}")
    duplicates = [customer_id for customer_id in range(1, instance.customer_count + 1)
                  if counts[customer_id] > 1]
    if duplicates:
        raise ValueError(f"solution repeats customer IDs {duplicates}")

    return SoftEvaluation(vehicles, distance, excess_load, time_warp)


@dataclass(frozen=True, slots=True)
class PenaltyParams:
    """Window and multiplier settings for adaptive feasibility penalties."""

    update_interval: int = 10
    target_feasible: float = 0.5
    tolerance: float = 0.05
    increase: float = 1.25
    decrease: float = 0.85
    min_weight: int = 100
    max_weight: int = 100_000_000

    def __post_init__(self) -> None:
        if type(self.update_interval) is not int or self.update_interval < 1:
            raise ValueError("update_interval must be a positive integer")
        for name in ("target_feasible", "tolerance", "increase", "decrease"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite real number")
        if not 0.0 <= self.target_feasible <= 1.0:
            raise ValueError("target_feasible must be between 0 and 1")
        if not 0.0 <= self.tolerance <= 1.0:
            raise ValueError("tolerance must be between 0 and 1")
        if self.increase <= 1.0:
            raise ValueError("increase must be greater than 1")
        if not 0.0 < self.decrease < 1.0:
            raise ValueError("decrease must be between 0 and 1")
        if type(self.min_weight) is not int or self.min_weight < 1:
            raise ValueError("min_weight must be a positive integer")
        if type(self.max_weight) is not int or self.max_weight < self.min_weight:
            raise ValueError("max_weight must be an integer at least min_weight")


class PenaltyManager:
    """Track disjoint feasibility windows and adapt load/time weights separately."""

    def __init__(
        self,
        instance: Instance,
        params: PenaltyParams = PenaltyParams(),
        adaptive: bool = True,
    ) -> None:
        if not isinstance(instance, Instance):
            raise TypeError("instance must be an Instance")
        if not isinstance(params, PenaltyParams):
            raise TypeError("params must be PenaltyParams")
        if type(adaptive) is not bool:
            raise ValueError("adaptive must be a bool")

        self.instance = instance
        self.params = params
        self.adaptive = adaptive
        self.load_weight = self._initial_load_weight(instance, params)
        self.time_weight = self._clamp_weight(1000, params)
        self.samples = 0
        self.updates = 0
        self._window_samples = 0
        self._window_load_feasible = 0
        self._window_time_feasible = 0
        self._window_feasible = 0
        self._last_rates: tuple[float, float, float] | None = None

    @staticmethod
    def _clamp_weight(weight: int, params: PenaltyParams) -> int:
        return min(params.max_weight, max(params.min_weight, weight))

    @classmethod
    def _initial_load_weight(cls, instance: Instance, params: PenaltyParams) -> int:
        node_count = len(instance.customers)
        total_distance = sum(
            instance.distance[i][j]
            for i in range(node_count)
            for j in range(node_count)
            if i != j
        )
        total_demand = instance.total_demand
        if total_demand == 0:
            estimate = params.min_weight
        else:
            # This is mean off-diagonal distance / mean customer demand * 1000,
            # reduced algebraically to an exact rational before ties-to-even rounding.
            estimate = round(Fraction(total_distance * 1000, node_count * total_demand))
        return cls._clamp_weight(estimate, params)

    def cost(self, evaluation: SoftRoute | SoftEvaluation) -> int:
        """Return distance and violation penalties on the 1000-point scale."""

        if not isinstance(evaluation, (SoftRoute, SoftEvaluation)):
            raise TypeError("evaluation must be a SoftRoute or SoftEvaluation")
        return (
            evaluation.distance * 1000
            + self.load_weight * evaluation.excess_load
            + self.time_weight * evaluation.time_warp
        )

    def register(self, evaluation: SoftEvaluation) -> bool:
        """Record one candidate and adapt after each complete disjoint window.

        Returns true only when at least one penalty weight changed. Rates refer
        to the current partial window, or the most recently completed window
        when no partial observations are available.
        """

        if not isinstance(evaluation, SoftEvaluation):
            raise TypeError("evaluation must be a SoftEvaluation")

        self.samples += 1
        self._window_samples += 1
        load_feasible = evaluation.excess_load == 0
        time_feasible = evaluation.time_warp == 0
        self._window_load_feasible += int(load_feasible)
        self._window_time_feasible += int(time_feasible)
        self._window_feasible += int(load_feasible and time_feasible)
        if self._window_samples < self.params.update_interval:
            return False

        denominator = self._window_samples
        rates = (
            self._window_load_feasible / denominator,
            self._window_time_feasible / denominator,
            self._window_feasible / denominator,
        )
        self._last_rates = rates
        self._window_samples = 0
        self._window_load_feasible = 0
        self._window_time_feasible = 0
        self._window_feasible = 0

        if not self.adaptive:
            return False

        new_load_weight = self._adapt(self.load_weight, rates[0])
        new_time_weight = self._adapt(self.time_weight, rates[1])
        changed = (new_load_weight != self.load_weight or new_time_weight != self.time_weight)
        if changed:
            self.updates += 1
            self.load_weight = new_load_weight
            self.time_weight = new_time_weight
        return changed

    def _adapt(self, weight: int, feasible_rate: float) -> int:
        target = self.params.target_feasible
        tolerance = self.params.tolerance
        if feasible_rate < target - tolerance:
            factor = self.params.increase
        elif feasible_rate > target + tolerance:
            factor = self.params.decrease
        else:
            return weight

        scaled = round(Fraction(weight) * Fraction(str(factor)))
        return self._clamp_weight(scaled, self.params)

    def _rates(self) -> tuple[float, float, float] | None:
        if self._window_samples:
            denominator = self._window_samples
            return (
                self._window_load_feasible / denominator,
                self._window_time_feasible / denominator,
                self._window_feasible / denominator,
            )
        return self._last_rates

    @property
    def load_feasible_rate(self) -> float | None:
        rates = self._rates()
        return None if rates is None else rates[0]

    @property
    def time_feasible_rate(self) -> float | None:
        rates = self._rates()
        return None if rates is None else rates[1]

    @property
    def feasible_rate(self) -> float | None:
        rates = self._rates()
        return None if rates is None else rates[2]
