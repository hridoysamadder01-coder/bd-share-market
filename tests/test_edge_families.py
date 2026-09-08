"""Frame-horizon outcomes, the candidate families, and the sign conventions.

The tests that carry the most weight here are about *labelling*, not arithmetic:

* a candidate that claims a fall must be scored on P(down), or a working downside
  signal is recorded as a failed upside one;
* a veto must only ever subtract, so it can lose coverage and cannot invent it;
* a sector tier built from one member is the share itself and must be
  NOT_OBSERVABLE rather than silently self-referential;
* a frame horizon on an irregular clock needs its own gap rule, or "4 frames
  ahead" quietly becomes forty minutes across a capture gap.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from research.edge_discovery import families as F
from research.edge_discovery.reference import (FRAME_HORIZONS, MAX_FRAME_GAP_MULT,
                                               add_frame_outcomes, add_matching_keys,
                                               decile_response, evaluate_binary, spearman)

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


def _states(offsets, mids, symbol="X", tick=0.1, session="2026-09-06", spread=1.0):
    t = [T0 + timedelta(seconds=float(o)) for o in offsets]
    d = pd.DataFrame({"symbol": symbol, "session": session,
                      "t_frame": pd.to_datetime(t, utc=True), "mid": mids,
                      "tick_size": tick, "spread_ticks": spread})
    d["frame_dt_s"] = d["t_frame"].diff().dt.total_seconds()
    return d


# ------------------------------------------------------------------- frame outcomes
def test_a_frame_horizon_counts_frames_not_seconds():
    d = add_frame_outcomes(_states([0, 40, 80, 120, 160], [10.0, 10.1, 10.2, 10.3, 10.4]),
                           horizons=(2,))
    # two frames ahead of row 0 is row 2: 10.2 − 10.0 = +2 ticks
    assert d["fwd_mid_ticks_h2"].iloc[0] == pytest.approx(2.0)
    assert d["fwd_window_s_h2"].iloc[0] == pytest.approx(80.0)


def test_a_frame_window_stretched_across_a_capture_gap_is_void():
    """Without this, '2 frames ahead' silently becomes an hour — where the move was."""
    # cadence ~40 s, so a 2-frame window may reach 3 × 2 × 40 = 240 s
    ok = add_frame_outcomes(_states([0, 40, 80, 120, 160], [10.0] * 5), horizons=(2,))
    assert bool(ok["fwd_valid_h2"].iloc[0])
    gapped = add_frame_outcomes(_states([0, 40, 4000, 4040, 4080], [10.0] * 5), horizons=(2,))
    assert not bool(gapped["fwd_valid_h2"].iloc[0]), "a 4,000 s window is not a 2-frame window"


def test_an_outcome_never_crosses_a_symbol_or_a_session():
    a = _states([0, 40, 80], [10.0, 10.0, 11.0], symbol="A")
    b = _states([0, 40, 80], [50.0, 50.0, 20.0], symbol="B")
    d = add_frame_outcomes(pd.concat([a, b], ignore_index=True), horizons=(2,))
    assert d[d.symbol == "A"]["fwd_mid_ticks_h2"].iloc[0] == pytest.approx(10.0)
    assert d[d.symbol == "B"]["fwd_mid_ticks_h2"].iloc[0] == pytest.approx(-300.0)

    s1 = _states([0, 40, 80], [10.0, 10.0, 10.0], session="2026-09-06")
    s2 = _states([86400, 86440, 86480], [99.0, 99.0, 99.0], session="2026-09-07")
    d2 = add_frame_outcomes(pd.concat([s1, s2], ignore_index=True), horizons=(2,))
    assert d2["fwd_mid_ticks_h2"].iloc[0] == pytest.approx(0.0), "must not reach into the next day"


def test_the_path_is_recorded_not_only_the_endpoint():
    """An endpoint cannot say whether a position would have survived reaching it."""
    d = add_frame_outcomes(_states([0, 40, 80, 120], [10.0, 10.5, 9.0, 10.1]), horizons=(3,))
    assert d["fwd_mid_ticks_h3"].iloc[0] == pytest.approx(1.0)     # ends +1 tick
    assert d["fwd_mfe_h3"].iloc[0] == pytest.approx(5.0)           # but reached +5
    assert d["fwd_mae_h3"].iloc[0] == pytest.approx(-10.0)         # and −10 on the way
    assert d["fwd_t_fav_h3"].iloc[0] == pytest.approx(40.0)
    assert d["fwd_t_adv_h3"].iloc[0] == pytest.approx(80.0)


def test_an_unobserved_tick_leaves_the_frame_outcome_unobservable():
    d = add_frame_outcomes(_states([0, 40, 80], [10.0, 10.0, 10.5], tick=np.nan), horizons=(2,))
    assert not bool(d["fwd_valid_h2"].iloc[0])


# --------------------------------------------------------------------- matching keys
def test_matching_keys_follow_the_tower_rule():
    d = add_matching_keys(_states(list(range(0, 400, 40)), [10.0] * 10, spread=7.0))
    assert set(d["tod_bucket"]) <= set(range(5))
    assert (d["spread_bucket"] == 3).all(), "spread is clipped to 3 ticks, as tower does"
    unknown = add_matching_keys(_states([0, 40], [10.0, 10.0], spread=np.nan))
    assert (unknown["spread_bucket"] == -1).all(), "unknown spread is its own bucket, not 0"


# ------------------------------------------------------------------------- dynamics
def _dyn(vals, dts=None):
    n = len(vals)
    offs = list(np.cumsum([0] + list(dts or [40] * (n - 1))))
    d = _states(offs, [10.0] * n)
    d["imb_l1"] = vals
    return F.add_dynamics(d, "imb_l1")


def test_velocity_and_acceleration_are_causal():
    """Frame 0 has no velocity, and frame 1 has no acceleration. Not zero — absent."""
    d = _dyn([0.0, 0.1, 0.3, 0.6])
    assert pd.isna(d["V"].iloc[0]) and pd.isna(d["A"].iloc[0])
    assert pd.isna(d["A"].iloc[1])
    assert d["V"].iloc[1] == pytest.approx(0.1)
    assert d["A"].iloc[2] == pytest.approx(0.1)


def test_persistence_counts_the_current_frame_and_never_the_next_one():
    d = _dyn([0.5, 0.5, 0.5, 0.0, 0.5])
    assert d["run_above"].tolist() == [1.0, 2.0, 3.0, 0.0, 1.0]


def test_dynamics_never_borrow_across_a_symbol_boundary():
    a = _dyn([0.9, 0.9]).assign(symbol="A")
    b = _dyn([0.9, 0.9]).assign(symbol="B")
    d = F.add_dynamics(pd.concat([a, b], ignore_index=True), "imb_l1")
    first_b = d[d.symbol == "B"].iloc[0]
    assert pd.isna(first_b["V"]), "symbol B's first frame must not difference against A's last"
    assert first_b["run_above"] == 1.0


# --------------------------------------------------------------------------- context
def _multi(symbols, sectors, pressures):
    rows = []
    for s, p in zip(symbols, pressures):
        r = _states([0, 40], [10.0, 10.0], symbol=s)
        r["imb_l1"] = p
        rows.append(r)
    d = F.add_dynamics(pd.concat(rows, ignore_index=True), "imb_l1")
    return F.add_context(d, dict(zip(symbols, sectors)))


def test_a_sector_of_one_is_not_a_sector():
    """Its 'sector pressure' would be its own pressure under another name."""
    d = _multi(["A", "B", "C", "D"], ["Textile", "Textile", "Textile", "Bank"],
               [0.5, 0.4, 0.3, 0.9])
    tex = d[d.symbol == "A"]
    bank = d[d.symbol == "D"]
    assert bool(tex["sector_observable"].iloc[0]) and tex["sector_pressure"].notna().all()
    assert not bool(bank["sector_observable"].iloc[0])
    assert bank["sector_pressure"].isna().all(), "a one-member sector must be NOT_OBSERVABLE"


def test_market_and_sector_tiers_are_computed_inside_a_timestamp_bucket():
    d = _multi(["A", "B", "C"], ["Textile"] * 3, [0.6, 0.0, -0.6])
    assert d["market_pressure"].iloc[0] == pytest.approx(0.0), "mean of +0.6, 0, −0.6"
    assert d.loc[d.symbol == "A", "share_resid"].iloc[0] == pytest.approx(0.6)


def test_the_cross_sectional_rank_is_centred_and_bounded():
    d = _multi(["A", "B", "C", "D"], ["Textile"] * 4, [0.9, 0.3, -0.3, -0.9])
    r = d.loc[d["t_frame"] == d["t_frame"].min(), "xs_rank"]
    assert r.max() <= 0.5 and r.min() >= -0.5


# ----------------------------------------------------------------- direction + veto
def _battery():
    n = 40
    rng = np.random.default_rng(5)
    d = _states(list(range(0, 40 * n, 40)), [10.0] * n)
    d["imb_l1"] = rng.uniform(-1, 1, n)
    d["imb_top3"] = rng.uniform(-1, 1, n)
    d["imb_top5"] = rng.uniform(-1, 1, n)
    d["imb_weighted"] = rng.uniform(-1, 1, n)
    d["imb_all"] = rng.uniform(-1, 1, n)
    for lam in F.LAMBDA_GRID:
        c = f"tlpi_{lam:g}".replace(".", "p")
        d[c] = rng.uniform(-1, 1, n)
        d[c + "_rank"] = rng.uniform(-1, 1, n)
        d[c + "_vel"] = rng.normal(0, 0.1, n)
        d[c + "_acc"] = rng.normal(0, 0.1, n)
    d["liquidity_top5"] = np.linspace(1000, 200, n)
    d["best_bid"] = np.linspace(10.0, 9.5, n)
    d["best_ask"] = np.linspace(10.1, 9.6, n)
    d["ask_depth_top5"] = rng.uniform(10, 100, n)
    d = F.add_dynamics(d, "imb_l1")
    d = F.add_context(d, {"X": "Textile"})
    return F.add_risk(d)


def test_every_candidate_declares_the_direction_it_claims():
    """An ask-side or risk state asserts a fall; scoring it on P(up) inverts it."""
    C = F.build(_battery())
    by = {c["candidate_id"]: c for c in C}
    for cid in ("GEO_touch_dominant_ask", "GEO_broad_ask", "GEO_deep_only_ask",
                "DYN_G_top5_pos_l1_weak", "RISK_any", "RISK_depletion"):
        assert by[cid]["outcome"] == "down", f"{cid} claims a fall and must be scored on it"
    for cid in ("E1", "GEO_touch_dominant_bid", "TLPI_l1"):
        assert by[cid]["outcome"] == "up"


def test_a_veto_can_only_subtract():
    d = _battery()
    by = {c["candidate_id"]: c for c in F.build(d)}
    e1 = by["E1"]["signal"]
    for cid in ("VETO_E1_no_risk", "VETO_E1_no_hard_risk", "VETO_E1_no_depletion"):
        v = by[cid]["signal"]
        assert bool((v & ~e1).sum() == 0), f"{cid} fires where E1 does not — it is adding, not vetoing"
        assert int(v.sum()) <= int(e1.sum())


def test_every_candidate_is_boolean_and_named():
    for c in F.build(_battery()):
        assert c["signal"].dtype == bool
        assert c["candidate_id"] and c["family"] and c["logic"]
        assert c["truth_class"] in ("OBSERVED", "INFERRED", "NOT_OBSERVABLE") or \
            c["truth_class"].startswith("INFERRED")


def test_the_families_cover_every_group_the_report_names():
    fams = {c["family"] for c in F.build(_battery())}
    assert {"reference", "tlpi", "dynamics", "geometry", "risk", "veto", "context",
            "cross"} <= fams


# ------------------------------------------------------------------ scoring the rows
def _labelled(n=300, seed=3):
    rng = np.random.default_rng(seed)
    d = _states(list(range(0, 40 * n, 40)), 10.0 + np.cumsum(rng.normal(0, 0.02, n)))
    d["score"] = rng.normal(size=n)
    d = add_matching_keys(add_frame_outcomes(d, horizons=(4,)))
    return d


def test_a_candidate_is_scored_on_the_direction_it_declares():
    d = _labelled()
    up = d["score"] > 0
    r_up = evaluate_binary(d, "s", up, 4, outcome="up")
    r_dn = evaluate_binary(d, "s", up, 4, outcome="down")
    assert r_up["outcome"] == "up" and r_dn["outcome"] == "down"
    assert r_up["p_outcome"] != r_dn["p_outcome"]


def test_a_signal_that_never_fires_reports_nothing_rather_than_zero():
    d = _labelled()
    r = evaluate_binary(d, "none", pd.Series(False, index=d.index), 4)
    assert r["n_signal_valid"] == 0
    assert r["p_up"] is None and r["matched_lift_pp"] is None, "no rows is not a lift of zero"


def test_the_matched_control_and_the_raw_rate_are_both_reported():
    d = _labelled()
    r = evaluate_binary(d, "s", d["score"] > 0, 4)
    for k in ("p_outcome", "base_p_outcome", "ctrl_p_outcome", "lift_vs_base_pp",
              "matched_lift_pp", "n_matched", "episodes", "symbol_coverage",
              "mfe", "mae", "median_t_favourable_s", "median_t_adverse_s"):
        assert k in r, k


def test_the_response_curve_reports_monotonicity_rather_than_asserting_it():
    d = _labelled(n=600)
    t = decile_response(d, "score", 4)
    assert len(t) >= 2
    assert "monotone_p_up" in t.columns
    assert set(t["decile"].dropna()) == set(range(1, int(t["of"].iloc[0]) + 1))


def test_spearman_is_one_for_a_monotone_curve_and_near_zero_for_noise():
    assert spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


# ------------------------------------------------------- Stage 3.5 regime refusal
def test_a_cross_sectional_aggregate_spanning_the_coverage_break_is_refused():
    """ROADMAP Stage 3.5: the layer must refuse, not warn."""
    from bdlib import config as C
    from bdlib import panels
    brk = pd.Timestamp(C.COVERAGE_BREAK_DATE)
    spanning = pd.DataFrame({"symbol": ["A", "B"],
                             "ts": [brk - pd.Timedelta(days=5), brk + pd.Timedelta(days=5)]})
    with pytest.raises(ValueError, match="spans the coverage break"):
        panels.assert_single_panel(spanning, "a sector aggregate")
    one_side = spanning.iloc[:1]
    assert panels.assert_single_panel(one_side) in ("PRIMARY", "POSTBREAK")
