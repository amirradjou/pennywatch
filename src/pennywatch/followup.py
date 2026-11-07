"""Following up the steps the nightly finds.

After a night, a fresh runner measures both builds of a step again and explains what moved. A job
that can write then records whether the step held and tells the owner, in an issue in this repo.

The runner that follows up runs nightly code from TestPyPI, so what it uploads is treated like any
other measured output. The job that writes reads only what it can check, and works out whether the
step held from the numbers itself, not from anything the runner said. Its issue names upstream pull
requests without linking them: on a public repo a link would add a cross-reference to their page,
and nothing here posts to their repositories.
"""

import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pennywatch import explain, history, measure, site, stacks, untrusted
from pennywatch.publish import (
    MAX_RESULT_BYTES,
    Rejected,
    _check_values,
    _refuse_constant,
    parse_result,
)
from pennywatch.suite import BACKENDS, MEMORY_WIRES, SUITES

LIMIT = 3
SCHEMA = 1
REPEATS = 3
SUITE = "full"
# About two hours of bisecting on a runner, inside the six hours a job may take.
BISECT_MEASUREMENTS = 30
EXPLANATION_FILE = "explanation.json"
MAX_EXPLANATION_BYTES = 1024 * 1024
EXPLAINED_METRICS = ("instructions", "compile", "imports")
FOLLOWED_METRICS = (*EXPLAINED_METRICS, "peak_kb")
PAGE = "https://amirradjou.github.io/pennywatch/"
METRIC_NAMES = {
    "instructions": "instructions",
    "compile": "compile cost",
    "imports": "import cost",
    "peak_kb": "peak memory",
}

_PULL_REQUEST_URL = re.compile(
    r"https://github\.com/PennyLaneAI/(pennylane|pennylane-lightning|catalyst)/pull/\d{1,6}"
)
_COMMIT = re.compile(r"[0-9a-f]{40}")
_VERSION = r"\d+\.\d+\.\d+(?:\.dev\d+)?"
_PACKAGE = re.compile(r"[a-z][a-z0-9-]{0,40}")
_TRAILING_REFERENCE = re.compile(r"\s*\(#\d+\)\s*$")


@dataclass(frozen=True)
class FollowUp:
    before: str  # Catalyst versions
    after: str
    cell: str  # as steps name it: a cell, imports/<backend> or memory/<backend>
    metric: str

    def __post_init__(self) -> None:
        for version in (self.before, self.after):
            history.version_key(version)
        if self.metric not in FOLLOWED_METRICS:
            raise ValueError(f"not a metric that gets followed up: {self.metric}")
        known = {cell.name for cell in SUITES[SUITE]}
        known |= {f"{kind}/{backend}" for kind in ("imports", "memory") for backend in BACKENDS}
        if self.cell not in known:
            raise ValueError(f"not a cell of the {SUITE} suite: {self.cell}")

    @property
    def key(self) -> str:
        """The step's name, as the page and the explanations name it."""
        return f"catalyst-{self.before}..catalyst-{self.after}/{self.cell}/{self.metric}"

    @property
    def name(self) -> str:
        return f"{self.before}--{self.after}--{self.cell.replace('/', '-')}--{self.metric}"

    @property
    def measured_cell(self) -> str:
        """The cell to run. Imports and memory come with any cell of their backend."""
        if self.cell.startswith(("imports/", "memory/")):
            return f"qft/{self.cell.split('/')[1]}"
        return self.cell

    @property
    def explainable(self) -> bool:
        return self.metric in EXPLAINED_METRICS

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "before": self.before,
            "after": self.after,
            "cell": self.cell,
            "metric": self.metric,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FollowUp":
        followup = cls(str(data["before"]), str(data["after"]), str(data["cell"]), data["metric"])
        if "name" in data and data["name"] != followup.name:
            raise ValueError("a planned step's name doesn't match the step")
        return followup

    @classmethod
    def from_key(cls, key: str) -> "FollowUp":
        match = re.fullmatch(
            rf"catalyst-({_VERSION})\.\.catalyst-({_VERSION})/([a-z]+/[a-z]+)/([a-z_]+)", key
        )
        if match is None:
            raise ValueError(f"not a step: {key}")
        return cls(*match.groups())


def of_step(step: history.Step) -> FollowUp:
    return FollowUp(
        step.before_stack.removeprefix("catalyst-"),
        step.after_stack.removeprefix("catalyst-"),
        step.cell,
        step.metric,
    )


