import json
import os

import pytest

from pennywatch import publish
from pennywatch.publish import Rejected, accept, parse_lock, parse_result

VERSION = "0.16.0.dev134"
GOOD_LOCK = b"""
lock-version = "1.0"
[[packages]]
name = "pennylane-catalyst"
version = "0.16.0.dev134"
wheels = [{ url = "https://test-files.pythonhosted.org/a.whl", hashes = { sha256 = "00" } }]
[[packages]]
name = "numpy"
version = "2.5.4"
wheels = [{ url = "https://files.pythonhosted.org/b.whl", hashes = { sha256 = "00" } }]
"""


def good_result(**changes):
    result = {
        "schema": 2,
        "stack": {"id": f"catalyst-{VERSION}", "pennylane-catalyst": VERSION},
        "valid": True,
        "problems": [],
        "cells": [
            {
                "name": "qft/qjit",
                "instructions": {"net": 100, "runs": [100]},
                "gates": {"device": {"gates": 7, "depth": 3}, "qjit": {"unavailable": "no specs"}},
            }
        ],
    }
    return result | changes


def encode(result):
    return json.dumps(result).encode()


def test_a_good_result_passes_unchanged():
    assert parse_result(encode(good_result())) == good_result()


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"[1]", "not an object"),
        (b"{not json", "not JSON"),
        (b'{"a": NaN}', "not a number"),
        (b'{"a": Infinity}', "not a number"),
        (encode(good_result(valid="yes")), "verdict"),
        (encode(good_result(valid=True, cells=[])), "nothing"),
        (encode(good_result(cells="x")), "no cells"),
        (encode(good_result(stack={"id": "x", "pennylane-catalyst": VERSION})), "doesn't match"),
        (
            encode(good_result(stack={"id": "catalyst-../../x", "pennylane-catalyst": "../../x"})),
            "not a version",
        ),
        (encode(good_result(cells=[{"name": "<script>"}])), "name"),
        (encode(good_result(cells=[{"name": "a/b", "instructions": {"net": "9"}}])), "count"),
        (encode(good_result(cells=[{"name": "a/b", "instructions": {"net": True}}])), "count"),
        (encode(good_result(cells=[{"name": "a/b", "instructions": {"net": -1}}])), "count"),
        (encode(good_result(cells=[{"name": "a/b", "gates": {"top": {"gates": 1.5}}}])), "count"),
        (encode(good_result(cells=[{"name": "a/b", "gates": {"top": 1}}])), "table"),
        (encode(good_result(note="\x1b[31mred")), "unexpected characters"),
        (encode(good_result(cells=[{"name": "a/b\n::set-output"}])), "name"),
        (
            encode(good_result(cells=[{"name": "a/b", "gates": {"x|y": {"gates": 1}}}])),
            "unknown level",
        ),
        (encode(good_result(memory=[{"backend": "evil", "wires": 1}])), "unknown backend"),
        (encode(good_result(memory=[{"backend": "qjit"}])), "without a size"),
        (
            encode(
                good_result(memory=[{"backend": "qjit", "wires": 1, "max_rss_kb": {"net": "9"}}])
            ),
            "without a count",
        ),
        (encode(good_result(memory="x")), "isn't a list"),
        (encode(good_result(note="x" * 3000)), "too long"),
    ],
)
def test_a_result_of_the_wrong_shape_is_refused(data, reason):
    with pytest.raises(Rejected, match=reason):
        parse_result(data)


def test_a_huge_result_is_refused_before_parsing():
    with pytest.raises(Rejected, match="too large"):
        parse_result(b" " * (publish.MAX_RESULT_BYTES + 1))


def test_deep_nesting_is_refused():
    nested = {}
    for _ in range(20):
        nested = {"a": nested}
    with pytest.raises(Rejected, match="deeply"):
        parse_result(encode(good_result(note=nested)))


def test_a_rejected_run_may_have_no_cells():
    assert parse_result(encode(good_result(valid=False, cells=[])))["valid"] is False


def test_a_good_lock_passes():
    assert parse_lock(GOOD_LOCK)["packages"][0]["name"] == "pennylane-catalyst"


@pytest.mark.parametrize(
    ("lock", "reason"),
    [
        (b"not toml =", "not a lock"),
        (b"packages = []", "no packages"),
        (
            b'[[packages]]\nname = "numpy"\nwheels = '
            b'[{url = "https://test-files.pythonhosted.org/n.whl"}]',
            "a wheel from test-files",
        ),
        (
            b'[[packages]]\nname = "pennylane"\nwheels = [{url = "https://evil.example/p.whl"}]',
            "a wheel from evil",
        ),
        (b'[[packages]]\nname = "numpy"\nsdist = {url = "x"}', "only wheels"),
    ],
)
def test_a_lock_that_breaks_the_rules_is_refused(lock, reason):
    with pytest.raises(Rejected, match=reason):
        parse_lock(lock)


def artifact(tmp_path, result, lock=GOOD_LOCK, name=None):
    folder = tmp_path / "artifacts" / "one"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name or f"catalyst-{VERSION}.json")).write_bytes(encode(result))
    if lock is not None:
        (folder / f"catalyst-{VERSION}.pylock.toml").write_bytes(lock)
    return tmp_path / "artifacts"


def test_accepting_writes_the_result_and_its_lock(tmp_path):
    data = tmp_path / "data"
    outcomes = accept(artifact(tmp_path, good_result()), data)
    assert outcomes == [f"- {VERSION}: accepted"]
    assert json.loads((data / "nightly" / f"catalyst-{VERSION}.json").read_text()) == good_result()
    assert (data / "stacks" / f"catalyst-{VERSION}.pylock.toml").read_bytes() == GOOD_LOCK


