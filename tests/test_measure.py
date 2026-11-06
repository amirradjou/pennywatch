import subprocess
import sys
import time
from dataclasses import replace

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


def test_a_small_circuits_canary_is_still_large_enough_to_see():
    assert measure.canary_gates(10) == measure.CANARY_MIN_GATES


def test_every_cell_gets_repeats_and_one_canary():
    jobs = measure.plan_cell_jobs(QFT, repeats=3, device_gates=721)
    assert [job.role for job in jobs] == ["full", "full", "full", "canary"]
    assert jobs[-1].extra_gates == 7


def test_only_qjit_measures_compile_separately():
    jobs = measure.plan_cell_jobs(QJIT, repeats=2, device_gates=721)
    assert [job.role for job in jobs].count("compile") == 2
    assert not any(j.role == "compile" for j in measure.plan_cell_jobs(QFT, 2, 721))


def test_baselines_are_per_backend_at_its_largest_size():
    cells = [QFT, Cell("qpe", "lightning", 12, 3), QJIT]
    jobs = measure.plan_baselines(cells)
    assert [(job.backend, job.wires) for job in jobs] == [("lightning", 18), ("qjit", 18)]


def test_job_labels_are_unique_across_the_suite():
    labels = []
    for cell in SUITES["full"]:
        labels += [job.label for job in measure.plan_cell_jobs(cell, 3, 700)]
        labels.append(measure.native_job(cell).label)
    labels += [job.label for job in measure.plan_baselines(SUITES["full"])]
    labels += [job.label for job in measure.plan_memory(25, 3)]
    assert len(labels) == len(set(labels))


def test_memory_runs_once_per_repeat_on_every_backend_and_outside_valgrind():
    jobs = measure.plan_memory(25, 2)
    assert len(jobs) == 6
    assert {job.backend for job in jobs} == {"default", "lightning", "qjit"}
    assert {job.mode for job in jobs} == {"native"}
    assert "--timing-repeats=1" in measure.workload_args(jobs[0])


def test_a_suite_without_a_memory_size_measures_none():
    assert measure.plan_memory(None, 3) == []


def test_memory_is_summarised_per_backend_and_a_failed_run_is_left_out():
    jobs = measure.plan_memory(4, 3)[:3]
    good = {"result": [0.0] * 4, "max_rss_kb": 100}
    outcomes = {
        jobs[0]: Outcome(jobs[0], sentinel=good),
        jobs[1]: Outcome(jobs[1], sentinel=good | {"max_rss_kb": 110}),
        jobs[2]: Outcome(jobs[2], error="exit 137: killed"),
    }
    summary = measure.summarise_memory(outcomes)
    assert summary == [
        {
            "backend": "default",
            "wires": 4,
            "max_rss_kb": {
                "net": 105,
                "runs": [100, 110],
                "spread_pct": 10 / 105 * 100,
                "noise": 10,
            },
        }
    ]


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


def _cell_outcomes(cell, gross=(1000, 1001, 1000), canary=10_000_000):
    jobs = measure.plan_cell_jobs(cell, repeats=len(gross), device_gates=700)
    outcomes = {}
    for job in jobs:
        total = canary if job.role == "canary" else gross[job.repeat]
        outcomes[job] = Outcome(job, counts=Counts(python=total), sentinel={})
    return jobs, outcomes


def test_summary_reports_the_cost_and_judges_the_canary():
    baselines = [Job("imports", "lightning", 18, repeat=r) for r in range(3)]
    jobs, outcomes = _cell_outcomes(QFT)
    for job in baselines:
        outcomes[job] = Outcome(job, counts=Counts(python=100), sentinel={})
    native = Outcome(measure.native_job(QFT), sentinel={"gates": {"device": {"gates": 700}}})

    summary = measure.summarise_cell(QFT, outcomes, jobs, baselines, native)

    assert summary["instructions"]["net"] == 1000
    assert summary["instructions"]["spread_pct"] == pytest.approx(0.1)
    assert summary["imports"]["net"] == 100
    assert summary["canary"]["detected"]
    assert summary["gates"] == {"device": {"gates": 700}}
    assert "compile" not in summary


def test_summary_keeps_the_compilers_own_count_for_qjit():
    jobs, outcomes = _cell_outcomes(QJIT)
    for job in jobs:
        if job.role == "compile":
            outcomes[job] = Outcome(job, counts=Counts(python=500, compiler=70), sentinel={})
    native = Outcome(measure.native_job(QJIT), sentinel={})

    summary = measure.summarise_cell(QJIT, outcomes, jobs, [], native)

    assert summary["compile"]["net"] == 570
    assert summary["compile"]["compiler_process"] == [70, 70, 70]


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


