import json
import os

import pytest

from pennywatch import followup, history
from pennywatch.followup import FollowUp
from pennywatch.publish import Rejected

COMMIT = "a" * 40
URL = "https://github.com/PennyLaneAI/pennylane/pull/9932"


def result(version, cells=(), memory=(), valid=True, cpu="AMD EPYC 7763"):
    return {
        "schema": 2,
        "stack": {"id": f"catalyst-{version}", "pennylane-catalyst": version},
        "host": {"cpu": cpu},
        "valid": valid,
        "problems": [],
        "cells": list(cells),
        "memory": list(memory),
    }


def cell(net, name="qpe/lightning", imports=None, compile_=None, spread=0):
    entry = {
        "name": name,
        "instructions": {"net": net, "runs": [net - spread, net, net + spread]},
        "gates": {"device": {"gates": 652, "depth": 238}},
    }
    if imports is not None:
        entry["imports"] = {"net": imports, "runs": [imports, imports, imports]}
    if compile_ is not None:
        entry["compile"] = {"net": compile_, "runs": [compile_, compile_, compile_]}
    return entry


def memory(net, backend="lightning"):
    return {"backend": backend, "wires": 25, "max_rss_kb": {"net": net, "runs": [net, net, net]}}


def dev(n):
    return f"0.16.0.dev{n}"


# What a follow-up is.


def test_a_step_name_round_trips():
    key = "catalyst-0.15.0..catalyst-0.16.0.dev135/qpe/lightning/instructions"
    assert FollowUp.from_key(key).key == key


@pytest.mark.parametrize(
    "key",
    [
        "catalyst-0.15.0..catalyst-latest/qpe/lightning/instructions",
        "catalyst-0.15.0..catalyst-0.16.0/qpe/lightning/gates.device",
        "catalyst-0.15.0..catalyst-0.16.0/nope/lightning/instructions",
        "catalyst-../x..catalyst-0.16.0/qpe/lightning/instructions",
        "catalyst-0.15.0..catalyst-0.16.0/qpe/lightning/instructions\n",
    ],
)
def test_anything_but_a_step_of_the_suite_is_refused(key):
    with pytest.raises(ValueError):
        FollowUp.from_key(key)


def test_imports_and_memory_are_measured_with_a_cell_of_their_backend():
    assert FollowUp("0.15.0", "0.16.0", "imports/qjit", "imports").measured_cell == "qft/qjit"
    assert FollowUp("0.15.0", "0.16.0", "memory/default", "peak_kb").measured_cell == "qft/default"
    assert FollowUp("0.15.0", "0.16.0", "qpe/lightning", "instructions").measured_cell == (
        "qpe/lightning"
    )


def test_memory_is_measured_again_but_not_explained():
    assert not FollowUp("0.15.0", "0.16.0", "memory/default", "peak_kb").explainable
    assert FollowUp("0.15.0", "0.16.0", "imports/qjit", "imports").explainable


def test_a_planned_step_whose_name_was_changed_is_refused():
    planned = FollowUp("0.15.0", "0.16.0", "qpe/lightning", "instructions").to_dict()
    with pytest.raises(ValueError, match="name"):
        FollowUp.from_dict(planned | {"name": "../../etc"})


# Which steps get followed up.


def test_one_follow_up_covers_the_imports_and_a_qjit_cells_compile_cost():
    results = [
        result(
            dev(1),
            [
                cell(1000, "qft/default", imports=5000),
                cell(1000, "qft/qjit", imports=6000, compile_=800),
            ],
        ),
        result(
            dev(2),
            [
                cell(1000, "qft/default", imports=5500),
                cell(1100, "qft/qjit", imports=7200, compile_=900),
            ],
        ),
    ]
    chosen = followup.worth_following(history.find_steps(results))
    assert sorted((s.cell, s.metric) for s in chosen) == [
        ("imports/qjit", "imports"),
        ("qft/qjit", "instructions"),
    ]


