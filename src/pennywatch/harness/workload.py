"""One measured process: imports, then whatever the mode asks for, then a sentinel file.

The sentinel is written last and carries the circuit's answer. A run counts only if it exists and
the answer is right, because a crash after the imports still gives a stable count.

Modes:
  imports  counts only the imports and creating the device
  compile  counts only Catalyst's compile (qjit only)
  full     counts only compiling, if any, and running the circuit
  native   not for Valgrind: wall-clock time, peak memory and gate counts
"""

from pennywatch.harness import instrument

# First thing, before even the standard library's imports. What gets counted before this line is
# Python's own startup, the same in every run.
instrument.stop()

import argparse
import gc
import json
import os
import resource
import time
from importlib import metadata

DEVICES = {"default": "default.qubit", "lightning": "lightning.qubit", "qjit": "lightning.qubit"}
CIRCUITS = ("grover", "memory", "qft", "qpe", "sampling", "trotter", "variational")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["imports", "compile", "full", "native"])
    parser.add_argument("--circuit", required=True, choices=CIRCUITS)
    parser.add_argument("--backend", required=True, choices=sorted(DEVICES))
    parser.add_argument("--wires", required=True, type=int)
    parser.add_argument("--rounds", required=True, type=int)
    parser.add_argument("--extra-gates", type=int, default=0)
    parser.add_argument("--timing-repeats", type=int, default=20)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def native_report(qml, adapter, args, circuit, qnode, program, seed) -> dict:
    report = {}
    started = time.perf_counter()
    function = adapter.jit(qml, program, circuit, seed) if args.backend == "qjit" else program
    report["compile_s"] = time.perf_counter() - started

    wall_s = []
    for _ in range(args.timing_repeats):
        started = time.perf_counter()
        result = function()
        wall_s.append(time.perf_counter() - started)
    report["wall_s"] = wall_s
    report["result"] = circuit.answer(result)
    report["max_rss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["max_rss_children_kb"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    arguments = adapter.qnode_arguments(circuit)
    report["gates"] = adapter.tape_counts(qml, qnode, arguments)
    if args.backend == "qjit":
        report["gates"]["qjit"] = adapter.qjit_counts(qml, qnode, circuit, seed)
    report["bare_qnode_specs"] = adapter.bare_qnode_specs(qml, qnode, arguments)
    return report


def settle_collector() -> None:
    """Enter the counted region with the garbage collector in the same state every time. The imports
    vary, which moves the collector's allocation counters, which moves where collections land inside
    the circuit. Freezing also keeps a full collection from walking everything the imports left."""
    gc.collect()
    gc.freeze()


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.mode == "imports":
        instrument.start()

    import pennylane as qml

    from pennywatch.harness import adapter, circuits

    if args.backend == "qjit":
        import catalyst  # noqa: F401 - qml.qjit needs it, and it's slow enough to measure

    report = {"mode": args.mode}

    if args.mode == "imports":
        qml.device(DEVICES[args.backend], wires=args.wires)
    else:
        seed = circuits.SEED
        under_qjit = args.backend == "qjit"
        circuit = circuits.CIRCUITS[args.circuit](args.wires, args.rounds, args.extra_gates)
        qnode = adapter.make_qnode(qml, DEVICES[args.backend], args.wires, circuit, seed)
        program = adapter.program(qml, qnode, circuit, under_qjit)
        if args.mode in ("compile", "full"):
            settle_collector()
        if args.mode == "compile":
            if not under_qjit:
                raise SystemExit("compile mode is for the qjit backend")
            instrument.start()
            # With no arguments it compiles when qjit is applied, not at the first call.
            compiled = adapter.jit(qml, program, circuit, seed).compiled_function
            instrument.stop()
            if compiled is None:
                raise SystemExit("qjit returned without compiling")
        elif args.mode == "full":
            instrument.start()
            function = adapter.jit(qml, program, circuit, seed) if under_qjit else program
            result = function()
            instrument.stop()
            report["result"] = circuit.answer(result)
        else:
            report |= native_report(qml, adapter, args, circuit, qnode, program, seed)

    instrument.stop()
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
