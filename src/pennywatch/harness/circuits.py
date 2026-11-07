"""The benchmark circuits. Each one exercises a different part of the stack."""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pennylane as qml

from pennywatch.harness.expected import (
    basis_bits,
    qpe_phase,
    variational_angles,
)

SHOTS = 10_000
SEED = 1234


def _floats(result) -> list[float]:
    return [float(value) for value in result]


@dataclass(frozen=True)
class Circuit:
    """A circuit and how it gets run. Most need only the first field."""

    body: Callable
    answer: Callable = (
        _floats  # what the stack returned, as the one number per wire the host checks
    )
    gradient_of: list | None = None  # differentiate at these angles instead of evaluating
    shots: int | None = None


def _z_on_every_wire(wires: int) -> list:
    return [qml.expval(qml.PauliZ(wire)) for wire in range(wires)]


def _canary_gates(wires: int, extra: int) -> None:
    """The canary's extra gates. RX(2 pi) is -I, a global phase, so the expected answer still holds
    while the simulator does the full work of a non-diagonal gate.

    They go first, before any entanglement, and never last: on default.qubit the final gate decides
    the memory layout of the state, which changes what every measurement costs. Appended at the end,
    7 gates made a run 23 million instructions cheaper and 3 made another 95 million dearer."""
    for i in range(extra):
        qml.RX(2 * math.pi, wires=i % wires)


def qft(wires: int, rounds: int, extra: int):
    """Controlled phases and swaps. QFT^4 = I, so each round returns to the start and the answer is
    exact. Not QFT followed by adjoint(QFT): default.qubit decomposes QFT from 6 wires up but keeps
    Adjoint(QFT) whole and builds its dense matrix, which is 512 GiB at 18 wires."""

    def circuit():
        qml.BasisState(basis_bits(wires), wires=range(wires))
        _canary_gates(wires, extra)
        for _ in range(4 * rounds):
            qml.QFT(wires=range(wires))
        return _z_on_every_wire(wires)

    return Circuit(circuit)


def _flip_sign_of(bits: list[int]) -> None:
    """Phase flip on one basis state. Not qml.FlipSign: default.qubit builds a dense matrix for its
    controlled Z, which takes tens of GiB by 16 wires, while MultiControlledX has a fast path."""
    last = len(bits) - 1
    zeros = [wire for wire, bit in enumerate(bits) if bit == 0]
    for wire in zeros:
        qml.PauliX(wire)
    qml.Hadamard(last)
    qml.MultiControlledX(wires=range(len(bits)))
    qml.Hadamard(last)
    for wire in zeros:
        qml.PauliX(wire)


def grover(wires: int, rounds: int, extra: int):
    """Multi-controlled gates, searching for the same bit string the QFT starts from."""

    def circuit():
        _canary_gates(wires, extra)
        for wire in range(wires):
            qml.Hadamard(wire)
        for _ in range(rounds):
            _flip_sign_of(basis_bits(wires))
            qml.GroverOperator(wires=range(wires))
        return _z_on_every_wire(wires)

    return Circuit(circuit)


# Transverse-field Ising chain. The constants are part of the golden values in expected.py.
TROTTER_COUPLING = 1.0
TROTTER_FIELD = 0.7
TROTTER_TIME = 1.0


def _ising_hamiltonian(wires: int):
    terms = [qml.PauliZ(i) @ qml.PauliZ(i + 1) for i in range(wires - 1)]
    terms += [qml.PauliX(i) for i in range(wires)]
    coeffs = [TROTTER_COUPLING] * (wires - 1) + [TROTTER_FIELD] * wires
    return qml.Hamiltonian(coeffs, terms)


def trotter(wires: int, rounds: int, extra: int):
    """Hamiltonian evolution through a second-order product formula, `rounds` steps long."""

    def circuit():
        _canary_gates(wires, extra)
        qml.TrotterProduct(_ising_hamiltonian(wires), TROTTER_TIME, n=rounds, order=2)
        return _z_on_every_wire(wires)

    return Circuit(circuit)


def qpe(wires: int, rounds: int, extra: int):
    """Quantum phase estimation of a phase gate, through the template, with its inverse QFT. Each
    round runs it and undoes it, so the last run still reads out the exact phase."""
    target = wires - 1
    estimation = range(wires - 1)
    angle = 2 * math.pi * qpe_phase(wires)

    def circuit():
        qml.PauliX(target)
        _canary_gates(wires, extra)
        unitary = qml.PhaseShift(angle, wires=target)
        for _ in range(rounds):
            qml.QuantumPhaseEstimation(unitary, estimation_wires=estimation)
            qml.adjoint(qml.QuantumPhaseEstimation)(unitary, estimation_wires=estimation)
        qml.QuantumPhaseEstimation(unitary, estimation_wires=estimation)
        return _z_on_every_wire(wires)

    return Circuit(circuit)


def variational(wires: int, rounds: int, extra: int):
    """A two-wire correlation over layers of RY, RZ and a CNOT ladder, differentiated by the adjoint
    method. The gradient is the point, so this is the only circuit that takes arguments. Not an
    Ising energy: default.qubit builds a dense matrix for a Hamiltonian here, 1 TiB at 18 wires."""

    def circuit(angles):
        _canary_gates(wires, extra)
        for layer in range(rounds):
            for wire in range(wires):
                qml.RY(angles[layer][wire][0], wires=wire)
                qml.RZ(angles[layer][wire][1], wires=wire)
            for wire in range(wires - 1):
                qml.CNOT(wires=[wire, wire + 1])
        return qml.expval(qml.PauliZ(0) @ qml.PauliZ(wires - 1))

    def first_layer_ry(gradient) -> list[float]:
        return _floats(np.asarray(gradient)[0, :, 0])

    return Circuit(circuit, answer=first_layer_ry, gradient_of=variational_angles(wires, rounds))


def sampling(wires: int, rounds: int, extra: int):
    """Finite shots, which go through each stack's sampler and nothing else here does. Each round
    entangles every wire and undoes it, so every shot gives the starting bits and the answer is
    exact whatever the random numbers were."""

    def circuit():
        qml.BasisState(basis_bits(wires), wires=range(wires))
        _canary_gates(wires, extra)
        for _ in range(rounds):
            qml.Hadamard(0)
            for wire in range(wires - 1):
                qml.CNOT(wires=[wire, wire + 1])
            for wire in reversed(range(wires - 1)):
                qml.CNOT(wires=[wire, wire + 1])
            qml.Hadamard(0)
        return qml.sample(wires=range(wires))

    def z_from_shots(samples) -> list[float]:
        return _floats(1 - 2 * np.asarray(samples).mean(axis=0))

    return Circuit(circuit, answer=z_from_shots, shots=SHOTS)


def memory(wires: int, rounds: int, extra: int):
    """A state vector that fills memory, for peak memory. Only a few gates: run natively, since
    Valgrind couldn't hold this many wires for long."""

    def circuit():
        for wire in range(wires):
            qml.Hadamard(wire)
        for wire in range(wires - 1):
            qml.CNOT(wires=[wire, wire + 1])
        return _z_on_every_wire(wires)

    return Circuit(circuit)


CIRCUITS = {
    "qft": qft,
    "grover": grover,
    "trotter": trotter,
    "qpe": qpe,
    "variational": variational,
    "sampling": sampling,
    "memory": memory,
}
