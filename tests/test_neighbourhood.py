"""M8: deterministic granular neighbours and structural move filtering."""

import pytest

from vrptw import Customer, Instance, Move
from vrptw.neighbourhood import allows_move, compute_neighbours


def make_instance(
    points: tuple[tuple[int, int], ...],
    windows: tuple[tuple[int, int], ...] | None = None,
) -> Instance:
    if windows is None:
        windows = tuple((0, 100_000) for _ in points)
    return Instance("neighbourhood", 2, 100, tuple(
        Customer(index, x, y, 0 if index == 0 else 1, ready, due, 0)
        for index, ((x, y), (ready, due)) in enumerate(zip(points, windows))
    ))


def test_neighbours_are_deterministic_and_break_equal_scores_by_customer_id():
    instance = make_instance(((0, 0),) * 5)

    first = compute_neighbours(instance, 1)
    second = compute_neighbours(instance, 1)

    assert first == second
    assert first == (
        frozenset(),
        frozenset({2, 3, 4}),
        frozenset({1}),
        frozenset({1}),
        frozenset({1}),
    )


def test_wait_time_changes_the_rank_of_equal_distance_candidates():
    points = ((0, 0), (0, 0), (1, 0), (1, 0), (1, 0))
    windows = ((0, 0), (0, 0), (10_000, 10_000), (2_000, 2_000),
               (10_000, 10_000))
    timed = make_instance(points, windows)
    no_wait_penalty = make_instance(points, ((0, 0),) * len(points))

    assert compute_neighbours(timed, 1)[1] == frozenset({3})
    assert compute_neighbours(no_wait_penalty, 1)[1] == frozenset({2})


def test_timewarp_changes_the_rank_of_equal_distance_candidates():
    points = ((0, 0), (0, 0), (1, 0), (1, 0), (1, 0))
    windows = ((0, 0), (0, 0), (0, 0), (0, 2_000), (0, 0))
    with_timewarp = make_instance(points, windows)
    without_timewarp = make_instance(points, ((0, 100_000),) * len(points))

    assert compute_neighbours(with_timewarp, 1)[1] == frozenset({3})
    assert compute_neighbours(without_timewarp, 1)[1] == frozenset({2})


def test_top_k_is_symmetrized_and_none_or_large_k_is_complete():
    instance = make_instance(((0, 0), (1, 0), (2, 0), (3, 0), (4, 0)))
    top_one = compute_neighbours(instance, 1)

    assert top_one[0] == frozenset()
    assert any(len(top_one[node]) > 1 for node in range(1, 5))
    for node in range(1, 5):
        assert all(node in top_one[other] for other in top_one[node])

    complete = (frozenset(),) + tuple(
        frozenset(other for other in range(1, 5) if other != node)
        for node in range(1, 5)
    )
    assert compute_neighbours(instance, None) == complete
    assert compute_neighbours(instance, 4) == complete
    assert compute_neighbours(instance, 100) == complete


@pytest.mark.parametrize("value", [0, -1, 1.0, True, False])
def test_num_neighbours_must_be_a_strictly_positive_integer_or_none(value):
    instance = make_instance(((0, 0), (1, 0)))
    with pytest.raises(ValueError, match="positive integer or None"):
        compute_neighbours(instance, value)


@pytest.mark.parametrize(("move", "expected_edge"), [
    (Move("relocate", 0, 1, 1, 1), (2, 3)),
    (Move("swap", 0, 1, 1, 1), (1, 4)),
    (Move("two_opt", 0, 1, 0, 3), (1, 5)),
    (Move("two_opt_star", 0, 1, 1, 1), (1, 4)),
])
def test_each_operator_requires_a_new_neighbour_connection(move, expected_edge):
    routes = ((1, 2, 5, 7), (3, 4, 6, 8))
    empty = tuple(frozenset() for _ in range(9))
    assert not allows_move(routes, move, empty)

    left, right = expected_edge
    related = [set(row) for row in empty]
    related[left].add(right)
    related[right].add(left)
    assert allows_move(routes, move, tuple(frozenset(row) for row in related))


@pytest.mark.parametrize(("routes", "move"), [
    (((1, 2, 5), (3, 4, 6)), Move("relocate", 0, 1, 1, 0)),
    (((1, 2, 5), (3, 4, 6)), Move("swap", 0, 0, 1, 1)),
    (((1, 2, 5, 7),), Move("two_opt", 0, 0, 0, 2)),
    (((1, 2, 5), (3, 4, 6)), Move("two_opt_star", 0, 0, 1, 1)),
])
def test_new_depot_connections_allow_moves(routes, move):
    empty = tuple(frozenset() for _ in range(9))
    assert allows_move(routes, move, empty)
