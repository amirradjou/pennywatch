import pytest

from pennywatch import history
from pennywatch.history import find_steps, is_step, version_key


def result(catalyst, *cells, memory=None, packages=None, valid=True):
    return {
        "stack": {
            "id": f"catalyst-{catalyst}",
            "pennylane-catalyst": catalyst,
            "packages": packages or {},
        },
        "cells": list(cells),
        "memory": memory or [],
        "valid": valid,
    }


def cell(net, name="qft/default", gates=721):
    return {
        "name": name,
        "instructions": {"net": net},
        "gates": {"device": {"gates": gates, "depth": 165}},
    }


def test_dev_builds_sort_before_their_release_and_numbers_sort_as_numbers():
    versions = ["0.16.0.dev10", "0.15.0", "0.16.0", "0.16.0.dev9", "0.9.0", "0.10.0"]
    assert sorted(versions, key=version_key) == [
        "0.9.0",
        "0.10.0",
        "0.15.0",
        "0.16.0.dev9",
        "0.16.0.dev10",
        "0.16.0",
    ]


@pytest.mark.parametrize("version", ["latest", "0.15.0\n", "0.15.0; rm -rf", "../0.15.0"])
def test_an_unknown_version_is_an_error_and_not_a_guess(version):
    with pytest.raises(ValueError, match="not a version"):
        version_key(version)


@pytest.mark.parametrize(
    ("metric", "before", "after", "expected"),
    [
        ("instructions", 1000, 1004, False),
        ("instructions", 1000, 1005, True),
        ("instructions", 1000, 995, True),
        ("compile", 1000, 1000, False),
        ("gates.device", 700, 701, True),
        ("depth.top", 90, 90, False),
        ("peak_kb", 1000, 1004, False),
        ("peak_kb", 1000, 1005, True),
    ],
)
def test_what_counts_as_a_step(metric, before, after, expected):
    assert is_step(metric, before, after) is expected


def test_runs_that_overlap_are_not_a_step_however_far_apart_the_medians_are():
    assert is_step("instructions", 1000, 1010, [990, 1000, 1005], [1004, 1010, 1020]) is False
    assert is_step("instructions", 1000, 1010, [999, 1000, 1001], [1009, 1010, 1011]) is True


def test_a_single_run_on_either_side_has_only_the_threshold_to_clear():
    assert is_step("instructions", 1000, 1010, [1000], [1010]) is True
    assert is_step("instructions", 1000, 1010) is True


def test_steps_are_found_between_consecutive_builds_in_build_order():
    results = [
        result("0.10.0", cell(1100)),
        result("0.9.0", cell(1000)),
        result("0.8.1", cell(1000)),
    ]
    steps = find_steps(results)
    assert [(s.before_stack, s.after_stack, s.metric) for s in steps] == [
        ("catalyst-0.9.0", "catalyst-0.10.0", "instructions")
    ]
    assert steps[0].change_pct == pytest.approx(10.0)


def test_a_cell_missing_from_one_build_is_not_a_step():
    assert find_steps([result("0.8.1", cell(1000)), result("0.9.0")]) == []


def test_imports_are_one_figure_per_backend_however_many_cells_carry_them():
    def carrying(net):
        return [
            {"name": f"{circuit}/qjit", "imports": {"net": net}} for circuit in ("qft", "grover")
        ]

    steps = find_steps([result("0.8.1", *carrying(1000)), result("0.9.0", *carrying(2000))])
    assert [(s.cell, s.metric) for s in steps] == [("imports/qjit", "imports")]


def test_a_step_names_every_package_that_moved():
    before = result("0.8.1", cell(1000), packages={"numpy": "1.26", "scipy": "1.12", "gone": "1"})
    after = result("0.9.0", cell(2000), packages={"numpy": "2.0", "scipy": "1.12", "new": "2"})
    assert find_steps([before, after])[0].packages_changed == (
        "gone 1 → absent",
        "new absent → 2",
        "numpy 1.26 → 2.0",
    )


