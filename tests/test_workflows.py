"""The checks on workflow files that a wrong edit would otherwise only reveal at night."""

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest

from pennywatch.suite import STABLE_STACKS

WORKFLOWS = sorted((Path(__file__).parents[1] / ".github" / "workflows").glob("*.yml"))


def test_the_history_workflow_measures_exactly_the_stable_stacks():
    text = next(path for path in WORKFLOWS if path.name == "history.yml").read_text()
    listed = re.search(r"stack: \[(.*?)\]", text)[1]
    assert tuple(item.strip(' "') for item in listed.split(",")) == STABLE_STACKS


@pytest.mark.parametrize("path", [p for p in WORKFLOWS if p.name != "ci.yml"], ids=lambda p: p.name)
def test_measuring_workflows_pin_every_action_by_sha(path):
    for action in re.findall(r"uses: (\S+)", path.read_text()):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_starts_with_a_read_only_token(path):
    assert re.search(r"^permissions:\n  contents: read$", path.read_text(), re.M)


def test_only_publish_and_pages_may_write():
    nightly = next(path for path in WORKFLOWS if path.name == "nightly.yml").read_text()
    assert nightly.count("contents: write") == 1
    assert nightly.index("contents: write") > nightly.index("  publish:")
    for path in WORKFLOWS:
        if path.name != "nightly.yml":
            assert "contents: write" not in path.read_text(), path.name


def test_a_branch_that_starts_one_measuring_workflow_starts_no_other():
    triggers = {}
    for path in WORKFLOWS:
        match = re.search(r'branches: \["(measure/[^"]+)"\]', path.read_text())
        if match:
            triggers[path.name] = match[1]
    assert set(triggers) == {"measure.yml", "history.yml", "nightly.yml"}
    for name, pattern in triggers.items():
        for other, branch in triggers.items():
            if other != name:
                assert not fnmatch(branch, pattern.replace("**", "*")), (name, other)


@pytest.mark.parametrize("name", ["history.yml", "nightly.yml"])
def test_published_results_always_use_several_memory_layouts(name):
    text = next(path for path in WORKFLOWS if path.name == name).read_text()
    assert "--repeats 3" in text
