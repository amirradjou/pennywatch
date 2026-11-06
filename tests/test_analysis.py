import pytest

from pennywatch import analysis


def test_spread_is_a_share_of_the_median():
    assert analysis.spread_pct([995, 1000, 1005]) == pytest.approx(1.0)


def test_a_single_run_has_no_spread():
    assert analysis.spread_pct([1000]) is None


def test_noise_is_the_range_of_the_runs():
    assert analysis.noise([100, 103, 101]) == 3


def test_noise_of_a_single_run_falls_back_to_the_assumed_figure():
    assert analysis.noise([100]) == analysis.ASSUMED_NOISE


def test_canary_well_above_the_noise_is_detected():
    verdict = analysis.canary_verdict(1_010_000, [1_000_000, 1_000_100, 1_000_000], 7)
    assert verdict["detected"]
    assert verdict["delta"] == 10_000
    assert verdict["noise"] == 100
    assert verdict["noise_ratio"] == pytest.approx(100)
    assert verdict["delta_pct"] == pytest.approx(1.0)
    assert verdict["extra_gates"] == 7


def test_canary_lost_in_the_noise_is_missed():
    verdict = analysis.canary_verdict(1_000_500, [1_000_000, 1_000_100, 1_000_000], 7)
    assert not verdict["detected"]


def test_a_canary_that_got_cheaper_is_missed():
    verdict = analysis.canary_verdict(900_000, [1_000_000, 1_000_000], 7)
    assert not verdict["detected"]


def test_canary_counts_when_runs_agree_exactly():
    verdict = analysis.canary_verdict(1_000_001, [1_000_000, 1_000_000], 1)
    assert verdict["detected"]


def test_a_single_run_judges_the_canary_against_the_assumed_noise():
    assert analysis.canary_verdict(1_000_000_000 + 20_000_000, [1_000_000_000], 7)["detected"]
    assert not analysis.canary_verdict(1_000_000_000 + 2_000_000, [1_000_000_000], 7)["detected"]


def test_relative_range():
    assert analysis.relative_range_pct([99.0, 101.0]) == pytest.approx(2.0)
