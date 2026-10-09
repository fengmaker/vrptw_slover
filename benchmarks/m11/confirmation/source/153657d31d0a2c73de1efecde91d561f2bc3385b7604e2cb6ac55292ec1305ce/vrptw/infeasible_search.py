"""Fixed-fleet ILS that can cross load/time violations using integer penalties.

Every candidate contains every client once. Penalties guide current state;
only the independent hard-constraint judge can update the returned best.
"""

from collections import deque
from random import Random
from time import perf_counter as monotonic

from .diagnostics import DiagnosticCollector, in_phase
from .evaluate import validate_solution
from .ils import HistoryRow, ILSResult
from .local_search import Move, Routes, apply_move, enumerate_moves
from .neighbourhood import allows_move
from .penalties import (PenaltyManager, PenaltyParams, SoftEvaluation,
                        evaluate_soft_route, evaluate_soft_solution)
from .problem import Instance


def _expired(deadline: float | None) -> bool:
    return deadline is not None and monotonic() >= deadline


def _route_value(instance, route, diagnostics):
    if diagnostics is not None:
        diagnostics.increment("route_evaluations")
    return evaluate_soft_route(instance, route)


def _aggregate(values) -> SoftEvaluation:
    return SoftEvaluation(len(values), sum(v.distance for v in values),
                          sum(v.excess_load for v in values), sum(v.time_warp for v in values))


