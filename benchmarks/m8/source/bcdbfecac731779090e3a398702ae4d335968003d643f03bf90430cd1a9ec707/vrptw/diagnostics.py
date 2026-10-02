"""Opt-in, per-solve counters and exclusive/inclusive stage timings."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from time import perf_counter as monotonic
from typing import Iterable, Iterator

from .evaluate import Evaluation, RouteEvaluation, evaluate_route, validate_solution
from .problem import Instance


PHASES = ("construction", "fleet_reduction", "perturbation", "repair",
          "local_search", "ils_control", "validation", "other")


@dataclass(frozen=True, slots=True)
class PhaseStats:
    phase: str
    elapsed_seconds: float = 0.0  # exclusive of nested phases
    inclusive_seconds: float = 0.0
    calls: int = 0
    candidates: int = 0
    feasible_candidates: int = 0
    infeasible_candidates: int = 0
    route_evaluations: int = 0
    validation_calls: int = 0
    accepted: int = 0
    capacity_prefilter_skips: int = 0  # route/customer pairs, not positions
    rejected_capacity: int = 0
    rejected_time_window: int = 0
    rejected_depot_close: int = 0
    rejected_empty_route: int = 0
    trials: int = 0
    failures: int = 0
    removed_customers: int = 0


@dataclass(frozen=True, slots=True)
class Diagnostics:
    schema_version: int
    phases: tuple[PhaseStats, ...]


class DiagnosticCollector:
    """A local collector; never alters RNG state, objectives, or candidates."""

    def __init__(self) -> None:
        self._values = {name: {field: 0 for field in PhaseStats.__dataclass_fields__
                              if field != "phase"} for name in PHASES}
        self._active = "other"
        self._changed_at = monotonic()
        self._depth = 0

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        now = monotonic()
        parent = self._active
        # 'other' is calculated from the full solve runtime in snapshot().
        if self._depth:
            self._values[parent]["elapsed_seconds"] += now - self._changed_at
        self._active = name
        self._changed_at = now
        self._depth += 1
        self.increment("calls")
        try:
            yield
        finally:
            ended = monotonic()
            self._values[name]["elapsed_seconds"] += ended - self._changed_at
            self._values[name]["inclusive_seconds"] += ended - now
            self._active = parent
            self._changed_at = ended
            self._depth -= 1

    def increment(self, field: str, count: int = 1) -> None:
        self._values[self._active][field] += count

    def evaluate_route(self, instance: Instance, route: Iterable[int]) -> RouteEvaluation:
        self.increment("route_evaluations")
        return evaluate_route(instance, route)

    def validate_solution(self, instance: Instance, routes: Iterable[Iterable[int]]) -> Evaluation:
        routes = tuple(tuple(route) for route in routes)
        self.increment("validation_calls")
        self.increment("route_evaluations", len(routes))
        return validate_solution(instance, routes)

    def candidate(self, *values: RouteEvaluation, empty: bool = False) -> None:
        """Count a position/move once; each rejection reason at most once."""
        self.increment("candidates")
        feasible = not empty and all(value.feasible for value in values)
        self.increment("feasible_candidates" if feasible else "infeasible_candidates")
        codes = {issue.code for value in values for issue in value.violations}
        for code in ("capacity", "time_window", "depot_close"):
            if code in codes:
                self.increment(f"rejected_{code}")
        if empty:
            self.increment("rejected_empty_route")

    def snapshot(self, runtime_seconds: float) -> Diagnostics:
        if self._depth:
            raise RuntimeError("cannot snapshot an active diagnostics phase")
        measured = sum(value["elapsed_seconds"] for name, value in self._values.items()
                       if name != "other")
        other = max(0.0, runtime_seconds - measured)
        phases = tuple(PhaseStats(name, **values) for name, values in self._values.items())
        phases = tuple(replace(value, elapsed_seconds=other, inclusive_seconds=other)
                       if value.phase == "other" else value for value in phases)
        return Diagnostics(1, phases)


def in_phase(name: str):
    """Time an algorithm only when its optional diagnostics argument is set."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            collector = kwargs.get("diagnostics")
            if collector is None:
                return function(*args, **kwargs)
            with collector.phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate
