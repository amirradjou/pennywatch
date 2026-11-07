import json
from pathlib import Path

import pytest

from pennywatch import cli, measure, stacks


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


def _suite_run_captured(monkeypatch):
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        raise stacks.StackUnavailable("stop here")

    monkeypatch.setattr(measure, "run_suite", capture)
    return seen


def test_the_smoke_suite_does_not_judge_canaries_and_a_real_suite_does(tmp_path, monkeypatch):
    seen = _suite_run_captured(monkeypatch)
    cli.main(["run", "--suite", "smoke", "--out", str(tmp_path / "a.json")])
    assert seen["judge_canaries"] is False
    cli.main(["run", "--suite", "full", "--out", str(tmp_path / "b.json")])
    assert seen["judge_canaries"] is True


def test_a_build_that_cannot_be_measured_is_saved_as_rejected_with_the_reason(
    tmp_path, monkeypatch, capsys
):
    def unavailable(*args, **kwargs):
        raise stacks.StackUnavailable("not on the index")

    monkeypatch.setattr(measure, "run_suite", unavailable)
    out = tmp_path / "catalyst-0.16.0.dev1.json"
    assert cli.main(["run", "--stack", "0.16.0.dev1", "--out", str(out)]) == 1
    saved = json.loads((tmp_path / "catalyst-0.16.0.dev1.rejected.json").read_text())
    assert saved["valid"] is False
    assert saved["cells"] == []
    assert saved["problems"] == ["not on the index"]
    assert "not on the index" in capsys.readouterr().out


def _with_stack(tmp_path, version, net):
    path = tmp_path / f"catalyst-{version}.json"
    path.write_text(
        json.dumps(
            {
                "valid": True,
                "stack": {"id": f"catalyst-{version}", "pennylane-catalyst": version},
                "cells": [{"name": "qft/qjit", "instructions": {"net": net}}],
            }
        )
    )
    return str(path)


def test_the_page_counts_the_step_from_the_last_release_to_the_first_dev_build(tmp_path):
    stable, data = tmp_path / "stable", tmp_path / "data"
    stable.mkdir()
    (data / "nightly").mkdir(parents=True)
    _with_stack(stable, "0.15.0", 100)
    _with_stack(data / "nightly", "0.16.0.dev1", 200)
    assert (
        cli.main(
            [
                "site",
                "--stable",
                str(stable),
                "--data",
                str(data),
                "--second-set",
                str(tmp_path / "none"),
                "--explanations",
                str(tmp_path / "none"),
                "--out",
                str(tmp_path / "site"),
            ]
        )
        == 0
    )
    html = (tmp_path / "site" / "index.html").read_text()
    assert '"before_stack":"0.15.0","after_stack":"0.16.0.dev1"' in html


def test_steps_can_be_limited_to_the_newest_two_builds(tmp_path, capsys):
    paths = [
        _with_stack(tmp_path, "0.16.0.dev1", 100),
        _with_stack(tmp_path, "0.16.0.dev2", 200),
        _with_stack(tmp_path, "0.16.0.dev3", 200),
    ]
    assert cli.main(["steps", *paths]) == 0
    assert "+100.00%" in capsys.readouterr().out
    assert cli.main(["steps", "--latest", *paths]) == 0
    assert "No steps." in capsys.readouterr().out
