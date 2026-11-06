"""Results across builds, and the steps between them.

A step is a change between two consecutive builds that is big enough to mean something. Gate counts
are exact, so any change is a step. Counted metrics have a threshold, and the runs of the two builds
mustn't overlap: runs differ in memory layout, and a code change that does nothing can still shift
the layout. A step counts as confirmed when a second, separate set of results shows the same change.
"""

import json
import re
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path

COUNTED_STEP_PCT = 0.5
# Set from measured noise: see the README's limits.
MEMORY_STEP_PCT = 1.0
# How close a second measurement of a step has to come to the first: this many percentage points,
# or this share of the step, whichever is more.
CONFIRM_TOLERANCE_POINTS = 0.25
CONFIRM_TOLERANCE_SHARE = 0.25

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-]dev(\d+))?$")


def version_key(version: str) -> tuple[int, int, int, float]:
    """Sorts by release, with a dev build before the release it leads to."""
    match = _VERSION.match(version)
    if match is None:
        raise ValueError(f"not a version this project knows: {version}")
    major, minor, patch, dev = match.groups()
    return (int(major), int(minor), int(patch), float("inf") if dev is None else int(dev))


def metrics_of(result: dict) -> dict[tuple[str, str], float]:
    """Every number in a result that a build can change, by cell and metric."""
    metrics: dict[tuple[str, str], float] = {}
    for cell in result["cells"]:
        name = cell["name"]
        for metric in ("instructions", "compile", "imports"):
            if metric in cell:
                metrics[(name, metric)] = cell[metric]["net"]
        for level, counts in (cell.get("gates") or {}).items():
            for field in ("gates", "depth"):
                if field in counts:
                    metrics[(name, f"{field}.{level}")] = counts[field]
    for entry in result.get("memory") or []:
        metrics[(f"memory/{entry['backend']}", "peak_kb")] = entry["max_rss_kb"]["net"]
    return metrics


def runs_of(result: dict) -> dict[tuple[str, str], list[int]]:
    """The separate runs behind each counted metric, where a result has more than one."""
    runs: dict[tuple[str, str], list[int]] = {}
    for cell in result["cells"]:
        for metric in ("instructions", "compile", "imports"):
            if metric in cell and cell[metric].get("runs"):
                runs[(cell["name"], metric)] = cell[metric]["runs"]
    for entry in result.get("memory") or []:
        if entry["max_rss_kb"].get("runs"):
            runs[(f"memory/{entry['backend']}", "peak_kb")] = entry["max_rss_kb"]["runs"]
    return runs


def _ranges_overlap(before: list[int] | None, after: list[int] | None) -> bool:
    if not before or not after:
        return False
    return min(after) <= max(before) and min(before) <= max(after)


def is_step(
    metric: str,
    before: float,
    after: float,
    before_runs: list[int] | None = None,
    after_runs: list[int] | None = None,
) -> bool:
    if before == after:
        return False
    if metric.startswith(("gates.", "depth.")):
        return True
    if not before:
        return True
    limit = MEMORY_STEP_PCT if metric == "peak_kb" else COUNTED_STEP_PCT
    if abs(after - before) / before * 100 < limit:
        return False
    return not _ranges_overlap(before_runs, after_runs)


def change_pct(before: float, after: float) -> float | None:
    return (after - before) / before * 100 if before else None


@dataclass(frozen=True)
class Step:
    before_stack: str
    after_stack: str
    cell: str
    metric: str
    before: float
    after: float
    change_pct: float | None
    packages_changed: tuple[str, ...]
    confirmed: bool | None = None  # None: not re-measured

    @property
    def is_counted(self) -> bool:
        return not self.metric.startswith(("gates.", "depth."))


def in_build_order(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda r: version_key(r["stack"]["pennylane-catalyst"]))