def test_gate_counts_are_not_followed_up():
    a = result(dev(1), [cell(1000)])
    b = result(dev(2), [cell(1000) | {"gates": {"device": {"gates": 94, "depth": 34}}}])
    assert followup.worth_following(history.find_steps([a, b])) == []


def test_the_newest_night_comes_first_then_the_largest_older_steps():
    results = [
        result(dev(1), [cell(1000), cell(1000, "qft/lightning"), cell(1000, "grover/lightning")]),
        result(dev(2), [cell(2000), cell(1300, "qft/lightning"), cell(1000, "grover/lightning")]),
        result(dev(3), [cell(2000), cell(1300, "qft/lightning"), cell(1010, "grover/lightning")]),
    ]
    planned = followup.plan(results, followed=set(), limit=2)
    assert [(f.after, f.cell) for f in planned] == [
        (dev(3), "grover/lightning"),
        (dev(2), "qpe/lightning"),
    ]


def test_a_step_already_followed_up_is_not_planned_again():
    results = [result(dev(1), [cell(1000)]), result(dev(2), [cell(2000)])]
    key = f"catalyst-{dev(1)}..catalyst-{dev(2)}/qpe/lightning/instructions"
    assert followup.plan(results, followed={key}) == []


def test_steps_into_the_nightly_from_a_release_are_left_to_a_person():
    stable = [result("0.15.0", [cell(1000)])]
    nightly = [result(dev(1), [cell(2000)])]
    assert followup.plan(nightly, followed=set(), stable=stable) == []


def test_one_step_can_be_named_even_if_followed_up_or_from_a_release():
    stable = [result("0.15.0", [cell(1000)])]
    nightly = [result(dev(1), [cell(2000)])]
    key = f"catalyst-0.15.0..catalyst-{dev(1)}/qpe/lightning/instructions"
    planned = followup.plan(nightly, followed={key}, step=key, stable=stable)
    assert [f.key for f in planned] == [key]


def test_a_named_step_that_isnt_one_is_an_error():
    nightly = [result(dev(1), [cell(1000)]), result(dev(2), [cell(1001)])]
    with pytest.raises(ValueError, match="no such step"):
        followup.plan(
            nightly,
            followed=set(),
            step=f"catalyst-{dev(1)}..catalyst-{dev(2)}/qpe/lightning/instructions",
        )


# Running one.


def test_a_follow_up_measures_both_builds_with_the_first_locks_and_explains(tmp_path, monkeypatch):
    calls = {"measured": [], "explained": []}
    (tmp_path / "locks").mkdir()
    (tmp_path / "locks" / f"catalyst-{dev(1)}.pylock.toml").write_text("lock 1")
    monkeypatch.setattr(followup.stacks, "STACKS_DIR", tmp_path / "stacks", raising=False)

    class Stack:
        def __init__(self, version):
            self.version = version
            self.id = f"catalyst-{version}"
            self.lock_path = tmp_path / "stacks" / f"{self.id}.pylock.toml"

    def run_suite(cells, stack, repeats, cpus, busy_check, memory_wires):
        calls["measured"].append((stack.version, cells[0].name, repeats, memory_wires))
        return result(stack.version, [cell(1000 if stack.version == dev(1) else 2000)])

    class Explanation:
        def to_dict(self):
            return {"cell": "qpe/lightning"}

    def explain(before, after, cell_name, metric, cpus, bisect, max_measured):
        calls["explained"].append((before.version, after.version, cell_name, bisect, max_measured))
        return Explanation()

    monkeypatch.setattr(followup.stacks, "Stack", Stack)
    monkeypatch.setattr(followup.measure, "run_suite", run_suite)
    monkeypatch.setattr(followup.explain, "explain", explain)
    monkeypatch.setattr(followup.explain, "render", lambda explanation: "")
    step = FollowUp(dev(1), dev(2), "qpe/lightning", "instructions")
    followup.run(step, tmp_path / "out", locks=tmp_path / "locks", max_measured=12)
    assert calls["measured"] == [
        (dev(1), "qpe/lightning", 3, None),
        (dev(2), "qpe/lightning", 3, None),
    ]
    assert calls["explained"] == [(dev(1), dev(2), "qpe/lightning", True, 12)]
    assert (tmp_path / "stacks" / f"catalyst-{dev(1)}.pylock.toml").read_text() == "lock 1"
    assert (tmp_path / "out" / f"catalyst-{dev(2)}.json").exists()
    assert (tmp_path / "out" / followup.EXPLANATION_FILE).exists()


