"""Why did a metric change between two builds?

Each package is narrowed as far as it allows. Everything that merged between the two builds is the
candidate list. The functions that moved rank it, and PennyLane, being pure Python, can be bisected
commit by commit. A wrong answer is possible at every stage, so the output says how far it got.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median

from pennywatch import cachegrind, container, history, measure, stacks, untrusted, upstream
from pennywatch.measure import Job
from pennywatch.suite import SUITES, Cell

LAYOUTS = 3
TOP_FUNCTIONS = 15
# A commit has to move this share of the step, and this share of the metric, to be worth chasing.
MIN_CULPRIT_SHARE = 0.1
COUNTED_STEP = 0.005
MAX_CULPRITS = 6
SAVED_CANDIDATES = 30
# A bisect only makes sense if the newest PennyLane alone gives most of the step.
BISECT_MIN_SHARE = 0.5
ARCHIVE_LIMIT_BYTES = 200 * 1024 * 1024
UNPACKED_LIMIT_BYTES = 500 * 1024 * 1024
ARCHIVE_MEMBERS_LIMIT = 50_000


@dataclass
class Side:
    """One build's numbers for one cell."""

    stack: str
    runs: list[int]
    functions: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    @property
    def net(self) -> int:
        return int(median(self.runs))


def find_cell(name: str, suite: str = "full") -> Cell:
    for cell in SUITES[suite]:
        if cell.name == name:
            return cell
    raise SystemExit(f"no cell {name} in suite {suite}")


def _job(cell: Cell, role: str, repeat: int = 0) -> Job:
    return Job(role, cell.backend, cell.wires, cell.circuit, cell.rounds, repeat=repeat)


def _workspace() -> Path:
    stacks.cache_dir().mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="explain-", dir=stacks.cache_dir()))
    workspace.chmod(0o755)
    return workspace


def _usable_calls(sentinel: dict | None) -> dict[str, int]:
    """Calls per function, as written by the code under test: anything odd is dropped."""
    calls = (sentinel or {}).get("calls")
    if not isinstance(calls, dict):
        return {}
    return {
        untrusted.clean_name(name): count
        for name, count in calls.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }


_IMPORT_LINE = re.compile(r"import time:\s+(\d+) \|\s+(\d+) \|(\s+)(\S+)")
IMPORT_TIMES_LIMIT = 4 * 1024 * 1024


def import_costs(text: str, package: str = "pennylane") -> dict[str, int]:
    """Microseconds per file of a package, from `python -X importtime`, with each outside package
    charged to whichever file of this one imported it. That's what points at a line of the package
    when the time itself goes to a dependency. Times are a guide: only instructions are exact."""
    rows = [
        (match[4], int(match[1]), int(match[2]), len(match[3]))
        for match in map(_IMPORT_LINE.match, text.splitlines())
        if match
    ]
    costs: dict[str, int] = {}

    def ours(module: str) -> bool:
        return module == package or module.startswith(f"{package}.")

    def path_of(module: str) -> str:
        return untrusted.clean_name(module.replace(".", "/") + ".py")

    # Children print before their parent, so a parent is the next line with less indent.
    for index, (module, own, cumulative, depth) in enumerate(rows):
        if ours(module):
            costs[path_of(module)] = costs.get(path_of(module), 0) + own
            continue
        parent = next((row for row in rows[index + 1 :] if row[3] < depth), None)
        if parent is not None and ours(parent[0]):
            costs[path_of(parent[0])] = costs.get(path_of(parent[0]), 0) + cumulative
    return costs


def measure_import_times(image: str, stack_dir: Path, cpus: list[int]) -> dict[str, int]:
    """How long each file of PennyLane took to import, on this stack. Not under Valgrind."""
    workspace = _workspace()
    output = workspace / "out"
    output.mkdir()
    (workspace / "src").mkdir()
    try:
        code, _ = measure.run_keeping_tail(
            [
                "docker",
                "run",
                f"--name=pennywatch-{os.getpid()}-importtime",
                *container.sandbox_args(cpus[0], stack_dir, workspace / "src", output),
                "--workdir=/tmp",
                image,
                "sh",
                "-c",
                "/stack/bin/python -P -s -X importtime -c 'import pennylane' "
                "2> /out/importtime.txt",
            ],
            measure.JOB_TIMEOUT_S,
        )
        if code != 0:
            raise MeasurementFailed(f"importing PennyLane exited {code}")
        data = untrusted.read_file(output / "importtime.txt", IMPORT_TIMES_LIMIT)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return import_costs(data.decode(errors="replace"))


