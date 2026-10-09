"""M9: explicit move budgets and independently selectable CLI operators."""

import json
from dataclasses import replace

import pytest

from vrptw import Config, Customer, Instance, solve, validate_solution
from vrptw.cli import main
from vrptw.local_search import SEGMENT_OPERATORS
from vrptw import Move, improve, move_delta


@pytest.mark.parametrize("limit", [None, 0, 2])
def test_move_budget_and_single_operator_configs_are_valid(limit):
    for operator in SEGMENT_OPERATORS:
        config = Config(max_moves=limit, operators=(operator,))
        assert config.max_moves == limit and config.operators == (operator,)


@pytest.mark.parametrize("limit", [-1, True, 1.5, "none"])
def test_invalid_move_budgets_are_rejected(limit):
    with pytest.raises(ValueError, match="max_moves"):
        Config(max_moves=limit)


def test_uncapped_fixed_work_and_neighbours_preserve_diagnostic_equivalence():
    instance = Instance("small", 2, 4, tuple(
        Customer(i, x, y, int(i != 0), 0, 100_000, 0)
        for i, (x, y) in enumerate(((0, 0), (1, 0), (0, 3), (2, 0),
                                   (0, 2), (3, 0), (0, 1)))
    ))
    config = Config(max_iterations=2, fleet_attempts_per_k=0, max_moves=None,
                    operators=SEGMENT_OPERATORS, num_neighbours=2, operator_schedule="cyclic")
    plain = solve(instance, config)
    measured = solve(instance, replace(config, diagnostics=True))
    assert plain.routes == measured.routes
    assert plain.evaluation == measured.evaluation
    assert validate_solution(instance, plain.routes).feasible
    assert plain.iterations == measured.iterations == 2


def test_fixed_fleet_search_does_not_activate_an_empty_route():
    instance = Instance("empty-slot", 2, 4, (
        Customer(0, 0, 0, 0, 0, 100_000, 0),
        *(Customer(i, x, 0, 1, start, start, 0)
          for i, (x, start) in enumerate(((10, 10_000), (1, 19_000),
                                         (2, 20_000), (11, 29_000)), 1)),
    ))
    routes = ((1, 2, 3, 4), ())
    assert validate_solution(instance, routes).objective == (1, 40_000)
    # Splitting off the middle pair is shorter, but uses a second vehicle.
    assert validate_solution(instance, ((1, 4), (2, 3))).objective == (2, 26_000)
    assert move_delta(instance, routes, Move("relocate_pair", 0, 1, 1, 0)) is None
    for operator in ("relocate", "relocate_pair", "two_opt_star"):
        result = improve(instance, routes, operators=(operator,), strategy="best")
        assert validate_solution(instance, result.routes).vehicles == 1
        assert result.routes[1] == ()


@pytest.mark.parametrize("move_args, expected_limit", [([], None), (["--max-moves", "2"], 2)])
def test_cli_persists_operator_selection_and_move_budget(tmp_path, move_args, expected_limit):
    path = tmp_path / "T01.txt"
    path.write_text("T01\nVEHICLE\nNUMBER CAPACITY\n2 2\nCUSTOMER\n"
                    "0 0 0 0 0 100 0\n1 1 0 1 0 100 0\n2 2 0 1 0 100 0\n",
                    encoding="utf-8")
    out = tmp_path / "result"
    assert main(["solve", str(path), "--max-iterations", "0", "--fleet-attempts", "0",
                 "--operators", "relocate_pair", "exchange_pairs", *move_args,
                 "--out", str(out)]) == 0
    payload = json.loads((out / "solution.json").read_text(encoding="utf-8"))
    assert payload["config"]["operators"] == ["relocate_pair", "exchange_pairs"]
    assert payload["config"]["max_moves"] == expected_limit


def test_unknown_operator_schedule_is_rejected():
    with pytest.raises(ValueError, match="operator_schedule"):
        Config(operator_schedule="random")
