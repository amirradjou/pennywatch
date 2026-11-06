"""An independent simulation of the Trotter circuit, sharing no code with PennyLane."""

import numpy as np

COUPLING = 1.0
FIELD = 0.7
TIME = 1.0


def _apply_zz(state: np.ndarray, first: int, second: int, angle: float) -> np.ndarray:
    """exp(-i angle Z_first Z_second) on a tensor with one axis per wire."""
    wires = state.ndim
    indices = np.indices(state.shape, sparse=True)
    parity = (indices[first] + indices[second]) % 2
    assert wires >= 2
    return state * np.exp(-1j * angle * (1 - 2 * parity))


def _apply_x(state: np.ndarray, wire: int, angle: float) -> np.ndarray:
    """exp(-i angle X_wire)."""
    return np.cos(angle) * state - 1j * np.sin(angle) * np.flip(state, axis=wire)


def trotter_z_expectations(wires: int, steps: int) -> list[float]:
    """<Z> on each wire after a second-order product formula, starting from |0...0>. A step applies
    every term for half the step's time, then every term again in reverse."""
    state = np.zeros((2,) * wires, dtype=complex)
    state[(0,) * wires] = 1.0
    half_step = TIME / steps / 2
    forward = [("zz", wire, COUPLING) for wire in range(wires - 1)]
    forward += [("x", wire, FIELD) for wire in range(wires)]
    for _ in range(steps):
        for kind, wire, coefficient in forward + forward[::-1]:
            angle = coefficient * half_step
            if kind == "zz":
                state = _apply_zz(state, wire, wire + 1, angle)
            else:
                state = _apply_x(state, wire, angle)
    probabilities = np.abs(state) ** 2
    values = []
    for wire in range(wires):
        others = tuple(axis for axis in range(wires) if axis != wire)
        marginal = probabilities.sum(axis=others)
        values.append(float(marginal[0] - marginal[1]))
    return values
