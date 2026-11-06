import math

import pytest
from trotter_numpy import trotter_z_expectations

from pennywatch.harness import expected


@pytest.mark.parametrize(("wires", "steps"), sorted(expected.TROTTER_GOLDEN))
def test_trotter_golden_values_match_the_independent_simulation(wires, steps):
    assert trotter_z_expectations(wires, steps) == pytest.approx(
        expected.TROTTER_GOLDEN[(wires, steps)], abs=1e-12
    )


def test_qft_returns_to_the_starting_bits():
    assert expected.qft(4, 1) == [-1.0, 1.0, -1.0, 1.0]


def test_grover_with_no_iterations_is_uniform():
    assert expected.grover(4, 0) == pytest.approx([0.0] * 4)


def test_grover_at_the_optimal_iteration_nearly_always_finds_the_marked_state():
    wires = 10
    optimal = math.floor(math.pi / 4 * math.sqrt(2**wires))
    values = expected.grover(wires, optimal)
    assert abs(values[0]) > 0.99
    assert values[0] < 0 < values[1]  # the marked bits are 1, 0, 1, 0, ...


def test_mismatch_accepts_the_right_answer_within_tolerance():
    assert expected.mismatch("qft", 2, 1, [-1.0 + 1e-10, 1.0]) is None


def test_mismatch_reports_the_wrong_wire():
    assert "wire 1" in expected.mismatch("qft", 2, 1, [-1.0, 0.5])


def test_mismatch_reports_the_wrong_length():
    assert "expected 2 values" in expected.mismatch("qft", 2, 1, [-1.0])


def test_trotter_without_golden_values_is_an_error():
    with pytest.raises(ValueError, match="no golden"):
        expected.trotter(3, 99)
