"""Running the suite: a fresh container per measured process, then outcomes into results."""

import hashlib
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from pennywatch import analysis, builds, cachegrind, container, stacks, untrusted
from pennywatch.cachegrind import Counts
from pennywatch.harness import expected
from pennywatch.suite import BACKENDS, Cell

SCHEMA = 2
JOB_TIMEOUT_S = 3600
CANARY_FRACTION = 0.01
# On a small circuit 1% rounds to a gate or two, which the noise can hide.
CANARY_MIN_GATES = 3
TIMING_REPEATS = 10
OUTPUT_TAIL_BYTES = 4096
HARNESS_DIR = Path(__file__).parent / "harness"

# Valgrind's own settings are part of the instrument. --trace-children takes in Catalyst's compiler,
# which is a child process.
VALGRIND = [
    "valgrind",
    "--tool=cachegrind",
    "--cache-sim=no",
    "--branch-sim=no",
    "--trace-children=yes",
    "--cachegrind-out-file=/out/cachegrind.%p",
    "--log-file=/out/valgrind.%p.log",
]
PYTHON = ["/stack/bin/python", "-P", "-s", "-m", "pennywatch.harness.workload"]
UNCOUNTED_MODES = ("native", "profile")


@dataclass(frozen=True)
class Job:
    """One measured process."""

    role: str  # imports | compile | full | canary | native | memory | profile
    backend: str
    wires: int
    circuit: str = "qft"  # the imports baseline never builds it, but the workload wants a name
    rounds: int = 1
    extra_gates: int = 0
    repeat: int = 0
    busy: bool = False

    @property
    def label(self) -> str:
        busy = "-busy" if self.busy else ""
        if self.role in ("imports", "memory"):
            return f"{self.role}-{self.backend}-{self.wires}w-{self.repeat}"
        return f"{self.circuit}-{self.backend}-{self.role}{busy}-{self.repeat}"

    @property
    def mode(self) -> str:
        return {"canary": "full", "memory": "native"}.get(self.role, self.role)


@dataclass
class Outcome:
    job: Job
    counts: Counts | None = None
    sentinel: dict | None = None
    error: str | None = None
    elapsed_s: float = 0.0
    functions: dict[str, int] | None = None


def workload_args(job: Job) -> list[str]:
    timing_repeats = 1 if job.role == "memory" else TIMING_REPEATS
    return [
        f"--mode={job.mode}",
        f"--circuit={job.circuit}",
        f"--backend={job.backend}",
        f"--wires={job.wires}",
        f"--rounds={job.rounds}",
        f"--extra-gates={job.extra_gates}",
        f"--timing-repeats={timing_repeats}",
        "--out=/out/sentinel.json",
    ]


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def validate_outcome(outcome: Outcome) -> str | None:
    """Why this outcome can't be used, or None. An exit code of 0 isn't enough: the count from a
    crashed or skipped circuit looks just as stable as a real one. The sentinel was written by the
    code under test, so its shape is checked before anything relies on it."""
    if outcome.error:
        return outcome.error
    job = outcome.job
    sentinel = outcome.sentinel
    if sentinel is None:
        return "no sentinel: the process did not reach its end"
    versions = sentinel.get("versions", {})
    if not isinstance(versions, dict) or not all(isinstance(v, str) for v in versions.values()):
        return "the sentinel's versions are malformed"
    if job.mode in ("full", "native", "profile"):
        result = sentinel.get("result")
        if result is None:
            return "the sentinel has no result"
        if not isinstance(result, list) or not all(_is_number(value) for value in result):
            return "the sentinel's result is malformed"
        mismatch = expected.mismatch(job.circuit, job.wires, job.rounds, result)
        if mismatch:
            return f"wrong answer: {mismatch}"
    return None


