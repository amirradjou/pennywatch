"""What each circuit's answer should be, so that a count from a circuit that didn't run is caught.

A crash after the imports gives a stable, meaningless count, so finishing isn't enough: the circuit
must also return the right numbers. Every circuit returns <Z> on each wire. Pure Python, because the
host does the checking.
"""

import math

TOLERANCE = 1e-8

# No closed form for a Trotterised Ising chain. These come from an independent NumPy simulation that
# shares no code with PennyLane; tests/test_expected.py recomputes them.
TROTTER_GOLDEN: dict[tuple[int, int], list[float]] = {
    (6, 2): [
        0.3607492381314597,
        0.635733745578811,
        0.6357337455788108,
        0.6357337455788109,
        0.635733745578811,
        0.36074923813145965,
    ],
    (18, 4): [
        0.3939107254818225,
        0.6784273570738896,
        0.6914017975321389,
        0.6914472559911111,
        0.6914472559911109,
        0.6914472559911109,
        0.6914472559911107,
        0.6914472559911113,
        0.6914472559911107,
        0.6914472559911116,
        0.6914472559911116,
        0.6914472559911102,
        0.6914472559911082,
        0.6914472559911116,
        0.6914472559911105,
        0.6914017975321353,
        0.6784273570738841,
        0.3939107254818311,
    ],
}


def basis_bits(wires: int) -> list[int]:
    """The basis state the QFT starts from, and the one Grover searches for: 1, 0, 1, 0, ..."""
    return [(i + 1) % 2 for i in range(wires)]


def qft(wires: int, rounds: int) -> list[float]:
    """QFT^4 = I, and each round is four of them, so the state ends where it started."""
    return [float((-1) ** bit) for bit in basis_bits(wires)]


def grover(wires: int, rounds: int) -> list[float]:
    """Textbook amplitude after `rounds` iterations, spread over the marked state and the rest."""
    size = 2**wires
    theta = math.asin(1 / math.sqrt(size))
    hit = math.sin((2 * rounds + 1) * theta) ** 2
    miss = (1 - hit) / (size - 1)
    return [float((-1) ** bit) * (hit - miss) for bit in basis_bits(wires)]


def trotter(wires: int, rounds: int) -> list[float]:
    try:
        return TROTTER_GOLDEN[(wires, rounds)]
    except KeyError:
        raise ValueError(f"no golden Trotter values for {wires} wires, {rounds} steps") from None


EXPECTED = {"qft": qft, "grover": grover, "trotter": trotter}


def mismatch(circuit: str, wires: int, rounds: int, result: list[float]) -> str | None:
    """Describe how `result` differs from the expected answer, or None if it doesn't."""
    want = EXPECTED[circuit](wires, rounds)
    if len(result) != len(want):
        return f"expected {len(want)} values, got {len(result)}"
    for wire, (got, expected) in enumerate(zip(result, want, strict=True)):
        if not math.isclose(got, expected, abs_tol=TOLERANCE):
            return f"wire {wire}: expected {expected}, got {got}"
    return None