def packages_changed(before: dict, after: dict) -> tuple[str, ...]:
    """Every package that differs between two stacks, since any of them can cause a step."""
    old, new = before["stack"].get("packages", {}), after["stack"].get("packages", {})
    return tuple(
        f"{name} {old.get(name, 'absent')} → {new.get(name, 'absent')}"
        for name in sorted(old.keys() | new.keys())
        if old.get(name) != new.get(name)
    )


def find_steps(results: list[dict]) -> list[Step]:
    steps = []
    for before, after in pairwise(in_build_order(results)):
        old, new = metrics_of(before), metrics_of(after)
        changed = packages_changed(before, after)
        for cell, metric in sorted(old.keys() & new.keys()):
            if is_step(metric, old[(cell, metric)], new[(cell, metric)]):
                steps.append(
                    Step(
                        before["stack"]["id"],
                        after["stack"]["id"],
                        cell,
                        metric,
                        old[(cell, metric)],
                        new[(cell, metric)],
                        change_pct(old[(cell, metric)], new[(cell, metric)]),
                        changed,
                    )
                )
    return steps


def _confirmed(step: Step, second: dict[str, dict]) -> bool | None:
    """Whether a second set of results shows a step too, the same way round and about the same size.
    None if it lacks either build."""
    if step.before_stack not in second or step.after_stack not in second:
        return None
    key = (step.cell, step.metric)
    first, last = second[step.before_stack], second[step.after_stack]
    before = metrics_of(first).get(key)
    after = metrics_of(last).get(key)
    if before is None or after is None:
        return None
    if not step.is_counted:
        return (before, after) == (step.before, step.after)
    change = change_pct(before, after)
    if change is None or step.change_pct is None:
        return False
    if not is_step(step.metric, before, after, runs_of(first).get(key), runs_of(last).get(key)):
        return False
    return abs(change - step.change_pct) <= max(
        CONFIRM_TOLERANCE_POINTS, abs(step.change_pct) * CONFIRM_TOLERANCE_SHARE
    )


def confirm_steps(steps: list[Step], second_set: list[dict]) -> list[Step]:
    second = {result["stack"]["id"]: result for result in second_set}
    return [Step(**(asdict(step) | {"confirmed": _confirmed(step, second)})) for step in steps]


def load_results(paths: list[Path]) -> list[dict]:
    """Saved results, leaving out rejected ones, which never get compared or published."""
    results = [json.loads(path.read_text()) for path in paths if ".rejected." not in path.name]
    return [result for result in results if result.get("valid", True)]


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _change(step: Step) -> str:
    if step.change_pct is None:
        return "from zero"
    return f"{step.change_pct:+.2f}%"


def _status(step: Step) -> str:
    return {True: "yes", False: "NO", None: "not re-measured"}[step.confirmed]


def render_steps(steps: list[Step]) -> str:
    if not steps:
        return "No steps.\n"
    lines = [
        "| Between | Cell | Metric | Before | After | Change | Confirmed |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for step in steps:
        lines.append(
            f"| {step.before_stack} → {step.after_stack} | {step.cell} | {step.metric} "
            f"| {_number(step.before)} | {_number(step.after)} | {_change(step)} "
            f"| {_status(step)} |"
        )
    return "\n".join(lines) + "\n"


def render_series(results: list[dict], metric: str = "instructions") -> str:
    """One row per cell and one column per build: the whole history of a metric at a glance."""
    ordered = in_build_order(results)
    tables = [metrics_of(result) for result in ordered]
    cells = sorted({cell for table in tables for (cell, name) in table if name == metric})
    versions = " | ".join(result["stack"]["pennylane-catalyst"] for result in ordered)
    lines = [f"| {metric} | {versions} |", "|---|" + "---:|" * len(ordered)]
    for cell in cells:
        values = (table.get((cell, metric)) for table in tables)
        row = " | ".join("" if value is None else f"{value / 1e9:.3f}B" for value in values)
        lines.append(f"| {cell} | {row} |")
    return "\n".join(lines) + "\n"