def run_keeping_tail(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a command and keep only the end of its output: the code under test decides how much it
    prints, and the host's memory shouldn't depend on that."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail = bytearray()

    def drain() -> None:
        while chunk := process.stdout.read1(65536):
            tail.extend(chunk)
            del tail[:-OUTPUT_TAIL_BYTES]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    finally:
        reader.join(timeout=10)
    return code, tail.decode(errors="replace")


class Runner:
    """Runs jobs in containers, at most one per CPU slot."""

    def __init__(
        self,
        image: str,
        stack_dir: Path,
        workspace: Path,
        cpus: list[int],
        overlay: Path | None = None,
        keep_functions: bool = False,
    ):
        self.image = image
        self.stack_dir = stack_dir
        self.workspace = workspace
        self.cpus = cpus
        self.overlay = overlay
        self.keep_functions = keep_functions
        self.source = workspace / "src"
        package = self.source / "pennywatch"
        # A clean copy, because a __pycache__ left in the checkout would change what Python does at
        # import and so the count.
        shutil.copytree(
            HARNESS_DIR, package / "harness", ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copyfile(HARNESS_DIR.parent / "__init__.py", package / "__init__.py")

    def execute(self, job: Job, cpu: int) -> Outcome:
        output = self.workspace / "runs" / job.label
        output.mkdir(parents=True)
        command = [*PYTHON, *workload_args(job)]
        if job.mode not in UNCOUNTED_MODES:
            command = [*VALGRIND, *command]
        if job.busy:
            command = container.with_spinners(command)
        name = f"pennywatch-{os.getpid()}-{job.label}"
        argv = [
            "docker",
            "run",
            f"--name={name}",
            *container.sandbox_args(
                cpu, self.stack_dir, self.source, output, layout=job.repeat, overlay=self.overlay
            ),
            # Catalyst 0.14's specs writes a file to the working directory, and crashes if it can't.
            # Only for what isn't counted: the counted runs all keep one directory.
            *(["--workdir=/tmp"] if job.mode in UNCOUNTED_MODES else []),
            self.image,
            *command,
        ]
        started = time.monotonic()
        try:
            code, tail = run_keeping_tail(argv, JOB_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)
            return Outcome(job, error=f"timed out after {JOB_TIMEOUT_S} s")
        outcome = Outcome(job, elapsed_s=time.monotonic() - started)
        if code != 0:
            outcome.error = f"exit {code}: {tail.strip()[-400:]}"
            return outcome
        outcome.sentinel = untrusted.read_sentinel(output / "sentinel.json")
        if job.mode not in UNCOUNTED_MODES:
            outcome.counts = cachegrind.collect(output)
            if self.keep_functions:
                outcome.functions = cachegrind.collect_functions(output)
        return outcome

    def run_all(self, jobs: list[Job]) -> dict[Job, Outcome]:
        return run_on_slots(jobs, self.cpus, self.execute)


def run_on_slots(
    jobs: list[Job], cpus: list[int], execute: Callable[[Job, int], Outcome]
) -> dict[Job, Outcome]:
    """Run jobs in parallel with each holding one CPU. Counts don't depend on load, so parallel runs
    are safe, and the limit is there for the laptop's sake."""
    free = queue.SimpleQueue()
    for cpu in cpus:
        free.put(cpu)
    total = len(jobs)
    finished = []

    def on_a_slot(job: Job) -> Outcome:
        cpu = free.get()
        try:
            outcome = execute(job, cpu)
        except Exception as error:  # noqa: BLE001 - one bad job must not lose the others
            outcome = Outcome(job, error=f"{type(error).__name__}: {error}")
        finally:
            free.put(cpu)
        finished.append(job)
        problem = validate_outcome(outcome)
        count = f"{outcome.counts.total:,}" if outcome.counts else "-"
        status = f"FAILED: {problem}" if problem else count
        print(
            f"[{len(finished)}/{total}] {job.label} {outcome.elapsed_s:.0f}s {status}",
            file=sys.stderr,
            flush=True,
        )
        return outcome

    with ThreadPoolExecutor(max_workers=len(cpus)) as pool:
        return dict(zip(jobs, pool.map(on_a_slot, jobs), strict=True))


def harness_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(HARNESS_DIR.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def git_revision(repository: Path = container.REPO_ROOT) -> dict:
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    # Untracked files don't count: an earlier run's results aren't a change to the code.
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {"commit": git("rev-parse", "HEAD"), "dirty": dirty}


def host_facts() -> dict:
    """What's needed to explain a difference between machines. Nothing that identifies this one."""
    model = "unknown"
    with open("/proc/cpuinfo") as file:
        for line in file:
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return {
        "cpu": model,
        "logical_cpus": os.cpu_count(),
        "kernel": platform.release(),
        "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
    }


def canary_gates(device_gates: int) -> int:
    return max(CANARY_MIN_GATES, round(device_gates * CANARY_FRACTION))


def plan_baselines(cells: list[Cell]) -> list[Job]:
    """What the imports and creating the device cost, per backend, at the suite's largest size.
    They aren't part of any other count, so this is a figure in its own right and not a baseline.
    One run each: across layouts they moved 0.05% at most, and they're slow to repeat."""
    widest: dict[str, int] = {}
    for cell in cells:
        widest[cell.backend] = max(widest.get(cell.backend, 0), cell.wires)
    return [Job("imports", backend, wires) for backend, wires in sorted(widest.items())]


def plan_memory(wires: int | None, repeats: int) -> list[Job]:
    """Peak memory with the state vector dominating. Nothing gets counted, so these are quick."""
    if wires is None:
        return []
    return [
        Job("memory", backend, wires, circuit="memory", repeat=repeat)
        for backend in BACKENDS
        for repeat in range(repeats)
    ]


def plan_cell_jobs(cell: Cell, repeats: int, device_gates: int) -> list[Job]:
    base = Job("full", cell.backend, cell.wires, cell.circuit, cell.rounds)
    jobs = [replace(base, repeat=repeat) for repeat in range(repeats)]
    jobs.append(replace(base, role="canary", extra_gates=canary_gates(device_gates)))
    if cell.backend == "qjit":
        jobs += [replace(base, role="compile", repeat=repeat) for repeat in range(repeats)]
    return jobs


def native_job(cell: Cell, busy: bool = False) -> Job:
    return Job("native", cell.backend, cell.wires, cell.circuit, cell.rounds, busy=busy)


def _counts(outcomes: dict[Job, Outcome], jobs: list[Job]) -> list[int]:
    return [outcomes[job].counts.total for job in jobs if outcomes[job].counts]


def _metric(runs: list[int]) -> dict:
    return {
        "net": int(median(runs)),
        "runs": runs,
        "spread_pct": analysis.spread_pct(runs),
        "noise": analysis.noise(runs),
    }


def summarise_cell(
    cell: Cell, outcomes: dict[Job, Outcome], jobs: list[Job], baselines: list[Job], native: Outcome
) -> dict:
    full = [job for job in jobs if job.role == "full"]
    runs = _counts(outcomes, full)
    imports = _counts(outcomes, baselines)
    summary = {**asdict(cell), "name": cell.name}
    if native.sentinel:
        summary |= {
            key: native.sentinel.get(key)
            for key in (
                "gates",
                "wall_s",
                "compile_s",
                "max_rss_kb",
                "max_rss_children_kb",
                "bare_qnode_specs",
            )
        }
    if imports:
        summary["imports"] = _metric(imports)
    if not runs:
        return summary

    summary["instructions"] = {**_metric(runs), "processes": asdict(outcomes[full[0]].counts)}

    compile_jobs = [job for job in jobs if job.role == "compile"]
    compile_runs = _counts(outcomes, compile_jobs)
    if compile_runs:
        summary["compile"] = {
            **_metric(compile_runs),
            # Exact from run to run so far, unlike the Python process around it.
            "compiler_process": [
                outcomes[job].counts.compiler for job in compile_jobs if outcomes[job].counts
            ],
        }

    pairs = canary_pairs(jobs, outcomes)
    if pairs:
        extra_gates = next(job.extra_gates for job in jobs if job.role == "canary")
        summary["canary"] = analysis.canary_verdict(pairs, extra_gates, cell.backend)
    return summary


def canary_pairs(jobs: list[Job], outcomes: dict[Job, Outcome]) -> list[tuple[int, int]]:
    """Each canary with the full run on its own layout, since layouts differ by more than its 1%."""
    full = {job.repeat: outcomes[job].counts for job in jobs if job.role == "full"}
    return [
        (outcomes[job].counts.total, full[job.repeat].total)
        for job in jobs
        if job.role == "canary" and outcomes[job].counts and full.get(job.repeat)
    ]


def canary_retries(
    cell: Cell, jobs: list[Job], outcomes: dict[Job, Outcome], repeats: int
) -> list[Job]:
    """More canaries for a cell whose first one missed, on the layouts it hasn't tried. Nothing for
    a cell that failed outright, or where the first canary was seen, which is nearly all of them."""
    pairs = canary_pairs(jobs, outcomes)
    canary = next((job for job in jobs if job.role == "canary"), None)
    if not pairs or canary is None:
        return []
    if analysis.canary_verdict(pairs, canary.extra_gates, cell.backend)["detected"]:
        return []
    return [replace(canary, repeat=layout) for layout in range(1, repeats)]


def summarise_memory(outcomes: dict[Job, Outcome]) -> list[dict]:
    """Peak resident size per backend, leaving out any run that failed: problems_with reports it."""
    runs: dict[tuple[str, int], list[int]] = {}
    for job, outcome in outcomes.items():
        if job.role == "memory" and not validate_outcome(outcome):
            runs.setdefault((job.backend, job.wires), []).append(outcome.sentinel["max_rss_kb"])
    return [
        {"backend": backend, "wires": wires, "max_rss_kb": _metric(values)}
        for (backend, wires), values in runs.items()
    ]


def problems_with(outcomes: dict[Job, Outcome], versions: dict[str, str]) -> list[str]:
    """Everything that makes this run unfit to publish."""
    problems = []
    for job, outcome in outcomes.items():
        if problem := validate_outcome(outcome):
            problems.append(f"{job.label}: {problem}")
        elif outcome.sentinel:
            for package, version in outcome.sentinel["versions"].items():
                # Installed builds report 0.46.0-dev142 where the lock says 0.46.0.dev142.
                if version.replace("-dev", ".dev") != versions.get(package):
                    problems.append(f"{job.label}: {package[:40]} is not the locked version")
    return problems


def run_suite(
    cells: list[Cell],
    stack: stacks.Stack,
    repeats: int,
    cpus: list[int],
    busy_check: bool,
    keep_raw: Path | None = None,
    memory_wires: int | None = None,
) -> dict:
    image = container.ensure_image()
    if not stack.lock_path.exists():
        stacks.write_lock(stack, image)
    versions = stacks.locked_versions(stack.lock_path)
    lock_sha256 = stacks.lock_sha256(stack.lock_path)
    stack_dir = stacks.install(stack, image)

    started = datetime.now(UTC)
    # Read now, because the harness is copied now: edits during a long run don't affect it.
    provenance = {**git_revision(), "harness_sha256": harness_sha256()}
    workspace = Path(tempfile.mkdtemp(prefix="run-", dir=stacks.cache_dir()))
    workspace.chmod(0o755)
    try:
        runner = Runner(image, stack_dir, workspace, cpus)
        outcomes = _measure(runner, cells, repeats, busy_check, memory_wires)
        if keep_raw:
            untrusted.copy_plain_files(workspace / "runs", keep_raw)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    summaries = []
    for cell in cells:
        jobs = [job for job in outcomes if _belongs_to(job, cell)]
        baselines = [
            job for job in outcomes if job.role == "imports" and job.backend == cell.backend
        ]
        summaries.append(
            summarise_cell(cell, outcomes, jobs, baselines, outcomes[native_job(cell)])
        )

    problems = problems_with(outcomes, versions)
    problems += [
        f"{summary['name']}: the canary was not detected"
        for summary in summaries
        if "canary" in summary and not summary["canary"]["detected"]
    ]
    return {
        "schema": SCHEMA,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pennywatch": provenance,
        "image": {"tag": image, **container.tool_versions(image)},
        "host": host_facts(),
        "stack": {
            "id": stack.id,
            "lock_sha256": lock_sha256,
            "pennylane": versions.get("pennylane"),
            "pennylane-lightning": versions.get("pennylane-lightning"),
            "pennylane-catalyst": versions.get("pennylane-catalyst"),
            "packages": versions,
        },
        "repeats": repeats,
        "cpus": len(cpus),
        "cells": summaries,
        "memory": summarise_memory(outcomes),
        "busy_check": _busy_summary(outcomes, cells) if busy_check else None,
        "availability": builds.availability(),
        "valid": not problems,
        "problems": problems,
    }


def unavailable_result(stack: stacks.Stack, reason: str) -> dict:
    """What's recorded for a build that can't be measured, so it isn't retried every night."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return {
        "schema": SCHEMA,
        "started_at": now,
        "finished_at": now,
        "stack": {"id": stack.id, "pennylane-catalyst": stack.catalyst},
        "cells": [],
        "valid": False,
        "problems": [reason],
    }


def _belongs_to(job: Job, cell: Cell) -> bool:
    return (
        job.role in ("full", "canary", "compile")
        and not job.busy
        and (job.circuit, job.backend, job.wires, job.rounds)
        == (cell.circuit, cell.backend, cell.wires, cell.rounds)
    )


def reference_cell(cells: list[Cell]) -> Cell:
    """The cell for the busy-machine check: Lightning, with the least Python around it."""
    return next((cell for cell in cells if cell.backend == "lightning"), cells[0])


def _busy_jobs(cell: Cell) -> list[Job]:
    full = Job("full", cell.backend, cell.wires, cell.circuit, cell.rounds, busy=True)
    return [full, native_job(cell, busy=True)]


def _measure(
    runner: Runner, cells: list[Cell], repeats: int, busy_check: bool, memory_wires: int | None
):
    # Gate counts come from the native runs, and the canary's size comes from the gate counts.
    native_jobs = [native_job(cell) for cell in cells] + plan_memory(memory_wires, repeats)
    if busy_check:
        native_jobs.append(native_job(reference_cell(cells), busy=True))
    outcomes = runner.run_all(native_jobs)

    jobs = plan_baselines(cells)
    for cell in cells:
        sentinel = outcomes[native_job(cell)].sentinel
        device_gates = sentinel["gates"]["device"]["gates"] if sentinel else 100
        jobs += plan_cell_jobs(cell, repeats, device_gates)
    if busy_check:
        jobs.append(_busy_jobs(reference_cell(cells))[0])
    outcomes |= runner.run_all(jobs)

    retries = [
        job
        for cell in cells
        for job in canary_retries(
            cell, [job for job in outcomes if _belongs_to(job, cell)], outcomes, repeats
        )
    ]
    return outcomes | runner.run_all(retries) if retries else outcomes


def _busy_summary(outcomes: dict[Job, Outcome], cells: list[Cell]) -> dict | None:
    cell = reference_cell(cells)
    busy_full, busy_native = _busy_jobs(cell)
    quiet = _counts(
        outcomes, [job for job in outcomes if _belongs_to(job, cell) and job.role == "full"]
    )
    busy_counts = outcomes[busy_full].counts
    quiet_wall = (outcomes[native_job(cell)].sentinel or {}).get("wall_s")
    busy_wall = (outcomes[busy_native].sentinel or {}).get("wall_s")
    if not (quiet and busy_counts and quiet_wall and busy_wall):
        return None
    return {
        "cell": cell.name,
        "spinners": container.SPINNERS,
        "quiet_runs": quiet,
        "busy_count": busy_counts.total,
        "quiet_wall_s": median(quiet_wall),
        "busy_wall_s": median(busy_wall),
        "wall_ratio": median(busy_wall) / median(quiet_wall),
    }
