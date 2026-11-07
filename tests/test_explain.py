import io
import tarfile

import pytest

from pennywatch import explain, upstream
from pennywatch.explain import Side, bisect_commits, rank_candidates


def _commits(count):
    return [f"c{index}" for index in range(count)]


def _step_at(position, low=100.0, high=200.0):
    return lambda commit: high if int(commit[1:]) >= position else low


@pytest.mark.parametrize("position", [0, 1, 7, 15, 31])
def test_bisect_finds_the_commit_wherever_it_is(position):
    result = bisect_commits(_commits(32), 100.0, 200.0, _step_at(position))
    assert result["found"] == f"c{position}"
    assert result["after"] == 200.0
    assert result["share_of_step"] == pytest.approx(1.0)


def test_bisect_works_for_a_step_down():
    result = bisect_commits(_commits(16), 200.0, 100.0, _step_at(5, low=200.0, high=100.0))
    assert result["found"] == "c5"


def test_bisect_needs_few_measurements():
    seen = []

    def measure(commit):
        seen.append(commit)
        return _step_at(700)(commit)

    bisect_commits(_commits(1024), 100.0, 200.0, measure)
    assert len(seen) <= 12


def test_bisect_steps_around_commits_that_cannot_be_measured():
    broken = {"c15", "c16", "c8"}

    def measure(commit):
        return None if commit in broken else _step_at(20)(commit)

    assert bisect_commits(_commits(32), 100.0, 200.0, measure)["found"] == "c20"


def test_when_the_step_is_among_broken_commits_it_returns_the_range_not_a_guess():
    def measure(commit):
        return None if commit in {"c4", "c5", "c6"} else _step_at(5)(commit)

    result = bisect_commits(_commits(8), 100.0, 200.0, measure)
    assert result["found"] is None
    assert result["range"] == ["c4", "c5", "c6", "c7"]


def test_when_nothing_else_can_be_measured_it_returns_the_range():
    def measure(commit):
        return 200.0 if commit == "c3" else None

    result = bisect_commits(_commits(4), 100.0, 200.0, measure)
    assert result["found"] is None
    assert result["range"] == ["c0", "c1", "c2", "c3"]


def test_a_step_split_over_two_commits_reports_the_share_of_the_one_it_found():
    def measure(commit):
        index = int(commit[1:])
        return 100.0 + (60.0 if index >= 3 else 0.0) + (40.0 if index >= 6 else 0.0)

    result = bisect_commits(_commits(8), 100.0, 200.0, measure)
    assert result["found"] == "c3"
    assert result["share_of_step"] == pytest.approx(0.6)


def _staircase(steps, low=100.0):
    """A metric that moves at these commits, by these amounts."""

    def measure(commit):
        index = int(commit[1:])
        return low + sum(amount for position, amount in steps.items() if index >= position)

    return measure


def test_every_commit_that_moved_a_step_is_found_largest_first():
    measure = _staircase({3: 30.0, 9: 50.0, 20: 20.0})
    result = explain.bisect_all(_commits(32), 100.0, 200.0, measure)
    assert [(c["commit"], c["before"], c["after"]) for c in result["culprits"]] == [
        ("c9", 130.0, 180.0),
        ("c3", 100.0, 130.0),
        ("c20", 180.0, 200.0),
    ]
    assert result["unresolved"] == []


def test_a_commit_too_small_to_matter_is_not_chased():
    measure = _staircase({3: 98.0, 9: 0.5})
    result = explain.bisect_all(_commits(16), 100.0, 198.5, measure)
    assert [c["commit"] for c in result["culprits"]] == ["c3"]


def test_commits_are_measured_only_once():
    seen = []
    staircase = _staircase({3: 30.0, 9: 70.0})

    def measure(commit):
        seen.append(commit)
        return staircase(commit)

    explain.bisect_all(_commits(32), 100.0, 200.0, measure)
    assert len(seen) == len(set(seen))
    assert len(seen) <= 16


def test_the_search_stops_at_the_limit():
    measure = _staircase({position: 10.0 for position in range(0, 20, 2)})
    result = explain.bisect_all(_commits(20), 100.0, 200.0, measure, max_found=3)
    assert len(result["culprits"]) == 3


def test_a_bisect_with_a_budget_stops_and_keeps_what_it_found():
    seen = []
    staircase = _staircase({3: 30.0, 9: 50.0, 20: 20.0})

    def measure(commit):
        seen.append(commit)
        return staircase(commit)

    result = explain.bisect_all(_commits(32), 100.0, 200.0, measure, max_measured=7)
    assert len(seen) == 7
    assert result["stopped"] is True
    assert result["measured"] == 7
    assert [c["commit"] for c in result["culprits"]] == ["c9"]
    assert result["unresolved"]


def test_a_bisect_within_its_budget_says_it_did_not_stop():
    result = explain.bisect_all(_commits(16), 100.0, 200.0, _staircase({5: 100.0}), max_measured=50)
    assert result["stopped"] is False


