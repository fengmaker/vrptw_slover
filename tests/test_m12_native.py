"""M12 native move evaluation and backend integration contracts."""

import importlib
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from vrptw import (Config, Customer, Instance, Move, apply_move, construct,
                   improve, move_delta, read_solomon, validate_solution)
from vrptw.local_search import OPERATORS, enumerate_moves
from vrptw.native_search import NativeMoveEvaluator, native_available, resolve_backend
from vrptw.neighbourhood import allows_move, compute_neighbours


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
solve_module = importlib.import_module("vrptw.solve")
native_module = importlib.import_module("vrptw.native_search")


def _operator_search_case():
    points = (
        (0, 0), (9, 43), (39, 22), (10, 35), (45, 42), (38, 11),
        (24, 18), (39, 27), (36, 37), (2, 46), (33, 34),
    )
    customers = tuple(
        Customer(i, x, y, 0 if i == 0 else 1, 0, 10**9, 0)
        for i, (x, y) in enumerate(points)
    )
    instance = Instance("m12-native-search", 3, 99, customers)
    routes = ((5, 1, 8, 2), (7, 4, 6, 3), (9, 10))
    assert validate_solution(instance, routes).feasible
    return instance, routes


def _boundary_case():
    """Two feasible routes that hit waiting, capacity, due, and depot limits."""
    instance = Instance("m12-native-boundaries", 2, 2, (
        Customer(0, 0, 0, 0, 0, 13_000, 0),
        Customer(1, 1, 0, 1, 10_000, 10_000, 0),
        Customer(2, 2, 0, 1, 11_000, 12_000, 0),
        Customer(3, 1, 1, 1, 0, 13_000, 0),
        Customer(4, 2, 1, 1, 0, 13_000, 0),
    ))
    routes = ((1, 2), (3, 4))
    verdict = validate_solution(instance, routes)
    assert verdict.feasible
    assert max(route.load for route in verdict.routes) == instance.capacity
    assert max(route.return_time for route in verdict.routes) == instance.customers[0].due
    return instance, routes


def _large_tick_case():
    base = 10**12
    instance = Instance("m12-native-large-ticks", 1, 1, (
        Customer(0, 0, 0, 0, 0, base + 5_000, 0),
        Customer(1, 1, 0, 1, base, base + 2_000, 0),
    ))
    return instance, ((1,),)


def _empty_slot_case():
    instance = Instance("m12-native-empty-slot", 3, 3, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        *(Customer(i, i, 0, 1, 0, 100_000, 0) for i in range(1, 4)),
    ))
    return instance, ((1,), (2, 3), ())


def _move_tuple(move):
    return (move.kind, move.route_a, move.index_a, move.route_b, move.index_b)


def _core_arguments(instance, routes):
    return (
        [list(row) for row in instance.distance],
        [customer.demand for customer in instance.customers],
        [customer.ready for customer in instance.customers],
        [customer.due for customer in instance.customers],
        [customer.service for customer in instance.customers],
        instance.capacity,
        [list(route) for route in routes],
    )


def _reference_scan(instance, routes, operators, strategy, neighbours=None):
    best_move = None
    best_delta = 0
    for move in enumerate_moves(routes, operators):
        if neighbours is not None and not allows_move(routes, move, neighbours):
            continue
        delta = move_delta(instance, routes, move)
        if delta is not None and delta < best_delta:
            best_move, best_delta = move, delta
            if strategy == "first":
                break
    return best_move, best_delta


def _reservoir_sample(iterable, count, rng):
    sample = []
    seen = 0
    for item in iterable:
        seen += 1
        if len(sample) < count:
            sample.append(item)
        else:
            slot = rng.randrange(seen)
            if slot < count:
                sample[slot] = item
    return sample, seen


def test_native_availability_and_backend_resolution_contracts(monkeypatch):
    assert type(native_available()) is bool
    assert Config().search_backend == "auto"
    assert resolve_backend("python", "full") == "python"
    assert resolve_backend("auto", "full") == "python"
    assert resolve_backend("auto", "incremental", infeasible_search=True) == "python"
    with pytest.raises(ValueError):
        resolve_backend("native", "full")
    with pytest.raises(ValueError):
        resolve_backend("native", "incremental", infeasible_search=True)

    if native_available():
        assert resolve_backend("native", "incremental") == "native"
        assert resolve_backend("auto", "incremental") == "native"
    else:
        assert resolve_backend("auto", "incremental") == "python"
        with pytest.raises(RuntimeError, match="native"):
            resolve_backend("native", "incremental")

    for backend in ("unknown", "", None):
        with pytest.raises(ValueError):
            Config(search_backend=backend)
    with pytest.raises(ValueError):
        Config(search_backend="native", evaluation_mode="cached")
    with pytest.raises(ValueError):
        Config(search_backend="native", infeasible_search=True)


