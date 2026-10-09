"""Capacity lower bound and budgeted fixed-fleet feasibility trials."""

from dataclasses import dataclass
from random import Random
from time import perf_counter as monotonic

from .diagnostics import DiagnosticCollector, in_phase
from .evaluate import evaluate_route, validate_solution
from .local_search import Routes
from .problem import Instance
from .route_cache import RouteCache


FLEET_STRATEGIES = ("reconstruct", "cached_reconstruct", "route_removal", "related", "hybrid")
DEFAULT_FLEET_STRATEGY = "hybrid"


@dataclass(frozen=True, slots=True)
class FleetMilestone:
    vehicles: int
    distance: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class FleetTarget:
    target: int
    trials: int
    repair_rounds: int
    elapsed_seconds: float
    first_feasible_seconds: float | None
    status: str  # found / not_found / time_limit; never an infeasibility claim


@dataclass(frozen=True, slots=True)
class FleetResult:
    routes: Routes
    attempts: tuple[tuple[int, int], ...]  # (target fleet, trials actually run)
    milestones: tuple[FleetMilestone, ...]
    lower_bound: int
    stop_reason: str
    targets: tuple[FleetTarget, ...] = ()


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
              cached_repair: bool = False,
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
    pool = [i for i in order if i not in seeded]
    if cached_repair:
        candidate, pending = _repair_partial(instance, routes, pool, order=repair_order,
                                              deadline=deadline, diagnostics=diagnostics,
                                              strategy=repair_strategy, stop_on_blocked=True)
        if pending:
            return None
    else:
        candidate = insert_fixed(instance, routes, pool,
                                 order=repair_order, deadline=deadline,
                                 strategy=repair_strategy, diagnostics=diagnostics)
    if candidate is None:
        return None
    verdict = validate(instance, candidate)
    if not verdict.feasible or verdict.vehicles != fleet_size:
        raise AssertionError(f"fixed-fleet construction invalid: {verdict.first_violation}")
    return candidate


def _insertion_choice(instance: Instance, route: tuple[int, ...],
                      cache: RouteCache | None, customer: int, *,
                      deadline: float | None,
                      diagnostics: DiagnosticCollector | None) -> tuple[int, int] | None:
    """Best (distance delta, position), using exact prefix/suffix time state.

    Source routes are feasible and the pending customer is absent. The prefix
    departs at its earliest feasible time; the untouched suffix's transfer
    function checks both its windows and the depot return. No rounded floats
    or approximate time-window filters enter this decision.
    """
    node = instance.customers[customer]
    if cache is not None and cache.load + node.demand > instance.capacity:
        if diagnostics is not None:
            diagnostics.increment("capacity_prefilter_skips")
        return None
    matrix, depot = instance.distance, instance.customers[0]
    best = None
    for position in range(len(route) + 1):
        if deadline is not None and monotonic() >= deadline:
            return None
        previous = route[position - 1] if position else 0
        nxt = route[position] if position < len(route) else 0
        departure = cache.prefix_departure[position] if cache else depot.ready
        start = max(departure + matrix[previous][customer], node.ready)
        arrival = start + node.service + matrix[customer][nxt]
        codes = set()
        if start > node.due:
            codes.add("time_window")
        if node.demand > instance.capacity:
            codes.add("capacity")
        if position < len(route):
            if arrival > cache.latest[position]:
                codes.add("time_window")
            return_time = max(arrival + cache.duration[position], cache.release[position])
        else:
            return_time = arrival
        if return_time > depot.due:
            codes.add("depot_close")
        if diagnostics is not None:
            diagnostics.increment("incremental_route_evaluations")
            diagnostics.candidate_codes(codes)
        if not codes:
            choice = (matrix[previous][customer] + matrix[customer][nxt]
                      - matrix[previous][nxt], position)
            if best is None or choice < best:
                best = choice
    return best


