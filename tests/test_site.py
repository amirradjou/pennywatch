import json
import re

import pytest

from pennywatch import history, site


def result(catalyst, net, **extra):
    return {
        "started_at": "2026-11-06T05:33:26+00:00",
        "valid": True,
        "stack": {
            "id": f"catalyst-{catalyst}",
            "pennylane-catalyst": catalyst,
            "pennylane": "0.45.1",
            "pennylane-lightning": "0.45.0",
        },
        "cells": [
            {
                "name": "qft/qjit",
                "instructions": {"net": net},
                "compile": {"net": net // 2},
                "imports": {"net": 7},
                "gates": {"device": {"gates": 721, "depth": 145}},
            },
            {"name": "qft/default", "instructions": {"net": net * 2}},
        ],
        "memory": [{"backend": "qjit", "wires": 25, "max_rss_kb": {"net": 2048}}],
        **extra,
    }


def test_builds_come_in_build_order_with_their_versions():
    data = site.page_data([result("0.10.0", 2), result("0.9.0", 1)], [], [])
    assert [build["catalyst"] for build in data["stable"]["builds"]] == ["0.9.0", "0.10.0"]
    assert data["stable"]["builds"][0]["pennylane"] == "0.45.1"
    assert data["stable"]["builds"][0]["measured"] == "2026-11-06"


def test_all_builds_run_as_one_history_from_stable_into_dev():
    data = site.page_data([result("0.15.0", 2)], [result("0.16.0.dev1", 3)], [])
    assert [build["catalyst"] for build in data["all"]["builds"]] == ["0.15.0", "0.16.0.dev1"]
    assert [build["catalyst"] for build in data["stable"]["builds"]] == ["0.15.0"]
    assert [build["catalyst"] for build in data["nightly"]["builds"]] == ["0.16.0.dev1"]


def test_each_circuit_is_one_chart_with_one_line_per_backend_that_has_values():
    data = site.page_data([result("0.9.0", 100), result("0.10.0", 110)], [], [])
    panel = data["stable"]["panels"]["instructions"][0]
    assert panel["title"] == "qft"
    assert {series["key"]: series["values"] for series in panel["series"]} == {
        "default": [200, 220],
        "qjit": [100, 110],
    }


def test_compile_cost_is_only_for_qjit():
    data = site.page_data([result("0.9.0", 100)], [], [])
    series = data["stable"]["panels"]["compile"][0]["series"]
    assert [line["key"] for line in series] == ["qjit"]


def test_memory_is_in_mib():
    data = site.page_data([result("0.9.0", 100)], [], [])
    panel = data["stable"]["panels"]["memory"][0]
    assert panel["unit"] == "MiB"
    assert panel["series"][0]["values"] == [2.0]


def test_a_build_missing_a_cell_leaves_a_hole_rather_than_a_zero():
    older = result("0.9.0", 100)
    older["cells"] = older["cells"][:1]
    data = site.page_data([older, result("0.10.0", 110)], [], [])
    default = next(
        series
        for series in data["stable"]["panels"]["instructions"][0]["series"]
        if series["key"] == "default"
    )
    assert default["values"] == [None, 220]


def test_steps_carry_their_numbers_and_explanation():
    steps = history.find_steps([result("0.9.0", 100), result("0.10.0", 110)])
    key = "catalyst-0.9.0..catalyst-0.10.0/qft/qjit/instructions"
    url = "https://github.com/PennyLaneAI/catalyst/pull/1"
    data = site.page_data([], [], steps, explanations={key: {"text": "#1", "url": url}})
    row = next(
        r for r in data["steps"] if r["cell"] == "qft/qjit" and r["metric"] == "instructions"
    )
    assert row["before"] == 100
    assert row["after"] == 110
    assert row["change_pct"] == pytest.approx(10.0)
    assert row["explanation"]["text"] == "#1"


def test_imports_are_one_chart_with_a_line_per_backend():
    data = site.page_data([result("0.9.0", 100)], [], [])
    panel = data["stable"]["panels"]["imports"][0]
    assert panel["title"] == "Imports"
    assert [series["key"] for series in panel["series"]] == ["qjit"]
    assert panel["series"][0]["values"] == [7]


def test_gaps_keep_a_short_reason_only():
    gap = {"stack": {"pennylane-catalyst": "0.16.0.dev1"}, "problems": ["x" * 1000, "b", "c", "d"]}
    gaps = site.page_data([], [], [], gaps=[gap])["gaps"]
    assert gaps[0]["build"] == "0.16.0.dev1"
    assert len(gaps[0]["why"]) == 3
    assert len(gaps[0]["why"][0]) == 300


def _embedded_json(html):
    return re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)[1]


