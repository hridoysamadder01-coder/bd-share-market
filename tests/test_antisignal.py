"""Machinery for the focused validation: de-overlap, corp-action detection, BH.

Each of these silently changes a headline number if it is wrong, so each is
pinned on a case where the right answer is countable by hand.
"""
import numpy as np
import pandas as pd
import pytest

from research.bigmove.antisignal import (DEX_THRESHOLD, DH_THRESHOLD, FLOOR_ERA,
                                         STRATA_FULL, STRATA_ORIGINAL,
                                         benjamini_hochberg, corp_action_flag,
                                         episode_id, regime_of, signal_mask,
                                         two_sided_p)


def frame(sym, dex, dh):
    n = len(dex)
    return pd.DataFrame({
        "symbol": sym if isinstance(sym, list) else [sym] * n,
        "date": pd.date_range("2015-01-01", periods=n, freq="D"),
        "downside_excursion_5": dex, "dist_from_20d_high": dh,
    })


# ------------------------------------------------------------------ definition
def test_the_definition_is_both_conditions_not_either():
    d = frame("A", [-0.01, -0.01, -0.10, -0.10], [-0.02, -0.20, -0.02, -0.20])
    m = signal_mask(d)
    assert list(m) == [True, False, False, False], "shallow low AND near the high"


def test_a_missing_input_is_not_a_signal():
    d = frame("A", [np.nan, -0.01], [-0.02, np.nan])
    assert list(signal_mask(d)) == [False, False], "unknown is never a signal"


def test_the_thresholds_are_the_ones_the_original_run_used():
    assert (DEX_THRESHOLD, DH_THRESHOLD) == (-0.03, -0.05)


# ------------------------------------------------------------------ de-overlap
def test_a_run_of_signal_days_collapses_to_one_episode():
    """Five consecutive signal days share almost all of a 20-day window."""
    d = frame("A", [-0.01] * 5 + [-0.50] * 30, [-0.01] * 5 + [-0.50] * 30)
    m = signal_mask(d)
    e = episode_id(d, m, min_gap=20)
    assert int((e >= 0).sum()) == 1, "one run, one episode"
    assert e.iloc[0] == 0, "and it is the FIRST day of the run"


def test_a_second_run_too_close_is_not_a_new_episode():
    dex = [-0.01, -0.01] + [-0.50] * 5 + [-0.01, -0.01]
    d = frame("A", dex, dex)
    e = episode_id(d, signal_mask(d), min_gap=20)
    assert int((e >= 0).sum()) == 1, "the runs are 7 sessions apart, windows overlap"


def test_a_second_run_far_enough_away_is_a_new_episode():
    dex = [-0.01] + [-0.50] * 25 + [-0.01]
    d = frame("A", dex, dex)
    e = episode_id(d, signal_mask(d), min_gap=20)
    assert int((e >= 0).sum()) == 2


def test_episodes_never_merge_across_symbols():
    d = pd.concat([frame("A", [-0.01, -0.01], [-0.01, -0.01]),
                   frame("B", [-0.01, -0.01], [-0.01, -0.01])], ignore_index=True)
    e = episode_id(d, signal_mask(d), min_gap=20)
    assert int((e >= 0).sum()) == 2, "each symbol gets its own first day"


def test_every_episode_id_is_unique():
    d = frame("A", [-0.01] + [-0.5] * 25 + [-0.01] + [-0.5] * 25 + [-0.01],
              [-0.01] + [-0.5] * 25 + [-0.01] + [-0.5] * 25 + [-0.01])
    e = episode_id(d, signal_mask(d), min_gap=20)
    kept = e[e >= 0]
    assert len(kept) == len(set(kept)) == 3


# ------------------------------------------------------------ corporate actions
def cframe(closes, opens):
    n = len(closes)
    return pd.DataFrame({"symbol": "A",
                         "date": pd.date_range("2015-01-01", periods=n, freq="D"),
                         "close": closes, "open": opens})


def test_a_bonus_sized_overnight_gap_is_flagged_with_its_neighbourhood():
    d = cframe([100.0] * 10, [100.0] * 5 + [70.0] + [100.0] * 4)
    flag = corp_action_flag(d, gap=0.20, window=2)
    assert flag.iloc[5], "the gap day itself"
    assert flag.iloc[3] and flag.iloc[7], "and two sessions either side"
    assert not flag.iloc[0] and not flag.iloc[9], "but not the whole series"


