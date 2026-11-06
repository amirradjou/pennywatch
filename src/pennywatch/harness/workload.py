"""One measured process: imports, then whatever the mode asks for, then a sentinel file.

The sentinel is written last and carries the circuit's answer. A run counts only if it exists and
the answer is right, because a crash after the imports still gives a stable count.

Modes:
  imports  the baseline subtracted from the others: imports and device creation
  compile  also Catalyst's compile, without running (qjit only)
  full     also runs the circuit
  native   not for Valgrind: wall-clock time, peak memory and gate counts
"""

import argparse
import json
import os
import resource
import time
from importlib import metadata

import pennylane as qml

from pennywatch.harness import adapter, circuits

DEVICES = {"default": "default.qubit", "lightning": "lightning.qubit", "qjit": "lightning.qubit"}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["imports", "compile", "full", "native"])
    parser.add_argument("--circuit", required=True, choices=sorted(circuits.CIRCUITS))
    parser.add_argument("--backend", required=True, choices=sorted(DEVICES))
    parser.add_argument("--wires", required=True, type=int)
    parser.add_argument("--rounds", required=True, type=int)
    parser.add_argument("--extra-gates", type=int, default=0)
    parser.add_argument("--timing-repeats", type=int, default=20)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def floats(result) -> list[float]:
    return [float(value) for value in result]


def native_report(args, qnode) -> dict:
    report = {}
    started = time.perf_counter()
    function = qml.qjit(qnode) if args.backend == "qjit" else qnode
    report["compile_s"] = time.perf_counter() - started

    wall_s = []
    for _ in range(args.timing_repeats):
        started = time.perf_counter()
        result = function()
        wall_s.append(time.perf_counter() - started)
    report["wall_s"] = wall_s
    report["result"] = floats(result)
    report["max_rss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["max_rss_children_kb"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    report["gates"] = adapter.tape_counts(qml, qnode)
    if args.backend == "qjit":
        report["gates"]["qjit"] = adapter.qjit_counts(qml, function)
    report["bare_qnode_specs"] = adapter.bare_qnode_specs(qml, qnode)
    return report


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.backend == "qjit":
        import catalyst  # noqa: F401 - qml.qjit needs it, and it belongs in the baseline

    device = qml.device(DEVICES[args.backend], wires=args.wires)
    report = {"mode": args.mode}

    if args.mode != "imports":
        body = circuits.CIRCUITS[args.circuit](args.wires, args.rounds, args.extra_gates)
        qnode = qml.QNode(body, device)
        if args.mode == "compile":
            if args.backend != "qjit":
                raise SystemExit("compile mode is for the qjit backend")
            # A QNode without arguments compiles when qjit is applied, not at the first call.
            if qml.qjit(qnode).compiled_function is None:
                raise SystemExit("qjit returned without compiling")
        elif args.mode == "full":
            function = qml.qjit(qnode) if args.backend == "qjit" else qnode
            report["result"] = floats(function())
        else:
            report |= native_report(args, qnode)

    report["versions"] = {
        package: metadata.version(package)
        for package in ("pennylane", "pennylane-lightning", "pennylane-catalyst")
    }
    temporary = f"{args.out}.partial"
    with open(temporary, "w") as file:
        json.dump(report, file)
    os.replace(temporary, args.out)


if __name__ == "__main__":
    main()