def test_a_step_that_has_gone_is_not_explained(tmp_path, monkeypatch):
    explained = []

    class Stack:
        def __init__(self, version):
            self.version = version
            self.id = f"catalyst-{version}"
            self.lock_path = tmp_path / f"{self.id}.pylock.toml"

    monkeypatch.setattr(followup.stacks, "Stack", Stack)
    monkeypatch.setattr(
        followup.measure,
        "run_suite",
        lambda cells, stack, **kwargs: result(stack.version, [cell(1000)]),
    )
    monkeypatch.setattr(followup.explain, "explain", lambda *a, **k: explained.append(a))
    followup.run(FollowUp(dev(1), dev(2), "qpe/lightning", "instructions"), tmp_path / "out")
    assert explained == []


def test_memory_is_measured_with_the_memory_runs(tmp_path, monkeypatch):
    seen = []

    class Stack:
        def __init__(self, version):
            self.version = version
            self.id = f"catalyst-{version}"
            self.lock_path = tmp_path / f"{self.id}.pylock.toml"

    def run_suite(cells, stack, repeats, cpus, busy_check, memory_wires):
        seen.append(memory_wires)
        return result(stack.version, [cell(1000, "qft/lightning")], [memory(1000)])

    monkeypatch.setattr(followup.stacks, "Stack", Stack)
    monkeypatch.setattr(followup.measure, "run_suite", run_suite)
    followup.run(FollowUp(dev(1), dev(2), "memory/lightning", "peak_kb"), tmp_path / "out")
    assert seen == [25, 25]


# Recording what came back, which was written next to nightly code.

FIRST = [result(dev(1), [cell(1000)]), result(dev(2), [cell(2000)])]
STEP = FollowUp(dev(1), dev(2), "qpe/lightning", "instructions")


def explanation(**changes):
    saved = {
        "cell": "qpe/lightning",
        "metric": "instructions",
        "before": {"stack": f"catalyst-{dev(1)}", "runs": [1000, 1000, 1000], "net": 1000},
        "after": {"stack": f"catalyst-{dev(2)}", "runs": [2000, 2000, 2000], "net": 2000},
        "packages": ["pennylane 0.46.0.dev1 → 0.46.0.dev2"],
        "python_functions": [["pennylane/ops/qft.py:compute_matrix", 10, 500]],
        "c_functions": [["_PyEval_EvalFrameDefault", 100, 200]],
        "candidates": {
            "pennylane": [
                {"score": 3, "subject": "Port QFT (#9932)", "pull_request": 9932, "url": URL}
            ]
        },
        "candidate_counts": {"pennylane": 40},
        "bisect": {
            "culprits": [
                {
                    "commit": COMMIT,
                    "before": 1000,
                    "after": 1900,
                    "pull_request": 9932,
                    "url": URL,
                    "subject": "Port QFT to Operator2 (#9932)",
                }
            ],
            "unresolved": [["b" * 40, "c" * 40]],
            "measured": 9,
            "stopped": False,
            "pennylane_alone": 2000,
            "pennylane_share": 1.0,
            "something_else": "dropped",
        },
    }
    return saved | changes


def came_back(tmp_path, before=1000, after=2000, saved=None, valid=True):
    directory = tmp_path / f"followup-{STEP.name}"
    directory.mkdir(parents=True, exist_ok=True)
    for version, net in ((dev(1), before), (dev(2), after)):
        measured = result(version, [cell(net)], valid=valid, cpu="Intel Xeon")
        (directory / f"catalyst-{version}.json").write_text(json.dumps(measured))
    if saved is not None:
        (directory / followup.EXPLANATION_FILE).write_text(json.dumps(saved))
    return directory


