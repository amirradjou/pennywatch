"""Checks against the real image and a real stack. Slow, so only with `uv run pytest -m docker`."""

import shutil
import tempfile
from pathlib import Path

import pytest

from pennywatch import container, measure, stacks
from pennywatch.suite import SUITES

pytestmark = pytest.mark.docker

CPUS = [0, 1]


@pytest.fixture(scope="module", params=["0.15.0", "0.16.0.dev134"])
def runner(request):
    image = container.ensure_image()
    stack = stacks.Stack(request.param)
    stack_dir = stacks.install(stack, image)
    workspace = Path(tempfile.mkdtemp(prefix="test-", dir=stacks.cache_dir()))
    workspace.chmod(0o755)
    yield measure.Runner(image, stack_dir, workspace, CPUS)
    shutil.rmtree(workspace, ignore_errors=True)


def test_every_small_circuit_gives_the_right_answer_on_every_backend(runner):
    jobs = [measure.native_job(cell) for cell in SUITES["smoke"]]
    outcomes = runner.run_all(jobs)
    assert {job.label: measure.validate_outcome(outcomes[job]) for job in jobs} == dict.fromkeys(
        (job.label for job in jobs), None
    )
    for job in jobs:
        gates = outcomes[job].sentinel["gates"]
        assert gates["device"]["gates"] >= gates["top"]["gates"] > 0
        if job.backend == "qjit":
            assert gates["qjit"]["gates"] > 0


def test_the_canary_keeps_the_expected_answer(runner):
    cell = SUITES["smoke"][0]
    job = measure.Job("native", cell.backend, cell.wires, cell.circuit, cell.rounds, extra_gates=5)
    outcome = runner.run_all([job])[job]
    assert measure.validate_outcome(outcome) is None
    assert outcome.sentinel["gates"]["device"]["gates"] > 5


def test_cachegrind_counts_the_compiler_and_the_link_step_separately(runner):
    job = measure.Job("compile", "qjit", 6, "qft", 1)
    counts = runner.run_all([job])[job].counts
    assert counts.python > 0
    assert counts.compiler > 0
    assert counts.linker > 0
    assert counts.total == counts.python + counts.compiler + counts.other


def test_a_process_that_dies_is_not_a_measurement(runner):
    job = measure.Job("full", "lightning", 6, "no-such-circuit", 1)
    outcome = runner.run_all([job])[job]
    assert outcome.error is not None
    assert measure.validate_outcome(outcome) is not None