@in_phase("repair")
def _repair_partial(instance: Instance, routes: Routes, pending: list[int], *,
                    order: str, deadline: float | None,
                    diagnostics: DiagnosticCollector | None,
                    strategy: str = "regret2",
                    stop_on_blocked: bool = False) -> tuple[Routes, list[int]]:
    """Exact cheapest/regret-2 insertion; keep missing clients internal.

    Unlike public insert_fixed, a blocked client does not discard useful work
    or prevent insertion of the other clients. Every source/accepted route is
    fully checked when its cache is built. The caller alone can publish a
    complete solution, after validate_solution checks coverage as well.
    """
    working, pending = list(routes), list(pending)
    if order == "due":
        pending.sort(key=lambda i: (instance.customers[i].due,
                                   instance.customers[i].due - instance.customers[i].ready, i))
    elif order == "slack":
        pending.sort(key=lambda i: (instance.customers[i].due - instance.customers[i].ready,
                                   instance.customers[i].due, i))
    rank = {customer: index for index, customer in enumerate(pending)}
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route

    def make_cache(route):
        value = evaluate(instance, route)
        if not value.feasible:
            raise AssertionError("fleet repair received an infeasible route")
        if diagnostics is not None:
            diagnostics.increment("route_cache_builds")
        return RouteCache(instance, route, _evaluation=value) if route else None

    caches = [make_cache(route) for route in working]
    choices = {customer: [None] * len(working) for customer in pending}
    changed = list(range(len(working)))
    while pending:
        selected = None
        selected_priority = None
        for customer in pending:
            for index in changed:
                if deadline is not None and monotonic() >= deadline:
                    return tuple(working), pending
                choice = _insertion_choice(instance, working[index], caches[index], customer,
                                           deadline=deadline, diagnostics=diagnostics)
                choices[customer][index] = ((choice[0], index, choice[1])
                                           if choice is not None else None)
            options = sorted(choice for choice in choices[customer] if choice is not None)
            if not options:
                if stop_on_blocked:
                    return tuple(working), pending
                continue
            best = options[0]
            if strategy == "cheapest":
                selected = customer, best
                break
            regret = options[1][0] - best[0] if len(options) > 1 else 0
            priority = (len(options) == 1, regret, -best[0], -rank[customer])
            if selected_priority is None or priority > selected_priority:
                selected, selected_priority = (customer, best), priority
        if deadline is not None and monotonic() >= deadline:
            return tuple(working), pending
        if selected is None:
            return tuple(working), pending
        customer, (_, index, position) = selected
        route = working[index]
        working[index] = route[:position] + (customer,) + route[position:]
        caches[index] = make_cache(working[index])
        pending.remove(customer)
        del choices[customer]
        changed = list(range(len(working))) if strategy == "cheapest" else [index]
        if diagnostics is not None:
            diagnostics.increment("accepted")
    return tuple(working), pending


