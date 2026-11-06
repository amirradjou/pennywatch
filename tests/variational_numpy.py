"""An independent gradient of the variational circuit, sharing no code with PennyLane.

The reverse sweep is the same mathematics as adjoint differentiation, so the tests also check it
against the parameter-shift rule, which needs nothing but the forward simulation.
"""

import numpy as np

Y = np.array([[0, -1j], [1j, 0]], dtype=complex)


def _ry(angle: float) -> np.ndarray:
    cos, sin = np.cos(angle / 2), np.sin(angle / 2)
    return np.array([[cos, -sin], [sin, cos]], dtype=complex)


def _rz(angle: float) -> np.ndarray:
    return np.diag([np.exp(-0.5j * angle), np.exp(0.5j * angle)])


def _apply(state: np.ndarray, matrix: np.ndarray, wire: int) -> np.ndarray:
    """A one-wire gate by slicing: tensordot goes through BLAS, and takes 30 s at 18 wires."""
    zero = [slice(None)] * state.ndim
    one = list(zero)
    zero[wire], one[wire] = 0, 1
    zero, one = tuple(zero), tuple(one)
    result = np.empty_like(state)
    result[zero] = matrix[0, 0] * state[zero] + matrix[0, 1] * state[one]
    result[one] = matrix[1, 0] * state[zero] + matrix[1, 1] * state[one]
    return result


def _cnot(state: np.ndarray, control: int, target: int) -> np.ndarray:
    """Self-inverse: flip the target wherever the control is 1."""
    result = state.copy()
    where = [slice(None)] * state.ndim
    where[control] = 1
    # The target's axis shifts down by one once the control's has been indexed away.
    axis = target - 1 if target > control else target
    result[tuple(where)] = np.flip(state[tuple(where)], axis=axis)
    return result


def _observable(state: np.ndarray) -> np.ndarray:
    """Z on the first wire and Z on the last, applied to a state."""
    indices = np.indices(state.shape, sparse=True)
    return state * (1 - 2 * indices[0]) * (1 - 2 * indices[-1])


def _gates(angles: list) -> list[tuple]:
    gates = []
    for layer in angles:
        wires = len(layer)
        for wire, (ry, rz) in enumerate(layer):
            gates.append(("ry", wire, ry))
            gates.append(("rz", wire, rz))
        gates.extend(("cnot", wire, wire + 1) for wire in range(wires - 1))
    return gates


def _forward(state: np.ndarray, gate: tuple) -> np.ndarray:
    kind, first, second = gate
    if kind == "cnot":
        return _cnot(state, first, second)
    return _apply(state, _ry(second) if kind == "ry" else _rz(second), first)


def _backward(state: np.ndarray, gate: tuple) -> np.ndarray:
    kind, first, second = gate
    if kind == "cnot":
        return _cnot(state, first, second)
    return _apply(state, (_ry(second) if kind == "ry" else _rz(second)).conj().T, first)


def _initial(wires: int) -> np.ndarray:
    state = np.zeros((2,) * wires, dtype=complex)
    state[(0,) * wires] = 1.0
    return state


def expectation(angles: list) -> float:
    state = _initial(len(angles[0]))
    for gate in _gates(angles):
        state = _forward(state, gate)
    return float(np.real(np.vdot(state, _observable(state))))


def first_layer_ry_gradient(angles: list) -> list[float]:
    """d<O>/d(RY angle) for each wire of the first layer, by one forward and one reverse sweep."""
    wires = len(angles[0])
    gates = _gates(angles)
    state = _initial(wires)
    for gate in gates:
        state = _forward(state, gate)
    bra = _observable(state)
    gradient = {}
    for position in range(len(gates) - 1, -1, -1):
        gate = gates[position]
        if position < 2 * wires and gate[0] == "ry":
            # RY(a) = exp(-i a Y / 2), so its derivative brings down -i Y / 2.
            derivative = _apply(state, -0.5j * Y, gate[1])
            gradient[gate[1]] = float(2 * np.real(np.vdot(bra, derivative)))
        state = _backward(state, gate)
        bra = _backward(bra, gate)
    return [gradient[wire] for wire in range(wires)]


def parameter_shift_gradient(angles: list) -> list[float]:
    """The same values from the forward simulation alone."""
    values = []
    for wire in range(len(angles[0])):
        shifted = [[list(pair) for pair in layer] for layer in angles]
        shifted[0][wire][0] += np.pi / 2
        plus = expectation(shifted)
        shifted[0][wire][0] -= np.pi
        minus = expectation(shifted)
        values.append((plus - minus) / 2)
    return values
