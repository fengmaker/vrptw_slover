"""Feasible-only iterated local search with explicit best/current/candidate state."""

from collections import deque
from dataclasses import dataclass
from random import Random
from time import monotonic

from .evaluate import validate_solution
from .fleet import insert_fixed
from .local_search import OPERATORS, Routes, improve
from .problem import Instance


@dataclass(frozen=True, slots=True)
class HistoryRow:
    iteration: int
    current_vehicles: int
    current_distance: int
    candidate_vehicles: int | None
    candidate_distance: int | None
    candidate_feasible: bool
    best_vehicles: int
    best_distance: int
    accepted: bool
    event: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ILSResult:
    routes: Routes
    history: tuple[HistoryRow, ...]
    iterations: int
    stop_reason: str


def _perturb(instance: Instance, routes: Routes, rng: Random, remove_count: int) -> Routes | None:
    """Remove a related/random client mix and repair within the same fleet."""
    all_customers = [customer for route in routes for customer in route]
    anchor = rng.choice(all_customers)
    nearest = sorted((i for i in all_customers if i != anchor),
                     key=lambda i: (instance.distance[anchor][i], i))
    related_count = max(1, remove_count // 2)
    removed = {anchor, *nearest[:related_count - 1]}
    rest = [i for i in all_customers if i not in removed]
    removed.update(rng.sample(rest, min(remove_count - len(removed), len(rest))))
    # Keep every current route nonempty, so this remains fixed-fleet search.
    pool: list[int] = []
    reduced: list[tuple[int, ...]] = []
    for route in routes:
        selected = [i for i in route if i in removed]
        if len(selected) == len(route):
            selected.pop(rng.randrange(len(selected)))
        selected_set = set(selected)
        reduced.append(tuple(i for i in route if i not in selected_set))
        pool.extend(selected)
    if not pool:
        return routes
    rng.shuffle(pool)
    return insert_fixed(instance, tuple(reduced), pool)


def run_ils(
    instance: Instance,
    start: Routes,
    *,
    seed: int,
    max_iterations: int | None,
    deadline: float | None,
    max_moves: int,
    search_strategy: str = "first",
    operators: tuple[str, ...] = OPERATORS,
    remove_min: int = 3,
    remove_max: int = 8,
    restart_after: int = 20,
    started_at: float | None = None,
) -> ILSResult:
    """Keep the best feasible solution; accept current by a rolling threshold."""
    if max_iterations is None and deadline is None:
        raise ValueError("ILS needs an iteration or time stopping limit")
    if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 0):
        raise ValueError("max_iterations must be nonnegative or None")
    if not 1 <= remove_min <= remove_max or restart_after < 1:
        raise ValueError("invalid perturbation or restart settings")
    rng = Random(seed)
    current = tuple(tuple(route) for route in start)
    verdict = validate_solution(instance, current)
    if not verdict.feasible:
        raise ValueError(f"ILS start is infeasible: {verdict.first_violation}")
    best = current
    best_distance = current_distance = verdict.distance
    vehicles = verdict.vehicles
    started_at = monotonic() if started_at is None else started_at
    history = [HistoryRow(0, vehicles, current_distance, None, None, True,
                          vehicles, best_distance, True, "initial",
                          monotonic() - started_at)]
    recent = deque([current_distance], maxlen=20)
    without_best = 0
    iteration = 0
    while True:
        if deadline is not None and monotonic() >= deadline:
            return ILSResult(best, tuple(history), iteration, "time_limit")
        if max_iterations is not None and iteration >= max_iterations:
            return ILSResult(best, tuple(history), iteration, "max_iterations")
        iteration += 1
        count = rng.randint(min(remove_min, instance.customer_count),
                            min(remove_max, instance.customer_count))
        candidate = _perturb(instance, current, rng, count)
        candidate_distance = None
        accepted = False
        event = "repair_failed"
        if candidate is not None:
            search = improve(instance, candidate, strategy=search_strategy,
                             operators=operators, max_moves=max_moves, deadline=deadline)
            candidate = search.routes
            checked = validate_solution(instance, candidate)
            if not checked.feasible or checked.vehicles != vehicles:
                raise AssertionError(f"ILS candidate invalid: {checked.first_violation}")
            candidate_distance = checked.distance
            recent.append(candidate_distance)
            threshold = (min(recent) + sum(recent) / len(recent)) / 2
            accepted = candidate_distance <= threshold
            if accepted:
                current, current_distance = candidate, candidate_distance
            event = "candidate"
            if candidate_distance < best_distance:
                best, best_distance = candidate, candidate_distance
                without_best = 0
                event = "best"
            else:
                without_best += 1
        else:
            without_best += 1
        if without_best >= restart_after:
            current, current_distance = best, best_distance
            recent.clear()
            recent.append(best_distance)
            without_best = 0
            event = "restart"
        history.append(HistoryRow(iteration, vehicles, current_distance, vehicles if candidate is not None else None,
                                  candidate_distance, candidate is not None, vehicles, best_distance,
                                  accepted, event, monotonic() - started_at))