@in_phase("perturbation")
def perturb_soft(instance: Instance, routes: Routes, rng: Random, count: int,
                 manager: PenaltyManager, *, deadline: float | None = None,
                 diagnostics: DiagnosticCollector | None = None) -> Routes:
    """Remove related/random clients and reinsert at minimum penalized delta.

    If the shared deadline interrupts an incomplete repair, return the complete
    source. No partial assignment enters acceptance or the feasibility sample.
    """
    customers = [i for route in routes for i in route]
    if not customers or _expired(deadline):
        return routes
    anchor = rng.choice(customers)
    nearest = sorted((i for i in customers if i != anchor),
                     key=lambda i: (instance.distance[anchor][i], i))
    removed = {anchor, *nearest[:max(1, count // 2) - 1]}
    rest = [i for i in customers if i not in removed]
    removed.update(rng.sample(rest, min(count - len(removed), len(rest))))
    pool, reduced = [], []
    for route in routes:
        selected = [i for i in route if i in removed]
        if len(selected) == len(route) and selected:
            selected.pop(rng.randrange(len(selected)))
        selected_set = set(selected)
        reduced.append(tuple(i for i in route if i not in selected_set))
        pool.extend(selected)
    rng.shuffle(pool)
    if diagnostics is not None:
        diagnostics.increment("removed_customers", len(pool))
    values = [_route_value(instance, route, diagnostics) for route in reduced]
    for client in pool:
        best = None
        for index, route in enumerate(reduced):
            if not route:  # never activate a previously empty vehicle
                continue
            old_cost = manager.cost(values[index])
            for position in range(len(route) + 1):
                if _expired(deadline):
                    return routes
                changed = route[:position] + (client,) + route[position:]
                value = _route_value(instance, changed, diagnostics)
                rank = (manager.cost(value) - old_cost, index, position)
                if best is None or rank < best[0]:
                    best = rank, changed, value
        if best is None:
            return routes
        _, index, _ = best[0]
        reduced[index], values[index] = best[1:]
    return tuple(reduced)


@in_phase("local_search")
def improve_soft(instance: Instance, routes: Routes, manager: PenaltyManager, *,
                 operators: tuple[str, ...], max_moves: int | None,
                 deadline: float | None, strategy: str = "first",
                 operator_schedule: str = "cyclic", neighbours=None,
                 diagnostics: DiagnosticCollector | None = None):
    """Descend a fixed penalty objective, caching unaffected route totals.

    Also retain intermediate feasible states: a later penalized improvement
    may leave feasibility again before this descent returns.
    """
    evaluation = evaluate_soft_solution(instance, routes)
    current = routes
    active = [i for i, route in enumerate(current) if route]
    values = {i: _route_value(instance, current[i], diagnostics) for i in active}
    feasible_best = None

    def retain_feasible(candidate, soft):
        nonlocal feasible_best
        if soft.feasible and (feasible_best is None or soft.distance < feasible_best[1].distance):
            validate = validate_solution if diagnostics is None else diagnostics.validate_solution
            verdict = validate(instance, candidate)
            if not verdict.feasible or verdict.objective != (soft.vehicles, soft.distance):
                raise AssertionError("soft feasibility disagrees with independent validation")
            feasible_best = candidate, soft

    retain_feasible(current, evaluation)
    offset, accepted = 0, 0
    while max_moves is None or accepted < max_moves:
        if _expired(deadline):
            break
        chosen, chosen_delta = None, 0
        scan_order = operators[offset:] + operators[:offset]
        for move in enumerate_moves(current, scan_order):
            if _expired(deadline):
                break
            if neighbours is not None and not allows_move(current, move, neighbours):
                if diagnostics is not None:
                    diagnostics.increment("neighbour_filtered")
                continue
            indices = (move.route_a,) if move.route_a == move.route_b else (move.route_a, move.route_b)
            local = Move(move.kind, 0, move.index_a, 0 if len(indices) == 1 else 1, move.index_b)
            changed = apply_move(tuple(current[i] for i in indices), local)
            if any(not route for route in changed):
                if diagnostics is not None:
                    diagnostics.candidate(empty=True)
                continue
            after = tuple(_route_value(instance, route, diagnostics) for route in changed)
            if diagnostics is not None:
                load = evaluation.excess_load + sum(v.excess_load - values[i].excess_load
                                                    for i, v in zip(indices, after))
                warp = evaluation.time_warp + sum(v.time_warp - values[i].time_warp
                                                  for i, v in zip(indices, after))
                diagnostics.increment("candidates")
                diagnostics.increment("penalized_candidates")
                diagnostics.increment("infeasible_candidates" if load or warp else "feasible_candidates")
            delta = sum(manager.cost(v) - manager.cost(values[i]) for i, v in zip(indices, after))
            if delta < chosen_delta:
                chosen, chosen_delta = (move, indices, changed, after), delta
                if strategy == "first":
                    break
        if chosen is None:
            break
        move, indices, changed, after = chosen
        updated = list(current)
        for index, route, value in zip(indices, changed, after):
            updated[index], values[index] = route, value
        current = tuple(updated)
        evaluation = _aggregate(values.values())
        retain_feasible(current, evaluation)
        accepted += 1
        if diagnostics is not None:
            diagnostics.increment("accepted")
            if not evaluation.feasible:
                diagnostics.increment("accepted_infeasible")
        if operator_schedule == "cyclic":
            offset = (operators.index(move.kind) + 1) % len(operators)
    return current, evaluation, feasible_best


@in_phase("ils_control")
def run_penalized_ils(instance: Instance, start: Routes, *, seed: int,
                      max_iterations: int | None, deadline: float | None,
                      max_moves: int | None, operators: tuple[str, ...],
                      search_strategy: str, remove_min: int, remove_max: int,
                      restart_after: int, params: PenaltyParams,
                      adaptive: bool, started_at: float, neighbours=None,
                      operator_schedule: str = "cyclic",
                      diagnostics: DiagnosticCollector | None = None) -> ILSResult:
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    initial = validate(instance, start)
    if not initial.feasible:
        raise ValueError("penalized ILS requires a feasible complete start")
    manager = PenaltyManager(instance, params=params, adaptive=adaptive)
    rng = Random(seed)
    current = best = start
    current_value = best_value = evaluate_soft_solution(instance, start)
    recent = deque([manager.cost(current_value)], maxlen=20)
    history = []
    iteration, without_best = 0, 0

    def record(candidate_value, accepted, event, updated=False):
        history.append(HistoryRow(
            iteration, current_value.vehicles, current_value.distance,
            candidate_value.vehicles if candidate_value is not None else None,
            candidate_value.distance if candidate_value is not None else None,
            candidate_value.feasible if candidate_value is not None else True,
            best_value.vehicles, best_value.distance, accepted, event,
            monotonic() - started_at, current_value.feasible,
            current_value.excess_load, current_value.time_warp,
            candidate_value.excess_load if candidate_value is not None else None,
            candidate_value.time_warp if candidate_value is not None else None,
            manager.load_weight, manager.time_weight,
            manager.load_feasible_rate, manager.time_feasible_rate,
            manager.feasible_rate, updated, manager.cost(current_value)))

    record(None, True, "initial")
    while True:
        if _expired(deadline):
            return ILSResult(best, tuple(history), iteration, "time_limit")
        if max_iterations is not None and iteration >= max_iterations:
            return ILSResult(best, tuple(history), iteration, "max_iterations")
        iteration += 1
        count = rng.randint(min(remove_min, instance.customer_count), min(remove_max, instance.customer_count))
        candidate = perturb_soft(instance, current, rng, count, manager,
                                 deadline=deadline, diagnostics=diagnostics)
        candidate, candidate_value, found = improve_soft(
            instance, candidate, manager, operators=operators, max_moves=max_moves,
            deadline=deadline, strategy=search_strategy,
            operator_schedule=operator_schedule, neighbours=neighbours, diagnostics=diagnostics)
        # Re-evaluate accepted descent output once from its complete sequence.
        if evaluate_soft_solution(instance, candidate) != candidate_value:
            raise AssertionError("penalized affected-route totals differ from full recomputation")
        improved = found is not None and found[1].distance < best_value.distance
        if improved:
            best, best_value = found
            without_best = 0
        else:
            without_best += 1
        score = manager.cost(candidate_value)
        recent.append(score)
        accepted = 2 * len(recent) * score <= min(recent) * len(recent) + sum(recent)
        if accepted:
            current, current_value = candidate, candidate_value
        updated = manager.register(candidate_value)
        # Costs from different penalty epochs cannot share a threshold.
        if updated:
            recent.clear()
            recent.append(manager.cost(current_value))
        event = "best" if improved else "candidate"
        if without_best >= restart_after:
            current, current_value = best, best_value
            recent.clear()
            recent.append(manager.cost(best_value))
            without_best = 0
            event = "restart"
        record(candidate_value, accepted, event, updated)
