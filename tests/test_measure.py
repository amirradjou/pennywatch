import subprocess
import sys
import time

import pytest

from pennywatch import measure
from pennywatch.cachegrind import Counts
from pennywatch.measure import Job, Outcome
from pennywatch.suite import SUITES, Cell

QFT = Cell("qft", "lightning", 18, 1)
QJIT = Cell("qft", "qjit", 18, 1)
RIGHT_ANSWER = [(-1.0) ** ((i + 1) % 2) for i in range(18)]


def full_job(cell=QFT, repeat=0):
    return Job("full", cell.backend, cell.wires, cell.circuit, cell.rounds, repeat=repeat)


def test_canary_is_one_percent_of_the_device_gates():
    assert measure.canary_gates(721) == 7
    assert measure.canary_gates(280) == 3


def test_canary_always_adds_at_least_one_gate():
    assert measure.canary_gates(10) == 1


def test_every_cell_gets_repeats_and_one_canary():
    jobs = measure.plan_cell_jobs(QFT, repeats=3, device_gates=721)
    assert [job.role for job in jobs] == ["full", "full", "full", "canary"]
    assert jobs[-1].extra_gates == 7


def test_only_qjit_measures_compile_separately():
    jobs = measure.plan_cell_jobs(QJIT, repeats=2, device_gates=721)
    assert [job.role for job in jobs].count("compile") == 2
    assert not any(j.role == "compile" for j in measure.plan_cell_jobs(QFT, 2, 721))


def test_baselines_are_per_backend_and_size_not_per_circuit():
    cells = [QFT, Cell("grover", "lightning", 18, 12), QJIT]
    jobs = measure.plan_baselines(cells, repeats=2)
    assert {(job.backend, job.wires) for job in jobs} == {("lightning", 18), ("qjit", 18)}
    assert len(jobs) == 4


def test_job_labels_are_unique_across_the_suite():
    labels = []
    for cell in SUITES["m0"]:
        labels += [job.label for job in measure.plan_cell_jobs(cell, 3, 700)]
        labels.append(measure.native_job(cell).label)
    labels += [job.label for job in measure.plan_baselines(SUITES["m0"], 3)]
    assert len(labels) == len(set(labels))


def test_a_clean_exit_without_a_sentinel_is_not_a_measurement():
    outcome = Outcome(full_job(), counts=Counts(python=5))
    assert "no sentinel" in measure.validate_outcome(outcome)


def test_a_wrong_answer_is_not_a_measurement():
    wrong = [0.0] * 18
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel={"result": wrong})
    assert "wrong answer" in measure.validate_outcome(outcome)


def test_a_right_answer_is_a_measurement():
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel={"result": RIGHT_ANSWER})
    assert measure.validate_outcome(outcome) is None


def test_a_full_run_without_a_result_is_not_a_measurement():
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel={"versions": {}})
    assert "no result" in measure.validate_outcome(outcome)


def test_baseline_needs_no_answer():
    outcome = Outcome(Job("imports", "lightning", 18), counts=Counts(python=5), sentinel={})
    assert measure.validate_outcome(outcome) is None


def test_the_error_comes_first():
    outcome = Outcome(full_job(), error="exit 1: boom")
    assert measure.validate_outcome(outcome) == "exit 1: boom"


def test_run_on_slots_never_shares_a_cpu_and_keeps_order():
    jobs = [full_job(repeat=repeat) for repeat in range(8)]
    in_use = set()

    def execute(job, cpu):
        assert cpu not in in_use
        in_use.add(cpu)
        time.sleep(0.01)  # long enough for the two workers to overlap
        in_use.remove(cpu)
        return Outcome(job, counts=Counts(python=job.repeat), sentinel={"result": RIGHT_ANSWER})

    outcomes = measure.run_on_slots(jobs, [0, 1], execute)
    assert list(outcomes) == jobs
    assert [outcomes[job].counts.python for job in jobs] == list(range(8))


def test_one_exploding_job_does_not_lose_the_others():
    jobs = [full_job(repeat=repeat) for repeat in range(3)]

    def execute(job, cpu):
        if job.repeat == 1:
            raise RuntimeError("boom")
        return Outcome(job, counts=Counts(python=1), sentinel={"result": RIGHT_ANSWER})

    outcomes = measure.run_on_slots(jobs, [0], execute)
    assert "boom" in outcomes[jobs[1]].error
    assert outcomes[jobs[2]].counts == Counts(python=1)


def _cell_outcomes(cell, gross=(1000, 1001, 1000), canary=1100):
    jobs = measure.plan_cell_jobs(cell, repeats=len(gross), device_gates=700)
    outcomes = {}
    for job in jobs:
        total = canary if job.role == "canary" else gross[job.repeat]
        outcomes[job] = Outcome(job, counts=Counts(python=total), sentinel={})
    return jobs, outcomes


def test_summary_nets_out_the_imports_and_judges_the_canary():
    baselines = [Job("imports", "lightning", 18, repeat=r) for r in range(3)]
    jobs, outcomes = _cell_outcomes(QFT)
    for job in baselines:
        outcomes[job] = Outcome(job, counts=Counts(python=100), sentinel={})
    native = Outcome(measure.native_job(QFT), sentinel={"gates": {"device": {"gates": 700}}})

    summary = measure.summarise_cell(QFT, outcomes, jobs, baselines, native)

    assert summary["instructions"]["net"] == 900
    assert summary["instructions"]["spread_pct"] == pytest.approx(1 / 900 * 100)
    assert summary["canary"]["detected"]
    assert summary["gates"] == {"device": {"gates": 700}}
    assert "compile" not in summary


def test_a_version_other_than_the_locked_one_is_a_problem():
    job = Job("imports", "lightning", 18)
    outcome = Outcome(job, counts=Counts(python=1), sentinel={"versions": {"pennylane": "9.9"}})
    problems = measure.problems_with({job: outcome}, {"pennylane": "0.45.1"})
    assert "not the locked version" in problems[0]


def test_dev_version_spellings_count_as_the_same_version():
    job = Job("imports", "lightning", 18)
    versions = {"pennylane": "0.46.0-dev142"}
    outcome = Outcome(job, counts=Counts(python=1), sentinel={"versions": versions})
    assert measure.problems_with({job: outcome}, {"pennylane": "0.46.0.dev142"}) == []


def test_a_result_of_the_wrong_type_is_not_a_measurement():
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel={"result": "1.0"})
    assert "malformed" in measure.validate_outcome(outcome)


def test_a_result_holding_non_numbers_is_not_a_measurement():
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel={"result": [True, "x"]})
    assert "malformed" in measure.validate_outcome(outcome)


def test_malformed_versions_are_not_a_measurement():
    sentinel = {"result": RIGHT_ANSWER, "versions": "0.45.1"}
    outcome = Outcome(full_job(), counts=Counts(python=5), sentinel=sentinel)
    assert "versions" in measure.validate_outcome(outcome)


def test_only_the_tail_of_a_flood_of_output_is_kept():
    code = "import sys; sys.stdout.write('x' * 2_000_000 + 'the end'); sys.exit(3)"
    returncode, tail = measure.run_keeping_tail([sys.executable, "-c", code], timeout=30)
    assert returncode == 3
    assert tail.endswith("the end")
    assert len(tail) == measure.OUTPUT_TAIL_BYTES


def test_a_command_that_outlives_its_timeout_is_killed():
    code = "import time; time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        measure.run_keeping_tail([sys.executable, "-c", code], timeout=0.2)
