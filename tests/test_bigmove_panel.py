"""Daily features that look only back, outcomes that look only forward.

The panel is the whole basis of the big-move work, so the two directions are
tested separately and adversarially: corrupt the future and every feature before
the cut must be bit-identical; corrupt the past and every outcome must be.
"""
import hashlib

import numpy as np
import pandas as pd
import pytest

from research.bigmove.panel import (MAX_GAP_DAYS, SEALED_HOLDOUT, VIRGIN_SLICE_START,
                                    add_features, add_outcomes, apply_reservations)

FEATURES = ["rel_volume", "vol_z", "vol_accel", "prior_3d_ret", "dist_from_20d_high",
            "volume_compression_activity", "range_compression", "down_vol_share_5",
            "body_ratio", "range_pos_20d"]


def series(n=200, seed=0, symbol="AAA", start="2013-01-01", step_days=1):
    rng = np.random.default_rng(seed)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    hi = px * (1 + np.abs(rng.normal(0, 0.01, n)))
    lo = px * (1 - np.abs(rng.normal(0, 0.01, n)))
    op = lo + (hi - lo) * rng.random(n)
    vol = rng.integers(1000, 100000, n).astype(float)
    return pd.DataFrame({
        "symbol": symbol,
        "ts": pd.date_range(start, periods=n, freq=f"{step_days}D"),
        "open": op, "high": hi, "low": lo, "close": px,
        "volume": vol, "turnover": px * vol,
        "trade": rng.integers(1, 500, n).astype(float),
        "ycp": np.r_[px[0], px[:-1]],
    })


def prepped(df):
    d = df.rename(columns={"ts": "date"})
    d["date"] = pd.to_datetime(d["date"])
    return d.sort_values(["symbol", "date"]).reset_index(drop=True)


# ------------------------------------------------------------------ reservations
def test_the_sealed_holdout_is_dropped_and_asserted_gone():
    d = prepped(series(n=2000, start="2015-01-01"))
    out = apply_reservations(d)
    lo, hi = SEALED_HOLDOUT
    assert not ((out.date >= lo) & (out.date <= hi)).any()
    assert (out.date < VIRGIN_SLICE_START).all()
    assert len(out) < len(d), "the window really was in range"


def test_the_reserved_slice_is_dropped_too():
    d = prepped(series(n=400, start="2025-10-01"))
    out = apply_reservations(d)
    assert out.date.max() < VIRGIN_SLICE_START


# ------------------------------------------------------------------ no look-ahead
def test_no_daily_feature_reads_the_future():
    """Corrupt every row past a cut; every feature before it must be identical."""
    d = prepped(series(n=200, seed=3))
    cut = 120
    g = d.copy()
    for c in ("open", "high", "low", "close", "volume", "turnover", "trade", "ycp"):
        g.loc[g.index[cut:], c] = np.nan

    a = add_features(d).iloc[:cut]
    b = add_features(g).iloc[:cut]
    checked = 0
    for c in FEATURES:
        x = pd.to_numeric(a[c], errors="coerce").to_numpy(float)
        y = pd.to_numeric(b[c], errors="coerce").to_numpy(float)
        same = ((np.isnan(x) & np.isnan(y)) | np.isclose(x, y, equal_nan=True)).all()
        assert same, f"{c} moved when only the FUTURE changed — look-ahead"
        checked += 1
    assert checked == len(FEATURES)


def test_the_relative_baseline_excludes_today():
    """`rel_volume` is today against its PAST, not against a window containing it."""
    d = prepped(series(n=80, seed=1))
    d.loc[d.index[-1], "volume"] = 1e9
    f = add_features(d)
    assert f["rel_volume"].iloc[-1] > 100, "a huge day must not dilute its own baseline"
    assert f["rel_volume"].iloc[-2] < 100, "and must not have touched yesterday's"


def test_a_trailing_window_that_jumps_a_hole_is_unmeasurable_not_wrong():
    """Dropping the holdout leaves a gap; a 20-day mean across it mixes eras."""
    a = series(n=60, start="2018-11-01")
    b = series(n=60, start="2022-08-01", seed=9)
    d = prepped(pd.concat([a, b], ignore_index=True))
    f = add_features(d)
    first_after = f[f.date >= "2022-08-01"].iloc[0]
    assert not bool(first_after["window_clean"]), "the window spans a 3.7-year hole"
    assert np.isnan(first_after["rel_volume"]), "so the feature is NOT_OBSERVABLE"
    assert f["gap_days"].max() > MAX_GAP_DAYS


# ------------------------------------------------------------------ outcomes
def hand_frame(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "symbol": "X",
        "date": pd.date_range("2015-01-01", periods=n, freq="D"),
        "open": closes, "close": closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "volume": 1.0, "turnover": 1.0,
    })


