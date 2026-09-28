"""Verified solver artifacts and readable experiment records."""

import csv
import hashlib
import json
from pathlib import Path

from .evaluate import Evaluation, validate_solution
from .problem import Instance
from .solve import Config, Result


NUMERIC_RULE_ID = "solomon_exact_1000_v1"


def code_fingerprint() -> str:
    """Hash installed solver source so a batch identifies its exact code."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_run(instance: Instance, result: Result, config: Config, out: str | Path,
              *, source_path: str | Path | None = None) -> Path:
    """Write one verified result and its trajectory to an output directory."""
    verdict = validate_solution(instance, result.routes)
    if not verdict.feasible or verdict != result.evaluation:
        raise ValueError(f"refusing to report unverified solution: {verdict.first_violation}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "instance": instance.name,
        "input_format": "solomon_txt",
        "routes": [list(route) for route in result.routes],
        "vehicles": verdict.vehicles,
        "distance_ticks": verdict.distance,
        "distance": verdict.distance / 1000,
        "feasible": True,
        "seed": result.seed,
        "threads": 1,
        "stop": {"reason": result.stop_reason,
                 "max_iterations": config.max_iterations,
                 "time_limit_seconds": config.time_limit_seconds},
        "iterations": result.iterations,
        "first_feasible_seconds": result.first_feasible_seconds,
        "runtime_seconds": result.runtime_seconds,
        "numeric_rule": NUMERIC_RULE_ID,
        "code_sha256": code_fingerprint(),
        "input_sha256": (hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
                         if source_path is not None else None),
        "fleet": {"capacity_lower_bound": result.fleet.lower_bound,
                  "reached_lower_bound": verdict.vehicles == result.fleet.lower_bound,
                  "stop_reason": result.fleet.stop_reason,
                  "attempts": [{"target": target, "trials": trials}
                               for target, trials in result.fleet.attempts]},
    }
    (out / "solution.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [f"Route #{index}: {' '.join(map(str, route))}"
             for index, route in enumerate(result.routes, start=1) if route]
    lines.append(f"Cost: {verdict.distance / 1000:.3f}")
    (out / "routes.sol").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (out / "history.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("iteration", "current_vehicles", "current_distance_ticks",
                         "candidate_vehicles", "candidate_distance_ticks", "candidate_feasible",
                         "best_vehicles", "best_distance_ticks", "accepted", "event",
                         "elapsed_seconds"))
        for row in result.history:
            writer.writerow((row.iteration, row.current_vehicles, row.current_distance,
                             row.candidate_vehicles, row.candidate_distance,
                             row.candidate_feasible, row.best_vehicles, row.best_distance,
                             row.accepted, row.event, f"{row.elapsed_seconds:.6f}"))
    _draw_routes(instance, result.routes, verdict, out / "routes.png")
    _draw_convergence(result, out / "convergence.png")
    return out


def validate_json(instance: Instance, path: str | Path) -> Evaluation:
    """Read only visit order; ignore every self-reported metric and flag."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    routes = payload["routes"]
    if not isinstance(routes, list) or any(not isinstance(route, list) for route in routes):
        raise ValueError("solution.json routes must be a list of lists")
    return validate_solution(instance, routes)


def _draw_routes(instance: Instance, routes: tuple[tuple[int, ...], ...],
                 verdict: Evaluation, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 7))
    clients = instance.customers[1:]
    ax.scatter([node.x for node in clients], [node.y for node in clients],
               s=11, c="0.72", zorder=2)
    depot = instance.customers[0]
    ax.scatter([depot.x], [depot.y], s=100, c="black", marker="s", zorder=4)
    colors = plt.get_cmap("tab20", max(1, len(routes)))
    for index, route in enumerate(routes):
        nodes = (0, *route, 0)
        ax.plot([instance.customers[i].x for i in nodes],
                [instance.customers[i].y for i in nodes],
                color=colors(index), linewidth=1.1, alpha=0.85)
    ax.set(title=f"{instance.name}: {verdict.vehicles} vehicles, {verdict.distance / 1000:.3f}",
           xlabel="X", ylabel="Y")
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _draw_convergence(result: Result, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.ticker import MaxNLocator

    steps = list(range(len(result.history)))
    vehicles = [row.best_vehicles for row in result.history]
    distance = [row.best_distance / 1000 for row in result.history]
    fig, left = plt.subplots(figsize=(8, 4))
    right = left.twinx()
    left.step(steps, vehicles, where="post", color="tab:blue", label="Best vehicles")
    right.step(steps, distance, where="post", color="tab:orange", label="Best distance")
    left.set(xlabel="Recorded step (construction, fleet, ILS)", ylabel="Vehicles")
    right.set_ylabel("Distance")
    left.yaxis.set_major_locator(MaxNLocator(integer=True))
    left.set_ylim(bottom=max(0, min(vehicles) - 1))
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