def _related_remove(instance: Instance, routes: Routes, pending: list[int], count: int,
                    rng: Random, *, deadline: float | None = None) -> tuple[Routes, list[int]]:
    """Remove clients near missing clients in space and time-window endpoints.

    A randomized rank selects among nearby clients, rather than reproducing
    the same repair. Empty routes remain usable slots up to the target cap.
    Rounded Euclidean arcs can violate the triangle inequality by one tick;
    any removal that makes a retained route infeasible is rolled back.
    """
    present = [customer for route in routes for customer in route]
    removed = set()
    matrix, customers = instance.distance, instance.customers
    for _ in range(min(count, len(present))):
        if deadline is not None and monotonic() >= deadline:
            break
        anchor = rng.choice(pending)
        node = customers[anchor]
        ranked = sorted((i for i in present if i not in removed), key=lambda i: (
            matrix[anchor][i] + (abs(customers[i].ready - node.ready)
                                + abs(customers[i].due - node.due)) // 4, i))
        # A cubic rank biases towards related customers but explores alternatives.
        removed.add(ranked[min(len(ranked) - 1, int(rng.random() ** 3 * len(ranked)))])
    reduced = []
    for route in routes:
        candidate = tuple(i for i in route if i not in removed)
        if not evaluate_route(instance, candidate).feasible:
            removed.difference_update(route)
            candidate = route
        reduced.append(candidate)
    pool = pending + sorted(removed)
    rng.shuffle(pool)
    return tuple(reduced), pool


def _try_route_removal(instance: Instance, incumbent: Routes, rng: Random, trial: int, *,
                       related_count: int, repair_rounds: int, repair_order: str,
                       deadline: float | None,
                       diagnostics: DiagnosticCollector | None) -> tuple[Routes | None, int]:
    # First cycle prioritises short/low-demand routes, later cycles diversify.
    ranked = sorted(range(len(incumbent)), key=lambda j: (
        len(incumbent[j]), sum(instance.customers[i].demand for i in incumbent[j]), j))
    index = ranked[trial % len(ranked)] if trial < len(ranked) else rng.choice(ranked)
    pending = list(incumbent[index])
    working = tuple(route for j, route in enumerate(incumbent) if j != index)
    rng.shuffle(pending)
    best, best_pending = working, pending
    rounds = 0
    for round_index in range(repair_rounds):
        if deadline is not None and monotonic() >= deadline:
            break
        if round_index:
            # Restart from the best coverage found in this trial. Increase the
            # ruin scale to escape a saturated set of routes without rebuilding.
            working, pending = _related_remove(instance, best, best_pending,
                                               related_count * (1 + (round_index - 1) // 2),
                                               rng, deadline=deadline)
            if diagnostics is not None:
                diagnostics.increment("removed_customers", len(pending) - len(best_pending))
        rounds += 1
        working, pending = _repair_partial(instance, working, pending, order=repair_order,
                                            deadline=deadline, diagnostics=diagnostics)
        if not pending:
            candidate = tuple(route for route in working if route)
            validate = validate_solution if diagnostics is None else diagnostics.validate_solution
            verdict = validate(instance, candidate)
            if not verdict.feasible or verdict.vehicles >= len(incumbent):
                raise AssertionError(f"route removal returned invalid solution: {verdict.first_violation}")
            return candidate, rounds
        score = (len(pending), sum(instance.customers[i].demand for i in pending))
        best_score = (len(best_pending), sum(instance.customers[i].demand for i in best_pending))
        if score <= best_score:
            best, best_pending = working, list(pending)
    return None, rounds


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
    strategy: str = DEFAULT_FLEET_STRATEGY,
    repair_rounds: int = 5,
    related_count: int = 8,
    diagnostics: DiagnosticCollector | None = None,
) -> FleetResult:
    """Try K-1, K-2, ...; failure within a budget is not infeasibility proof."""
    if type(seed) is not int or type(attempts_per_k) is not int or attempts_per_k < 0:
        raise ValueError("seed must be int and attempts_per_k nonnegative int")
    if repair_order not in ("input", "due", "slack"):
        raise ValueError("repair_order must be 'input', 'due' or 'slack'")
    if repair_strategy not in ("cheapest", "regret2"):
        raise ValueError("repair_strategy must be 'cheapest' or 'regret2'")
    if strategy not in FLEET_STRATEGIES:
        raise ValueError(f"strategy must be one of {FLEET_STRATEGIES}")
    if (type(repair_rounds) is not int or repair_rounds < 1
            or type(related_count) is not int or related_count < 1):
        raise ValueError("repair_rounds and related_count must be positive integers")
    current = tuple(tuple(route) for route in incumbent if route)
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    verdict = validate(instance, current)
    if not verdict.feasible:
        raise ValueError(f"incumbent is infeasible: {verdict.first_violation}")
    lower = max(1, capacity_lower_bound(instance))
    attempts: list[tuple[int, int]] = []
    milestones: list[FleetMilestone] = []
    targets: list[FleetTarget] = []
    started_at = monotonic() if started_at is None else started_at
    while len(current) > lower:
        target = len(current) - 1
        # Separate streams keep K's trial sequence stable if earlier K changes.
        rng = Random(seed + (target - lower) * 9973)
        removal_rng = Random(seed + (target - lower) * 9973 + 1_000_003)
        found = None
        count = 0
        rounds = 0
        reconstruction_trials = removal_trials = 0
        target_started = monotonic()

        def finish_target(status):
            ended = monotonic()
            attempts.append((target, count))
            targets.append(FleetTarget(target, count, rounds, ended - target_started,
                                        ended - target_started if status == "found" else None,
                                        status))

        for trial in range(attempts_per_k):
            if deadline is not None and monotonic() >= deadline:
                finish_target("time_limit")
                return FleetResult(current, tuple(attempts), tuple(milestones), lower,
                                   "time_limit", tuple(targets))
            count += 1
            if diagnostics is not None:
                diagnostics.increment("trials")
            if strategy in ("reconstruct", "cached_reconstruct") or (strategy == "hybrid" and trial % 2 == 0):
                found = try_fleet(instance, target, rng, reconstruction_trials,
                                  repair_order=repair_order, repair_strategy=repair_strategy,
                                  cached_repair=strategy != "reconstruct",
                                  deadline=deadline, diagnostics=diagnostics)
                reconstruction_trials += 1
                rounds += 1
            else:
                found, used_rounds = _try_route_removal(
                    instance, current, removal_rng, removal_trials,
                    related_count=related_count,
                    repair_rounds=1 if strategy == "route_removal" else repair_rounds,
                    repair_order=repair_order, deadline=deadline, diagnostics=diagnostics)
                removal_trials += 1
                rounds += used_rounds
            if found is not None:
                break
            if diagnostics is not None:
                diagnostics.increment("failures")
        expired = deadline is not None and monotonic() >= deadline
        finish_target("found" if found is not None else "time_limit" if expired else "not_found")
        if found is None:
            return FleetResult(current, tuple(attempts), tuple(milestones), lower,
                               "time_limit" if expired else "target_not_found", tuple(targets))
        current = found
        distance = validate(instance, current).distance
        milestones.append(FleetMilestone(len(current), distance, monotonic() - started_at))
    return FleetResult(current, tuple(attempts), tuple(milestones), lower,
                       "lower_bound", tuple(targets))
