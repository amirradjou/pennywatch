import pytest

from pennywatch import upstream
from pennywatch.upstream import REPOS, NotFound


def versions(*names):
    table = {f"c{index}": name for index, name in enumerate(names)}
    return list(table), table.get


def test_a_dev_build_is_found_among_rising_versions():
    commits, read = versions("0.46.0-dev140", "0.46.0-dev141", "0.46.0-dev142", "0.46.0-dev143")
    assert upstream.commit_of_dev_build("0.46.0.dev142", commits, read) == "c2"


def test_dev_numbers_compare_as_numbers_and_series_reset_them():
    commits, read = versions(
        "0.45.0-dev9", "0.45.0-rc0", "0.46.0-dev0", "0.46.0-dev9", "0.46.0-dev10"
    )
    assert upstream.commit_of_dev_build("0.46.0.dev10", commits, read) == "c4"
    assert upstream.commit_of_dev_build("0.46.0.dev0", commits, read) == "c2"


def test_binary_search_reads_only_a_few_commits():
    names = [f"0.46.0-dev{number}" for number in range(1024)]
    commits, table = versions(*names)
    reads = []

    def read(commit):
        reads.append(commit)
        return table(commit)

    assert upstream.commit_of_dev_build("0.46.0.dev700", commits, read) == "c700"
    assert len(reads) <= 12


def test_a_commit_with_no_readable_version_is_stepped_over():
    table = {"c0": "0.46.0-dev1", "c1": None, "c2": "0.46.0-dev2"}
    assert upstream.commit_of_dev_build("0.46.0.dev2", list(table), table.get) == "c2"


def test_a_build_the_history_lacks_is_an_error_not_the_nearest_build():
    commits, read = versions("0.46.0-dev1", "0.46.0-dev3")
    with pytest.raises(NotFound, match="0.46.0.dev2"):
        upstream.commit_of_dev_build("0.46.0.dev2", commits, read)
    with pytest.raises(NotFound):
        upstream.commit_of_dev_build("0.46.0.dev9", commits, read)


LOG = (
    "\0aaa\tFix adjoint (#10436)\n\npennylane/ops/a.py\ndoc/releases/changelog-dev.md\n"
    "\0bbb\t[no ci] bump nightly version to dev146\n\npennylane/_version.py\n"
    "\0ccc\tDirect push with no number\n\npennylane/b.py\n"
)


def test_the_log_gives_pull_requests_and_their_files_and_skips_version_bumps():
    merged = upstream.parse_log(REPOS["pennylane"], LOG)
    assert [m.commit for m in merged] == ["aaa", "ccc"]
    assert merged[0].pull_request == 10436
    assert merged[0].url == "https://github.com/PennyLaneAI/pennylane/pull/10436"
    assert merged[0].files == ("pennylane/ops/a.py", "doc/releases/changelog-dev.md")


def test_a_commit_with_no_pull_request_links_to_the_commit():
    merged = upstream.parse_log(REPOS["pennylane"], LOG)
    assert merged[1].pull_request is None
    assert merged[1].url == "https://github.com/PennyLaneAI/pennylane/commit/ccc"
