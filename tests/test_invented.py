"""The invented quantities, and the look-ahead test that caught one of them.

`test_no_feature_reads_the_future` is the reason this file exists. The first
version of `qt_fusion` combined its inputs with `rank(pct=True)` over the whole
frame — a percentile that depends on every row after t, so the number could not
have been computed live and the measurement using it was reading its own future.
It inflated the h4 result from "not established" to "established", which is
exactly the kind of false positive that survives review and dies in production.

The test corrupts every frame past a cut point and requires every feature value
*before* the cut to be bit-identical. A whole-sample rank fails it immediately.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from seeing.features.invented import (FLOW_WINDOW, MIN_FLOW_RATE, WALK_SIZES,
                                      causal_rank, herfindahl, invented_frame,
                                      walk_book)

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- 2. cost geometry
def test_walking_the_book_prices_the_whole_order_not_the_touch():
    """A 300-share order that eats two levels does not fill at the first one."""
    asks = [(10.1, 100), (10.2, 100), (10.3, 100)]
    slip, filled = walk_book(asks, 300, mid=10.05, tick=0.1, side="ask")
    # VWAP = (100*10.1 + 100*10.2 + 100*10.3)/300 = 10.2 ; (10.2 - 10.05)/0.1 = 1.5
    assert slip == pytest.approx(1.5)
    assert filled == pytest.approx(1.0)


def test_a_book_that_cannot_fill_the_order_reports_unknown_not_cheap():
    """Filling the remainder at the last shown price prices the hardest fill cheapest."""
    asks = [(10.1, 50)]
    slip, filled = walk_book(asks, 500, mid=10.05, tick=0.1, side="ask")
    assert np.isnan(slip), "an unfillable walk has no cost, it does not have a low cost"
    assert filled == pytest.approx(0.1), "and how much COULD fill is recorded"


def test_the_two_sides_are_priced_in_opposite_directions():
    bids = [(10.0, 500)]
    asks = [(10.1, 500)]
    buy, _ = walk_book(asks, 100, 10.05, 0.1, "ask")
    sell, _ = walk_book(bids, 100, 10.05, 0.1, "bid")
    assert buy == pytest.approx(0.5) and sell == pytest.approx(0.5), \
        "both sides cost half a spread; slippage is signed against the trader"


def test_cost_geometry_is_unobservable_without_a_tick():
    assert np.isnan(walk_book([(10.1, 100)], 100, 10.05, None, "ask")[0])
    assert np.isnan(walk_book([(10.1, 100)], 100, None, 0.1, "ask")[0])


# --------------------------------------------------------------------------- 3. shape
def test_concentration_separates_a_wall_from_a_staircase():
    wall = herfindahl([(10.0, 1000), (9.9, 1), (9.8, 1)])
    stair = herfindahl([(10.0, 334), (9.9, 333), (9.8, 333)])
    assert wall > 0.99 and stair == pytest.approx(1 / 3, abs=0.01)
    assert np.isnan(herfindahl([]))


# ------------------------------------------------------------------- the causal rank
def test_a_causal_rank_uses_only_the_past():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 0.0])
    key = pd.DataFrame({"symbol": ["X"] * 6})
    r = causal_rank(s, key, min_history=2)
    assert np.isnan(r.iloc[0]) and np.isnan(r.iloc[1]), "no percentile without history"
    assert r.iloc[2] == pytest.approx(0.5), "3.0 beats both prior values → 1.0 − 0.5"
    assert r.iloc[5] == pytest.approx(-0.5), "0.0 beats none of the five priors"


def test_a_causal_rank_never_crosses_a_symbol():
    s = pd.Series([1.0, 2.0, 3.0, 100.0, 200.0, 300.0])
    key = pd.DataFrame({"symbol": ["A", "A", "A", "B", "B", "B"]})
    r = causal_rank(s, key, min_history=2)
    assert np.isnan(r.iloc[3]), "B's first frame has no B history, whatever A did"


# ---------------------------------------------------------------------- the frame
def _frame(n=60, seed=1, cadence=45.0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        base = 10.0 + rng.normal(scale=0.05)
        bids = [(round(base - 0.1 * k, 2), float(rng.integers(50, 900))) for k in range(5)]
        asks = [(round(base + 0.1 * (k + 1), 2), float(rng.integers(50, 900))) for k in range(5)]
        rows.append({
            "symbol": "X", "session": "2026-09-06",
            "t_frame": T0 + timedelta(seconds=cadence * i),
            "frame_dt_s": np.nan if i == 0 else cadence,
            "bid_levels": bids, "ask_levels": asks,
            "mid": (bids[0][0] + asks[0][0]) / 2, "tick_size": 0.1,
            "bid_qty1": bids[0][1], "ask_qty1": asks[0][1],
            "n_bid": 5, "n_ask": 5,
            "spread": asks[0][0] - bids[0][0],
            "bid_depth_top5": sum(q for _, q in bids),
            "ask_depth_top5": sum(q for _, q in asks),
            "liquidity_top5": sum(q for _, q in bids) + sum(q for _, q in asks),
            "imb_l1": (bids[0][1] - asks[0][1]) / (bids[0][1] + asks[0][1]),
            "int_d_volume": float(rng.integers(0, 4000)),
            "signed_int_volume": float(rng.normal(0, 800)),
        })
    return pd.DataFrame(rows)


def test_every_invented_quantity_computes_and_varies_on_a_realistic_frame():
    f = invented_frame(_frame())
    for c in ["ttc_ask_s", "ttc_bid_s", "ttc_asym", "hhi_bid", "hhi_ask", "hhi_asym",
              "qt_flow", "qt_fusion", "pressure_per_cost"]:
        assert c in f, c
        assert f[c].notna().any(), f"{c} is never computable"
        assert f[c].std(skipna=True) > 0, f"{c} is constant — not a measurement"
    for Q in WALK_SIZES:
        for pre in ("slip_buy", "slip_sell", "wbc_asym", "round_trip", "walk_filled_buy"):
            assert f"{pre}_{Q}" in f


def test_no_feature_reads_the_future():
    """Corrupt every frame past a cut; every value before it must be identical.

    This caught `qt_fusion` combining its inputs with a whole-sample percentile
    rank — a number that depends on rows after t and inflated the h4 result into
    false significance. It is the single most important test in this file.
    """
    f = _frame(n=80)
    cut = 40
    g = f.copy()
    for c in ("mid", "bid_qty1", "ask_qty1", "signed_int_volume", "int_d_volume",
              "liquidity_top5", "bid_levels", "ask_levels"):
        g.loc[g.index[cut:], c] = np.nan

    a = invented_frame(f).iloc[:cut]
    b = invented_frame(g).iloc[:cut]
    checked = 0
    for c in ["ttc_asym", "qt_flow", "qt_fusion", "hhi_asym", "pressure_per_cost",
              f"slip_buy_{WALK_SIZES[0]}", f"wbc_asym_{WALK_SIZES[0]}"]:
        x = pd.to_numeric(a[c], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(b[c], errors="coerce").to_numpy(dtype=float)
        same = ((np.isnan(x) & np.isnan(y)) | np.isclose(x, y, equal_nan=True)).all()
        assert same, f"{c} changed when only the FUTURE was corrupted — look-ahead"
        checked += 1
    assert checked >= 7


def test_the_exhaustion_clock_divides_size_by_flow_not_by_frames():
    """Same wall, twice the flow, half the time. The quantity is in seconds."""
    slow = invented_frame(_frame(n=40, seed=2))
    fast = _frame(n=40, seed=2)
    fast["int_d_volume"] = fast["int_d_volume"] * 4
    fast["signed_int_volume"] = fast["signed_int_volume"] * 4
    fast = invented_frame(fast)
    s = slow["ttc_ask_s"].dropna()
    q = fast["ttc_ask_s"].dropna()
    common = s.index.intersection(q.index)
    assert len(common) > 5
    ratio = (s.loc[common] / q.loc[common]).median()
    assert ratio == pytest.approx(4.0, rel=0.15), "four times the flow, a quarter the time"


def test_a_dead_tape_leaves_the_clock_unobservable_rather_than_infinite():
    f = _frame(n=30)
    f["int_d_volume"] = 0.0
    f["signed_int_volume"] = 0.0
    g = invented_frame(f)
    assert g["ttc_ask_s"].isna().all(), "no flow is not an infinite time, it is no answer"
    assert g["ttc_asym"].isna().all()


def test_the_flow_rate_never_includes_the_current_frame():
    """The rate that decides whether THIS wall breaks is the rate before it."""
    f = _frame(n=30)
    f.loc[f.index[10], "int_d_volume"] = 1e9        # one enormous frame
    f.loc[f.index[10], "signed_int_volume"] = 1e9
    g = invented_frame(f)
    # frame 10 must not see its own volume; frame 11 must
    assert g["buy_rate_s"].iloc[10] < 1e6
    assert g["buy_rate_s"].iloc[11] > 1e6


def test_the_fusion_is_a_rank_sum_with_no_fitted_weights():
    f = invented_frame(_frame(n=80))
    ok = f["qt_fusion"].notna()
    assert ok.sum() > 10
    # each input contributes a value in [-0.5, +0.5], so the sum is bounded
    assert f.loc[ok, "qt_fusion"].abs().max() <= 1.0 + 1e-9
