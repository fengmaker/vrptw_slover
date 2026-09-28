"""M1 hand-calculated boundaries and independent C101 solution check."""

from pathlib import Path

import pytest

from vrptw import Customer, Instance, evaluate_route, read_solomon, validate_solution


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tiny() -> Instance:
    return Instance("hand", 2, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 2, 3_000, 3_000, 1_000),
        Customer(2, 2, 0, 3, 0, 10_000, 0),
        Customer(3, 3, 0, 1, 0, 10_000, 0),
    ))


def test_wait_deadline_and_schedule(tiny):
    route = evaluate_route(tiny, (1, 2))
    assert route.feasible
    assert [(v.arrival, v.waiting, v.start, v.departure, v.load) for v in route.visits] == [
        (1000, 2000, 3000, 4000, 2),
        (5000, 0, 5000, 5000, 5),
    ]
    assert (route.return_time, route.distance) == (7000, 4000)


def test_one_tick_late_capacity_and_depot_return(tiny):
    late = Instance("late", 1, 5, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 999, 0),
    ))
    assert evaluate_route(late, (1,)).violations[0].code == "time_window"
    assert evaluate_route(tiny, (1, 2, 3)).violations[0].code == "capacity"
    return_late = Instance("return-late", 1, 5, (
        Customer(0, 0, 0, 0, 0, 1_999, 0),
        Customer(1, 1, 0, 1, 0, 10_000, 0),
    ))
    assert evaluate_route(return_late, (1,)).violations[0].code == "depot_close"


@pytest.mark.parametrize("routes, code", [
    (((1, 2),), "missing_customer"),
    (((1, 2), (2, 3)), "duplicate_customer"),
    (((1,), (2,), (3,)), "vehicle_limit"),
    (((0, 1, 2), (3,)), "unknown_customer"),
    (((1, 2, 4), (3,)), "unknown_customer"),
])
def test_full_validator_rejects_bad_coverage_and_limits(tiny, routes, code):
    report = validate_solution(tiny, routes)
    assert not report.feasible
    assert code in [violation.code for violation in report.violations]
    assert report.objective is None


def test_empty_routes_do_not_use_vehicles(tiny):
    report = validate_solution(tiny, ((), (1, 2), (3,)))
    assert report.feasible
    assert (report.vehicles, report.distance, report.objective) == (2, 10_000, (2, 10_000))


def test_c101_known_routes_are_independently_validated():
    instance = read_solomon(ROOT / "data" / "C101.txt")
    lines = (ROOT / "tests" / "fixtures" / "C101.sol").read_text().splitlines()
    routes = tuple(tuple(map(int, line.split(":", 1)[1].split()))
                   for line in lines if line.startswith("Route #"))
    # Ignore the reference file's self-reported Cost line.
    report = validate_solution(instance, routes)
    assert report.feasible, report.first_violation
    assert report.vehicles == 10
    assert report.distance == 828_937
    assert sum(len(route) for route in routes) == 100
