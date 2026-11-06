import pytest

from pennywatch import analysis


def test_spread_is_a_share_of_the_median():
    assert analysis.spread_pct([995, 1000, 1005]) == pytest.approx(1.0)


def test_a_single_run_has_no_spread():
    assert analysis.spread_pct([1000]) is None


def test_noise_is_the_range_of_the_runs():
    assert analysis.noise([100, 103, 101]) == 3


def test_a_single_run_has_no_noise_of_its_own():
    assert analysis.noise([100]) == 0


def test_canary_well_above_the_noise_is_detected():
    runs = [1_000_000_000, 1_000_900_000, 1_000_000_000]
    verdict = analysis.canary_verdict(1_010_000_000, runs, 7, "qjit")
    assert verdict["detected"]
    assert verdict["delta"] == 10_000_000
    assert verdict["noise"] == 900_000
    assert verdict["noise_ratio"] == pytest.approx(10_000_000 / 900_000)
    assert verdict["delta_pct"] == pytest.approx(1.0)
    assert verdict["extra_gates"] == 7


def test_canary_lost_in_the_noise_is_missed():
    runs = [1_000_000_000, 1_001_000_000, 1_000_000_000]
    assert not analysis.canary_verdict(1_004_000_000, runs, 7, "qjit")["detected"]


def test_a_canary_that_got_cheaper_is_missed():
    assert not analysis.canary_verdict(900_000_000, [1_000_000_000] * 2, 7, "default")["detected"]


def test_runs_that_agree_exactly_do_not_make_any_canary_detected():
    runs = [1_000_000_000, 1_000_000_000]
    assert not analysis.canary_verdict(1_000_000_001, runs, 1, "default")["detected"]


def test_a_single_run_judges_the_canary_against_the_floor_for_its_backend():
    run = [1_000_000_000]
    assert analysis.canary_verdict(1_000_000_000 + 5_000_000, run, 3, "lightning")["detected"]
    assert not analysis.canary_verdict(1_000_000_000 + 1_000_000, run, 3, "lightning")["detected"]
    assert analysis.canary_verdict(1_000_000_000 + 7_500_000, run, 3, "qjit")["detected"]
    assert not analysis.canary_verdict(1_000_000_000 + 3_000_000, run, 3, "qjit")["detected"]


def test_relative_range():
    assert analysis.relative_range_pct([99.0, 101.0]) == pytest.approx(2.0)
