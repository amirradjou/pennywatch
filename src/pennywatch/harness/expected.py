"""What each circuit's answer should be, so that a count from a circuit that didn't run is caught.

A crash after the imports gives a stable, meaningless count, so finishing isn't enough: the circuit
must also return the right numbers. Every circuit gives one number per wire: <Z>, except for the
variational circuit, which gives a gradient. Pure Python, because the host does the checking.
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


# The first layer's RY gradient, one per wire. Also from a separate NumPy simulation, and the tests
# recompute these too.
VARIATIONAL_GOLDEN: dict[tuple[int, int], list[float]] = {
    (6, 2): [
        -0.13024719132273815,
        -0.3014569485410673,
        0.1669465802071694,
        -0.36044399474088795,
        0.17950045115301982,
        -0.33503163635544214,
    ],
    (18, 4): [
        -0.012508127517024792,
        0.015015131714860557,
        0.04126506465053583,
        -0.016161008351890487,
        0.00042545789204320944,
        0.03562293194059588,
        0.009068368021406615,
        0.010353967393077635,
        -0.0002931957269705454,
        -0.006170336826637521,
        0.0014114269496247021,
        -0.001630858531799431,
        -0.0004487583630576907,
        0.000404563572403188,
        -0.00011588668197611488,
        3.090904062910804e-05,
        7.196464211859762e-05,
        -0.00012705142985087987,
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


def phase_bits(wires: int) -> list[int]:
    """The bits of the phase QPE estimates: one wire is the target, and the rest read these out."""
    return basis_bits(wires - 1)


def qpe_phase(wires: int) -> float:
    """A phase that fits the register exactly, so the estimate is exact rather than peaked."""
    return sum(bit / 2 ** (position + 1) for position, bit in enumerate(phase_bits(wires)))


def qpe(wires: int, rounds: int) -> list[float]:
    """The register holds the phase's bits, and the target stays in |1>."""
    return [float((-1) ** bit) for bit in phase_bits(wires)] + [-1.0]


def variational_angles(wires: int, layers: int) -> list[list[list[float]]]:
    """(RY, RZ) for each wire of each layer. A fixed formula, so the golden values stay valid."""
    return [
        [
            [0.3 + 0.1 * layer + 0.05 * wire, 0.2 - 0.03 * layer + 0.07 * wire]
            for wire in range(wires)
        ]
        for layer in range(layers)
    ]


def variational(wires: int, rounds: int) -> list[float]:
    try:
        return VARIATIONAL_GOLDEN[(wires, rounds)]
    except KeyError:
        raise ValueError(f"no golden gradient for {wires} wires, {rounds} layers") from None


def sampling(wires: int, rounds: int) -> list[float]:
    """Each round entangles and then disentangles, so every shot reads the starting bits."""
    return qft(wires, rounds)


def memory(wires: int, rounds: int) -> list[float]:
    """A uniform superposition: CNOT leaves |+...+> alone, and <Z> is 0 on every wire."""
    return [0.0] * wires


EXPECTED = {
    "qft": qft,
    "grover": grover,
    "trotter": trotter,
    "qpe": qpe,
    "variational": variational,
    "sampling": sampling,
    "memory": memory,
}


def mismatch(circuit: str, wires: int, rounds: int, result: list[float]) -> str | None:
    """Describe how `result` differs from the expected answer, or None if it doesn't."""
    want = EXPECTED[circuit](wires, rounds)
    if len(result) != len(want):
        return f"expected {len(want)} values, got {len(result)}"
    for wire, (got, expected) in enumerate(zip(result, want, strict=True)):
        if not math.isclose(got, expected, abs_tol=TOLERANCE):
            return f"wire {wire}: expected {expected}, got {got}"
    return None
