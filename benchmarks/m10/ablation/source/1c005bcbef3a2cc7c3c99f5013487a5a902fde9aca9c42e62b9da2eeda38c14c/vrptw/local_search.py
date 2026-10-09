"""Feasible fixed-fleet search with optional exact route caches and neighbours."""

from dataclasses import dataclass
from time import perf_counter as monotonic
from typing import Iterable, Iterator

from .diagnostics import DiagnosticCollector, in_phase
from .evaluate import RouteEvaluation, evaluate_route, validate_solution
from .problem import Instance
from .route_cache import RouteCache
from .neighbourhood import allows_move, compute_neighbours


BASE_OPERATORS = ("relocate", "swap", "two_opt", "two_opt_star")
SEGMENT_OPERATORS = ("relocate_pair", "exchange_pair_single", "exchange_pairs")
OPERATORS = BASE_OPERATORS + SEGMENT_OPERATORS
DEFAULT_OPERATORS = BASE_OPERATORS
DEFAULT_OPERATOR_SCHEDULE = "cyclic"
Routes = tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class Move:
    """Zero-based positions; relocate destinations refer to the reduced route.

    Pair moves preserve segment order. Exchange positions refer to the source
    routes; same-route segments must not overlap. two_opt is half-open.
    """

    kind: str
    route_a: int
    index_a: int
    route_b: int
    index_b: int


@dataclass(frozen=True, slots=True)
class AcceptedMove:
    move: Move
    delta: int
    distance_after: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    routes: Routes
    initial_distance: int
    distance: int
    moves: tuple[AcceptedMove, ...]
    stop_reason: str


def apply_move(routes: Iterable[Iterable[int]], move: Move) -> Routes:
    """Apply one structural move without checking VRPTW feasibility."""
    changed = [list(route) for route in routes]
    a, b = move.route_a, move.route_b
    if not 0 <= a < len(changed) or not 0 <= b < len(changed):
        raise ValueError("move route index out of range")
    if move.kind == "relocate":
        last_slot = len(changed[b]) - (a == b)
        if not 0 <= move.index_a < len(changed[a]) or not 0 <= move.index_b <= last_slot:
            raise ValueError("relocate position out of range")
        node = changed[a].pop(move.index_a)
        changed[b].insert(move.index_b, node)
    elif move.kind == "swap":
        if not 0 <= move.index_a < len(changed[a]) or not 0 <= move.index_b < len(changed[b]):
            raise ValueError("swap position out of range")
        changed[a][move.index_a], changed[b][move.index_b] = (
            changed[b][move.index_b], changed[a][move.index_a]
        )
    elif move.kind == "two_opt":
        if a != b:
            raise ValueError("two_opt must stay within one route")
        if not 0 <= move.index_a < move.index_b <= len(changed[a]) or move.index_b - move.index_a < 2:
            raise ValueError("two_opt segment must contain at least two customers")
        changed[a][move.index_a:move.index_b] = reversed(
            changed[a][move.index_a:move.index_b]
        )
    elif move.kind == "two_opt_star":
        if a == b:
            raise ValueError("two_opt_star needs two routes")
        if not 0 <= move.index_a <= len(changed[a]) or not 0 <= move.index_b <= len(changed[b]):
            raise ValueError("two_opt_star cut out of range")
        first_tail = changed[a][move.index_a:]
        second_tail = changed[b][move.index_b:]
        changed[a][move.index_a:] = second_tail
        changed[b][move.index_b:] = first_tail
    elif move.kind == "relocate_pair":
        ia, ib = move.index_a, move.index_b
        last_slot = len(changed[b]) - (2 if a == b else 0)
        if not 0 <= ia <= len(changed[a]) - 2 or not 0 <= ib <= last_slot:
            raise ValueError("relocate_pair position out of range")
        segment = changed[a][ia:ia + 2]
        del changed[a][ia:ia + 2]
        changed[b][ib:ib] = segment
    elif move.kind in ("exchange_pair_single", "exchange_pairs"):
        ia, ib = move.index_a, move.index_b
        size_b = 1 if move.kind == "exchange_pair_single" else 2
        if not 0 <= ia <= len(changed[a]) - 2 or not 0 <= ib <= len(changed[b]) - size_b:
            raise ValueError(f"{move.kind} position out of range")
        segment_a = changed[a][ia:ia + 2]
        segment_b = changed[b][ib:ib + size_b]
        if a != b:
            changed[a][ia:ia + 2] = segment_b
            changed[b][ib:ib + size_b] = segment_a
        elif ia + 2 <= ib:
            changed[a] = (changed[a][:ia] + segment_b + changed[a][ia + 2:ib]
                          + segment_a + changed[a][ib + size_b:])
        elif ib + size_b <= ia:
            changed[a] = (changed[a][:ib] + segment_a + changed[a][ib + size_b:ia]
                          + segment_b + changed[a][ia + 2:])
        else:
            raise ValueError("exchange segments must not overlap")
    else:
        raise ValueError(f"unknown move kind: {move.kind}")
    return tuple(tuple(route) for route in changed)


