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
    verdict = analysis.canary_verdict([(1_010_000_000, 1_000_000_000)], 7, "qjit")
    assert verdict["detected"]
    assert verdict["delta"] == 10_000_000
    assert verdict["noise"] == analysis.NOISE_FLOOR["qjit"]
    assert verdict["noise_ratio"] == pytest.approx(10_000_000 / analysis.NOISE_FLOOR["qjit"])
    assert verdict["delta_pct"] == pytest.approx(1.0)
    assert verdict["extra_gates"] == 7


def test_canary_lost_in_the_noise_is_missed():
    assert not analysis.canary_verdict([(1_001_000_000, 1_000_000_000)], 7, "qjit")["detected"]


def test_a_canary_that_got_cheaper_is_missed():
    assert not analysis.canary_verdict([(900_000_000, 1_000_000_000)], 7, "default")["detected"]


def test_a_canary_of_one_extra_instruction_is_missed():
    assert not analysis.canary_verdict([(1_000_000_001, 1_000_000_000)], 1, "default")["detected"]


def test_the_floor_depends_on_the_backend():
    run = 1_000_000_000
    assert analysis.canary_verdict([(run + 5_000_000, run)], 3, "lightning")["detected"]
    assert not analysis.canary_verdict([(run + 1_000_000, run)], 3, "lightning")["detected"]
    assert analysis.canary_verdict([(run + 7_500_000, run)], 3, "qjit")["detected"]
    assert not analysis.canary_verdict([(run + 3_000_000, run)], 3, "qjit")["detected"]


def test_the_median_of_several_pairs_outvotes_one_unlucky_layout():
    pairs = [
        (998_000_000, 1_000_000_000),
        (1_105_000_000, 1_100_000_000),
        (1_205_400_000, 1_200_000_000),
    ]
    verdict = analysis.canary_verdict(pairs, 3, "lightning")
    assert verdict["detected"]
    assert verdict["delta"] == 5_000_000


def test_several_pairs_do_not_rescue_a_canary_that_is_not_there():
    pairs = [
        (1_000_100_000, 1_000_000_000),
        (1_099_900_000, 1_100_000_000),
        (1_200_000_000, 1_200_000_000),
    ]
    assert not analysis.canary_verdict(pairs, 3, "lightning")["detected"]


def test_relative_range():
    assert analysis.relative_range_pct([99.0, 101.0]) == pytest.approx(2.0)