def test_explicit_missing_native_backend_fails_before_construction(monkeypatch):
    instance, _ = _boundary_case()

    def unavailable():
        raise RuntimeError("native search backend is unavailable")

    monkeypatch.setattr(native_module, "_extension", unavailable)

    def construction_must_not_run(*args, **kwargs):
        pytest.fail("backend availability must be resolved before construction")

    monkeypatch.setattr(solve_module, "construct", construction_must_not_run)
    with pytest.raises(RuntimeError, match="native"):
        solve_module.solve(instance, Config(search_backend="native"))


def test_auto_backend_falls_back_when_native_loader_is_unavailable(monkeypatch):
    instance, routes = _operator_search_case()

    def unavailable():
        raise RuntimeError("native search backend is unavailable")

    monkeypatch.setattr(native_module, "_extension", unavailable)
    assert native_module.native_available() is False
    assert resolve_backend("auto", "incremental") == "python"
    automatic = improve(instance, routes, max_moves=4, search_backend="auto")
    python = improve(instance, routes, max_moves=4, search_backend="python")
    assert automatic == python


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_core_rejects_malformed_dimensions_and_route_customer_sets():
    instance, routes = _empty_slot_case()
    original = _core_arguments(instance, routes)
    core_type = native_module._extension().MoveEvaluator

    malformed = []
    wrong_matrix_rows = list(original)
    wrong_matrix_rows[0] = wrong_matrix_rows[0][:-1]
    malformed.append(wrong_matrix_rows)
    wrong_matrix_columns = list(original)
    wrong_matrix_columns[0] = [row[:-1] if index == 0 else row
                                for index, row in enumerate(original[0])]
    malformed.append(wrong_matrix_columns)
    for vector_index in (1, 2, 3, 4):
        wrong_vector_length = list(original)
        wrong_vector_length[vector_index] = wrong_vector_length[vector_index][:-1]
        malformed.append(wrong_vector_length)

    for arguments in malformed:
        with pytest.raises(ValueError):
            core_type(*arguments)

    for bad_routes in (
        ((1, 2), (2, 3), ()),  # duplicate customer
        ((1, 2), (), ()),      # missing customer
        ((1, 2), (3, 4), ()),  # unknown customer
        ((0, 2), (3,), ()),    # depot in a route
    ):
        arguments = list(original)
        arguments[6] = [list(route) for route in bad_routes]
        with pytest.raises(ValueError):
            core_type(*arguments)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_core_rejects_oversized_fields_and_int64_conversion_overflow():
    instance, routes = _empty_slot_case()
    original = _core_arguments(instance, routes)
    core_type = native_module._extension().MoveEvaluator
    limit = 10**12

    oversized = []
    for vector_index in (1, 2, 3, 4):
        arguments = list(original)
        values = list(arguments[vector_index])
        values[1] = limit + 1
        if vector_index == 2:
            values[1] = limit + 1
            arguments[3] = list(arguments[3])
            arguments[3][1] = limit + 1
        arguments[vector_index] = values
        oversized.append(arguments)
    too_far = list(original)
    too_far[0] = [list(row) for row in original[0]]
    too_far[0][0][1] = limit + 1
    oversized.append(too_far)
    too_large_capacity = list(original)
    too_large_capacity[5] = limit + 1
    oversized.append(too_large_capacity)

    for arguments in oversized:
        with pytest.raises(ValueError):
            core_type(*arguments)

    too_large_for_int64 = list(original)
    too_large_for_int64[1] = list(original[1])
    too_large_for_int64[1][1] = 1 << 63
    with pytest.raises((OverflowError, TypeError)):
        core_type(*too_large_for_int64)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_core_rejects_negative_and_int_max_positions_without_mutating_state():
    instance, routes = _empty_slot_case()
    core = native_module._extension().MoveEvaluator(*_core_arguments(instance, routes))
    safe_move = ("swap", 1, 0, 1, 1)
    expected = core.delta(*safe_move)
    int_max = (1 << 31) - 1
    invalid_moves = (
        ("relocate", -1, 0, 1, 0),
        ("relocate", 0, -1, 1, 0),
        ("relocate", 0, 0, -1, 0),
        ("relocate", 0, 0, 1, -1),
        ("relocate_pair", 0, int_max, 1, 0),
        ("relocate_pair", 0, 0, 1, int_max),
        ("exchange_pairs", 0, int_max, 1, 0),
        ("exchange_pairs", 0, 0, 1, int_max),
    )

    for move in invalid_moves:
        with pytest.raises(ValueError):
            core.delta(*move)
        with pytest.raises(ValueError):
            core.apply(*move)
        assert core.delta(*safe_move) == expected


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_random_real_moves_match_full_python_delta_for_all_seven_operators():
    checked = 0
    for name in ("C103", "R101", "RC101"):
        instance = read_solomon(DATA / f"{name}.txt")
        routes = construct(instance)
        baseline = validate_solution(instance, routes)
        assert baseline.feasible and all(routes)
        evaluator = NativeMoveEvaluator(instance, routes)
        rng = Random(20261010 + sum(map(ord, name)))

        for kind in OPERATORS:
            sample, available = _reservoir_sample(
                enumerate_moves(routes, operators=(kind,)), 110, rng
            )
            assert available >= len(sample) >= 110, f"{name}/{kind} has too few real moves"
            for move in sample:
                expected = move_delta(instance, routes, move)
                assert evaluator.delta(move) == expected, (name, move)
                if expected is not None:
                    candidate = apply_move(routes, move)
                    full = validate_solution(instance, candidate)
                    assert full.feasible
                    assert full.vehicles == baseline.vehicles
                    assert full.distance - baseline.distance == expected
                checked += 1

    assert checked >= 2_310


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_boundary_deltas_match_full_recomputation_with_waiting_and_exact_limits():
    instance, routes = _boundary_case()
    evaluator = NativeMoveEvaluator(instance, routes)

    # Reversing the waiting route violates its exact first-stop due time;
    # cross-route moves also probe the exact depot-close and capacity limits.
    candidates = (
        Move("two_opt", 0, 0, 0, 2),
        Move("swap", 0, 0, 1, 0),
        Move("two_opt_star", 0, 1, 1, 1),
    )
    assert any(move_delta(instance, routes, move) is None for move in candidates)
    for move in candidates:
        assert evaluator.delta(move) == move_delta(instance, routes, move)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_apply_refreshes_cache_after_a_sequence_of_accepted_moves():
    instance, initial = _operator_search_case()
    routes = initial
    evaluator = NativeMoveEvaluator(instance, routes)
    current_distance = validate_solution(instance, routes).distance
    accepted = 0

    for _ in range(8):
        chosen = None
        for move in enumerate_moves(routes):
            expected = move_delta(instance, routes, move)
            assert evaluator.delta(move) == expected
            if expected is not None and expected < 0:
                chosen = move
                break
        if chosen is None:
            break

        expected_delta = move_delta(instance, routes, chosen)
        expected_routes = apply_move(routes, chosen)
        routes = evaluator.apply(chosen)
        assert routes == expected_routes
        verdict = validate_solution(instance, routes)
        assert verdict.feasible
        assert verdict.distance < current_distance
        assert verdict.distance - current_distance == expected_delta
        current_distance = verdict.distance
        accepted += 1

    assert accepted > 0
    for move in enumerate_moves(routes):
        assert evaluator.delta(move) == move_delta(instance, routes, move)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
