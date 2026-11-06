"""What gets measured. A new size means new results, so sizes live here and go in the JSON."""

from dataclasses import dataclass

BACKENDS = ("default", "lightning", "qjit")


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
SUITES = {
    "m0": [
        *_on_every_backend("qft", wires=18, rounds=1),
        *_on_every_backend("grover", wires=18, rounds=12),
        *_on_every_backend("trotter", wires=18, rounds=4),
    ],
    # For checking the plumbing in seconds. Its numbers mean nothing.
    "smoke": [
        *_on_every_backend("qft", wires=6, rounds=1),
        *_on_every_backend("grover", wires=6, rounds=2),
        *_on_every_backend("trotter", wires=6, rounds=2),
    ],
}
