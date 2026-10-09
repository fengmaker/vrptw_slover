"""Capacity lower bound and budgeted fixed-fleet feasibility trials."""

from dataclasses import dataclass
from random import Random
from time import perf_counter as monotonic

from .diagnostics import DiagnosticCollector, in_phase
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


@in_phase("repair")
def insert_fixed(instance: Instance, routes: Routes, customers: list[int], *,
                 order: str = "input", deadline: float | None = None,
                 strategy: str = "cheapest",
                 diagnostics: DiagnosticCollector | None = None) -> Routes | None:
    """Cheapest feasible insertion; return None on failure or deadline expiry.

    Only complete repairs escape. `due` orders by deadline then window width;
    `slack` orders by window width then deadline. Both break ties by customer ID.
    """
    if order not in ("input", "due", "slack"):
        raise ValueError("order must be 'input', 'due' or 'slack'")
    if strategy not in ("cheapest", "regret2"):
        raise ValueError("strategy must be 'cheapest' or 'regret2'")
    if deadline is not None and monotonic() >= deadline:
        return None
    working = list(routes)
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
    evaluations = [evaluate(instance, route) for route in working]
    if any(not value.feasible for value in evaluations):
        raise ValueError("insert_fixed requires feasible source routes")
    costs = [value.distance for value in evaluations]
    loads = [value.load for value in evaluations]
    pending = list(customers)
    if order == "due":
        pending.sort(key=lambda i: (instance.customers[i].due,
                                   instance.customers[i].due - instance.customers[i].ready, i))
    elif order == "slack":
        pending.sort(key=lambda i: (instance.customers[i].due - instance.customers[i].ready,
                                   instance.customers[i].due, i))
    if strategy == "regret2":
        return _insert_regret(instance, working, pending, costs, loads,
                              deadline=deadline, diagnostics=diagnostics)
    for customer in pending:
        demand = instance.customers[customer].demand
        best: tuple[int, int, int, tuple[int, ...], int] | None = None
        for route_index, route in enumerate(working):
            if deadline is not None and monotonic() >= deadline:
                return None
            if loads[route_index] + demand > instance.capacity:
                if diagnostics is not None:
                    diagnostics.increment("capacity_prefilter_skips")
                continue
            for position in range(len(route) + 1):
                if deadline is not None and monotonic() >= deadline:
                    return None
                candidate = route[:position] + (customer,) + route[position:]
                value = evaluate(instance, candidate)
                if diagnostics is not None:
                    diagnostics.candidate(value)
                if value.feasible:
                    choice = (value.distance - costs[route_index], route_index,
                              position, candidate, value.distance)
                    if best is None or choice[:3] < best[:3]:
                        best = choice
        if best is None:
            if diagnostics is not None:
                diagnostics.increment("failures")
            return None
        _, route_index, _, candidate, cost = best
        working[route_index] = candidate
        costs[route_index] = cost
        loads[route_index] += demand
        if diagnostics is not None:
            diagnostics.increment("accepted")
    return tuple(working)


def _insert_regret(instance: Instance, working: list[tuple[int, ...]], pending: list[int],
                   costs: list[int], loads: list[int], *, deadline: float | None,
                   diagnostics: DiagnosticCollector | None) -> Routes | None:
    """Regret-2 across distinct routes, using fully evaluated insertion positions.

    Customers with only one feasible route take priority, then the largest
    second-best minus best insertion cost. Ties favour the cheaper insertion
    and the requested customer order. Choices on an unchanged route remain
    valid; every remaining customer's choice on a changed route is recomputed.
    """
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
    # A choice holds delta, route index, position, candidate sequence, new cost.
    choices: dict[int, list[tuple | None]] = {i: [None] * len(working) for i in pending}
    rank = {customer: index for index, customer in enumerate(pending)}
    changed = list(range(len(working)))
    while pending:
        for customer in pending:
            for route_index in changed:
                if deadline is not None and monotonic() >= deadline:
                    return None
                route = working[route_index]
                best = None
                if loads[route_index] + instance.customers[customer].demand > instance.capacity:
                    if diagnostics is not None:
                        diagnostics.increment("capacity_prefilter_skips")
                else:
                    for position in range(len(route) + 1):
                        if deadline is not None and monotonic() >= deadline:
                            return None
                        candidate = route[:position] + (customer,) + route[position:]
                        value = evaluate(instance, candidate)
                        if diagnostics is not None:
                            diagnostics.candidate(value)
                        if value.feasible:
                            choice = (value.distance - costs[route_index], route_index,
                                      position, candidate, value.distance)
                            if best is None or choice[:3] < best[:3]:
                                best = choice
                choices[customer][route_index] = best
            if not any(choice is not None for choice in choices[customer]):
                if diagnostics is not None:
                    diagnostics.increment("failures")
                return None
        selected = None
        selected_priority = None
        for customer in pending:
            options = sorted((choice for choice in choices[customer] if choice is not None),
                             key=lambda choice: choice[:3])
            best = options[0]
            regret = options[1][0] - best[0] if len(options) > 1 else 0
            priority = (len(options) == 1, regret, -best[0], -rank[customer])
            if selected_priority is None or priority > selected_priority:
                selected, selected_priority = (customer, best), priority
        customer, (_, route_index, _, candidate, cost) = selected
        working[route_index] = candidate
        costs[route_index] = cost
        loads[route_index] += instance.customers[customer].demand
        pending.remove(customer)
        del choices[customer]
        changed = [route_index]
        if diagnostics is not None:
            diagnostics.increment("accepted")
    return tuple(working)


