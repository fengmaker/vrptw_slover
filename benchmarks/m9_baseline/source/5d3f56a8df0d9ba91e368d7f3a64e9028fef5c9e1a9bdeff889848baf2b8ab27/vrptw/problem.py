"""Frozen Solomon VRPTW input and its single, thousand-scaled arc matrix."""

from dataclasses import dataclass, field
from math import hypot


SCALE = 1000
NUMERIC_RULE = "round(1000 * hypot(dx, dy)); Python ties-to-even"


@dataclass(frozen=True, slots=True)
class Customer:
    """A depot or customer. Time fields are already in thousandths."""

    id: int
    x: int
    y: int
    demand: int
    ready: int
    due: int
    service: int

    def __post_init__(self) -> None:
        for name in ("id", "x", "y", "demand", "ready", "due", "service"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"customer {self.id}: {name} must be an integer")
        if self.id < 0 or self.demand < 0 or self.ready < 0 or self.due < 0 or self.service < 0:
            raise ValueError(f"customer {self.id}: negative ID, demand, or time")
        if self.ready > self.due:
            raise ValueError(f"customer {self.id}: ready exceeds due")


@dataclass(frozen=True, slots=True)
class Instance:
    """One depot (ID 0), contiguous customers, and a homogeneous fleet."""

    name: str
    vehicle_count: int
    capacity: int
    customers: tuple[Customer, ...]
    distance: tuple[tuple[int, ...], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        customers = tuple(self.customers)
        object.__setattr__(self, "customers", customers)
        if not self.name.strip():
            raise ValueError("instance name must not be empty")
        if type(self.vehicle_count) is not int or self.vehicle_count < 1:
            raise ValueError("vehicle_count must be a positive integer")
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if len(customers) < 2 or any(not isinstance(c, Customer) for c in customers):
            raise ValueError("customers must contain a depot and at least one customer")
        if tuple(c.id for c in customers) != tuple(range(len(customers))):
            raise ValueError("customer IDs must be contiguous from depot 0")
        if customers[0].demand != 0 or customers[0].service != 0:
            raise ValueError("depot demand and service must be zero")

        matrix = tuple(
            tuple(round(SCALE * hypot(a.x - b.x, a.y - b.y)) for b in customers)
            for a in customers
        )
        object.__setattr__(self, "distance", matrix)

    @property
    def customer_count(self) -> int:
        return len(self.customers) - 1

    @property
    def total_demand(self) -> int:
        return sum(customer.demand for customer in self.customers[1:])
