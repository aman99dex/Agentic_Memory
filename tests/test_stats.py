"""The statistics, checked against numbers worked out by hand."""

from eval.stats import holm, kappa, mcnemar_exact, paired, wilson


def test_mcnemar_exact_matches_hand_values():
    assert mcnemar_exact(0, 5) == 0.0625  # 2 * (1/32)
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(0, 0) == 1.0


def test_holm_adjusts_in_step_down_order():
    assert holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def test_wilson_stays_inside_zero_one_for_tiny_samples():
    low, high = wilson(0, 10)
    assert low < 1e-12 and 0 < high < 0.35


def test_paired_counts_only_the_disagreements():
    a = {str(i): i < 10 for i in range(20)}  # right on 10 of 20
    b = {str(i): i < 16 for i in range(20)}  # right on those 10 and 6 more
    r = paired(a, b)
    assert (r.delta, r.only_a, r.only_b) == (0.3, 0, 6)
    assert r.ci_low > 0 and r.p_mcnemar == 0.03125


def test_kappa_by_hand():
    human = [True, True, True, True, False, False, False, False]
    judge = [True, True, True, False, False, False, False, False]  # disagree on 1 of 8
    # observed 7/8; chance = 0.375*0.5 + 0.625*0.5 = 0.5; kappa = (0.875 - 0.5) / 0.5
    assert kappa(human, judge) == 0.75
    assert kappa(human, human) == 1.0
    assert kappa([True, False], [False, True]) == -1.0
    assert kappa([True, True], [True, True]) == 1.0  # one label only: nothing to be wrong about
