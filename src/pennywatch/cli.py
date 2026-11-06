"""pennywatch: measure a stack, and compare measurements."""

import argparse
import json
import sys
from pathlib import Path

from pennywatch import compare, container, measure, report, stacks
from pennywatch.suite import SUITES

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
    run.add_argument("--repeats", type=int, default=3, help="runs per cell (default: %(default)s)")
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
    return parser


def result_path(result: dict, requested: Path | None) -> Path:
    """A rejected run keeps its numbers for debugging under a name nothing will publish."""
    if requested is None:
        stamp = result["started_at"].replace(":", "").replace("+0000", "Z")
        requested = container.REPO_ROOT / "results" / f"{result['stack']['id']}-{stamp}.json"
    if result["valid"]:
        return requested
    return requested.with_suffix(".rejected.json")


def run_command(args: argparse.Namespace) -> int:
    result = measure.run_suite(
        SUITES[args.suite],
        stacks.Stack(args.stack),
        repeats=args.repeats,
        cpus=[int(cpu) for cpu in args.cpus.split(",")],
        busy_check=not args.no_busy_check,
        keep_raw=args.keep_raw,
    )
    path = result_path(result, args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    print(report.render(result))
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
    return compare_command(args)


if __name__ == "__main__":
    sys.exit(main())
