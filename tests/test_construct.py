"""M2: a complete self-constructed route set or a specific failure."""

from pathlib import Path

import pytest

from vrptw import ConstructionError, Customer, Instance, construct, read_solomon, validate_solution


DATA = Path(__file__).resolve().parents[1] / "data"


def test_c101_constructs_a_complete_feasible_solution():
    instance = read_solomon(DATA / "C101.txt")
    routes = construct(instance)
    verdict = validate_solution(instance, routes)
    assert verdict.feasible, verdict.first_violation
    assert verdict.vehicles <= instance.vehicle_count
    assert sum(map(len, routes)) == instance.customer_count == 100
    assert routes == construct(instance)  # deterministic baseline for later stages


def test_failure_names_uninsertable_customer_and_fleet_usage():
    instance = Instance("too-few-vehicles", 1, 1, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 1, 0, 10_000, 0),
        Customer(2, 2, 0, 1, 0, 10_000, 0),
    ))
    with pytest.raises(ConstructionError) as error:
        construct(instance, order="id")
    assert (error.value.customer, error.value.assigned,
            error.value.vehicles, error.value.vehicle_limit) == (2, 1, 1, 1)
    assert "vehicle limit reached" in str(error.value)


def test_singleton_infeasibility_is_explicit():
    instance = Instance("over-capacity", 2, 1, (
        Customer(0, 0, 0, 0, 0, 10_000, 0),
        Customer(1, 1, 0, 2, 0, 10_000, 0),
    ))
    with pytest.raises(ConstructionError, match="customer 1: singleton route violates capacity"):
        construct(instance)


def test_invalid_order_is_rejected():
    instance = read_solomon(DATA / "C101.txt")
    with pytest.raises(ValueError, match="order must be"):
        construct(instance, order="random")