def test_a_step_that_shows_again_is_recorded_as_confirmed(tmp_path):
    record = followup.record_one(STEP, came_back(tmp_path, 1000, 1990, explanation()), FIRST)
    assert record["confirmed"] is True
    assert record["second"]["change_pct"] == pytest.approx(99.0)
    assert record["second"]["host"] == "Intel Xeon"
    assert record["first"]["change_pct"] == pytest.approx(100.0)
    assert record["explanation"]["bisect"]["culprits"][0]["pull_request"] == 9932
    assert record["explanation"]["bisect"]["unresolved_commits"] == [2]
    assert "something_else" not in record["explanation"]["bisect"]
    assert record["problems"] == []


def test_a_host_that_isnt_an_object_is_just_left_out(tmp_path):
    directory = came_back(tmp_path, 1000, 1990)
    path = directory / f"catalyst-{dev(1)}.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"host": "x"}))
    record = followup.record_one(STEP, directory, FIRST)
    assert record["confirmed"] is True
    assert record["second"]["host"] == ""


def test_a_step_that_doesnt_show_again_is_recorded_as_not_confirmed(tmp_path):
    record = followup.record_one(STEP, came_back(tmp_path, 1000, 1001), FIRST)
    assert record["confirmed"] is False
    assert record["explanation"] is None


def test_what_the_runner_says_about_the_step_is_never_believed(tmp_path):
    directory = came_back(tmp_path, 1000, 1001)
    (directory / "verdict.json").write_text('{"confirmed": true}')
    assert followup.record_one(STEP, directory, FIRST)["confirmed"] is False


def test_an_invalid_second_measurement_confirms_nothing(tmp_path):
    record = followup.record_one(STEP, came_back(tmp_path, valid=False), FIRST)
    assert record["confirmed"] is None
    assert "isn't valid" in record["problems"][0]


def test_a_follow_up_that_never_came_back_is_recorded_so_it_isnt_retried_for_ever(tmp_path):
    lines = followup.record(tmp_path / "nothing", tmp_path / "data", [STEP], FIRST)
    assert lines == [f"- {STEP.key}: couldn't be measured again (no result for {dev(1)} came back)"]
    assert followup.followed_keys(tmp_path / "data") == {STEP.key}


def test_a_linked_result_is_refused(tmp_path):
    directory = came_back(tmp_path)
    target = directory / f"catalyst-{dev(1)}.json"
    target.rename(tmp_path / "elsewhere.json")
    os.symlink(tmp_path / "elsewhere.json", target)
    record = followup.record_one(STEP, directory, FIRST)
    assert record["confirmed"] is None


def test_a_step_that_was_never_published_is_refused(tmp_path):
    other = FollowUp(dev(1), dev(2), "qft/lightning", "instructions")
    lines = followup.record(tmp_path, tmp_path / "data", [other], FIRST)
    assert "refused, not a step" in lines[0]
    assert not (tmp_path / "data" / "followups").exists()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"cell": "qft/lightning"}, "different step"),
        ({"before": {"stack": "catalyst-0.1.0", "runs": [1], "net": 1}}, "before side"),
        (
            {"candidates": {"pennylane": [{"score": 1, "url": "https://evil.example/pull/1"}]}},
            "PennyLane pull request",
        ),
        ({"candidates": {"../x": []}}, "odd name"),
        ({"python_functions": [["f", -1, 2]]}, "count"),
    ],
)
def test_an_explanation_that_isnt_what_this_project_writes_is_refused(changes, reason):
    with pytest.raises(Rejected, match=reason):
        followup.parse_explanation(json.dumps(explanation(**changes)).encode(), STEP)


def test_a_culprit_that_isnt_a_commit_is_refused():
    saved = explanation()
    saved["bisect"]["culprits"][0]["commit"] = "HEAD; rm -rf"
    with pytest.raises(Rejected, match="commit"):
        followup.parse_explanation(json.dumps(saved).encode(), STEP)


