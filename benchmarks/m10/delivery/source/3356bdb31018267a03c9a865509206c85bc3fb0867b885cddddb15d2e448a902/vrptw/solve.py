"""Single VRPTW entry point: construction, fleet reduction, then ILS."""

from dataclasses import dataclass, replace
from math import isfinite
from time import perf_counter as monotonic

from .construct import construct
from .diagnostics import DiagnosticCollector, Diagnostics
from .evaluate import Evaluation, validate_solution
from .fleet import DEFAULT_FLEET_STRATEGY, FLEET_STRATEGIES, FleetResult, minimise_fleet
from .ils import HistoryRow, run_ils
from .local_search import DEFAULT_OPERATORS, DEFAULT_OPERATOR_SCHEDULE, OPERATORS, Routes
from .problem import Instance
from .neighbourhood import compute_neighbours


@dataclass(frozen=True, slots=True)
class Config:
    seed: int = 0
    max_iterations: int | None = 20
    time_limit_seconds: float | None = None
    fleet_attempts_per_k: int = 100
    fleet_time_fraction: float = 0.75  # share of time left after construction
    fleet_strategy: str = DEFAULT_FLEET_STRATEGY
    fleet_repair_rounds: int = 5
    fleet_related_count: int = 8
    max_moves: int | None = None  # None: local optimum or the shared deadline
    construction_order: str = "due"
    repair_order: str = "due"
    repair_strategy: str = "cheapest"
    search_strategy: str = "first"
    operators: tuple[str, ...] = DEFAULT_OPERATORS
    remove_min: int = 3
    remove_max: int = 8
    restart_after: int = 20
    diagnostics: bool = False
    evaluation_mode: str = "incremental"
    num_neighbours: int | None = None
    operator_schedule: str = DEFAULT_OPERATOR_SCHEDULE

    def __post_init__(self) -> None:
        object.__setattr__(self, "operators", tuple(self.operators))
        if type(self.diagnostics) is not bool:
            raise ValueError("diagnostics must be a boolean")
        if self.evaluation_mode not in ("full", "cached", "incremental"):
            raise ValueError("evaluation_mode must be 'full', 'cached' or 'incremental'")
        if self.operator_schedule not in ("fixed", "cyclic"):
            raise ValueError("operator_schedule must be 'fixed' or 'cyclic'")
        if self.num_neighbours is not None and (
                type(self.num_neighbours) is not int or self.num_neighbours < 1):
            raise ValueError("num_neighbours must be a positive integer or None")
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
        if self.fleet_strategy not in FLEET_STRATEGIES:
            raise ValueError(f"fleet_strategy must be one of {FLEET_STRATEGIES}")
        if (type(self.fleet_repair_rounds) is not int or self.fleet_repair_rounds < 1
                or type(self.fleet_related_count) is not int or self.fleet_related_count < 1):
            raise ValueError("fleet_repair_rounds and fleet_related_count must be positive integers")
        if (type(self.fleet_time_fraction) not in (int, float)
                or not isfinite(self.fleet_time_fraction)
                or not 0 <= self.fleet_time_fraction <= 1):
            raise ValueError("fleet_time_fraction must be finite and between 0 and 1")
        if self.max_moves is not None and (type(self.max_moves) is not int or self.max_moves < 0):
            raise ValueError("max_moves must be nonnegative or None")
        if self.construction_order not in ("due", "id", "slack"):
            raise ValueError("construction_order must be 'due', 'id' or 'slack'")
        if self.repair_order not in ("input", "due", "slack"):
            raise ValueError("repair_order must be 'input', 'due' or 'slack'")
        if self.repair_strategy not in ("cheapest", "regret2"):
            raise ValueError("repair_strategy must be 'cheapest' or 'regret2'")
        if self.search_strategy not in ("first", "best"):
            raise ValueError("search_strategy must be 'first' or 'best'")
        if not self.operators or set(self.operators) - set(OPERATORS):
            raise ValueError("operators must be a nonempty subset of supported moves")
        if (any(type(value) is not int for value in
                (self.remove_min, self.remove_max, self.restart_after))
                or not 1 <= self.remove_min <= self.remove_max or self.restart_after < 1):
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
    diagnostics: Diagnostics | None = None


def solve(instance: Instance, config: Config | None = None) -> Result:
    """Return the best independently validated feasible solution found."""
    if config is None:
        config = Config()
    started = monotonic()
    diagnostics = DiagnosticCollector() if config.diagnostics else None
    deadline = None if config.time_limit_seconds is None else started + config.time_limit_seconds
    initial_routes = construct(instance, order=config.construction_order, diagnostics=diagnostics)
    if diagnostics is None:
        initial = validate_solution(instance, initial_routes)
    else:
        with diagnostics.phase("validation"):
            initial = diagnostics.validate_solution(instance, initial_routes)
    first_feasible_seconds = monotonic() - started
    fleet_deadline = deadline
    if deadline is not None and config.fleet_time_fraction < 1:
        now = monotonic()
        fleet_deadline = min(deadline, now + max(0, deadline - now) * config.fleet_time_fraction)
    fleet = minimise_fleet(instance, initial_routes, seed=config.seed,
                           attempts_per_k=config.fleet_attempts_per_k, deadline=fleet_deadline,
                           repair_order=config.repair_order,
                           repair_strategy=config.repair_strategy,
                           strategy=config.fleet_strategy,
                           repair_rounds=config.fleet_repair_rounds,
                           related_count=config.fleet_related_count,
                           started_at=started, diagnostics=diagnostics)
    if (fleet.stop_reason == "time_limit" and fleet_deadline != deadline
            and monotonic() < deadline):
        fleet = replace(fleet, stop_reason="fleet_budget")
    neighbours = (compute_neighbours(instance, config.num_neighbours)
                  if config.num_neighbours is not None
                  and config.num_neighbours < instance.customer_count - 1 and config.max_moves != 0
                  and (deadline is None or monotonic() < deadline) else None)
    searched = run_ils(instance, fleet.routes, seed=config.seed,
                       max_iterations=config.max_iterations, deadline=deadline,
                       max_moves=config.max_moves, search_strategy=config.search_strategy,
                       operators=config.operators, remove_min=config.remove_min,
                       remove_max=config.remove_max, restart_after=config.restart_after,
                       repair_order=config.repair_order,
                       repair_strategy=config.repair_strategy,
                       started_at=started, diagnostics=diagnostics,
                       evaluation_mode=config.evaluation_mode, neighbours=neighbours,
                       operator_schedule=config.operator_schedule)
    if diagnostics is None:
        verdict = validate_solution(instance, searched.routes)
    else:
        with diagnostics.phase("validation"):
            verdict = diagnostics.validate_solution(instance, searched.routes)
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
    runtime = monotonic() - started
    return Result(tuple(tuple(route) for route in searched.routes), verdict,
                  initial.distance, first_feasible_seconds, fleet, tuple(history), searched.iterations,
                  runtime, config.seed, searched.stop_reason,
                  diagnostics.snapshot(runtime) if diagnostics is not None else None)
