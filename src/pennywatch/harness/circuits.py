"""The benchmark circuits. Each one exercises a different part of the stack."""

import math

import pennylane as qml

from pennywatch.harness.expected import basis_bits


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

    return circuit


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

    return circuit


# Transverse-field Ising chain. The constants are part of the golden values in expected.py.
TROTTER_COUPLING = 1.0
TROTTER_FIELD = 0.7
TROTTER_TIME = 1.0


def trotter(wires: int, rounds: int, extra: int):
    """Hamiltonian evolution through a second-order product formula, `rounds` steps long."""

    def circuit():
        terms = [qml.PauliZ(i) @ qml.PauliZ(i + 1) for i in range(wires - 1)]
        terms += [qml.PauliX(i) for i in range(wires)]
        coeffs = [TROTTER_COUPLING] * (wires - 1) + [TROTTER_FIELD] * wires
        _canary_gates(wires, extra)
        qml.TrotterProduct(qml.Hamiltonian(coeffs, terms), TROTTER_TIME, n=rounds, order=2)
        return _z_on_every_wire(wires)

    return circuit


CIRCUITS = {"qft": qft, "grover": grover, "trotter": trotter}
