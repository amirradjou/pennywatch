"""pennywatch: measure a stack, and compare measurements."""

import argparse
import json
import sys
from pathlib import Path

from pennywatch import (
    builds,
    compare,
    container,
    explain,
    followup,
    history,
    measure,
    publish,
    report,
    site,
    stacks,
)
from pennywatch.suite import MEMORY_WIRES, PARTS, SUITES, Cell

DEFAULT_STACK = "0.15.0"
AGREEMENT_LIMIT_PCT = 0.05


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pennywatch", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="measure the suite on one stack and print the results")
    run.add_argument(
        "--stack", default=DEFAULT_STACK, help="Catalyst version (default: %(default)s)"
    )
    run.add_argument("--suite", default="m0", choices=sorted(SUITES))
    run.add_argument("--cells", help="only these cells of the suite, comma-separated")
    run.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="runs per cell, each under a different memory layout (default: %(default)s)",
    )
    run.add_argument(
        "--cpus",
        default="0,1",
        help="CPUs to use, one process on each at a time (default: %(default)s)",
    )
    run.add_argument("--no-busy-check", action="store_true", help="skip the busy-machine runs")
    run.add_argument("--out", type=Path, help="where to write the JSON (default: results/)")
    run.add_argument("--keep-raw", type=Path, help="also keep Cachegrind's files in this directory")

    lock = commands.add_parser("lock", help="resolve a stack and write its lock file")
    lock.add_argument("stack", help="Catalyst version")

    show = commands.add_parser("report", help="print a saved result as Markdown")
    show.add_argument("result", type=Path)

    diff = commands.add_parser("compare", help="how far saved results disagree")
    diff.add_argument("results", type=Path, nargs="+")
    diff.add_argument(
        "--limit",
        type=float,
        default=AGREEMENT_LIMIT_PCT,
        help="fail if any metric ranges wider than this percentage (default: %(default)s)",
    )

    steps = commands.add_parser("steps", help="what changed from one build to the next")
    steps.add_argument("results", type=Path, nargs="+")
    steps.add_argument(
        "--second-set", type=Path, nargs="*", default=[], help="separate results to confirm against"
    )
    steps.add_argument("--series", action="store_true", help="also print each metric's history")
    steps.add_argument("--latest", action="store_true", help="only the newest two builds")

    waiting = commands.add_parser("pending", help="which dev builds to measure next, as JSON")
    waiting.add_argument("--data", type=Path, required=True, help="where published results live")
    waiting.add_argument("--since", default="2026-08-08", help="first upload date to consider")
    waiting.add_argument("--limit", type=int, default=1)

    accept = commands.add_parser("accept", help="check measured results and store them")
    accept.add_argument("artifacts", type=Path)
    accept.add_argument("--into", type=Path, required=True)
    accept.add_argument("--planned", help="a JSON list: refuse results for any other build")

    follow = commands.add_parser("followup", help="measure a nightly step again and explain it")
    follow_commands = follow.add_subparsers(dest="followup", required=True)
    follow_plan = follow_commands.add_parser("plan", help="which steps to follow up, as JSON")
    follow_plan.add_argument("--data", type=Path, required=True)
    follow_plan.add_argument("--stable", type=Path, default=Path("results/stable"))
    follow_plan.add_argument("--limit", type=int, default=followup.LIMIT)
    follow_plan.add_argument("--step", default="", help="one step by name, followed up or not")
    follow_run = follow_commands.add_parser("run", help="measure one step again and explain it")
    follow_run.add_argument("--step", required=True, help="one planned step, as JSON")
    follow_run.add_argument("--out", type=Path, required=True)
    follow_run.add_argument("--locks", type=Path, help="lock files of the first measurement")
    follow_run.add_argument("--cpus", default="0,1")
    follow_run.add_argument("--max-measured", type=int, default=followup.BISECT_MEASUREMENTS)
    follow_record = follow_commands.add_parser("record", help="check what came back and store it")
    follow_record.add_argument("artifacts", type=Path)
    follow_record.add_argument("--into", type=Path, required=True)
    follow_record.add_argument("--planned", required=True, help="the planned steps, as JSON")
    follow_record.add_argument("--stable", type=Path, default=Path("results/stable"))
    follow_record.add_argument("--run-id", default="")
    follow_issue = follow_commands.add_parser("issue", help="an issue for the steps that held")
    follow_issue.add_argument("--data", type=Path, required=True)
    follow_issue.add_argument("--planned", required=True, help="the planned steps, as JSON")
    follow_issue.add_argument("--body", type=Path, required=True, help="where to write the body")

    why = commands.add_parser("explain", help="narrow a change down to its pull requests")
    why.add_argument("before", help="Catalyst version before the change")
    why.add_argument("after", help="Catalyst version after the change")
    why.add_argument("--cell", required=True)
    why.add_argument(
        "--metric", default="instructions", choices=["instructions", "compile", "imports"]
    )
    why.add_argument("--bisect", action="store_true", help="also bisect PennyLane commit by commit")
    why.add_argument(
        "--with",
        dest="extras",
        default="",
        help="packages the commits need and the older stack lacks, comma-separated",
    )
    why.add_argument("--cpus", default="0,1")
    why.add_argument("--out", type=Path, default=Path("results/explanations"))

    page = commands.add_parser("site", help="build the public page")
    page.add_argument("--stable", type=Path, default=Path("results/stable"))
    page.add_argument("--second-set", type=Path, default=Path("results/stable-repeat"))
    page.add_argument("--data", type=Path, help="the data branch's checkout, with nightly results")
    page.add_argument("--explanations", type=Path, default=Path("results/explanations"))
    page.add_argument("--out", type=Path, default=Path("site"))
    return parser