def test_the_path_comes_from_the_contents_and_not_the_file_name(tmp_path):
    data = tmp_path / "data"
    accept(artifact(tmp_path, good_result(), name="catalyst-evil.json"), data)
    assert [path.name for path in (data / "nightly").glob("*.json")] == [f"catalyst-{VERSION}.json"]


def test_a_rejected_run_is_recorded_apart_from_the_results(tmp_path):
    data = tmp_path / "data"
    rejected = good_result(valid=False, problems=["the canary was not detected"])
    accept(artifact(tmp_path, rejected, name=f"catalyst-{VERSION}.rejected.json"), data)
    assert not list((data / "nightly").glob("*.json"))
    assert (data / "nightly" / "rejected" / f"catalyst-{VERSION}.json").exists()
    assert not (data / "stacks").exists()


def test_a_valid_result_with_a_bad_lock_is_only_a_rejection(tmp_path):
    data = tmp_path / "data"
    bad = b'[[packages]]\nname = "numpy"\nwheels = [{url = "https://evil.example/n.whl"}]'
    outcomes = accept(artifact(tmp_path, good_result(), lock=bad), data)
    assert outcomes == [f"- {VERSION}: recorded as rejected"]
    saved = json.loads((data / "nightly" / "rejected" / f"catalyst-{VERSION}.json").read_text())
    assert "evil.example" in saved["problems"][0]
    assert saved["valid"] is False


def test_a_valid_result_with_no_lock_is_only_a_rejection(tmp_path):
    data = tmp_path / "data"
    assert accept(artifact(tmp_path, good_result(), lock=None), data) == [
        f"- {VERSION}: recorded as rejected"
    ]


def test_garbage_is_refused_and_nothing_is_written(tmp_path):
    folder = tmp_path / "artifacts"
    folder.mkdir()
    (folder / "catalyst-x.json").write_bytes(b"{")
    data = tmp_path / "data"
    assert "refused" in accept(folder, data)[0]
    assert not data.exists()


def test_a_symlink_is_never_followed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text(json.dumps(good_result()))
    folder = tmp_path / "artifacts"
    folder.mkdir()
    os.symlink(secret, folder / f"catalyst-{VERSION}.json")
    data = tmp_path / "data"
    assert "not a plain file" in accept(folder, data)[0]
    assert not data.exists()


def test_a_build_that_was_not_planned_is_refused(tmp_path):
    data = tmp_path / "data"
    outcomes = accept(artifact(tmp_path, good_result()), data, planned={"0.16.0.dev1"})
    assert "not one of the builds planned" in outcomes[0]
    assert not data.exists()


def test_a_planned_build_is_accepted(tmp_path):
    data = tmp_path / "data"
    assert "accepted" in accept(artifact(tmp_path, good_result()), data, planned={VERSION})[0]


def test_a_file_name_cannot_issue_a_command_to_the_runner(tmp_path):
    folder = tmp_path / "artifacts"
    folder.mkdir()
    (folder / "catalyst-\n::add-mask::x.json").write_bytes(b"{")
    line = accept(folder, tmp_path / "data")[0]
    assert line.startswith("- ")
    assert "\n" not in line


def test_done_builds_include_the_rejected_ones(tmp_path):
    (tmp_path / "nightly" / "rejected").mkdir(parents=True)
    (tmp_path / "nightly" / "catalyst-0.16.0.dev1.json").write_text("{}")
    (tmp_path / "nightly" / "rejected" / "catalyst-0.16.0.dev2.json").write_text("{}")
    assert publish.done_builds(tmp_path) == {"0.16.0.dev1", "0.16.0.dev2"}


def test_done_builds_of_nothing_is_nothing(tmp_path):
    assert publish.done_builds(tmp_path) == set()


def kokkos_result(**changes):
    result = good_result(part="kokkos")
    result["cells"] = [
        {"name": "qft/kokkos", "instructions": {"net": 100}, "gates": {"device": {"gates": 7}}}
    ]
    return result | changes


def test_a_kokkos_part_is_stored_beside_the_main_result(tmp_path):
    data = tmp_path / "data"
    accept(artifact(tmp_path / "a", good_result()), data)
    outcomes = accept(artifact(tmp_path / "b", kokkos_result()), data)
    assert outcomes == [f"- {VERSION} (kokkos): accepted"]
    assert (data / "nightly" / f"catalyst-{VERSION}.json").exists()
    assert json.loads((data / "nightly" / f"catalyst-{VERSION}.kokkos.json").read_text())["part"]


def test_a_rejected_part_doesnt_make_its_build_done_or_missing(tmp_path):
    data = tmp_path / "data"
    accept(artifact(tmp_path, kokkos_result(valid=False, problems=["x"])), data)
    assert (data / "nightly" / "rejected" / f"catalyst-{VERSION}.kokkos.json").exists()
    assert publish.done_builds(data) == set()


@pytest.mark.parametrize(
    "changes",
    [
        {"part": "gpu"},
        {"part": None},
        {"memory": [{"backend": "lightning", "wires": 25, "max_rss_kb": {"net": 1}}]},
    ],
)
def test_a_part_holds_only_its_own_backend(changes):
    with pytest.raises(Rejected, match="part"):
        parse_result(encode(kokkos_result(**changes)))


def test_the_main_run_holds_no_part_s_backend():
    main = good_result()
    main["cells"] = kokkos_result()["cells"]
    with pytest.raises(Rejected, match="part"):
        parse_result(encode(main))