def counted_role(metric: str) -> str:
    return {"instructions": "full", "compile": "compile", "imports": "imports"}[metric]


def measure_side(
    image: str,
    stack_dir: Path,
    stack_name: str,
    cell: Cell,
    cpus: list[int],
    metric: str = "instructions",
    overlay: Path | None = None,
    profile: bool = True,
) -> Side:
    """Run a cell across layouts, with the functions of the first layout and a Python profile."""
    workspace = _workspace()
    role = counted_role(metric)
    try:
        runner = measure.Runner(
            image, stack_dir, workspace, cpus, overlay=overlay, keep_functions=True
        )
        jobs = [_job(cell, role, repeat=layout) for layout in range(LAYOUTS)]
        extra = [_job(cell, "profile")] if profile else []
        outcomes = runner.run_all(jobs + extra)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    problems = [
        f"{job.label}: {problem}"
        for job, outcome in outcomes.items()
        if (problem := measure.validate_outcome(outcome))
    ]
    if problems:
        raise MeasurementFailed("; ".join(problems))
    side = Side(stack_name, [outcomes[job].counts.total for job in jobs])
    side.functions = outcomes[jobs[0]].functions or {}
    if profile:
        side.calls = _usable_calls(outcomes[extra[0]].sentinel)
    return side


class MeasurementFailed(RuntimeError):
    """A run of a build or a commit gave no usable number."""


def call_changes(before: dict[str, int], after: dict[str, int], top: int) -> list[list]:
    moved = [
        [name, before.get(name, 0), after.get(name, 0)]
        for name in before.keys() | after.keys()
        if before.get(name, 0) != after.get(name, 0)
    ]
    moved.sort(key=lambda row: (-abs(row[2] - row[1]), row[0]))
    return moved[:top]


def rank_candidates(
    merged: list[upstream.Merged], moved_calls: list[list]
) -> list[tuple[int, upstream.Merged]]:
    """Most likely first: a pull request scores the calls that moved in the files it touched. Ties
    keep merge order. This is a pointer for a person to check, not a verdict."""
    weight: dict[str, int] = {}
    for name, before, after in moved_calls:
        path = name.rsplit(":", 1)[0]
        weight[path] = weight.get(path, 0) + abs(after - before)
    scored = [(sum(weight.get(path, 0) for path in change.files), change) for change in merged]
    return sorted(scored, key=lambda item: -item[0])


def candidates(before: dict[str, str], after: dict[str, str]) -> dict[str, list[upstream.Merged]]:
    """What merged between two stacks, per package whose version moved."""
    found = {}
    for package, repo in upstream.REPOS.items():
        old, new = before.get(package), after.get(package)
        if old == new or old is None or new is None:
            continue
        found[package] = upstream.merged_between(
            repo, upstream.commit_of(repo, old), upstream.commit_of(repo, new)
        )
    return found


def fetch_overlay(commit: str, extras: tuple[str, ...] = ()) -> Path:
    """PennyLane's Python files at one commit, from GitHub's archive, unpacked once into the cache,
    with any packages this commit needs and the stack lacks. Bytecode gets compiled offline, as for
    a stack, so no measured run pays for writing it."""
    name = "+".join((commit, *sorted(extras)))
    target = stacks.cache_dir() / "overlays" / name
    if target.is_dir():
        return target
    url = f"https://github.com/{upstream.REPOS['pennylane'].slug}/archive/{commit}.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read(ARCHIVE_LIMIT_BYTES + 1)
    if len(archive) > ARCHIVE_LIMIT_BYTES:
        raise BadArchive(f"the archive of {commit[:12]} is larger than expected")
    partial = Path(tempfile.mkdtemp(prefix=f"{commit[:12]}.", dir=_overlays_dir()))
    partial.chmod(0o755)
    unpack_package(archive, partial)
    if extras:
        stacks.install_extras(container.ensure_image(), partial, extras)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cpus=2",
            "--network=none",
            *container.HARDENING,
            container.user_flag(),
            "-e",
            "HOME=/tmp",
            f"--volume={partial}:/overlay",
            container.ensure_image(),
            "python",
            "-I",
            "-S",
            "-m",
            "compileall",
            "-q",
            "/overlay",
        ],
        check=True,
    )
    partial.rename(target)
    return target


