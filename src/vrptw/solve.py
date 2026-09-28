"""Single VRPTW entry point: construction, fleet reduction, then ILS."""

from dataclasses import dataclass
from math import isfinite
from time import monotonic

from .construct import construct
from .evaluate import Evaluation, validate_solution
from .fleet import FleetResult, minimise_fleet
from .ils import HistoryRow, run_ils
from .local_search import OPERATORS, Routes
from .problem import Instance


@dataclass(frozen=True, slots=True)
class Config:
    seed: int = 0
    max_iterations: int | None = 20
    time_limit_seconds: float | None = None
    fleet_attempts_per_k: int = 100
    max_moves: int = 2  # per ILS candidate
    construction_order: str = "due"
    search_strategy: str = "first"
    operators: tuple[str, ...] = OPERATORS
    remove_min: int = 3
    remove_max: int = 8
    restart_after: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "operators", tuple(self.operators))
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if self.max_iterations is None and self.time_limit_seconds is None:
            raise ValueError("provide max_iterations or time_limit_seconds")
        if self.max_iterations is not None and (
            type(self.max_iterations) is not int or self.max_iterations < 0
        ):
            raise ValueError("max_iterations must be a nonnegative integer")
        if self.time_limit_seconds is not None and (
            type(self.time_limit_seconds) not in (int, float)
            or not isfinite(self.time_limit_seconds) or self.time_limit_seconds < 0
        ):
            raise ValueError("time_limit_seconds must be finite and nonnegative")
        if type(self.fleet_attempts_per_k) is not int or self.fleet_attempts_per_k < 0:
            raise ValueError("fleet_attempts_per_k must be nonnegative")
        if type(self.max_moves) is not int or self.max_moves < 0:
            raise ValueError("max_moves must be nonnegative")
        if self.construction_order not in ("due", "id"):
            raise ValueError("construction_order must be 'due' or 'id'")
        if self.search_strategy not in ("first", "best"):
            raise ValueError("search_strategy must be 'first' or 'best'")
        if not self.operators or set(self.operators) - set(OPERATORS):
            raise ValueError("operators must be a nonempty subset of supported moves")
        if not 1 <= self.remove_min <= self.remove_max or self.restart_after < 1:
            raise ValueError("invalid perturbation or restart settings")


@dataclass(frozen=True, slots=True)
class Result:
    routes: Routes
    evaluation: Evaluation
    initial_distance: int
    first_feasible_seconds: float
    fleet: FleetResult
    history: tuple[HistoryRow, ...]
    iterations: int
    runtime_seconds: float
    seed: int
    stop_reason: str


def solve(instance: Instance, config: Config | None = None) -> Result:
    """Return the best independently validated feasible solution found."""
    if config is None:
        config = Config()
    started = monotonic()
    deadline = None if config.time_limit_seconds is None else started + config.time_limit_seconds
    initial_routes = construct(instance, order=config.construction_order)
    initial = validate_solution(instance, initial_routes)
    first_feasible_seconds = monotonic() - started
    fleet = minimise_fleet(instance, initial_routes, seed=config.seed,
                           attempts_per_k=config.fleet_attempts_per_k, deadline=deadline,
                           started_at=started)
    searched = run_ils(instance, fleet.routes, seed=config.seed,
                       max_iterations=config.max_iterations, deadline=deadline,
                       max_moves=config.max_moves, search_strategy=config.search_strategy,
                       operators=config.operators, remove_min=config.remove_min,
                       remove_max=config.remove_max, restart_after=config.restart_after,
                       started_at=started)
    verdict = validate_solution(instance, searched.routes)
    if not verdict.feasible:
        raise AssertionError(f"solver returned invalid routes: {verdict.first_violation}")
    history = [HistoryRow(0, initial.vehicles, initial.distance, initial.vehicles,
                          initial.distance, True, initial.vehicles, initial.distance,
                          True, "construction", first_feasible_seconds)]
    history.extend(HistoryRow(0, step.vehicles, step.distance, step.vehicles,
                              step.distance, True, step.vehicles, step.distance,
                              True, "fleet", step.elapsed_seconds)
                   for step in fleet.milestones)
    history.extend(searched.history[1:])
    return Result(tuple(tuple(route) for route in searched.routes), verdict,
                  initial.distance, first_feasible_seconds, fleet, tuple(history), searched.iterations,
                  monotonic() - started, config.seed, searched.stop_reason)