def _git_repository(path):
    def git(*arguments):
        subprocess.run(
            ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (path / "code.py").write_text("x = 1\n")
    git("add", "code.py")
    git("commit", "-qm", "init", "--no-verify")


def test_an_earlier_runs_results_do_not_make_the_code_dirty(tmp_path):
    _git_repository(tmp_path)
    (tmp_path / "results.json").write_text("{}")
    assert measure.git_revision(tmp_path)["dirty"] is False


def test_a_change_to_tracked_code_makes_it_dirty(tmp_path):
    _git_repository(tmp_path)
    (tmp_path / "code.py").write_text("x = 2\n")
    assert measure.git_revision(tmp_path)["dirty"] is True


def test_each_repeat_of_a_cell_runs_under_its_own_layout(tmp_path):
    def padding(job):
        args = measure.container.sandbox_args(0, tmp_path, tmp_path, tmp_path, layout=job.repeat)
        return next(arg for arg in args if arg.startswith("PENNYWATCH_LAYOUT="))

    jobs = [full_job(repeat=repeat) for repeat in range(3)]
    assert len({padding(job) for job in jobs}) == 3


def test_a_layout_is_the_same_on_every_machine_and_every_night():
    assert measure.container.layout_padding(0) == "x" * 16
    assert measure.container.layout_padding(2) == "x" * 64


def _canary_outcomes(canary_counts):
    jobs = measure.plan_cell_jobs(QFT, repeats=3, device_gates=700)
    canary = next(job for job in jobs if job.role == "canary")
    jobs += [replace(canary, repeat=layout) for layout in range(1, 3)]
    base = {0: 1_000_000_000, 1: 1_100_000_000, 2: 1_200_000_000}
    outcomes = {}
    for job in jobs:
        total = canary_counts.get(job.repeat) if job.role == "canary" else base[job.repeat]
        counts = Counts(python=total) if total else None
        outcomes[job] = Outcome(job, counts=counts, sentinel={})
    return jobs, outcomes


def test_a_canary_that_was_seen_gets_no_retries():
    jobs, outcomes = _canary_outcomes({0: 1_010_000_000})
    first = [j for j in jobs if j.role != "canary" or j.repeat == 0]
    assert measure.canary_retries(QFT, first, outcomes, 3) == []


def test_a_canary_that_missed_is_tried_on_the_other_layouts():
    jobs, outcomes = _canary_outcomes({0: 999_000_000})
    first = [j for j in jobs if j.role != "canary" or j.repeat == 0]
    retries = measure.canary_retries(QFT, first, outcomes, 3)
    assert [(job.role, job.repeat) for job in retries] == [("canary", 1), ("canary", 2)]
    assert all(job.extra_gates == 7 for job in retries)


def test_a_single_repeat_has_no_other_layout_to_try():
    jobs, outcomes = _canary_outcomes({0: 999_000_000})
    first = [j for j in jobs if j.role != "canary" or j.repeat == 0]
    assert measure.canary_retries(QFT, first, outcomes, 1) == []


def test_retried_canaries_are_judged_together():
    jobs, outcomes = _canary_outcomes({0: 999_000_000, 1: 1_110_000_000, 2: 1_210_000_000})
    native = Outcome(measure.native_job(QFT), sentinel={})
    summary = measure.summarise_cell(QFT, outcomes, jobs, [], native)
    assert summary["canary"]["detected"]
    assert summary["canary"]["delta"] == 10_000_000
    assert len(summary["canary"]["pairs"]) == 3


def test_only_what_is_not_counted_runs_in_a_writable_directory(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, timeout):
        seen[next(a for a in argv if a.startswith("--name="))] = argv
        return 1, ""

    monkeypatch.setattr(measure, "run_keeping_tail", fake)
    runner = measure.Runner("image", tmp_path, tmp_path, [0])
    runner.execute(measure.native_job(QFT), 0)
    runner.execute(full_job(), 0)
    argvs = list(seen.values())
    assert "--workdir=/tmp" in argvs[0]
    assert "--workdir=/tmp" not in argvs[1]


def test_the_canary_is_judged_against_the_full_run_on_its_own_layout():
    # The layouts differ by far more than the canary adds, so the median would hide it.
    jobs = measure.plan_cell_jobs(QFT, repeats=3, device_gates=700)
    counts = {0: 1_000_000_000, 1: 1_100_000_000, 2: 1_200_000_000}
    outcomes = {
        job: Outcome(
            job,
            counts=Counts(python=1_010_000_000 if job.role == "canary" else counts[job.repeat]),
            sentinel={},
        )
        for job in jobs
    }
    native = Outcome(measure.native_job(QFT), sentinel={})
    summary = measure.summarise_cell(QFT, outcomes, jobs, [], native)
    assert summary["instructions"]["net"] == 1_100_000_000
    assert summary["canary"]["delta"] == 10_000_000
    assert summary["canary"]["detected"]