def _followable(steps: list[history.Step]) -> list[history.Step]:
    """Steps a follow-up can measure: counted, in a cell of the suite."""
    kept = []
    for step in steps:
        try:
            of_step(step)
        except ValueError:
            continue
        kept.append(step)
    return kept


def _size(step: history.Step) -> float:
    return float("inf") if step.change_pct is None else abs(step.change_pct)


def worth_following(steps: list[history.Step]) -> list[history.Step]:
    """Counted steps, one per cause where several metrics share one: the imports move together on
    every backend, and a cell under qjit counts its compile cost in its instructions."""
    counted = _followable(steps)
    pairs_with = {
        (s.before_stack, s.after_stack, s.cell) for s in counted if s.metric == "instructions"
    }
    chosen, imports = [], {}
    for step in counted:
        pair = (step.before_stack, step.after_stack)
        if step.metric == "imports":
            if pair not in imports or _size(step) > _size(imports[pair]):
                imports[pair] = step
        elif step.metric == "compile" and (*pair, step.cell) in pairs_with:
            continue
        else:
            chosen.append(step)
    return chosen + list(imports.values())


def plan(
    nightly: list[dict],
    followed: set[str],
    limit: int = LIMIT,
    step: str = "",
    stable: list[dict] | None = None,
) -> list[FollowUp]:
    """What to follow up in one run. The newest night's steps come first, as the news, then the
    largest of the older ones, so a quiet night works through the backlog. Only steps between two
    dev builds qualify on their own; `step` names any one step by hand, followed up or not."""
    if step:
        wanted = FollowUp.from_key(step)
        found = {s.key for s in history.find_steps((stable or []) + nightly)}
        if wanted.key not in found:
            raise ValueError(f"no such step in the results: {step}")
        return [wanted]
    steps = [s for s in worth_following(history.find_steps(nightly)) if s.key not in followed]
    if not steps:
        return []
    newest = max(steps, key=lambda s: history.version_key(s.after_stack.removeprefix("catalyst-")))
    news = sorted(
        (s for s in steps if s.after_stack == newest.after_stack), key=_size, reverse=True
    )
    rest = sorted(
        (s for s in steps if s.after_stack != newest.after_stack), key=_size, reverse=True
    )
    return [of_step(s) for s in (news + rest)[:limit]]


def followed_keys(data_dir: Path) -> set[str]:
    return {record["key"] for record in load(data_dir)}


def load(data_dir: Path | None) -> list[dict]:
    """The recorded follow-ups. Only `record` writes these, from numbers it checked."""
    directory = data_dir / "followups" if data_dir else None
    if directory is None or not directory.is_dir():
        return []
    return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]


def _shows_step(followup: FollowUp, before: dict, after: dict) -> bool:
    key = (followup.cell, followup.metric)
    first, last = history.metrics_of(before).get(key), history.metrics_of(after).get(key)
    if first is None or last is None:
        return False
    return history.is_step(
        followup.metric,
        first,
        last,
        history.runs_of(before).get(key),
        history.runs_of(after).get(key),
    )


def run(
    followup: FollowUp,
    out: Path,
    locks: Path | None = None,
    cpus: tuple[int, ...] = (0, 1),
    max_measured: int = BISECT_MEASUREMENTS,
) -> int:
    """Measure both builds of a step again and, if it still shows, explain it. Runs on a fresh
    runner, with the same lock files as the first measurement."""
    out.mkdir(parents=True, exist_ok=True)
    cell = next(cell for cell in SUITES[SUITE] if cell.name == followup.measured_cell)
    results = {}
    for version in (followup.before, followup.after):
        stack = stacks.Stack(version)
        if locks is not None and (locks / stack.lock_path.name).is_file():
            stack.lock_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(locks / stack.lock_path.name, stack.lock_path)
        try:
            result = measure.run_suite(
                [cell],
                stack,
                repeats=REPEATS,
                cpus=list(cpus),
                busy_check=False,
                memory_wires=MEMORY_WIRES[SUITE] if followup.metric == "peak_kb" else None,
            )
        except stacks.StackUnavailable as error:
            result = measure.unavailable_result(stack, str(error))
        (out / f"{stack.id}.json").write_text(json.dumps(result, indent=1) + "\n")
        results[version] = result
    before, after = results[followup.before], results[followup.after]
    # Only to save hours on a step that has gone: the job that records it works this out again.
    held = before["valid"] and after["valid"] and _shows_step(followup, before, after)
    print(f"{followup.key}: {'still a step' if held else 'not a step this time'}")
    if not (held and followup.explainable):
        return 0
    explanation = explain.explain(
        stacks.Stack(followup.before),
        stacks.Stack(followup.after),
        followup.measured_cell,
        metric=followup.metric,
        cpus=cpus,
        bisect=True,
        max_measured=max_measured,
    )
    (out / EXPLANATION_FILE).write_text(json.dumps(explanation.to_dict(), indent=1) + "\n")
    print(explain.render(explanation))
    return 0