def test_a_string_from_a_measured_process_cannot_close_the_script_element():
    hostile = "</script><script>alert(1)</script>"
    gap = {"stack": {"pennylane-catalyst": "0.16.0.dev1"}, "problems": [hostile]}
    html = site.render(site.page_data([], [], [], gaps=[gap]))
    assert hostile not in html
    assert json.loads(_embedded_json(html))["gaps"][0]["why"] == [hostile]


def test_the_page_uses_only_inline_code():
    html = site.render(site.page_data([], [], []))
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+stylesheet", html)
    assert "innerHTML" not in html
    assert "/*DATA*/" not in html


def _saved(**extra):
    return {
        "cell": "qft/qjit",
        "metric": "instructions",
        "before": {"stack": "catalyst-0.9.0"},
        "after": {"stack": "catalyst-0.10.0"},
        "candidates": {"pennylane": [{}, {}], "pennylane-catalyst": [{}]},
        **extra,
    }


KEY = "catalyst-0.9.0..catalyst-0.10.0/qft/qjit/instructions"


def test_a_bisected_change_links_to_its_pull_request():
    url = "https://github.com/x/y/pull/7"
    bisect = {"culprits": [{"commit": "abc", "subject": "Fix adjoint (#7)", "url": url}]}
    assert site.explanation_entry(_saved(bisect=bisect)) == (
        KEY,
        {"text": "Fix adjoint (#7)", "url": url},
    )


def test_a_change_with_several_causes_says_how_many():
    url = "https://github.com/x/y/pull/7"
    culprits = [{"subject": "Big (#7)", "url": url}, {"subject": "Small (#8)", "url": "u"}]
    entry = site.explanation_entry(_saved(bisect={"culprits": culprits}))[1]
    assert entry == {"text": "Big (#7), and 1 more", "url": url}


def test_a_bisect_that_stopped_says_why():
    entry = site.explanation_entry(_saved(bisect={"verdict": "the cause is elsewhere"}))[1]
    assert entry == {"text": "the cause is elsewhere", "url": None}


def test_with_no_bisect_it_says_how_many_candidates_there_are():
    entry = site.explanation_entry(_saved())[1]
    assert entry["text"] == "3 candidates, not narrowed further"


def test_the_count_of_candidates_survives_the_list_being_cut_short():
    saved = _saved(candidate_counts={"pennylane": 591, "pennylane-catalyst": 411})
    assert site.explanation_entry(saved)[1]["text"] == "1002 candidates, not narrowed further"


def test_saved_explanations_are_matched_to_their_steps(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_saved()))
    explanations = site.load_explanations(tmp_path)
    steps = history.find_steps([result("0.9.0", 100), result("0.10.0", 110)])
    rows = site.step_rows(steps, explanations)
    row = next(r for r in rows if r["cell"] == "qft/qjit" and r["metric"] == "instructions")
    assert row["explanation"]["text"] == "3 candidates, not narrowed further"


def test_a_step_in_the_imports_is_matched_by_its_backend():
    key = site.explanation_entry(_saved(cell="qft/default", metric="imports"))[0]
    assert key == "catalyst-0.9.0..catalyst-0.10.0/imports/default/imports"


def test_what_a_person_checked_replaces_what_the_tool_said(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_saved()))
    checked = {KEY: {"text": "numpy, not PennyLane", "url": None}}
    (tmp_path / site.CHECKED_FILE).write_text(json.dumps(checked))
    assert site.load_explanations(tmp_path)[KEY]["text"] == "numpy, not PennyLane"


def test_no_directory_of_explanations_is_no_explanations(tmp_path):
    assert site.load_explanations(tmp_path / "missing") == {}
    assert site.load_explanations(None) == {}