def _overlays_dir() -> Path:
    path = stacks.cache_dir() / "overlays"
    path.mkdir(parents=True, exist_ok=True)
    return path


class BadArchive(ValueError):
    """An archive that isn't what a release of the package looks like."""


def unpack_package(archive: bytes, target: Path, package: str = "pennylane") -> None:
    """Only regular files inside the package's own directory come out of the archive, and a member
    is read as it arrives, so the limits hold whatever its header claims."""
    written = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for count, member in enumerate(tar, start=1):
            if count > ARCHIVE_MEMBERS_LIMIT:
                raise BadArchive("too many files in the archive")
            _, _, inside = member.name.partition("/")
            if not member.isfile() or not inside.startswith(f"{package}/"):
                continue
            if ".." in Path(inside).parts:
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            data = source.read(UNPACKED_LIMIT_BYTES - written + 1)
            written += len(data)
            if written > UNPACKED_LIMIT_BYTES:
                raise BadArchive("the archive unpacks to more than expected")
            destination = target / inside
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def bisect_commits(commits: list[str], low: float, high: float, measure_commit) -> dict:
    """Find the commit where a metric moved from about `low` to about `high`.

    `commits` run oldest to newest, the last being the newer build. `measure_commit` gives a number,
    or None for a commit that can't be measured, which gets stepped around as `git bisect skip`
    does. If several commits each moved it part of the way, this finds one of them, and the result
    says how much of the step that commit accounts for."""
    midpoint = (low + high) / 2
    moved_past = (lambda value: value >= midpoint) if high >= low else (lambda v: v <= midpoint)
    left, right = -1, len(commits) - 1  # left: not yet moved. right: moved.
    tried: list[dict] = []
    values: dict[int, float | None] = {}

    def value_at(index: int) -> float | None:
        if index not in values:
            values[index] = measure_commit(commits[index])
            tried.append({"commit": commits[index], "value": values[index]})
        return values[index]

    while right - left > 1:
        middle = (left + right) // 2
        probe = middle
        while probe < right and value_at(probe) is None:
            probe += 1
        if probe == right:
            # Nothing between the middle and the right edge can be measured, so look left of it.
            probe = middle - 1
            while probe > left and value_at(probe) is None:
                probe -= 1
            if probe == left:
                return {"found": None, "range": commits[left + 1 : right + 1], "tried": tried}
            if moved_past(values[probe]):
                right = probe
            else:
                return {"found": None, "range": commits[probe + 1 : right + 1], "tried": tried}
            continue
        if moved_past(values[probe]):
            right = probe
        else:
            left = probe
    before_value = low if left < 0 else values[left]
    after_value = values.get(right)
    if after_value is None:
        after_value = value_at(right)
    return {
        "found": commits[right],
        "before": before_value,
        "after": after_value,
        "share_of_step": None
        if after_value is None or high == low
        else (after_value - before_value) / (high - low),
        "tried": tried,
    }


def bisect_all(
    commits: list[str], low: float, high: float, measure_commit, max_found: int = MAX_CULPRITS
) -> dict:
    """Every commit that moved a metric by a meaningful part of a step. A step often has several, so
    after each find this looks on either side of it, reusing what it has already measured."""
    known: dict[str, float | None] = {}

    def remembered(commit: str) -> float | None:
        if commit not in known:
            known[commit] = measure_commit(commit)
        return known[commit]

    smallest = max(abs(high - low) * MIN_CULPRIT_SHARE, abs(low) * COUNTED_STEP)
    found: list[dict] = []
    unresolved: list[list[str]] = []

    def walk(segment: list[str], first: float, last: float) -> None:
        if not segment or abs(last - first) < smallest or len(found) >= max_found:
            return
        result = bisect_commits(segment, first, last, remembered)
        if result["found"] is None:
            unresolved.append(result["range"])
            return
        at = segment.index(result["found"])
        found.append(
            {"commit": result["found"], "before": result["before"], "after": result["after"]}
        )
        walk(segment[:at], first, result["before"])
        if result["after"] is not None:
            walk(segment[at + 1 :], result["after"], last)

    walk(commits, low, high)
    return {
        "culprits": sorted(found, key=lambda step: -abs(step["after"] - step["before"])),
        "unresolved": unresolved,
        "measured": len(known),
    }