# Reading what a follow-up uploaded.


def _count(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Rejected(f"{what} isn't a count")
    return value


def _number(value, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Rejected(f"{what} isn't a number")
    return value


def _text(value, what: str, limit: int = 200) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Rejected(f"{what} isn't text")
    return untrusted.clean_name(value)[:limit]


def _url(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _PULL_REQUEST_URL.fullmatch(value):
        raise Rejected("a link that isn't to a PennyLane pull request")
    return value


def _list(value, what: str, limit: int) -> list:
    if not isinstance(value, list):
        raise Rejected(f"{what} isn't a list")
    return value[:limit]


def _side(saved: dict, name: str, version: str) -> dict:
    side = saved.get(name)
    if not isinstance(side, dict) or side.get("stack") != f"catalyst-{version}":
        raise Rejected(f"the {name} side isn't {version}")
    runs = [_count(run, "a run") for run in _list(side.get("runs"), "runs", 10)]
    return {"stack": side["stack"], "runs": runs, "net": _count(side.get("net"), "net")}


def _functions(value, what: str) -> list[list]:
    rows = []
    for row in _list(value, what, explain.TOP_FUNCTIONS * 2):
        if not isinstance(row, list) or len(row) != 3:
            raise Rejected(f"a row of {what} isn't name, before, after")
        rows.append(
            [_text(row[0], "a function"), _count(row[1], "a count"), _count(row[2], "a count")]
        )
    return rows


def _candidates(value) -> dict[str, list[dict]]:
    if not isinstance(value, dict):
        raise Rejected("candidates aren't by package")
    found = {}
    for package, rows in list(value.items())[:10]:
        if not isinstance(package, str) or not _PACKAGE.fullmatch(package):
            raise Rejected("a candidate package with an odd name")
        found[package] = [
            {
                "score": _number(row.get("score"), "a score"),
                "subject": _text(row.get("subject"), "a subject"),
                "pull_request": None
                if row.get("pull_request") is None
                else _count(row["pull_request"], "a pull request"),
                "url": _url(row.get("url")),
            }
            for row in _list(rows, "candidates", explain.SAVED_CANDIDATES)
            if isinstance(row, dict)
        ]
    return found


def _bisect(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise Rejected("the bisect isn't an object")
    bisected = {}
    if "verdict" in value:
        bisected["verdict"] = _text(value["verdict"], "the verdict", 300)
    if "culprits" in value:
        culprits = []
        for row in _list(value["culprits"], "culprits", explain.MAX_CULPRITS):
            if not isinstance(row, dict) or not _COMMIT.fullmatch(str(row.get("commit"))):
                raise Rejected("a culprit isn't a commit")
            culprits.append(
                {
                    "commit": row["commit"],
                    "before": _number(row.get("before"), "a culprit's before"),
                    "after": _number(row.get("after"), "a culprit's after"),
                    "pull_request": None
                    if row.get("pull_request") is None
                    else _count(row["pull_request"], "a pull request"),
                    "url": _url(row.get("url")),
                    "subject": _text(row.get("subject"), "a subject"),
                }
            )
        bisected["culprits"] = culprits
        bisected["unresolved_commits"] = [
            len(_list(commits, "unresolved commits", 100_000))
            for commits in _list(value.get("unresolved", []), "unresolved", 20)
        ]
        bisected["measured"] = _count(value.get("measured", 0), "measured")
        bisected["stopped"] = value.get("stopped") is True
    for name in ("pennylane_alone", "pennylane_share"):
        if name in value:
            bisected[name] = _number(value[name], name)
    return bisected


def parse_explanation(data: bytes, followup: FollowUp) -> dict:
    """An explanation that is safe to keep and to show, rebuilt from the fields this project writes,
    or Rejected."""
    if len(data) > MAX_EXPLANATION_BYTES:
        raise Rejected("too large")
    try:
        saved = json.loads(data, parse_constant=_refuse_constant)
    except ValueError as error:
        raise Rejected(f"not JSON: {error}") from error
    if not isinstance(saved, dict):
        raise Rejected("not an object")
    _check_values(saved)
    if saved.get("cell") != followup.measured_cell or saved.get("metric") != followup.metric:
        raise Rejected("it explains a different step")
    candidates = _candidates(saved.get("candidates") or {})
    counts = saved.get("candidate_counts") or {}
    if not isinstance(counts, dict):
        raise Rejected("candidate counts aren't by package")
    return {
        "cell": saved["cell"],
        "metric": saved["metric"],
        "before": _side(saved, "before", followup.before),
        "after": _side(saved, "after", followup.after),
        "packages": [
            _text(p, "a package") for p in _list(saved.get("packages", []), "packages", 60)
        ],
        "python_functions": _functions(saved.get("python_functions", []), "Python functions"),
        "c_functions": _functions(saved.get("c_functions", []), "native functions"),
        "candidates": candidates,
        "candidate_counts": {
            package: _count(counts[package], "a candidate count")
            for package in candidates
            if package in counts
        },
        "bisect": _bisect(saved.get("bisect")),
    }


def _read(path: Path, limit: int) -> bytes:
    try:
        return untrusted.read_file(path, limit)
    except untrusted.UnsafeOutput as error:
        raise Rejected(str(error)) from error


def _remeasured(directory: Path, version: str) -> dict:
    path = directory / f"catalyst-{version}.json"
    if not path.exists() and not path.is_symlink():
        raise Rejected(f"no result for {version} came back")
    result = parse_result(_read(path, MAX_RESULT_BYTES))
    if result["stack"]["pennylane-catalyst"] != version:
        raise Rejected(f"the result for {version} is for another build")
    if not result["valid"]:
        raise Rejected(f"{version} measured again isn't valid")
    return result


def _first_step(followup: FollowUp, results: list[dict]) -> history.Step | None:
    return next((s for s in history.find_steps(results) if s.key == followup.key), None)


def record_one(followup: FollowUp, directory: Path, results: list[dict], run_id: str = "") -> dict:
    """What a follow-up showed, from what can be checked: the first measurement from the published
    results, the second from the files that came back."""
    step = _first_step(followup, results)
    if step is None:
        raise Rejected("not a step in the published results")
    record = {
        "schema": SCHEMA,
        "key": followup.key,
        **followup.to_dict(),
        "first": {"before": step.before, "after": step.after, "change_pct": step.change_pct},
        "second": None,
        "confirmed": None,
        "explanation": None,
        "problems": [],
        "run": run_id,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        before = _remeasured(directory, followup.before)
        after = _remeasured(directory, followup.after)
    except Rejected as error:
        record["problems"].append(str(error))
        return record
    checked = history.confirm_steps([step], [before, after])[0]
    key = (followup.cell, followup.metric)
    host = before.get("host")
    record["second"] = {
        "before": history.metrics_of(before).get(key),
        "after": history.metrics_of(after).get(key),
        "change_pct": checked.second_change_pct,
        "host": untrusted.clean_name(host.get("cpu", "") if isinstance(host, dict) else ""),
    }
    record["confirmed"] = checked.confirmed
    explanation = directory / EXPLANATION_FILE
    if followup.explainable and (explanation.exists() or explanation.is_symlink()):
        try:
            record["explanation"] = parse_explanation(
                _read(explanation, MAX_EXPLANATION_BYTES), followup
            )
        except Rejected as error:
            record["problems"].append(f"explanation: {error}")
    return record


def record(
    artifacts: Path, data_dir: Path, planned: list[FollowUp], results: list[dict], run_id: str = ""
) -> list[str]:
    """Record every planned follow-up, and say what happened. The lines go to the job's log, so each
    starts with something fixed and has no line breaks."""
    lines = []
    for followup in planned:
        try:
            outcome = record_one(followup, artifacts / f"followup-{followup.name}", results, run_id)
        except Rejected as error:
            lines.append(f"{followup.key}: refused, {error}")
            continue
        directory = data_dir / "followups"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{followup.name}.json").write_text(json.dumps(outcome, indent=1) + "\n")
        verdict = {True: "confirmed", False: "not confirmed", None: "couldn't be measured again"}
        problems = f" ({'; '.join(outcome['problems'])})" if outcome["problems"] else ""
        lines.append(f"{followup.key}: {verdict[outcome['confirmed']]}{problems}")
    return [f"- {untrusted.clean_name(line)}" for line in lines]


# Telling the owner.


PACKAGE_NAMES = {
    "pennylane": "PennyLane",
    "pennylane-lightning": "Lightning",
    "pennylane-catalyst": "Catalyst",
}


def _code(text: object, limit: int = 100) -> str:
    """Untrusted text as a code span: GitHub turns nothing inside one into a link or a mention."""
    cleaned = _TRAILING_REFERENCE.sub("", untrusted.clean_name(text))
    return f"`{cleaned[:limit]}`"


def _elsewhere(explanation: dict) -> str:
    """Where the candidates are when PennyLane isn't the cause."""
    counts = explanation.get("candidate_counts") or {}
    named = [
        f"{count} in {PACKAGE_NAMES.get(package, package)}"
        for package, count in counts.items()
        if package != "pennylane" and count
    ]
    return f"; candidate pull requests: {', '.join(named)}" if named else ""


def _pct(value: float | None) -> str:
    return "from zero" if value is None else f"{value:+.2f}%"


def cause(record: dict) -> str:
    """The cause, as issue text: pull requests by number in code spans, never linked."""
    explanation = record.get("explanation")
    if not explanation:
        if record["metric"] == "peak_kb":
            return "Not explained: memory isn't bisected"
        return "Not explained" if record["confirmed"] else ""
    bisect = explanation.get("bisect") or {}
    culprits = bisect.get("culprits") or []
    if "culprits" in bisect and not culprits:
        unresolved = sum(bisect.get("unresolved_commits") or [])
        return f"Bisected, but no single commit found ({unresolved} commits unresolved)"
    if culprits:
        span = (bisect.get("pennylane_alone") or 0) - explanation["before"]["net"]
        parts = []
        for culprit in culprits[:3]:
            number = culprit.get("pull_request")
            name = (
                f"PennyLane pull request {_code(number)}"
                if number
                else f"commit {_code(culprit['commit'][:12])}"
            )
            share = ""
            if span and culprit.get("before") is not None and culprit.get("after") is not None:
                share = f", {(culprit['after'] - culprit['before']) / span:.0%} of it"
            parts.append(f"{name}: {_code(culprit.get('subject') or '')}{share}")
        more = f"; and {len(culprits) - 3} more" if len(culprits) > 3 else ""
        return "; ".join(parts) + more
    if bisect.get("verdict"):
        return f"Bisect: {_code(bisect['verdict'], 200)}{_elsewhere(explanation)}"
    count = sum((explanation.get("candidate_counts") or {}).values())
    return f"{count} candidate pull requests, not narrowed further"


def issue(records: list[dict], page: str = PAGE) -> tuple[str, str] | None:
    """A title and body for the steps that held, or None if none did."""
    held = [r for r in records if r.get("confirmed")]
    if not held:
        return None
    newest = max(held, key=lambda r: history.version_key(r["after"]))["after"]
    if len(held) == 1:
        only = held[0]
        title = (
            f"{only['cell']} {METRIC_NAMES[only['metric']]} {_pct(only['first']['change_pct'])} "
            f"at {only['after']}, measured again"
        )
    else:
        title = f"{len(held)} changes up to {newest} held when measured again"
    lines = [
        "These changes showed up again when a fresh runner measured both builds a second time.",
        "",
        "| Between | Cell | Metric | First | Again | Cause |",
        "|---|---|---|---:|---:|---|",
    ]
    for r in held:
        lines.append(
            f"| {r['before']} → {r['after']} | {r['cell']} | {METRIC_NAMES[r['metric']]} "
            f"| {_pct(r['first']['change_pct'])} | {_pct(r['second']['change_pct'])} | {cause(r)} |"
        )
    others = len(records) - len(held)
    if others:
        lines += [
            "",
            f"{others} more didn't hold, or couldn't be measured again: see the run's summary.",
        ]
    lines += [
        "",
        f"The history is on [the page]({page}), and each follow-up is in `followups/` on the "
        "`data` branch.",
        "",
        "Written by the Follow-up workflow. It names upstream pull requests without linking "
        "them, so nothing here appears on their pages, and it posts nothing to any PennyLane "
        "repository. Anything reported upstream is written by a person.",
    ]
    return untrusted.clean_name(title), "\n".join(lines) + "\n"


def apply(steps: list[history.Step], records: list[dict]) -> list[history.Step]:
    """Steps with what a follow-up found, where nothing else has confirmed them yet."""
    found = {r["key"]: r for r in records if r.get("second")}
    applied = []
    for step in steps:
        r = found.get(step.key)
        if step.confirmed is None and r is not None:
            step = replace(
                step, confirmed=r["confirmed"], second_change_pct=r["second"]["change_pct"]
            )
        applied.append(step)
    return applied


def explanations(records: list[dict]) -> dict:
    """What the page says about each followed-up step, from its recorded explanation."""
    return dict(site.explanation_entry(r["explanation"]) for r in records if r.get("explanation"))