def move_delta(instance: Instance, routes: Iterable[Iterable[int]], move: Move, *,
               diagnostics: DiagnosticCollector | None = None) -> int | None:
    """Recompute affected routes; return None if the move breaks feasibility."""
    original = tuple(tuple(route) for route in routes)
    candidate = apply_move(original, move)
    affected = {move.route_a, move.route_b}
    if any(not original[index] or not candidate[index] for index in affected):
        if diagnostics is not None:
            diagnostics.candidate(empty=True)
        return None  # M3 holds the number of used vehicles fixed.
    evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
    before = [evaluate(instance, original[index]) for index in affected]
    if any(not result.feasible for result in before):
        raise ValueError("move_delta requires feasible source routes")
    after = [evaluate(instance, candidate[index]) for index in affected]
    if diagnostics is not None:
        diagnostics.candidate(*after)
    if any(not result.feasible for result in after):
        return None
    return sum(result.distance for result in after) - sum(result.distance for result in before)


def enumerate_moves(routes: Routes, operators: Iterable[str] = OPERATORS) -> Iterator[Move]:
    """Yield deterministic fixed-fleet candidates in the supplied operator order."""
    operators = tuple(operators)
    unknown = set(operators) - set(OPERATORS)
    if unknown:
        raise ValueError(f"unknown operators: {sorted(unknown)}")
    count = len(routes)
    for kind in operators:
        if kind == "relocate":
            for a, route_a in enumerate(routes):
                for ia in range(len(route_a)):
                    for b, route_b in enumerate(routes):
                        if not route_b:
                            continue
                        if a != b and len(route_a) == 1:
                            continue
                        slots = len(route_b) if a == b else len(route_b) + 1
                        for ib in range(slots):
                            if a != b or ia != ib:
                                yield Move(kind, a, ia, b, ib)
        elif kind == "swap":
            for a in range(count):
                for b in range(a, count):
                    for ia in range(len(routes[a])):
                        first_b = ia + 1 if a == b else 0
                        for ib in range(first_b, len(routes[b])):
                            yield Move(kind, a, ia, b, ib)
        elif kind == "two_opt":
            for a, route in enumerate(routes):
                for start in range(len(route) - 1):
                    for end in range(start + 2, len(route) + 1):
                        yield Move(kind, a, start, a, end)
        elif kind == "two_opt_star":
            for a in range(count):
                for b in range(a + 1, count):
                    if not routes[a] or not routes[b]:
                        continue
                    for cut_a in range(len(routes[a]) + 1):
                        for cut_b in range(len(routes[b]) + 1):
                            if (cut_a, cut_b) not in ((0, 0),
                                                      (len(routes[a]), len(routes[b]))):
                                yield Move(kind, a, cut_a, b, cut_b)
        elif kind == "relocate_pair":
            for a, route_a in enumerate(routes):
                for ia in range(len(route_a) - 1):
                    for b, route_b in enumerate(routes):
                        if not route_b:
                            continue
                        if a != b and len(route_a) == 2:
                            continue
                        slots = len(route_b) - 1 if a == b else len(route_b) + 1
                        for ib in range(slots):
                            if a != b or ia != ib:
                                yield Move(kind, a, ia, b, ib)
        else:  # continuous pair/single and pair/pair exchanges
            size_b = 1 if kind == "exchange_pair_single" else 2
            for a, route_a in enumerate(routes):
                # Unequal exchanges are directional: the pair may be on either route.
                for b in range(a if size_b == 2 else 0, count):
                    for ia in range(len(route_a) - 1):
                        for ib in range(len(routes[b]) - size_b + 1):
                            if a == b:
                                if not (ia + 2 <= ib or ib + size_b <= ia):
                                    continue
                                if size_b == 2 and ib < ia:
                                    continue
                            yield Move(kind, a, ia, b, ib)