def test_a_refused_explanation_still_leaves_the_confirmation(tmp_path):
    saved = explanation(cell="qft/default")
    record = followup.record_one(STEP, came_back(tmp_path, 1000, 1990, saved), FIRST)
    assert record["confirmed"] is True
    assert record["explanation"] is None
    assert record["problems"] == ["explanation: it explains a different step"]


# Telling the owner, without posting anywhere upstream.


def recorded(tmp_path, before=1000, after=1990, saved=None):
    return followup.record_one(STEP, came_back(tmp_path, before, after, saved), FIRST)


def test_no_issue_when_nothing_held(tmp_path):
    assert followup.issue([recorded(tmp_path, 1000, 1001)]) is None


def test_the_issue_names_the_step_and_its_cause(tmp_path):
    title, body = followup.issue([recorded(tmp_path, saved=explanation())])
    assert title == f"qpe/lightning instructions +100.00% at {dev(2)}, measured again"
    assert f"| {dev(1)} → {dev(2)} | qpe/lightning | instructions | +100.00% | +99.00% |" in body
    assert "PennyLane pull request `9932`: `Port QFT to Operator2`, 90% of it" in body


def test_the_issue_links_nothing_upstream_and_mentions_nobody(tmp_path):
    saved = explanation()
    culprit = saved["bisect"]["culprits"][0]
    culprit["subject"] = (
        "Fix @someone's bug, see https://github.com/PennyLaneAI/pennylane/pull/1 #2"
    )
    _, body = followup.issue([recorded(tmp_path, saved=saved)])
    outside_code = "".join(body.split("`")[::2])
    assert "github.com/PennyLaneAI" not in outside_code
    assert "@" not in outside_code
    assert not any(word.startswith("#") for word in outside_code.split())
    assert URL not in body


def test_a_subject_cannot_break_out_of_its_code_span_or_the_table(tmp_path):
    saved = explanation()
    saved["bisect"]["culprits"][0]["subject"] = "a` @x | b\nc"
    _, body = followup.issue([recorded(tmp_path, saved=saved)])
    row = next(line for line in body.splitlines() if line.startswith(f"| {dev(1)}"))
    assert row.count("|") == 7
    assert row.count("`") % 2 == 0


def test_when_pennylane_isnt_the_cause_the_issue_says_where_the_candidates_are(tmp_path):
    saved = explanation(
        bisect={"verdict": "PennyLane alone gives 41% of the step, so the cause is elsewhere."},
        candidate_counts={"pennylane": 40, "pennylane-lightning": 30, "pennylane-catalyst": 118},
        candidates={"pennylane": [], "pennylane-lightning": [], "pennylane-catalyst": []},
    )
    _, body = followup.issue([recorded(tmp_path, saved=saved)])
    assert "Bisect: `PennyLane alone gives 41% of the step, so the cause is elsewhere.`" in body
    assert "candidate pull requests: 30 in Lightning, 118 in Catalyst" in body


def test_steps_that_didnt_hold_are_counted_but_not_listed(tmp_path):
    held = recorded(tmp_path / "a", saved=explanation())
    gone = followup.record_one(
        FollowUp(dev(1), dev(2), "qpe/lightning", "instructions"),
        came_back(tmp_path / "b", 1000, 1001),
        FIRST,
    )
    title, body = followup.issue([held, gone])
    assert "1 more didn't hold" in body


# What the page shows.


def test_the_page_takes_the_confirmation_and_cause_from_a_follow_up(tmp_path):
    record = recorded(tmp_path, saved=explanation())
    steps = followup.apply(history.find_steps(FIRST), [record])
    assert [(s.confirmed, round(s.second_change_pct)) for s in steps] == [(True, 99)]
    entries = followup.explanations([record])
    assert entries[STEP.key]["text"].startswith("Port QFT to Operator2")
    assert entries[STEP.key]["url"] == URL


def test_a_follow_up_never_overrides_a_second_measurement_already_made(tmp_path):
    record = recorded(tmp_path, 1000, 1001)
    step = history.find_steps(FIRST)[0]
    already = history.confirm_steps([step], FIRST)
    assert followup.apply(already, [record])[0].confirmed is True