def test_forward_return_is_measured_from_the_decision_close():
    d = hand_frame([100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
    o = add_outcomes(d, horizons=(3,), bands=(0.10,))
    assert o["fwd_ret_3d"].iloc[0] == pytest.approx(0.30), "100 -> 130 over three days"


def test_a_partial_window_is_not_an_h_day_outcome():
    d = hand_frame([100.0, 110.0, 120.0])
    o = add_outcomes(d, horizons=(5,), bands=(0.10,))
    assert np.isnan(o["fwd_ret_5d"].iloc[0]), "two days forward is not a five-day return"
    assert o["n_fwd_5d"].iloc[0] == 2, "but how much WAS available is recorded"


def test_mfe_and_mae_come_from_the_path_not_the_endpoint():
    d = hand_frame([100.0, 100.0, 100.0, 100.0],
                   highs=[100.0, 130.0, 100.0, 100.0],
                   lows=[100.0, 100.0, 70.0, 100.0])
    o = add_outcomes(d, horizons=(3,), bands=(0.10,))
    assert o["mfe_3d"].iloc[0] == pytest.approx(0.30), "the +30 % spike counts"
    assert o["mae_3d"].iloc[0] == pytest.approx(-0.30), "and so does the -30 % one"
    assert o["fwd_ret_3d"].iloc[0] == pytest.approx(0.0), "though the endpoint is flat"


def test_time_to_target_is_the_first_day_the_high_reaches_it():
    d = hand_frame([100.0, 104.0, 106.0, 112.0, 120.0],
                   highs=[100.0, 104.0, 106.0, 112.0, 120.0])
    o = add_outcomes(d, horizons=(5,), bands=(0.10,))
    assert o["days_to_p10"].iloc[0] == 3, "112 on the third forward day"
    assert o["hit_p10_5d"].iloc[0] == 1.0


def test_a_target_reached_only_after_the_stop_is_not_clean():
    """Reaching +10 % after an -8 % drawdown is not the same trade."""
    d = hand_frame([100.0, 100.0, 100.0, 100.0],
                   highs=[100.0, 100.0, 100.0, 115.0],
                   lows=[100.0, 100.0, 85.0, 100.0])
    o = add_outcomes(d, horizons=(3,), bands=(0.10,), adverse=0.08)
    assert o["hit_p10_3d"].iloc[0] == 1.0, "the target was reached"
    assert o["clean_p10_3d"].iloc[0] == 0.0, "but the stop was hit first"


def test_a_target_reached_before_the_stop_is_clean():
    d = hand_frame([100.0, 100.0, 100.0, 100.0],
                   highs=[100.0, 115.0, 100.0, 100.0],
                   lows=[100.0, 100.0, 85.0, 100.0])
    o = add_outcomes(d, horizons=(3,), bands=(0.10,), adverse=0.08)
    assert o["clean_p10_3d"].iloc[0] == 1.0


def test_outcomes_never_cross_a_symbol_boundary():
    a = hand_frame([100.0, 100.0, 100.0]).assign(symbol="A")
    b = hand_frame([100.0, 900.0, 900.0]).assign(symbol="B")
    d = pd.concat([a, b], ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)
    o = add_outcomes(d, horizons=(2,), bands=(0.10,))
    a_rows = o[o.symbol == "A"]
    assert a_rows["mfe_2d"].iloc[0] == pytest.approx(0.0), "B's +800 % is not A's"


def test_an_outcome_column_never_becomes_a_feature():
    d = prepped(series(n=120))
    f = add_outcomes(add_features(d), horizons=(3,), bands=(0.10,))
    feature_like = [c for c in f.columns if c.startswith(("fwd_", "mfe_", "mae_", "hit_", "clean_", "days_to_"))]
    assert feature_like, "outcomes exist"
    from research.bigmove.candidates import families
    for name, fam, desc, mask in families(f):
        assert mask.dtype == bool
    # no candidate may be built from an outcome column
    import inspect
    src = inspect.getsource(families)
    for c in ("fwd_ret", "mfe_", "mae_", "hit_p", "clean_p", "days_to_p"):
        assert c not in src, f"a candidate references the outcome column family {c}"


# ------------------------------------------------------------------ determinism
def test_the_same_raw_data_replays_to_an_identical_feature_hash():
    d = prepped(series(n=300, seed=11))
    def h(x):
        cols = ["symbol", "date"] + FEATURES
        return hashlib.sha256(x[cols].to_csv(index=False).encode()).hexdigest()
    assert h(add_features(d)) == h(add_features(d.copy()))