class CachedMoveEvaluator:
    """Evaluate affected routes only; cached source costs never change on rejection."""

    def __init__(self, instance: Instance, routes: Routes, *, mode: str = "incremental",
                 diagnostics: DiagnosticCollector | None = None,
                 evaluations: tuple[RouteEvaluation, ...] | None = None) -> None:
        if mode not in ("cached", "incremental"):
            raise ValueError("cache mode must be 'cached' or 'incremental'")
        self.instance, self.mode, self.diagnostics = instance, mode, diagnostics
        self.routes = tuple(tuple(route) for route in routes)
        self.caches = [RouteCache(instance, route, _evaluation=None if evaluations is None else evaluations[i])
                       for i, route in enumerate(self.routes)]
        if diagnostics is not None:
            diagnostics.increment("route_cache_builds", len(self.routes))
            if evaluations is None:
                diagnostics.increment("route_evaluations", len(self.routes))

    def update(self, routes: Routes, affected: Iterable[int], *,
               evaluations: tuple[RouteEvaluation, ...] | None = None) -> None:
        self.routes = tuple(tuple(route) for route in routes)
        for index in set(affected):
            self.caches[index] = RouteCache(self.instance, self.routes[index],
                                           _evaluation=None if evaluations is None else evaluations[index])
            if self.diagnostics is not None:
                self.diagnostics.increment("route_cache_builds")
                if evaluations is None:
                    self.diagnostics.increment("route_evaluations")

    def delta(self, move: Move) -> int | None:
        a, b = move.route_a, move.route_b
        if not 0 <= a < len(self.routes) or not 0 <= b < len(self.routes):
            raise ValueError("move route index out of range")
        indices = (a,) if a == b else (a, b)
        # apply_move's structural checks remain shared, but copy no other route.
        local = Move(move.kind, 0, move.index_a, 0 if a == b else 1, move.index_b)
        changed = apply_move(tuple(self.routes[index] for index in indices), local)
        diagnostics = self.diagnostics
        if any(not route for route in changed):
            if diagnostics is not None:
                diagnostics.candidate(empty=True)
            return None
        codes: set[str] = set()
        distance = 0
        for index, route in zip(indices, changed):
            if self.mode == "incremental":
                result = self.caches[index].evaluate(route)
                codes.update(result.codes)
                if diagnostics is not None:
                    diagnostics.increment("route_evaluations")
                    diagnostics.increment("incremental_route_evaluations")
                    diagnostics.increment("reused_prefix_customers", result.reused_prefix)
                    diagnostics.increment("reused_suffix_customers", result.reused_suffix)
            else:
                evaluate = evaluate_route if diagnostics is None else diagnostics.evaluate_route
                result = evaluate(self.instance, route)
                codes.update(issue.code for issue in result.violations)
            distance += result.distance - self.caches[index].distance
        if diagnostics is not None:
            diagnostics.candidate_codes(codes)
        return None if codes else distance


