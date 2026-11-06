"""What gets measured. A new size means new results, so sizes live here and go in the JSON."""

from dataclasses import dataclass

BACKENDS = ("default", "lightning", "qjit")

# The newest patch of each of the last eight stable series. Catalyst names a whole stack.
STABLE_STACKS = ("0.8.1", "0.9.0", "0.10.0", "0.11.0", "0.12.0", "0.13.0", "0.14.1", "0.15.0")


@dataclass(frozen=True)
class Cell:
    """One circuit on one backend."""

    circuit: str
    backend: str
    wires: int
    rounds: int

    @property
    def name(self) -> str:
        return f"{self.circuit}/{self.backend}"


def _on_every_backend(circuit: str, wires: int, rounds: int) -> list[Cell]:
    return [Cell(circuit, backend, wires, rounds) for backend in BACKENDS]


# Sized to about 700 device gates where that fits, so the canary's 1% is several gates. Trotter is
# smaller: PennyLane spends about 15 million instructions per gate decomposing it, 7 times the QFT.
_FIRST_THREE = [
    *_on_every_backend("qft", wires=18, rounds=1),
    *_on_every_backend("grover", wires=18, rounds=12),
    *_on_every_backend("trotter", wires=18, rounds=4),
]

SUITES = {
    "m0": _FIRST_THREE,
    "full": [
        *_FIRST_THREE,
        # Only 12 wires, because default.qubit builds a dense matrix for the template's inverse QFT.
        *_on_every_backend("qpe", wires=12, rounds=3),
        *_on_every_backend("variational", wires=18, rounds=13),
        *_on_every_backend("sampling", wires=18, rounds=20),
    ],
    # For checking the plumbing in seconds. Its numbers mean nothing.
    "smoke": [
        *_on_every_backend("qft", wires=6, rounds=1),
        *_on_every_backend("grover", wires=6, rounds=2),
        *_on_every_backend("trotter", wires=6, rounds=2),
        *_on_every_backend("qpe", wires=6, rounds=1),
        *_on_every_backend("variational", wires=6, rounds=2),
        *_on_every_backend("sampling", wires=6, rounds=2),
    ],
}

# 2^25 amplitudes are 512 MiB, about twice what the imports hold, so the state vector dominates.
# Measured natively: Valgrind couldn't hold this for long, and it isn't counting anything here.
MEMORY_WIRES = {"full": 25}