@pytest.mark.parametrize("strategy", ["first", "best"])
@pytest.mark.parametrize("schedule", ["fixed", "cyclic"])
@pytest.mark.parametrize("operators", [OPERATORS, tuple(reversed(OPERATORS))])
def test_native_fixed_work_improvement_trace_matches_python(strategy, schedule, operators):
    instance, routes = _operator_search_case()
    python = improve(instance, routes, strategy=strategy, operator_schedule=schedule,
                     evaluation_mode="incremental", search_backend="python", operators=operators)
    native = improve(instance, routes, strategy=strategy, operator_schedule=schedule,
                     evaluation_mode="incremental", search_backend="native", operators=operators)

    assert native.moves == python.moves
    assert native.routes == python.routes
    assert native.distance == python.distance
    assert native.stop_reason == python.stop_reason == "local_optimum"
    assert validate_solution(instance, native.routes).feasible


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
@pytest.mark.parametrize("strategy", ["first", "best"])
def test_native_scan_matches_python_for_every_operator_and_neighbour_filter(strategy):
    instance, routes = _operator_search_case()
    neighbours = compute_neighbours(instance, 2)
    evaluator = NativeMoveEvaluator(instance, routes)

    for kind in OPERATORS:
        expected_move, expected_delta = _reference_scan(
            instance, routes, (kind,), strategy, neighbours
        )
        result = evaluator.scan((kind,), strategy, neighbours=neighbours)
        assert isinstance(result, dict)
        assert result["move"] == (None if expected_move is None else _move_tuple(expected_move))
        assert type(result["delta"]) is int
        assert result["delta"] == expected_delta
        assert result["timed_out"] is False
        assert isinstance(result["stats"], dict)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_scan_timeout_empty_route_slots_and_rejected_apply_are_safe():
    instance, routes = _operator_search_case()
    # Solvers can retain unused vehicle slots. A scan must preserve the same
    # fixed-fleet candidates as Python and avoid indexing an empty route.
    with_empty_slot = (*routes, ())
    evaluator = NativeMoveEvaluator(instance, with_empty_slot)
    expected, delta = _reference_scan(
        instance, with_empty_slot, OPERATORS, "best"
    )

    timed = evaluator.scan(OPERATORS, "best", remaining_seconds=0)
    assert timed["timed_out"] is True
    assert timed["move"] is None
    assert evaluator.scan(OPERATORS, "best")["move"] == (
        None if expected is None else _move_tuple(expected)
    )
    assert evaluator.scan(OPERATORS, "best")["delta"] == delta

    # A structurally valid 2-opt* that empties one route is not a fixed-fleet
    # feasible move. Rejection must leave the evaluator usable at its old state.
    emptying = Move("two_opt_star", 0, len(routes[0]), 1, 0)
    assert move_delta(instance, with_empty_slot, emptying) is None
    with pytest.raises(ValueError):
        evaluator.apply(emptying)
    safe_move = next(enumerate_moves(routes, operators=("two_opt",)))
    assert evaluator.delta(safe_move) == move_delta(instance, with_empty_slot, safe_move)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_native_rejects_moves_that_empty_or_activate_a_route_slot():
    instance, routes = _empty_slot_case()
    evaluator = NativeMoveEvaluator(instance, routes)
    moves = (
        Move("relocate", 0, 0, 1, 0),  # empties the singleton source route
        Move("relocate", 1, 0, 2, 0),  # activates the unused destination slot
    )
    safe = Move("swap", 1, 0, 1, 1)

    for move in moves:
        assert move_delta(instance, routes, move) is None
        assert evaluator.delta(move) is None
        with pytest.raises(ValueError):
            evaluator.apply(move)
        assert evaluator.delta(safe) == move_delta(instance, routes, safe)


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_auto_backend_uses_python_for_full_evaluation():
    instance, routes = _operator_search_case()
    full = improve(instance, routes, max_moves=4, evaluation_mode="full",
                   search_backend="auto")
    python = improve(instance, routes, max_moves=4, evaluation_mode="full",
                     search_backend="python")
    assert full == python


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_auto_backend_uses_python_for_infeasible_search():
    instance, _ = _operator_search_case()
    common = dict(seed=19, max_iterations=2, fleet_attempts_per_k=0,
                  max_moves=0, infeasible_search=True)
    automatic = solve_module.solve(
        instance, Config(**common, search_backend="auto")
    )
    python = solve_module.solve(
        instance, Config(**common, search_backend="python")
    )
    assert automatic.routes == python.routes
    assert automatic.evaluation == python.evaluation
    assert tuple(replace(row, elapsed_seconds=0) for row in automatic.history) == tuple(
        replace(row, elapsed_seconds=0) for row in python.history
    )


@pytest.mark.skipif(not native_available(), reason="native extension is unavailable")
def test_auto_backend_falls_back_when_instance_exceeds_native_integer_domain():
    instance, routes = _large_tick_case()
    automatic = improve(instance, routes, max_moves=0, search_backend="auto")
    python = improve(instance, routes, max_moves=0, search_backend="python")
    assert automatic == python
    with pytest.raises(ValueError, match="int64"):
        improve(instance, routes, max_moves=0, search_backend="native")

