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
