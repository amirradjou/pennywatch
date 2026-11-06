import pytest

from pennywatch import analysis


def test_spread_is_a_share_of_the_base():
    assert analysis.spread_pct([1000, 1010], 2000) == pytest.approx(0.5)


def test_a_single_run_has_no_spread():
    assert analysis.spread_pct([1000], 2000) is None


def test_net_subtracts_the_median_baseline():
    assert analysis.net([110, 100, 120], [10, 20, 90]) == 90


def test_noise_adds_the_range_of_runs_and_baseline():
    assert analysis.noise([100, 103], [50, 52], 10_000) == 5


def test_noise_of_single_runs_falls_back_to_the_assumed_figure():
    assert analysis.noise([100], [50], 1_000_000) == 500


def test_canary_well_above_the_noise_is_detected():
    verdict = analysis.canary_verdict(1_010_000, [1_000_000, 1_000_100], [0, 100], 7)
    assert verdict["detected"]
    assert verdict["delta"] == 9_950
    assert verdict["noise_ratio"] == pytest.approx(49.75)
    assert verdict["extra_gates"] == 7


def test_canary_lost_in_the_noise_is_missed():
    verdict = analysis.canary_verdict(1_000_500, [1_000_000, 1_000_100], [0, 100], 7)
    assert not verdict["detected"]


def test_a_canary_that_got_cheaper_is_missed():
    verdict = analysis.canary_verdict(900_000, [1_000_000, 1_000_000], [0, 0], 7)
    assert not verdict["detected"]


def test_canary_counts_when_runs_agree_exactly():
    verdict = analysis.canary_verdict(1_000_001, [1_000_000, 1_000_000], [0, 0], 1)
    assert verdict["detected"]


def test_relative_range():
    assert analysis.relative_range_pct([99.0, 101.0]) == pytest.approx(2.0)
