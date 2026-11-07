from pennywatch import compare


def result(net=1000, compile_net=None, cpu="A", harness="h", gates=None):
    cell = {"name": "qft/qjit", "instructions": {"net": net}, "gates": gates or {"device": 7}}
    if compile_net is not None:
        cell["compile"] = {"net": compile_net}
    return {
        "started_at": "2026-11-05T00:00:00+00:00",
        "host": {"cpu": cpu, "kernel": "6"},
        "pennywatch": {"harness_sha256": harness},
        "image": {"tag": "i"},
        "stack": {"lock_sha256": "l"},
        "cells": [cell],
    }


def test_identical_runs_have_no_range():
    assert compare.compare([result(), result()])["worst_pct"] == 0


def test_range_is_a_share_of_the_mean():
    comparison = compare.compare([result(net=990), result(net=1010)])
    assert round(comparison["worst_pct"], 6) == 2.0


def test_compile_cost_is_compared_too():
    comparison = compare.compare([result(compile_net=100), result(compile_net=110)])
    metrics = {row["metric"]: row for row in comparison["rows"]}
    assert metrics["compile"]["values"] == [100, 110]
    assert comparison["worst_pct"] > 9


def test_a_failed_metric_is_left_out_rather_than_compared_with_nothing():
    comparison = compare.compare([result(compile_net=100), result()])
    assert [row["metric"] for row in comparison["rows"]] == ["net"]


def test_warns_when_the_runs_are_not_like_for_like():
    comparison = compare.compare([result(harness="a"), result(harness="b")])
    assert any("different harness" in warning for warning in comparison["warnings"])


def test_warns_when_gate_counts_differ():
    comparison = compare.compare([result(gates={"device": 7}), result(gates={"device": 8})])
    assert any("Gate counts" in warning for warning in comparison["warnings"])


def test_render_names_each_run_and_the_worst_range():
    results = [result(cpu="EPYC"), result(net=1010, cpu="Xeon")]
    text = compare.render(results, compare.compare(results))
    assert "EPYC" in text and "Xeon" in text
    assert "Worst range" in text