def test_a_part_among_broken_commits_is_reported_as_unresolved():
    staircase = _staircase({2: 50.0, 10: 50.0})

    def measure(commit):
        return None if commit in {"c9", "c10"} else staircase(commit)

    result = explain.bisect_all(_commits(16), 100.0, 200.0, measure)
    assert [c["commit"] for c in result["culprits"]] == ["c2"]
    # c11 is the first that can be measured after the move, so any of the three may have done it.
    assert result["unresolved"] == [["c9", "c10", "c11"]]


def _merged(commit, files):
    return upstream.Merged("pennylane", commit, commit, None, "u", tuple(files))


def test_candidates_touching_the_code_that_moved_come_first():
    merged = [
        _merged("unrelated", ["doc/changelog.md"]),
        _merged("small", ["pennylane/b.py"]),
        _merged("big", ["pennylane/a.py", "tests/test_a.py"]),
    ]
    moved = [["pennylane/a.py:apply", 100, 900], ["pennylane/b.py:other", 10, 20]]
    ranked = rank_candidates(merged, moved)
    assert [change.commit for _, change in ranked] == ["big", "small", "unrelated"]
    assert ranked[0][0] == 800


def test_candidates_nothing_points_to_keep_merge_order():
    merged = [_merged("first", ["x.py"]), _merged("second", ["y.py"])]
    assert [change.commit for _, change in rank_candidates(merged, [])] == ["first", "second"]


def test_call_changes_come_largest_first_and_skip_what_did_not_move():
    assert explain.call_changes({"a": 1, "b": 5, "c": 9}, {"a": 2, "b": 5, "c": 1}, 5) == [
        ["c", 9, 1],
        ["a", 1, 2],
    ]


def test_calls_written_by_the_code_under_test_are_cleaned_before_use():
    sentinel = {"calls": {"f": 3, "g": "many", "h": True, "x" * 400: 2}}
    calls = explain._usable_calls(sentinel)
    assert calls["f"] == 3
    assert "g" not in calls
    assert "h" not in calls
    assert max(len(name) for name in calls) == explain.untrusted.NAME_LIMIT
    assert explain._usable_calls({"calls": "nope"}) == {}
    assert explain._usable_calls(None) == {}


def _archive(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_only_the_package_comes_out_of_an_archive(tmp_path):
    archive = _archive(
        [
            ("pennylane-abc/pennylane/__init__.py", b"x = 1", tarfile.REGTYPE),
            ("pennylane-abc/tests/test.py", b"y", tarfile.REGTYPE),
            ("pennylane-abc/setup.py", b"z", tarfile.REGTYPE),
            ("pennylane-abc/pennylane/../../escape.py", b"bad", tarfile.REGTYPE),
            ("pennylane-abc/pennylane/link.py", b"", tarfile.SYMTYPE),
        ]
    )
    explain.unpack_package(archive, tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] == [
        "pennylane/__init__.py"
    ]
    assert not (tmp_path.parent / "escape.py").exists()


def test_an_archive_that_unpacks_too_large_is_refused_early(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "UNPACKED_LIMIT_BYTES", 10)
    archive = _archive([("p-abc/pennylane/big.py", b"x" * 100, tarfile.REGTYPE)])
    with pytest.raises(explain.BadArchive, match="more than expected"):
        explain.unpack_package(archive, tmp_path)
    assert not list(tmp_path.rglob("*.py"))


def test_an_archive_with_too_many_files_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "ARCHIVE_MEMBERS_LIMIT", 1)
    archive = _archive(
        [
            ("p-abc/pennylane/a.py", b"a", tarfile.REGTYPE),
            ("p-abc/pennylane/b.py", b"b", tarfile.REGTYPE),
        ]
    )
    with pytest.raises(explain.BadArchive, match="too many"):
        explain.unpack_package(archive, tmp_path)


def test_an_overlay_with_extras_is_kept_apart_from_one_without(tmp_path, monkeypatch):
    monkeypatch.setattr(explain.stacks, "cache_dir", lambda: tmp_path)
    plain = tmp_path / "overlays" / "abc"
    extended = tmp_path / "overlays" / "abc+toml+tomlkit"
    plain.mkdir(parents=True)
    extended.mkdir()
    assert explain.fetch_overlay("abc") == plain
    assert explain.fetch_overlay("abc", ("tomlkit", "toml")) == extended


def _side(stack, runs, calls=None, functions=None):
    return Side(stack, runs, functions or {}, calls or {})


IMPORT_TIMES = """import time: self [us] | cumulative | imported package
import time:       100 |        100 |     numpy.core
import time:       200 |        300 |   numpy
import time:        50 |         50 |     scipy.stats._distn
import time:      1000 |       1050 |   scipy.stats
import time:        70 |       1420 | pennylane.transforms.split
import time:         5 |         5 |     json
import time:        30 |        35 |   pennylane.ops
import time:        40 |      1495 | pennylane
"""


