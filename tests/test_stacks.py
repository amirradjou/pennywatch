import subprocess
import tomllib
import urllib.error

import pytest

from pennywatch import stacks
from pennywatch.stacks import (
    COMPILE_BYTECODE,
    Stack,
    exact_pins,
    locked_versions,
    project_file,
)

DEV_REQUIRES = [
    "pennylane==0.46.0-dev142",
    "pennylane-lightning==0.46.0-dev34",
    "pennylane-lightning-kokkos==0.46.0-dev34",
    "jax==0.7.1",
    "numpy>2.0.0",
    "scipy-openblas32<0.3.33,>=0.3.26",
]


def test_exact_pins_keep_only_exact_requirements_and_normalise_dev_versions():
    assert exact_pins(DEV_REQUIRES) == {
        "pennylane": "0.46.0.dev142",
        "pennylane-lightning": "0.46.0.dev34",
        "pennylane-lightning-kokkos": "0.46.0.dev34",
        "jax": "0.7.1",
    }


def test_a_dev_version_means_testpypi():
    assert Stack("0.16.0.dev134").is_dev
    assert not Stack("0.15.0").is_dev


def test_stable_stack_uses_only_pypi():
    project = tomllib.loads(project_file(Stack("0.15.0"), {}))
    assert project["project"]["dependencies"] == [
        "pennylane-catalyst==0.15.0",
        "pennylane==0.45.*",
        "pennylane-lightning==0.45.*",
    ]
    assert "tool" not in project


def test_catalyst_0_10_goes_with_pennylane_0_40_whatever_its_metadata_says():
    assert stacks.pennylane_series("0.10.0") == "0.40"
    assert stacks.pennylane_series("0.8.1") == "0.38"


def _files(upload_time):
    return [{"upload_time_iso_8601": upload_time}]


def test_the_newest_of_a_series_compares_numbers_and_skips_withdrawn_and_dev_releases():
    releases = {
        "0.45.2": [],
        "0.45.1": _files("2026-06-01T00:00:00Z"),
        "0.45.0": _files("2026-05-01T00:00:00Z"),
        "0.45.10.dev1": _files("2026-09-01T00:00:00Z"),
        "0.450.0": _files("2026-10-01T00:00:00Z"),
    }
    assert stacks.newest_of_series(releases, "0.45") == releases["0.45.1"]
    releases["0.45.10"] = _files("2026-07-01T00:00:00Z")
    assert stacks.newest_of_series(releases, "0.45") == releases["0.45.10"]


def test_a_series_with_no_release_is_an_error():
    with pytest.raises(ValueError, match="series 0.99"):
        stacks.newest_of_series({"0.45.0": _files("2026-05-01T00:00:00Z")}, "0.99")


def test_a_stable_stack_is_resolved_as_of_the_last_upload_of_its_set(monkeypatch):
    releases = {
        "pennylane": {"0.45.1": _files("2026-06-01T10:00:00.5Z")},
        "pennylane-lightning": {"0.45.0": _files("2026-05-01T00:00:00Z")},
    }
    monkeypatch.setattr(
        stacks, "fetch_json", lambda url: {"releases": releases[url.split("/")[-2]]}
    )
    catalyst = _files("2026-05-12T00:00:00Z")
    assert stacks.as_of(Stack("0.15.0"), catalyst, {}) == "2026-06-01T10:00:01Z"


def test_a_dev_stack_is_resolved_as_of_the_last_upload_of_its_pins(monkeypatch):
    uploads = {
        "https://test.pypi.org/pypi/pennylane/0.46.0.dev142/json": "2026-11-01T00:00:00Z",
        "https://test.pypi.org/pypi/pennylane-lightning/0.46.0.dev34/json": "2026-11-02T00:00:00Z",
    }
    monkeypatch.setattr(stacks, "fetch_json", lambda url: {"urls": _files(uploads[url])})
    pins = {"pennylane": "0.46.0.dev142", "pennylane-lightning": "0.46.0.dev34"}
    catalyst = _files("2026-11-03T00:00:00Z")
    assert stacks.as_of(Stack("0.16.0.dev134"), catalyst, pins) == "2026-11-03T00:00:01Z"