def bisect_pennylane(
    image: str,
    stack_dir: Path,
    cell: Cell,
    merged: list[upstream.Merged],
    low: float,
    high: float,
    last_commit: str,
    cpus: list[int],
    metric: str,
    extras: tuple[str, ...] = (),
) -> dict:
    """Bisect PennyLane alone on top of the older stack, so nothing else can be the cause."""

    def run(commit: str) -> float | None:
        try:
            overlay = fetch_overlay(commit, extras)
            side = measure_side(
                image, stack_dir, commit[:12], cell, cpus, metric, overlay=overlay, profile=False
            )
        except (
            MeasurementFailed,
            OSError,
            subprocess.CalledProcessError,
            tarfile.TarError,
            BadArchive,
        ) as error:
            print(f"{commit[:12]} can't be measured: {str(error)[:160]}")
            return None
        print(f"{commit[:12]} {side.net:,}", flush=True)
        return side.net

    newest = run(last_commit)
    if newest is None:
        return {"verdict": "The newer PennyLane doesn't run on the older stack, so no bisect."}
    share = (newest - low) / (high - low) if high != low else 0.0
    if share < BISECT_MIN_SHARE:
        return {
            "verdict": f"PennyLane alone gives {share:.0%} of the step, so the cause is elsewhere: "
            "another package or the third-party ones.",
            "pennylane_alone": newest,
        }
    commits = [change.commit for change in merged]
    if not commits or commits[-1] != last_commit:
        commits.append(last_commit)
    outcome = bisect_all(commits, low, newest, run)
    outcome["pennylane_alone"] = newest
    outcome["pennylane_share"] = share
    return outcome


def _saved_candidate(row: dict) -> dict:
    return {key: row[key] for key in ("score", "subject", "pull_request", "url") if key in row}


@dataclass
class Explanation:
    cell: str
    metric: str
    before: Side
    after: Side
    packages: list[str]
    python_functions: list
    c_functions: list
    candidates: dict[str, list[dict]]
    bisect: dict | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        for side in ("before", "after"):
            data[side].pop("functions")
            data[side].pop("calls")
            data[side]["net"] = getattr(self, side).net
        # The whole list, with every commit's files, is hundreds of kilobytes per explanation.
        data["candidate_counts"] = {name: len(rows) for name, rows in self.candidates.items()}
        data["candidates"] = {
            name: [_saved_candidate(row) for row in rows[:SAVED_CANDIDATES]]
            for name, rows in self.candidates.items()
        }
        return data


def explain(
    before: stacks.Stack,
    after: stacks.Stack,
    cell_name: str,
    metric: str = "instructions",
    cpus: tuple[int, ...] = (0, 1),
    bisect: bool = False,
    suite: str = "full",
    extras: tuple[str, ...] = (),
) -> Explanation:
    cell = find_cell(cell_name, suite)
    image = container.ensure_image()
    versions, sides, directories = {}, {}, {}
    for name, stack in (("before", before), ("after", after)):
        if not stack.lock_path.exists():
            stacks.write_lock(stack, image)
        versions[name] = stacks.locked_versions(stack.lock_path)
        directories[name] = stacks.install(stack, image)
        sides[name] = measure_side(image, directories[name], stack.id, cell, list(cpus), metric)
    if metric == "imports":
        # What the circuit does says nothing about a step in the imports.
        for name in ("before", "after"):
            sides[name].calls = measure_import_times(image, directories[name], list(cpus))
    moved_calls = call_changes(sides["before"].calls, sides["after"].calls, 200)
    merged = candidates(versions["before"], versions["after"])
    changed = {
        name: {"before": versions["before"][name], "after": versions["after"][name]}
        for name in versions["before"].keys() & versions["after"].keys()
        if versions["before"][name] != versions["after"][name]
    }
    ranked = {
        package: [
            {"score": score, **asdict(change)}
            for score, change in rank_candidates(changes, moved_calls)
        ]
        for package, changes in merged.items()
    }
    bisected = None
    if bisect and "pennylane" in merged:
        repo = upstream.REPOS["pennylane"]
        bisected = bisect_pennylane(
            image,
            directories["before"],
            cell,
            merged["pennylane"],
            sides["before"].net,
            sides["after"].net,
            upstream.commit_of(repo, versions["after"]["pennylane"]),
            list(cpus),
            metric,
            extras,
        )
        by_commit = {change.commit: change for change in merged["pennylane"]}
        for culprit in bisected.get("culprits", []):
            hit = by_commit.get(culprit["commit"])
            culprit["pull_request"] = hit.pull_request if hit else None
            culprit["url"] = hit.url if hit else None
            culprit["subject"] = hit.subject if hit else None
    return Explanation(
        cell_name,
        metric,
        sides["before"],
        sides["after"],
        [f"{name} {v['before']} → {v['after']}" for name, v in sorted(changed.items())],
        call_changes(sides["before"].calls, sides["after"].calls, TOP_FUNCTIONS),
        [
            list(row)
            for row in cachegrind.function_changes(
                sides["before"].functions, sides["after"].functions, TOP_FUNCTIONS
            )
        ],
        ranked,
        bisected,
    )