def result_path(result: dict, requested: Path | None) -> Path:
    """A rejected run keeps its numbers for debugging under a name nothing will publish."""
    if requested is None:
        stamp = result["started_at"].replace(":", "").replace("+0000", "Z")
        part = f".{result['part']}" if result.get("part") else ""
        requested = container.REPO_ROOT / "results" / f"{result['stack']['id']}-{stamp}{part}.json"
    if result["valid"]:
        return requested
    return requested.with_suffix(".rejected.json")


def chosen_cells(suite: str, names: str | None) -> list[Cell]:
    cells = SUITES[suite]
    if names is None:
        return cells
    wanted = set(names.split(","))
    if unknown := wanted - {cell.name for cell in cells}:
        raise SystemExit(f"not in suite {suite}: {', '.join(sorted(unknown))}")
    return [cell for cell in cells if cell.name in wanted]


def run_command(args: argparse.Namespace) -> int:
    cells = chosen_cells(args.suite, args.cells)
    stack = stacks.Stack(args.stack)
    try:
        result = measure.run_suite(
            cells,
            stack,
            repeats=args.repeats,
            cpus=[int(cpu) for cpu in args.cpus.split(",")],
            busy_check=not args.no_busy_check,
            keep_raw=args.keep_raw,
            memory_wires=None if args.cells else MEMORY_WIRES.get(args.suite),
            # Too small for 1% extra gates to clear the noise, and its numbers mean nothing anyway.
            judge_canaries=args.suite != "smoke",
            # A part, such as kokkos's, has a verdict of its own, and a circuit an older build of
            # its backend can't run is named rather than failing the rest.
            part=PARTS.get(args.suite),
            drop_unrunnable=args.suite in PARTS,
        )
    except stacks.StackUnavailable as error:
        result = measure.unavailable_result(stack, str(error), PARTS.get(args.suite))
    path = result_path(result, args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    print(
        report.render(result) if result["cells"] else f"Can't be measured: {result['problems'][0]}"
    )
    print(f"Saved to {path}")
    return 0 if result["valid"] else 1


def compare_command(args: argparse.Namespace) -> int:
    results = [json.loads(path.read_text()) for path in args.results]
    if len(results) < 2:
        print("compare needs at least two results", file=sys.stderr)
        return 2
    comparison = compare.compare(results)
    print(compare.render(results, comparison))
    return 0 if comparison["worst_pct"] <= args.limit else 1


def steps_command(args: argparse.Namespace) -> int:
    results = history.load_results(args.results)
    if args.latest:
        results = history.in_build_order(results)[-2:]
    found = history.find_steps(results)
    if args.second_set:
        found = history.confirm_steps(found, history.load_results(args.second_set))
    if args.series:
        for metric in ("instructions", "compile", "imports"):
            print(history.render_series(results, metric))
    print(history.render_steps(found))
    return 0


def _json_files(directory: Path | None) -> list[Path]:
    return sorted(directory.glob("*.json")) if directory and directory.is_dir() else []


def site_command(args: argparse.Namespace) -> int:
    stable = history.load_results(_json_files(args.stable))
    nightly_dir = args.data / "nightly" if args.data else None
    nightly = history.load_results(_json_files(nightly_dir))
    gaps_dir = nightly_dir / "rejected" if nightly_dir else None
    # A build is missing when its main run is: a rejected part only lacks that part's backend.
    gaps = [
        gap
        for gap in (json.loads(path.read_text()) for path in _json_files(gaps_dir))
        if not gap.get("part")
    ]
    # As one history: the step from the last release to the first dev build counts too.
    steps = history.confirm_steps(
        history.find_steps(stable + nightly),
        history.load_results(_json_files(args.second_set)),
    )
    records = followup.load(args.data)
    steps = followup.apply(steps, records)
    # A person's check beats a hand-run explanation, which beats a follow-up's.
    explanations = followup.explanations(records) | site.load_explanations(args.explanations)
    print(site.write(site.page_data(stable, nightly, steps, gaps, explanations), args.out))
    return 0


def _planned(text: str) -> list[followup.FollowUp]:
    return [followup.FollowUp.from_dict(item) for item in json.loads(text)]


def followup_command(args: argparse.Namespace) -> int:
    if args.followup == "plan":
        planned = followup.plan(
            history.load_results(_json_files(args.data / "nightly")),
            followup.followed_keys(args.data),
            limit=args.limit,
            step=args.step,
            stable=history.load_results(_json_files(args.stable)),
        )
        print(json.dumps([item.to_dict() for item in planned]))
        return 0
    if args.followup == "run":
        return followup.run(
            followup.FollowUp.from_dict(json.loads(args.step)),
            args.out,
            locks=args.locks,
            cpus=tuple(int(cpu) for cpu in args.cpus.split(",")),
            max_measured=args.max_measured,
        )
    if args.followup == "record":
        results = history.load_results(_json_files(args.stable)) + history.load_results(
            _json_files(args.into / "nightly")
        )
        for line in followup.record(
            args.artifacts, args.into, _planned(args.planned), results, args.run_id
        ):
            print(line)
        return 0
    planned = {item.key for item in _planned(args.planned)}
    written = followup.issue([r for r in followup.load(args.data) if r["key"] in planned])
    if written is not None:
        title, body = written
        args.body.write_text(body)
        print(title)
    return 0


def explain_command(args: argparse.Namespace) -> int:
    explanation = explain.explain(
        stacks.Stack(args.before),
        stacks.Stack(args.after),
        args.cell,
        metric=args.metric,
        cpus=tuple(int(cpu) for cpu in args.cpus.split(",")),
        bisect=args.bisect,
        extras=tuple(package for package in args.extras.split(",") if package),
    )
    print(explain.render(explanation))
    print(f"Saved to {explain.save(explanation, args.out)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "lock":
        print(stacks.write_lock(stacks.Stack(args.stack), container.ensure_image()))
        return 0
    if args.command == "report":
        print(report.render(json.loads(args.result.read_text())))
        return 0
    if args.command == "steps":
        return steps_command(args)
    if args.command == "site":
        return site_command(args)
    if args.command == "explain":
        return explain_command(args)
    if args.command == "followup":
        return followup_command(args)
    if args.command == "pending":
        print(json.dumps(builds.pending(publish.done_builds(args.data), args.since, args.limit)))
        return 0
    if args.command == "accept":
        planned = set(json.loads(args.planned)) if args.planned else None
        for outcome in publish.accept(args.artifacts, args.into, planned):
            print(outcome)
        return 0
    return compare_command(args)


if __name__ == "__main__":
    sys.exit(main())
