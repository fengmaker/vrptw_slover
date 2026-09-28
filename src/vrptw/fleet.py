"""Capacity lower bound and budgeted fixed-fleet feasibility trials."""

from dataclasses import dataclass
from random import Random
from time import monotonic

from .evaluate import evaluate_route, validate_solution
from .local_search import Routes
from .problem import Instance


@dataclass(frozen=True, slots=True)
class FleetMilestone:
    vehicles: int
    distance: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class FleetResult:
    routes: Routes
    attempts: tuple[tuple[int, int], ...]  # (target fleet, trials actually run)
    milestones: tuple[FleetMilestone, ...]
    lower_bound: int
    stop_reason: str


def capacity_lower_bound(instance: Instance) -> int:
    return (instance.total_demand + instance.capacity - 1) // instance.capacity


def insert_fixed(instance: Instance, routes: Routes, customers: list[int]) -> Routes | None:
    """Cheapest feasible insertion into existing routes, with no new vehicle."""
    working = list(routes)
    evaluations = [evaluate_route(instance, route) for route in working]
    if any(not value.feasible for value in evaluations):
        raise ValueError("insert_fixed requires feasible source routes")
    costs = [value.distance for value in evaluations]
    loads = [value.load for value in evaluations]
    for customer in customers:
        demand = instance.customers[customer].demand
        best: tuple[int, int, int, tuple[int, ...], int] | None = None
        for route_index, route in enumerate(working):
            if loads[route_index] + demand > instance.capacity:
                continue
            for position in range(len(route) + 1):
                candidate = route[:position] + (customer,) + route[position:]
                value = evaluate_route(instance, candidate)
                if value.feasible:
                    choice = (value.distance - costs[route_index], route_index,
                              position, candidate, value.distance)
                    if best is None or choice[:3] < best[:3]:
                        best = choice
        if best is None:
            return None
        _, route_index, _, candidate, cost = best
        working[route_index] = candidate
        costs[route_index] = cost
        loads[route_index] += demand
    return tuple(working)


def _spatial_seeds(instance: Instance, fleet_size: int, rng: Random) -> list[int]:
    """Choose geographically spread initial routes using squared distances."""
    ids = list(range(1, instance.customer_count + 1))
    seeds = [rng.choice(ids)]
    while len(seeds) < fleet_size:
        weights = [min(instance.distance[i][j] for j in seeds) ** 2 for i in ids]
        for customer in seeds:
            weights[customer - 1] = 0
        if not any(weights):
            seeds.append(next(customer for customer in ids if customer not in seeds))
        else:
            seeds.append(rng.choices(ids, weights=weights, k=1)[0])
    return seeds


def try_fleet(instance: Instance, fleet_size: int, rng: Random, trial: int) -> Routes | None:
    """Build exactly K nonempty routes from randomized spread seeds."""
    if not 1 <= fleet_size <= min(instance.vehicle_count, instance.customer_count):
        raise ValueError("fleet_size outside supported range")
    if fleet_size < capacity_lower_bound(instance):
        return None
    ids = list(range(1, instance.customer_count + 1))
    seeds = _spatial_seeds(instance, fleet_size, rng)
    routes = tuple((customer,) for customer in seeds)
    if any(not evaluate_route(instance, route).feasible for route in routes):
        return None
    if trial % 4 == 0:
        order = sorted(ids, key=lambda i: instance.customers[i].due)
    elif trial % 4 == 1:
        order = sorted(ids, key=lambda i: instance.customers[i].ready)
    elif trial % 4 == 2:
        order = sorted(ids, key=lambda i: i + rng.gauss(0, 5))
    else:
        order = sorted(ids, key=lambda i: instance.customers[i].due + rng.gauss(0, 50_000))
    seeded = set(seeds)
    candidate = insert_fixed(instance, routes, [i for i in order if i not in seeded])
    if candidate is None:
        return None
    verdict = validate_solution(instance, candidate)
    if not verdict.feasible or verdict.vehicles != fleet_size:
        raise AssertionError(f"fixed-fleet construction invalid: {verdict.first_violation}")
    return candidate


def minimise_fleet(
    instance: Instance,
    incumbent: Routes,
    *,
    seed: int,
    attempts_per_k: int,
    deadline: float | None = None,
    started_at: float | None = None,
) -> FleetResult:
    """Try K-1, K-2, ...; failure within a budget is not infeasibility proof."""
    if type(seed) is not int or type(attempts_per_k) is not int or attempts_per_k < 0:
        raise ValueError("seed must be int and attempts_per_k nonnegative int")
    current = tuple(tuple(route) for route in incumbent if route)
    verdict = validate_solution(instance, current)
    if not verdict.feasible:
        raise ValueError(f"incumbent is infeasible: {verdict.first_violation}")
    lower = max(1, capacity_lower_bound(instance))
    attempts: list[tuple[int, int]] = []
    milestones: list[FleetMilestone] = []
    started_at = monotonic() if started_at is None else started_at
    while len(current) > lower:
        target = len(current) - 1
        # Separate streams keep K's trial sequence stable if earlier K changes.
        rng = Random(seed + (target - lower) * 9973)
        found = None
        count = 0
        for trial in range(attempts_per_k):
            if deadline is not None and monotonic() >= deadline:
                attempts.append((target, count))
                return FleetResult(current, tuple(attempts), tuple(milestones), lower, "time_limit")
            count += 1
            found = try_fleet(instance, target, rng, trial)
            if found is not None:
                break
        attempts.append((target, count))
        if found is None:
            return FleetResult(current, tuple(attempts), tuple(milestones), lower,
                               "target_not_found")
        current = found
        distance = validate_solution(instance, current).distance
        milestones.append(FleetMilestone(len(current), distance, monotonic() - started_at))
    return FleetResult(current, tuple(attempts), tuple(milestones), lower, "lower_bound")