def test_an_ordinary_move_is_not_flagged():
    d = cframe([100.0] * 6, [100.0, 105.0, 95.0, 100.0, 110.0, 100.0])
    assert not corp_action_flag(d, gap=0.20, window=2).any()


def test_the_flag_never_leaks_across_a_symbol():
    a = cframe([100.0] * 4, [100.0] * 4).assign(symbol="A")
    b = cframe([100.0] * 4, [100.0, 50.0, 100.0, 100.0]).assign(symbol="B")
    d = pd.concat([a, b], ignore_index=True)
    flag = corp_action_flag(d, gap=0.20, window=2)
    assert not flag[d.symbol == "A"].any(), "B's ex-date is not A's"
    assert flag[d.symbol == "B"].any()


def test_the_first_row_of_a_symbol_has_no_measurable_overnight_gap():
    """No previous close means no gap — an unmeasurable one, not a zero one."""
    d = cframe([100.0] * 3, [50.0, 100.0, 100.0])
    assert not corp_action_flag(d, gap=0.20, window=0).iloc[0]


# ------------------------------------------------------------------ regime
def test_the_floor_era_is_labelled_by_its_actual_dates():
    d = pd.Series(pd.to_datetime(["2020-01-01", "2022-07-28", "2023-06-01",
                                  "2024-01-31", "2024-02-01"]))
    assert list(regime_of(d)) == ["PRE_FLOOR", "FLOOR", "FLOOR", "FLOOR", "POST_FLOOR"]
    assert FLOOR_ERA[0] == pd.Timestamp("2022-07-28")


# ------------------------------------------------------------------ FDR
def test_bh_rejects_nothing_when_every_p_is_large():
    rej, thr = benjamini_hochberg([0.4, 0.6, 0.9], q=0.10)
    assert not rej.any() and thr == 0.0


def test_bh_rejects_the_obvious_ones_and_stops():
    rej, thr = benjamini_hochberg([0.001, 0.002, 0.5, 0.9], q=0.10)
    assert list(rej) == [True, True, False, False]
    assert thr == pytest.approx(0.002)


def test_bh_is_a_step_up_not_a_per_test_threshold():
    """A p just above its own rank threshold still passes if a later one does."""
    p = [0.01, 0.03, 0.04]           # thresholds at q=.10 are .033, .067, .10
    rej, _ = benjamini_hochberg(p, q=0.10)
    assert rej.all(), "the step-up carries the middle one through"


def test_bh_ignores_non_finite_p_without_counting_them():
    rej, _ = benjamini_hochberg([0.001, np.nan, 0.002], q=0.10)
    assert rej[0] and rej[2] and not rej[1]


def test_the_p_value_matches_familiar_normal_quantiles():
    assert two_sided_p(1.96) == pytest.approx(0.05, abs=0.001)
    assert two_sided_p(-11.60) < 1e-20, "a t of -11.6 is not marginal"
    assert np.isnan(two_sided_p(np.nan))


# ------------------------------------------------------------------ strata
def test_the_hardened_strata_add_volatility_which_the_original_lacked():
    assert "vol_bucket" not in STRATA_ORIGINAL
    assert "vol_bucket" in STRATA_FULL
    assert set(STRATA_ORIGINAL) < set(STRATA_FULL), "hardening only adds"


def test_add_strata_builds_a_key_for_whichever_set_it_is_given():
    from research.bigmove.evaluate import add_strata
    n = 200
    rng = np.random.default_rng(0)
    d = pd.DataFrame({
        "year": rng.integers(2013, 2018, n), "adv20_mn": rng.random(n),
        "price_bucket": rng.choice(list("abc"), n), "prior_5d_ret": rng.normal(0, .05, n),
        "vol20": rng.random(n), "dist_from_20d_high": -rng.random(n),
        "prior_3d_ret": rng.normal(0, .05, n), "rel_turnover": rng.random(n),
    })
    k_small = add_strata(d, STRATA_ORIGINAL)["strata_key"]
    k_big = add_strata(d, STRATA_FULL)["strata_key"]
    assert k_big.nunique() >= k_small.nunique(), "finer strata cannot be coarser"
