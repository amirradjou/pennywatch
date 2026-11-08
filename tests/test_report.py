from pennywatch import report


def measured_cell(name="qft/lightning", spread=0.006, ratio=129.0, detected=True):
    circuit, backend = name.split("/")
    return {
        "name": name,
        "circuit": circuit,
        "backend": backend,
        "gates": {
            "top": {"gates": 90, "depth": 73},
            "device": {"gates": 806, "depth": 165},
        },
        "wall_s": [0.9, 0.12, 0.29],
        "bare_qnode_specs": "ok",
        "instructions": {
            "net": 1_800_000_000,
            "spread_pct": spread,
            "processes": {"python": 1, "compiler": 0, "linker": 0, "other": 0},
        },
        "canary": {
            "extra_gates": 8,
            "delta": 14_600_000,
            "delta_pct": 0.81,
            "noise_ratio": ratio,
            "detected": detected,
        },
    }


def base_result(cells):
    return {
        "started_at": "2026-11-05T00:00:00+00:00",
        "valid": True,
        "problems": [],
        "repeats": 3,
        "cpus": 2,
        "stack": {
            "pennylane": "0.45.1",
            "pennylane-lightning": "0.45.0",
            "pennylane-catalyst": "0.15.0",
        },
        "image": {"tag": "img", "valgrind": "valgrind-3.24.0"},
        "host": {"cpu": "Some CPU"},
        "cells": cells,
    }


def test_stability_names_the_worst_cell():
    cells = [measured_cell("qft/lightning", spread=0.006), measured_cell("qft/qjit", spread=0.02)]
    row = report.stability_row(base_result(cells))
    assert "qft/qjit" in row and "0.0200%" in row


def test_canary_row_counts_detections_and_names_the_weakest():
    cells = [
        measured_cell("qft/lightning", ratio=129.0),
        measured_cell("qft/qjit", ratio=3.0, detected=False),
    ]
    row = report.canary_row(base_result(cells))
    assert "1 of 2" in row and "qft/qjit" in row


def test_wall_row_ignores_the_first_call():
    row = report.wall_row(base_result([measured_cell()]))
    assert "0.120 to 0.290" in row


def test_gates_row_shows_both_levels():
    row = report.gates_row(base_result([measured_cell()]))
    assert "90 → 806" in row and "73 → 165" in row


def test_api_row_quotes_the_rejection():
    cell = measured_cell()
    cell["bare_qnode_specs"] = "ValueError: qp.specs no longer supports"
    assert "no longer supports" in report.api_row(base_result([cell]))


def test_missing_sections_say_so_rather_than_crash():
    result = base_result([{"name": "qft/lightning", "circuit": "qft", "backend": "lightning"}])
    text = report.render(result)
    assert "Not measured" in text
    assert "failed" in text


def test_a_rejected_run_lists_its_problems():
    result = base_result([measured_cell()]) | {"valid": False, "problems": ["canary missed"]}
    text = report.render(result)
    assert "REJECTED" in text and "canary missed" in text


def test_a_part_names_its_version_and_what_it_couldnt_run():
    result = base_result([measured_cell("qft/kokkos")])
    result["stack"] = result["stack"] | {"pennylane-lightning-kokkos": "0.38.0"}
    result["unavailable"] = {"grover/kokkos": "DecompositionUndefinedError"}
    text = report.render(result)
    assert "Lightning Kokkos 0.38.0" in text
    assert "- grover/kokkos: DecompositionUndefinedError" in text


def compiled_cell(compiler):
    cell = measured_cell("qft/qjit")
    cell["compile"] = {"net": 3_000_000_000, "spread_pct": 0.01, "compiler_process": compiler}
    return cell


def test_compiler_row_says_when_the_compiler_process_is_exact():
    row = report.compiler_row(base_result([compiled_cell([784, 784, 784])]))
    assert "the same in every run" in row


def test_compiler_row_says_when_the_compiler_process_varies():
    row = report.compiler_row(base_result([compiled_cell([784, 785])]))
    assert "varying" in row


def test_wall_row_draws_no_conclusion_from_a_steady_clock():
    cell = measured_cell()
    cell["wall_s"] = [9.0, 1.0, 1.001]
    assert "invisible" not in report.wall_row(base_result([cell]))


def test_busy_row_measures_the_movement_against_the_net_cost():
    result = base_result([measured_cell()]) | {
        "busy_check": {
            "cell": "qft/lightning",
            "spinners": 4,
            "quiet_runs": [1_800_000_000, 1_800_000_000, 1_800_000_000],
            "busy_count": 1_800_180_000,
            "wall_ratio": 3.8,
        }
    }
    row = report.busy_row(result)
    assert "0.0100%" in row  # 180,000 of 1.8 billion
    assert "3.8 times slower" in row


def test_imports_row_lists_each_backend_once():
    cell = measured_cell() | {"imports": {"net": 8_300_000_000, "spread_pct": 0.00002}}
    row = report.imports_row(base_result([cell, cell]))
    assert row.count("lightning") == 1
    assert "8.30B" in row
