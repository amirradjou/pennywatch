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


def _side(stack, runs, calls=None, functions=None):
    return Side(stack, runs, functions or {}, calls or {})


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
            "found": "abc",
            "before": 105.0,
            "after": 205.0,
            "share_of_step": 1.0,
            "subject": "Change apply (#7)",
            "url": "https://github.com/PennyLaneAI/pennylane/pull/7",
        },
    )
    text = explain.render(explanation)
    assert "105 → 205" in text
    assert "+95.24%" in text
    assert "numpy 2.4 → 2.5" in text
    assert "`pennylane/a.py:apply`" in text
    assert "`_PyEval_EvalFrameDefault`" in text
    assert "**Bisected to:** Change apply (#7)" in text
    assert "touches moved code: 2 calls" in text


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
