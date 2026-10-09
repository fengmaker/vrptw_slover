"""Optional native fixed-fleet search; the public Python validator stays final."""

from importlib import import_module
from pathlib import Path
import hashlib


def _extension():
    try:
        module = import_module("._native", __package__)
    except ImportError as exc:
        raise RuntimeError(
            "native search backend is unavailable; build with "
            "VRPTW_BUILD_NATIVE=1 python setup.py build_ext --inplace "
            "or select --search-backend python"
        ) from exc
    if getattr(module, "API_VERSION", None) != 1:
        raise RuntimeError("native search backend has an incompatible API; rebuild it")
    return module


def native_available() -> bool:
    try:
        _extension()
    except RuntimeError:
        return False
    return True


def resolve_backend(backend: str, evaluation_mode: str = "incremental",
                    infeasible_search: bool = False) -> str:
    if backend not in ("python", "native", "auto"):
        raise ValueError("search_backend must be 'python', 'native' or 'auto'")
    compatible = evaluation_mode == "incremental" and not infeasible_search
    if backend == "native":
        if not compatible:
            raise ValueError("native search requires incremental evaluation and feasible search")
        _extension()  # Fail before construction, even for a zero search budget.
        return "native"
    if backend == "auto" and compatible and native_available():
        return "native"
    return "python"


def native_metadata() -> dict:
    module = _extension()
    path = Path(module.__file__)
    return {"api_version": module.API_VERSION,
            "build_info": getattr(module, "BUILD_INFO", "unknown"),
            "binary": path.name,
            "binary_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def check_native_input(instance, backend: str, requested: str) -> str:
    """Keep Python's unbounded integer contract when auto selects a backend."""
    if backend != "native":
        return backend
    limit = 10**12
    supported = (instance.customer_count <= 100_000 and instance.capacity <= limit
                 and all(max(c.demand, c.ready, c.due, c.service) <= limit
                         for c in instance.customers)
                 and all(max(row) <= limit for row in instance.distance))
    if supported:
        return backend
    if requested == "auto":
        return "python"
    raise ValueError("native input exceeds the safe int64 domain (100000 customers; scalars <= 10^12)")


class NativeMoveEvaluator:
    """One native snapshot, with caches rebuilt only when a move is applied."""

    def __init__(self, instance, routes, *, diagnostics=None):
        customers = instance.customers
        self.core = _extension().MoveEvaluator(
            instance.distance, [c.demand for c in customers],
            [c.ready for c in customers], [c.due for c in customers],
            [c.service for c in customers], instance.capacity, routes)
        self.diagnostics = diagnostics
        if diagnostics is not None:
            diagnostics.increment("route_cache_builds", len(routes))

    def delta(self, move):
        return self.core.delta(move.kind, move.route_a, move.index_a,
                               move.route_b, move.index_b)

    def apply(self, move):
        routes = self.core.apply(move.kind, move.route_a, move.index_a,
                                 move.route_b, move.index_b)
        if self.diagnostics is not None:
            self.diagnostics.increment("route_cache_builds",
                                       1 if move.route_a == move.route_b else 2)
        return tuple(tuple(route) for route in routes)

    def scan(self, operators, strategy, remaining_seconds=None, neighbours=None):
        result = self.core.scan(operators, strategy, remaining_seconds,
                                None if neighbours is None else [sorted(row) for row in neighbours])
        if self.diagnostics is not None:
            for name, value in result["stats"].items():
                self.diagnostics.increment(name, value)
        return result