def test_an_outside_package_is_charged_to_the_file_that_imported_it():
    costs = explain.import_costs(IMPORT_TIMES)
    assert costs["pennylane/transforms/split.py"] == 70 + 300 + 1050
    assert costs["pennylane/ops.py"] == 30 + 5
    assert costs["pennylane.py"] == 40


def test_an_outside_package_imported_by_another_outside_package_is_not_counted_twice():
    costs = explain.import_costs(IMPORT_TIMES)
    assert sum(costs.values()) == 1495


def test_lines_that_are_not_import_times_are_ignored():
    assert explain.import_costs("hello\nimport time: nonsense\n") == {}


def test_import_times_rank_the_pull_request_that_touched_the_file():
    before = {"pennylane/transforms/split.py": 70, "pennylane/ops.py": 35}
    after = {"pennylane/transforms/split.py": 1500000, "pennylane/ops.py": 40}
    moved = [[path, old, after[path]] for path, old in before.items()]
    merged = [
        _merged("other", ["pennylane/ops.py"]),
        _merged("right", ["pennylane/transforms/split.py"]),
    ]
    assert rank_candidates(merged, moved)[0][1].commit == "right"


def test_an_imports_report_talks_about_files_and_not_calls():
    explanation = explain.Explanation(
        "qft/default",
        "imports",
        _side("a", [1000, 1001, 1002]),
        _side("b", [1100, 1101, 1102]),
        [],
        [["pennylane/transforms/split.py", 70, 1500000]],
        [],
        {},
    )
    assert "| File, with the packages it imports |" in explain.render(explanation)


def test_the_report_shows_the_numbers_candidates_and_bisect():
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("catalyst-0.14.1", [100, 110, 105]),
        _side("catalyst-0.15.0", [205, 200, 210]),
        ["numpy 2.4 → 2.5"],
        [["pennylane/a.py:apply", 1, 3]],
        [["_PyEval_EvalFrameDefault", 10, 20]],
        {
            "pennylane": [
                {
                    "score": 2,
                    "subject": "Change apply (#7)",
                    "url": "https://github.com/PennyLaneAI/pennylane/pull/7",
                }
            ]
        },
        {
            "culprits": [
                {
                    "commit": "abc",
                    "before": 105.0,
                    "after": 205.0,
                    "subject": "Change apply (#7)",
                    "url": "https://github.com/PennyLaneAI/pennylane/pull/7",
                }
            ],
            "unresolved": [["x", "y"]],
            "measured": 9,
            "pennylane_alone": 205.0,
            "pennylane_share": 1.0,
        },
    )
    text = explain.render(explanation)
    assert "105 → 205" in text
    assert "+95.24%" in text
    assert "numpy 2.4 → 2.5" in text
    assert "`pennylane/a.py:apply`" in text
    assert "`_PyEval_EvalFrameDefault`" in text
    assert "- Change apply (#7) (https://github.com/PennyLaneAI/pennylane/pull/7)" in text
    assert "after measuring 9 commits" in text
    assert "100% of that" in text
    assert "Somewhere among 2 commits" in text
    assert "touches code that moved: 2" in text


def test_a_change_within_the_noise_says_so():
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("a", [1000, 1010, 1005]),
        _side("b", [1002, 1012, 1007]),
        [],
        [],
        [],
        {},
    )
    assert "within the noise" in explain.render(explanation)


def test_a_clear_change_does_not_say_it_is_noise():
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("a", [1000, 1001, 1002]),
        _side("b", [1100, 1101, 1102]),
        [],
        [],
        [],
        {},
    )
    assert "within the noise" not in explain.render(explanation)


def test_a_bisect_that_stopped_says_why():
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("a", [1]),
        _side("b", [2]),
        [],
        [],
        [],
        {},
        {"verdict": "PennyLane alone gives 3% of the step"},
    )
    assert "**Bisect:** PennyLane alone gives 3%" in explain.render(explanation)


def test_saving_drops_the_bulky_tables_and_keeps_the_answer(tmp_path):
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("catalyst-0.14.1", [1, 2, 3], calls={"a": 1}, functions={"b": 2}),
        _side("catalyst-0.15.0", [4, 5, 6]),
        [],
        [],
        [],
        {},
    )
    path = explain.save(explanation, tmp_path)
    assert path.name == "0.14.1--0.15.0--qft-default--instructions.json"
    text = path.read_text()
    assert '"net": 2' in text
    assert '"calls"' not in text


def test_saving_keeps_the_likeliest_candidates_and_how_many_there_were(tmp_path):
    rows = [{"score": 0, "subject": f"s{i}", "url": "u", "files": ["a.py"]} for i in range(100)]
    explanation = explain.Explanation(
        "qft/default",
        "instructions",
        _side("a", [1]),
        _side("b", [2]),
        [],
        [],
        [],
        {"pennylane": rows},
    )
    saved = explanation.to_dict()
    assert saved["candidate_counts"] == {"pennylane": 100}
    assert len(saved["candidates"]["pennylane"]) == explain.SAVED_CANDIDATES
    assert "files" not in saved["candidates"]["pennylane"][0]