def _signed(value: int) -> str:
    return f"{value:+,}"


def _render_bisect(bisected: dict, low: float) -> list[str]:
    if "culprits" not in bisected:
        return [f"**Bisect:** {bisected.get('verdict', 'no single commit found')}", ""]
    alone = bisected["pennylane_alone"]
    share = bisected.get("pennylane_share", 0.0)
    lines = [
        f"**Bisect:** on the older stack, PennyLane alone takes this to {alone:,.0f}, {share:.0%} "
        f"of the step, after measuring {bisected['measured']} commits.",
        "",
    ]
    span = alone - low
    for culprit in bisected["culprits"]:
        moved = culprit["after"] - culprit["before"]
        portion = f", {moved / span:.0%} of that" if span else ""
        lines.append(
            f"- {untrusted.clean_name(culprit.get('subject'))} ({culprit.get('url')}): "
            f"{culprit['before']:,.0f} → {culprit['after']:,.0f}{portion}"
        )
    for commits in bisected.get("unresolved", []):
        lines.append(f"- Somewhere among {len(commits)} commits that can't be measured")
    lines.append("")
    return lines


def render(explanation: Explanation) -> str:
    before, after = explanation.before, explanation.after
    change = history.change_pct(before.net, after.net) or 0.0
    lines = [
        f"## {explanation.cell}, {explanation.metric}: {before.stack} → {after.stack}",
        "",
        f"Re-measured across {LAYOUTS} memory layouts: {before.net:,} → {after.net:,} "
        f"(**{change:+.2f}%**). Runs: {before.runs} → {after.runs}.",
        "",
    ]
    if not history.is_step(explanation.metric, before.net, after.net, before.runs, after.runs):
        lines += [
            "**This is within the noise.** Below the threshold, or the runs of the two builds "
            "overlap, so there's no change here to explain. What follows is for reference.",
            "",
        ]
    if explanation.packages:
        lines += ["**Packages that moved:** " + ", ".join(explanation.packages), ""]
    if explanation.bisect:
        lines += _render_bisect(explanation.bisect, before.net)
    if explanation.python_functions:
        heading = (
            "| File, with the packages it imports | Before, µs | After, µs | Moved |"
            if explanation.metric == "imports"
            else "| Python function | Calls before | Calls after | Moved |"
        )
        lines += [heading, "|---|---:|---:|---:|"]
        lines += [
            f"| `{name}` | {old:,} | {new:,} | {_signed(new - old)} |"
            for name, old, new in explanation.python_functions
        ]
        lines.append("")
    if explanation.c_functions:
        lines += ["| Native function | Before | After | Moved |", "|---|---:|---:|---:|"]
        lines += [
            f"| `{name[:80]}` | {old:,} | {new:,} | {_signed(new - old)} |"
            for name, old, new in explanation.c_functions
        ]
        lines.append("")
    for package, changes in explanation.candidates.items():
        lines.append(
            f"**{package}:** {len(changes)} commits between the builds. Most likely first:"
        )
        lines.append("")
        lines += [
            f"- {untrusted.clean_name(change['subject'])[:100]} ({change['url']})"
            + (f", touches code that moved: {change['score']:,}" if change["score"] else "")
            for change in changes[:12]
        ]
        lines.append("")
    if "pennylane-catalyst" in explanation.candidates:
        lines += [
            "Catalyst is too heavy to build per commit, so for its changes this stops at the "
            "candidates.",
            "",
        ]
    return "\n".join(lines)


def save(explanation: Explanation, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    before = explanation.before.stack.removeprefix("catalyst-")
    after = explanation.after.stack.removeprefix("catalyst-")
    name = f"{before}--{after}--{explanation.cell.replace('/', '-')}--{explanation.metric}"
    path = directory / f"{name}.json"
    path.write_text(json.dumps(explanation.to_dict(), indent=1, ensure_ascii=False) + "\n")
    return path