def _spatial_seeds(instance: Instance, fleet_size: int, rng: Random, *,
                   deadline: float | None = None) -> list[int] | None:
    """Choose geographically spread initial routes using squared distances."""
    ids = list(range(1, instance.customer_count + 1))
    seeds = [rng.choice(ids)]
    while len(seeds) < fleet_size:
        if deadline is not None and monotonic() >= deadline:
            return None
        weights = [min(instance.distance[i][j] for j in seeds) ** 2 for i in ids]
        for customer in seeds:
            weights[customer - 1] = 0
        if not any(weights):
            seeds.append(next(customer for customer in ids if customer not in seeds))
        else:
            seeds.append(rng.choices(ids, weights=weights, k=1)[0])
    return seeds


def try_fleet(instance: Instance, fleet_size: int, rng: Random, trial: int, *,
              repair_order: str = "input", deadline: float | None = None,
              repair_strategy: str = "cheapest",
              diagnostics: DiagnosticCollector | None = None) -> Routes | None:
    """Build exactly K nonempty routes from randomized spread seeds."""
    if not 1 <= fleet_size <= min(instance.vehicle_count, instance.customer_count):
        raise ValueError("fleet_size outside supported range")
    if fleet_size < capacity_lower_bound(instance):
        return None
    if deadline is not None and monotonic() >= deadline:
        return None
    ids = list(range(1, instance.customer_count + 1))
    seeds = _spatial_seeds(instance, fleet_size, rng, deadline=deadline)
    if seeds is None:
        return None
    routes = tuple((customer,) for customer in seeds)
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    if any(not evaluate(instance, route).feasible for route in routes):
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
    candidate = insert_fixed(instance, routes, [i for i in order if i not in seeded],
                             order=repair_order, deadline=deadline,
                             strategy=repair_strategy,
                             diagnostics=diagnostics)
    if candidate is None:
        return None
    verdict = validate(instance, candidate)
    if not verdict.feasible or verdict.vehicles != fleet_size:
        raise AssertionError(f"fixed-fleet construction invalid: {verdict.first_violation}")
    return candidate


@in_phase("fleet_reduction")
def minimise_fleet(
    instance: Instance,
    incumbent: Routes,
    *,
    seed: int,
    attempts_per_k: int,
    deadline: float | None = None,
    started_at: float | None = None,
    repair_order: str = "input",
    repair_strategy: str = "cheapest",
    diagnostics: DiagnosticCollector | None = None,
) -> FleetResult:
    """Try K-1, K-2, ...; failure within a budget is not infeasibility proof."""
    if type(seed) is not int or type(attempts_per_k) is not int or attempts_per_k < 0:
        raise ValueError("seed must be int and attempts_per_k nonnegative int")
    if repair_order not in ("input", "due", "slack"):
        raise ValueError("repair_order must be 'input', 'due' or 'slack'")
    if repair_strategy not in ("cheapest", "regret2"):
        raise ValueError("repair_strategy must be 'cheapest' or 'regret2'")
    current = tuple(tuple(route) for route in incumbent if route)
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    verdict = validate(instance, current)
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
            if diagnostics is not None:
                diagnostics.increment("trials")
            found = try_fleet(instance, target, rng, trial, repair_order=repair_order,
                              repair_strategy=repair_strategy,
                              deadline=deadline, diagnostics=diagnostics)
            if found is not None:
                break
            if diagnostics is not None:
                diagnostics.increment("failures")
        attempts.append((target, count))
        if found is None:
            return FleetResult(current, tuple(attempts), tuple(milestones), lower,
                               "time_limit" if deadline is not None and monotonic() >= deadline
                               else "target_not_found")
        current = found
        distance = validate(instance, current).distance
        milestones.append(FleetMilestone(len(current), distance, monotonic() - started_at))
    return FleetResult(current, tuple(attempts), tuple(milestones), lower, "lower_bound")
