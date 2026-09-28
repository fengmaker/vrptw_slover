"""Feasible fixed-fleet neighbourhood search with full route recomputation."""

from dataclasses import dataclass
from time import monotonic
from typing import Iterable, Iterator

from .evaluate import evaluate_route, validate_solution
from .problem import Instance


OPERATORS = ("relocate", "swap", "two_opt", "two_opt_star")
Routes = tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class Move:
    """Route indices are zero-based; two_opt uses half-open [index_a:index_b]."""

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
    else:
        raise ValueError(f"unknown move kind: {move.kind}")
    return tuple(tuple(route) for route in changed)


def move_delta(instance: Instance, routes: Iterable[Iterable[int]], move: Move) -> int | None:
    """Recompute affected routes; return None if the move breaks feasibility."""
    original = tuple(tuple(route) for route in routes)
    candidate = apply_move(original, move)
    affected = {move.route_a, move.route_b}
    if any(not candidate[index] for index in affected):
        return None  # M3 holds the number of used vehicles fixed.
    before = [evaluate_route(instance, original[index]) for index in affected]
    if any(not result.feasible for result in before):
        raise ValueError("move_delta requires feasible source routes")
    after = [evaluate_route(instance, candidate[index]) for index in affected]
    if any(not result.feasible for result in after):
        return None
    return sum(result.distance for result in after) - sum(result.distance for result in before)


def enumerate_moves(routes: Routes, operators: Iterable[str] = OPERATORS) -> Iterator[Move]:
    """Yield deterministic relocate, swap, 2-opt and 2-opt* candidates."""
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
        else:  # two_opt_star
            for a in range(count):
                for b in range(a + 1, count):
                    for cut_a in range(len(routes[a]) + 1):
                        for cut_b in range(len(routes[b]) + 1):
                            if (cut_a, cut_b) not in ((0, 0),
                                                      (len(routes[a]), len(routes[b]))):
                                yield Move(kind, a, cut_a, b, cut_b)


def improve(
    instance: Instance,
    routes: Iterable[Iterable[int]],
    *,
    strategy: str = "first",
    operators: Iterable[str] = OPERATORS,
    max_moves: int | None = None,
    deadline: float | None = None,
) -> SearchResult:
    """Accept strict distance improvements until local optimum or move limit."""
    if strategy not in ("first", "best"):
        raise ValueError("strategy must be 'first' or 'best'")
    if max_moves is not None and (type(max_moves) is not int or max_moves < 0):
        raise ValueError("max_moves must be a nonnegative integer or None")
    operators = tuple(operators)
    if not operators or set(operators) - set(OPERATORS):
        raise ValueError("operators must be a nonempty subset of supported moves")
    current = tuple(tuple(route) for route in routes)
    initial = validate_solution(instance, current)
    if not initial.feasible:
        raise ValueError(f"initial solution is infeasible: {initial.first_violation}")
    current_distance = initial.distance
    accepted: list[AcceptedMove] = []

    while max_moves is None or len(accepted) < max_moves:
        if deadline is not None and monotonic() >= deadline:
            return SearchResult(current, initial.distance, current_distance,
                                tuple(accepted), "time_limit")
        best_move: Move | None = None
        best_delta = 0
        for move in enumerate_moves(current, operators):
            if deadline is not None and monotonic() >= deadline:
                return SearchResult(current, initial.distance, current_distance,
                                    tuple(accepted), "time_limit")
            delta = move_delta(instance, current, move)
            if delta is not None and delta < best_delta:
                best_move, best_delta = move, delta
                if strategy == "first":
                    break
        if best_move is None:
            return SearchResult(current, initial.distance, current_distance,
                                tuple(accepted), "local_optimum")

        candidate = apply_move(current, best_move)
        verdict = validate_solution(instance, candidate)
        if (not verdict.feasible or verdict.vehicles != initial.vehicles
                or verdict.distance != current_distance + best_delta):
            raise AssertionError(f"accepted move failed full validation: {best_move}")
        current = candidate
        current_distance = verdict.distance
        accepted.append(AcceptedMove(best_move, best_delta, current_distance))

    return SearchResult(current, initial.distance, current_distance,
                        tuple(accepted), "move_limit")