def test_a_gate_count_change_is_a_step_however_small():
    steps = find_steps(
        [result("0.8.1", cell(1000, gates=700)), result("0.9.0", cell(1000, gates=701))]
    )
    assert [s.metric for s in steps] == ["gates.device"]


def test_memory_is_a_metric_of_its_own():
    def entry(kb):
        return [{"backend": "lightning", "wires": 25, "max_rss_kb": {"net": kb}}]

    steps = find_steps([result("0.8.1", memory=entry(1000)), result("0.9.0", memory=entry(1100))])
    assert [(s.cell, s.metric) for s in steps] == [("memory/lightning", "peak_kb")]


def test_a_second_measurement_confirms_the_same_change():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]
    second = [result("0.8.1", cell(2000)), result("0.9.0", cell(2200))]
    steps = history.confirm_steps(find_steps(first), second)
    assert [s.confirmed for s in steps] == [True]


def test_a_second_measurement_within_a_quarter_of_the_step_confirms():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1200))]
    second = [result("0.8.1", cell(1000)), result("0.9.0", cell(1160))]
    assert history.confirm_steps(find_steps(first), second)[0].confirmed is True


def test_a_second_measurement_the_other_way_does_not_confirm():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]
    second = [result("0.8.1", cell(1000)), result("0.9.0", cell(900))]
    assert history.confirm_steps(find_steps(first), second)[0].confirmed is False


def test_a_second_measurement_showing_something_else_does_not_confirm():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]
    second = [result("0.8.1", cell(1000)), result("0.9.0", cell(1000))]
    assert history.confirm_steps(find_steps(first), second)[0].confirmed is False


def test_a_step_keeps_the_size_the_second_measurement_saw():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]
    second = [result("0.8.1", cell(1000)), result("0.9.0", cell(1200))]
    step = history.confirm_steps(find_steps(first), second)[0]
    assert step.confirmed is False
    assert step.second_change_pct == 20.0
    assert "| +10.00% | +20.00% | NO |" in history.render_steps([step])


def test_a_step_with_no_second_measurement_says_so():
    first = [result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]
    assert history.confirm_steps(find_steps(first), first[:1])[0].confirmed is None


def test_gate_counts_must_match_exactly_to_confirm():
    first = [result("0.8.1", cell(1, gates=700)), result("0.9.0", cell(1, gates=701))]
    same = [result("0.8.1", cell(1, gates=700)), result("0.9.0", cell(1, gates=701))]
    other = [result("0.8.1", cell(1, gates=700)), result("0.9.0", cell(1, gates=702))]
    assert history.confirm_steps(find_steps(first), same)[0].confirmed is True
    assert history.confirm_steps(find_steps(first), other)[0].confirmed is False


def test_rejected_results_are_never_loaded(tmp_path):
    (tmp_path / "a.json").write_text('{"valid": true}')
    (tmp_path / "b.rejected.json").write_text('{"valid": false}')
    (tmp_path / "c.json").write_text('{"valid": false}')
    assert history.load_results(sorted(tmp_path.iterdir())) == [{"valid": True}]


def test_the_steps_table_gives_the_numbers_and_the_status():
    steps = history.confirm_steps(
        find_steps([result("0.8.1", cell(1000)), result("0.9.0", cell(1100))]), []
    )
    text = history.render_steps(steps)
    assert "| catalyst-0.8.1 → catalyst-0.9.0 | qft/default | instructions |" in text
    assert "+10.00%" in text
    assert "not re-measured" in text


def test_the_table_cannot_carry_a_hostile_name():
    step = history.Step("a", "b", "qft/qjit", "gates.x|y\n::set-output", 1, 2, 100.0, ())
    text = history.render_steps([step])
    assert "x/y?::set-output" in text
    assert len(text.splitlines()) == 3


def test_no_steps_says_so():
    assert history.render_steps([]) == "No steps.\n"


def test_the_series_has_one_column_per_build_in_order():
    text = history.render_series([result("0.9.0", cell(2e9)), result("0.8.1", cell(1e9))])
    assert text.splitlines()[0] == "| instructions | 0.8.1 | 0.9.0 |"
    assert text.splitlines()[2] == "| qft/default | 1.000B | 2.000B |"
