"""M0 frozen data and numeric rule."""

from dataclasses import FrozenInstanceError

import pytest

from vrptw import Customer, Instance


def test_frozen_data_and_single_rounded_matrix():
    source = [Customer(0, 0, 0, 0, 0, 10_000, 0),
              Customer(1, 1, 1, 2, 0, 10_000, 0)]
    instance = Instance("tiny", 2, 5, source)
    source.pop()
    assert instance.customer_count == 1
    assert instance.total_demand == 2
    assert instance.distance == ((0, 1414), (1414, 0))
    with pytest.raises(FrozenInstanceError):
        instance.capacity = 7


@pytest.mark.parametrize("customers, message", [
    ((Customer(1, 0, 0, 0, 0, 1, 0), Customer(2, 1, 0, 1, 0, 1, 0)), "contiguous"),
    ((Customer(0, 0, 0, 1, 0, 1, 0), Customer(1, 1, 0, 1, 0, 1, 0)), "depot demand"),
])
def test_instance_rejects_invalid_nodes(customers, message):
    with pytest.raises(ValueError, match=message):
        Instance("bad", 2, 5, customers)


@pytest.mark.parametrize("kwargs, message", [
    ({"demand": -1}, "negative"),
    ({"service": -1}, "negative"),
    ({"ready": 2, "due": 1}, "ready exceeds due"),
    ({"id": 1.0}, "integer"),
])
def test_customer_rejects_invalid_fields(kwargs, message):
    fields = dict(id=1, x=1, y=0, demand=1, ready=0, due=10, service=0)
    fields.update(kwargs)
    with pytest.raises(ValueError, match=message):
        Customer(**fields)
