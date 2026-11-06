import json
from pathlib import Path

import pytest

from pennywatch import cli


def saved(tmp_path, name, net):
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "started_at": "2026-11-05T00:00:00+00:00",
                "host": {"cpu": "A", "kernel": "6"},
                "pennywatch": {"harness_sha256": "h"},
                "image": {"tag": "i"},
                "stack": {"lock_sha256": "l"},
                "cells": [{"name": "qft/qjit", "instructions": {"net": net}}],
            }
        )
    )
    return str(path)


def test_compare_passes_within_the_limit(tmp_path, capsys):
    paths = [saved(tmp_path, "a.json", 100_000), saved(tmp_path, "b.json", 100_010)]
    assert cli.main(["compare", *paths]) == 0
    assert "Worst range" in capsys.readouterr().out


def test_compare_fails_beyond_the_limit(tmp_path):
    paths = [saved(tmp_path, "a.json", 100_000), saved(tmp_path, "b.json", 101_000)]
    assert cli.main(["compare", *paths]) == 1


def test_compare_needs_two_results(tmp_path):
    assert cli.main(["compare", saved(tmp_path, "a.json", 1)]) == 2


def test_rejected_runs_are_saved_under_a_name_nothing_publishes():
    result = {"valid": False, "started_at": "x", "stack": {"id": "s"}}
    assert cli.result_path(result, Path("results/a.json")) == Path("results/a.rejected.json")


def test_valid_runs_are_saved_where_asked():
    result = {"valid": True, "started_at": "x", "stack": {"id": "s"}}
    assert cli.result_path(result, Path("results/a.json")) == Path("results/a.json")


def test_every_cell_of_the_suite_unless_some_are_named():
    assert cli.chosen_cells("m0", None) == cli.SUITES["m0"]
    names = [cell.name for cell in cli.chosen_cells("m0", "qft/qjit,grover/default")]
    assert names == ["qft/qjit", "grover/default"]


def test_a_cell_outside_the_suite_is_an_error():
    with pytest.raises(SystemExit, match="not in suite m0: qpe/qjit"):
        cli.chosen_cells("m0", "qpe/qjit,qft/qjit")
