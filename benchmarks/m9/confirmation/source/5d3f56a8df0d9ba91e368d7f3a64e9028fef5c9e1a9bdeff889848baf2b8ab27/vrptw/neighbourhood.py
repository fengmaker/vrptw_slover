"""Granular candidate neighbours and move filtering for Solomon VRPTW."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .problem import Instance


Neighbours = tuple[frozenset[int], ...]


def compute_neighbours(instance: Instance, num_neighbours: int | None) -> Neighbours:
    """Compute deterministic, symmetric customer candidate sets.

    Directional proximity is ``distance + 0.2 * wait + timewarp``. The
    equivalent integer score ``5 * distance + wait + 5 * timewarp`` avoids
    floating-point ordering. Symmetric proximity uses the smaller directional
    score, and the resulting top-k edges are symmetrised by union.

    ``None`` or a value at least ``customer_count - 1`` returns the complete
    customer graph. Depot index zero always has an empty neighbourhood.
    """
    if num_neighbours is not None and (
        type(num_neighbours) is not int or num_neighbours <= 0
    ):
        raise ValueError("num_neighbours must be a positive integer or None")

    count = instance.customer_count
    if num_neighbours is None or num_neighbours >= count - 1:
        return (frozenset(),) + tuple(
            frozenset(other for other in range(1, count + 1) if other != client)
            for client in range(1, count + 1)
        )

    customers = instance.customers
    distances = instance.distance

    def directed_score(first: int, second: int) -> int:
        left = customers[first]
        right = customers[second]
        distance = distances[first][second]
        wait = max(right.ready - distance - left.service - left.due, 0)
        timewarp = max(left.ready + left.service + distance - right.due, 0)
        return 5 * distance + wait + 5 * timewarp

    adjacency = [set() for _ in range(count + 1)]
    for client in range(1, count + 1):
        ranked = sorted(
            (
                (min(directed_score(client, other), directed_score(other, client)), other)
                for other in range(1, count + 1)
                if other != client
            ),
            key=lambda pair: (pair[0], pair[1]),
        )
        for _, other in ranked[:num_neighbours]:
            adjacency[client].add(other)
            adjacency[other].add(client)

    return tuple(frozenset(row) for row in adjacency)


def allows_move(
    routes: tuple[tuple[int, ...], ...] | Iterable[Iterable[int]],
    move: object,
    neighbours: Neighbours,
) -> bool:
    """Return whether a structural move creates a promising connection.

    The function deliberately reads only ``kind``, ``route_a``, ``index_a``,
    ``route_b`` and ``index_b`` from ``move`` so it can be used without
    importing a search operator type. A move survives when at least one of its
    new customer-customer connections is in ``neighbours``, or when it creates
    a customer-depot connection. Existing connections are not enough by
    themselves to admit a move.
    """
    # Search passes a tuple of tuple routes. Keep that hot path allocation-free;
    # still accept a one-shot iterable for standalone callers.
    current = routes if isinstance(routes, Sequence) else tuple(tuple(r) for r in routes)
    kind = move.kind
    a, ia = move.route_a, move.index_a
    b, ib = move.route_b, move.index_b

    def allowed_edge(left: int, right: int) -> bool:
        if left == 0 and right == 0:
            return False
        if left == 0 or right == 0:
            return True
        return right in neighbours[left]

    if kind == "relocate":
        source = current[a]
        node = source[ia]
        if a == b:
            destination = source[:ia] + source[ia + 1 :]
        else:
            destination = current[b]
        left = destination[ib - 1] if ib > 0 else 0
        right = destination[ib] if ib < len(destination) else 0
        return allowed_edge(left, node) or allowed_edge(node, right)

    if kind == "swap":
        first, second = current[a][ia], current[b][ib]
        def incident_edges(
            route: Sequence[int], position: int, replacements: dict[int, int] | None = None
        ) -> set[tuple[int, int]]:
            replacements = replacements or {}
            node = replacements.get(position, route[position])
            left = replacements.get(position - 1, route[position - 1]) if position > 0 else 0
            right = (replacements.get(position + 1, route[position + 1])
                     if position + 1 < len(route) else 0)
            return {(min(left, node), max(left, node)),
                    (min(node, right), max(node, right))}

        old_edges = (incident_edges(current[a], ia)
                     | incident_edges(current[b], ib))
        if a == b:
            replacements = {ia: second, ib: first}
            new_edges = (incident_edges(current[a], ib, replacements)
                         | incident_edges(current[a], ia, replacements))
        else:
            new_edges = (incident_edges(current[b], ib, {ib: first})
                         | incident_edges(current[a], ia, {ia: second}))
        return any(allowed_edge(*edge) for edge in new_edges - old_edges)

    if kind == "two_opt":
        route = current[a]
        left = route[ia - 1] if ia > 0 else 0
        first = route[ia]
        last = route[ib - 1]
        right = route[ib] if ib < len(route) else 0
        return allowed_edge(left, last) or allowed_edge(first, right)

    if kind == "two_opt_star":
        route_a, route_b = current[a], current[b]
        left_a = route_a[ia - 1] if ia > 0 else 0
        first_b = route_b[ib] if ib < len(route_b) else 0
        left_b = route_b[ib - 1] if ib > 0 else 0
        first_a = route_a[ia] if ia < len(route_a) else 0
        return allowed_edge(left_a, first_b) or allowed_edge(left_b, first_a)

    raise ValueError(f"unknown move kind: {kind}")