def test_only_the_three_named_packages_may_come_from_testpypi():
    project = tomllib.loads(project_file(Stack("0.16.0.dev134"), exact_pins(DEV_REQUIRES)))
    assert set(project["tool"]["uv"]["sources"]) == {
        "pennylane",
        "pennylane-lightning",
        "pennylane-catalyst",
    }
    index = project["tool"]["uv"]["index"][0]
    assert index["explicit"] is True
    assert index["url"] == "https://test.pypi.org/simple/"


def test_dev_stack_names_catalysts_pins_and_drops_kokkos():
    project = tomllib.loads(project_file(Stack("0.16.0.dev134"), exact_pins(DEV_REQUIRES)))
    assert project["project"]["dependencies"] == [
        "pennylane-catalyst==0.16.0.dev134",
        "pennylane==0.46.0.dev142",
        "pennylane-lightning==0.46.0.dev34",
    ]
    assert any("kokkos" in item for item in project["tool"]["uv"]["override-dependencies"])
    assert not any("kokkos" in item for item in project["project"]["dependencies"])


def test_locked_versions(tmp_path):
    lock = tmp_path / "pylock.toml"
    lock.write_text(
        '[[packages]]\nname = "a"\nversion = "1"\n[[packages]]\nname = "b"\nversion = "2"\n'
    )
    assert locked_versions(lock) == {"a": "1", "b": "2"}


def test_compiling_bytecode_cannot_run_a_wheels_pth_file():
    # -S stops site from processing .pth files, which is the only way a wheel runs code at install.
    assert "-S" in COMPILE_BYTECODE


def _http_error(code):
    return urllib.error.HTTPError("https://test.pypi.org/x", code, "no", {}, None)


def test_a_build_that_left_the_index_can_never_be_measured(monkeypatch):
    def gone(url):
        raise _http_error(404)

    monkeypatch.setattr(stacks, "fetch_json", gone)
    with pytest.raises(stacks.StackUnavailable, match="not on the index"):
        stacks.write_lock(Stack("0.16.0.dev134"), "image")


def test_a_server_error_is_not_mistaken_for_a_missing_build(monkeypatch):
    def broken(url):
        raise _http_error(503)

    monkeypatch.setattr(stacks, "fetch_json", broken)
    with pytest.raises(urllib.error.HTTPError):
        stacks.write_lock(Stack("0.16.0.dev134"), "image")


def test_a_dev_build_that_pins_nothing_can_never_be_measured(monkeypatch):
    monkeypatch.setattr(
        stacks,
        "fetch_json",
        lambda url: {"info": {"requires_dist": []}, "urls": _files("2026-11-03T00:00:00Z")},
    )
    with pytest.raises(stacks.StackUnavailable, match="doesn't pin"):
        stacks.write_lock(Stack("0.16.0.dev134"), "image")


def _lock_with_uv_failing(monkeypatch, stderr):
    monkeypatch.setattr(
        stacks,
        "fetch_json",
        lambda url: {
            "info": {"requires_dist": DEV_REQUIRES},
            "urls": _files("2026-11-03T00:00:00Z"),
        },
    )
    monkeypatch.setattr(stacks, "as_of", lambda *args: "2026-11-03T00:00:01Z")

    def fail(*args):
        raise subprocess.CalledProcessError(1, "uv", stderr=stderr)

    monkeypatch.setattr(stacks, "_uv", fail)
    stacks.write_lock(Stack("0.16.0.dev134"), "image")


def test_packages_that_cannot_go_together_can_never_be_measured(monkeypatch):
    with pytest.raises(stacks.StackUnavailable, match="installed together"):
        _lock_with_uv_failing(monkeypatch, "  x No solution found when resolving dependencies")


def test_any_other_uv_failure_is_tried_again_another_day(monkeypatch):
    with pytest.raises(subprocess.CalledProcessError):
        _lock_with_uv_failing(monkeypatch, "error: connection reset")