@in_phase("local_search")
def improve(
    instance: Instance,
    routes: Iterable[Iterable[int]],
    *,
    strategy: str = "first",
    operators: Iterable[str] = DEFAULT_OPERATORS,
    max_moves: int | None = None,
    deadline: float | None = None,
    diagnostics: DiagnosticCollector | None = None,
    evaluation_mode: str = "incremental",
    num_neighbours: int | None = None,
    neighbours: tuple[frozenset[int], ...] | None = None,
    operator_schedule: str = DEFAULT_OPERATOR_SCHEDULE,
) -> SearchResult:
    """Accept strict distance improvements until local optimum, limit or deadline.

    Cyclic scheduling starts the next scan after the successful operator. A
    complete unsuccessful scan still checks every enabled neighbourhood.
    """
    if strategy not in ("first", "best"):
        raise ValueError("strategy must be 'first' or 'best'")
    if evaluation_mode not in ("full", "cached", "incremental"):
        raise ValueError("evaluation_mode must be 'full', 'cached' or 'incremental'")
    if operator_schedule not in ("fixed", "cyclic"):
        raise ValueError("operator_schedule must be 'fixed' or 'cyclic'")
    if num_neighbours is not None and (type(num_neighbours) is not int or num_neighbours < 1):
        raise ValueError("num_neighbours must be a positive integer or None")
    if max_moves is not None and (type(max_moves) is not int or max_moves < 0):
        raise ValueError("max_moves must be a nonnegative integer or None")
    operators = tuple(operators)
    if not operators or set(operators) - set(OPERATORS):
        raise ValueError("operators must be a nonempty subset of supported moves")
    current = tuple(tuple(route) for route in routes)
    validate = validate_solution if diagnostics is None else diagnostics.validate_solution
    initial = validate(instance, current)
    if not initial.feasible:
        raise ValueError(f"initial solution is infeasible: {initial.first_violation}")
    current_distance = initial.distance
    accepted: list[AcceptedMove] = []
    evaluator = None
    if (evaluation_mode != "full" and all(current) and max_moves != 0
            and (deadline is None or monotonic() < deadline)):
        evaluator = CachedMoveEvaluator(instance, current, mode=evaluation_mode, diagnostics=diagnostics,
                                        evaluations=initial.routes)
    if (neighbours is None and num_neighbours is not None
            and num_neighbours < instance.customer_count - 1 and max_moves != 0
            and (deadline is None or monotonic() < deadline)):
        neighbours = compute_neighbours(instance, num_neighbours)
    if num_neighbours is not None and num_neighbours >= instance.customer_count - 1:
        neighbours = None

    operator_offset = 0
    while max_moves is None or len(accepted) < max_moves:
        if deadline is not None and monotonic() >= deadline:
            return SearchResult(current, initial.distance, current_distance,
                                tuple(accepted), "time_limit")
        best_move: Move | None = None
        best_delta = 0
        scan_order = (operators if operator_offset == 0 else
                      operators[operator_offset:] + operators[:operator_offset])
        for move in enumerate_moves(current, scan_order):
            if deadline is not None and monotonic() >= deadline:
                return SearchResult(current, initial.distance, current_distance,
                                    tuple(accepted), "time_limit")
            if neighbours is not None and not allows_move(current, move, neighbours):
                if diagnostics is not None:
                    diagnostics.increment("neighbour_filtered")
                continue
            delta = (move_delta(instance, current, move, diagnostics=diagnostics)
                     if evaluator is None else evaluator.delta(move))
            if delta is not None and delta < best_delta:
                best_move, best_delta = move, delta
                if strategy == "first":
                    break
        if best_move is None:
            return SearchResult(current, initial.distance, current_distance,
                                tuple(accepted), "local_optimum")

        candidate = apply_move(current, best_move)
        verdict = validate(instance, candidate)
        if (not verdict.feasible or verdict.vehicles != initial.vehicles
                or verdict.distance != current_distance + best_delta):
            raise AssertionError(f"accepted move failed full validation: {best_move}")
        current = candidate
        if evaluator is not None:
            evaluator.update(current, (best_move.route_a, best_move.route_b), evaluations=verdict.routes)
        current_distance = verdict.distance
        accepted.append(AcceptedMove(best_move, best_delta, current_distance))
        if operator_schedule == "cyclic":
            operator_offset = (operators.index(best_move.kind) + 1) % len(operators)
        if diagnostics is not None:
            diagnostics.increment("accepted")

    return SearchResult(current, initial.distance, current_distance,
                        tuple(accepted), "move_limit")
