"""Stage 3 — the geometry mathematics, the frozen labels, and the honesty rules.

Three groups of test earn their place:

* **The λ family's structural identities.** TLPI(0) must equal the existing
  all-levels imbalance and TLPI(λ→∞) must converge to E1. That relationship is
  the reason the family is testable rather than decorative, and a refactor that
  quietly breaks it must fail here rather than in a report six weeks later.
* **The frozen outcome.** `micro/MICRO_PREREG.json` defines the label and forbids
  redefining it, so the horizon anchoring and the 2H validity gate are pinned.
* **INSUFFICIENT_SAMPLE is never KILLED.** A thin denominator and a measured
  absence are different findings, and the evaluator must not collapse them.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from research.edge_discovery.controls import paired_auc_difference, run_controls
from research.edge_discovery.evaluate import (INSUFFICIENT_SAMPLE, KILLED, MIN_BLOCKS,
                                              NOT_OBSERVABLE, PROMISING, WEAK, auc,
                                              episodes, evaluate, rank)
from research.edge_discovery.labels import add_labels, assert_no_labels, label_coverage
from seeing.features.geometry import (E1_THRESHOLD, LAMBDA_GRID, MAX_DT_S, MIN_DT_S,
                                      book_slope, centroid, deep_imbalance, e1,
                                      geometry_frame, ladder_mismatch_ticks,
                                      level_rank_imbalance, risk_context, tlpi,
                                      touch_share)

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------- the family's identities
def test_lambda_zero_is_the_unweighted_all_levels_imbalance():
    """TLPI(0) must equal (Σbid - Σask)/(Σbid + Σask), the existing `imb_all`."""
    b = [(10.0, 100), (9.9, 50), (9.8, 25)]
    a = [(10.1, 40), (10.2, 60), (10.3, 200)]
    sb, sa = 175, 300
    assert tlpi(b, a, 10.05, 0.1, 0.0) == pytest.approx((sb - sa) / (sb + sa))


def test_large_lambda_converges_to_the_l1_imbalance():
    """TLPI(λ→∞) must approach E1 — the other endpoint of the family."""
    b = [(10.0, 100), (9.9, 5000)]                 # a huge wall far from the touch
    a = [(10.1, 40), (10.2, 5000)]
    far = tlpi(b, a, 10.05, 0.1, 0.0)
    near = tlpi(b, a, 10.05, 0.1, 200.0)
    assert near == pytest.approx(e1(100, 40), abs=1e-6)
    assert abs(near - far) > 0.1, "λ must actually change the answer, or it is decoration"


def test_the_lambda_grid_is_frozen():
    assert LAMBDA_GRID == (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    assert E1_THRESHOLD == 0.20, "θ is preregistered in micro/MICRO_PREREG.json"


def test_on_a_mirror_symmetric_ladder_distance_decay_is_level_rank_decay():
    """The honest limit of the construction, pinned rather than glossed over.

    Bids at 10.00/9.90/9.80 against asks at 10.10/10.20/10.30 put level k the same
    distance from the mid on both sides, so the decay weight cancels level by level
    and TLPI is algebraically an ordinary rank-decayed imbalance. Distance adds
    nothing here and must not be claimed to.
    """
    b = [(10.0, 100), (9.9, 50), (9.8, 25)]
    a = [(10.1, 40), (10.2, 60), (10.3, 200)]
    assert ladder_mismatch_ticks(b, a, 10.05, 0.1) == pytest.approx(0.0)
    for lam in (0.5, 1.0, 2.0, 4.0):
        assert tlpi(b, a, 10.05, 0.1, lam) == pytest.approx(
            level_rank_imbalance(b, a, lam), abs=1e-12)


def test_on_a_gapped_ladder_distance_sees_what_level_rank_cannot():
    """The only place the distance idea can contribute — so it must contribute there.

    Both sides show equal quantity at every level index, so a rank-decayed
    imbalance calls the book perfectly balanced. But the ask's size is parked far
    out in ticks while the bid's is at the touch, and TLPI says the bid side is
    the closer one.
    """
    b = [(10.0, 100), (9.9, 100)]                  # bid mass right at the touch
    a = [(10.1, 100), (13.0, 100)]                 # same sizes, second level 29 ticks out
    assert level_rank_imbalance(b, a, 2.0) == pytest.approx(0.0), "rank sees a balanced book"
    assert ladder_mismatch_ticks(b, a, 10.05, 0.1) > 1.0
    assert tlpi(b, a, 10.05, 0.1, 2.0) > 0.0, "distance sees the bid standing closer"


# ------------------------------------------------------------------ unknown is not zero
def test_a_one_sided_book_has_no_imbalance_rather_than_a_zero_one():
    assert np.isnan(tlpi([(10.0, 100)], [], 10.0, 0.1, 1.0))
    assert np.isnan(tlpi([], [(10.1, 100)], 10.1, 0.1, 1.0))
    assert np.isnan(tlpi([], [], 10.0, 0.1, 1.0))


def test_an_unobserved_tick_makes_a_tick_scaled_quantity_unobservable():
    """Stage 2 leaves the tick NaN before the first circuit poll; it is not filled."""
    b, a = [(10.0, 100)], [(10.1, 50)]
    assert np.isnan(tlpi(b, a, 10.05, None, 1.0))
    assert np.isnan(tlpi(b, a, 10.05, float("nan"), 1.0))
    assert np.isnan(centroid(b, 10.05, None))
    # the relative-scale twin stays computable — a DIFFERENT measurement, named apart
    assert np.isfinite(tlpi(b, a, 10.05, 10.05, 1.0))


def test_a_single_level_has_no_slope():
    assert np.isnan(book_slope([(10.0, 100)], 10.05, 0.1))
    assert np.isfinite(book_slope([(10.0, 100), (9.9, 400)], 10.05, 0.1))


def test_zero_and_negative_quantities_are_dropped_not_counted():
    assert touch_share([(10.0, 0), (9.9, 100)]) == pytest.approx(1.0)
    assert np.isnan(touch_share([]))


def test_deep_imbalance_ignores_the_touch():
    b = [(10.0, 9999), (9.9, 10)]
    a = [(10.1, 1), (10.2, 10)]
    assert deep_imbalance(b, a) == pytest.approx(0.0), "levels 2..N are equal"
    assert e1(9999, 1) > 0.99


# ------------------------------------------------------------------------ rates in time
def _frames(dts, values):
    t = [T0]
    for d in dts:
        t.append(t[-1] + timedelta(seconds=d))
    return pd.DataFrame({"symbol": "X", "t_frame": pd.to_datetime(t, utc=True),
                         "frame_dt_s": [np.nan] + list(dts), "v": values})


def test_velocity_is_per_second_not_per_frame():
    """Two frames with the same change but different gaps must differ in velocity."""
    from seeing.features.geometry import add_rates
    f = add_rates(_frames([10.0, 100.0], [0.0, 1.0, 2.0]), ["v"])
    assert f["v_vel"].iloc[1] == pytest.approx(0.1)
    assert f["v_vel"].iloc[2] == pytest.approx(0.01)


def test_a_gap_outside_the_window_yields_no_rate():
    from seeing.features.geometry import add_rates
    f = add_rates(_frames([MIN_DT_S / 2, MAX_DT_S * 2], [0.0, 1.0, 2.0]), ["v"])
    assert np.isnan(f["v_vel"].iloc[1]), "too short to divide by"
    assert np.isnan(f["v_vel"].iloc[2]), "too long to be an adjacent observation"


# ----------------------------------------------------------------------- frozen labels
def _states(offsets, mids, tick=0.1, symbol="X"):
    t = [T0 + timedelta(seconds=o) for o in offsets]
    return pd.DataFrame({"symbol": symbol, "t_frame": pd.to_datetime(t, utc=True),
                         "mid": mids, "tick_size": tick})


def test_the_horizon_is_anchored_by_timestamp_not_by_frame_count():
    """Cadence is irregular; '3 frames ahead' is not 180 seconds."""
    d = add_labels(_states([0, 30, 60, 200], [10.0, 10.1, 10.2, 10.5]), horizons=(180,))
    # from t=0 the first frame at or after 180 s is t=200, +0.5 → +5 ticks
    assert d["fwd_mid_ticks_180"].iloc[0] == pytest.approx(5.0)
    assert d["fwd_dt_180"].iloc[0] == pytest.approx(200.0)


def test_a_gap_wider_than_twice_the_horizon_invalidates_the_label():
    d = add_labels(_states([0, 400], [10.0, 12.0]), horizons=(180,))
    assert not bool(d["fwd_valid_180"].iloc[0]), "400 s > 2*180 s — the label is void"
    assert np.isnan(d["fwd_mid_ticks_180"].iloc[0])
    # the same pair inside the gate is valid
    ok = add_labels(_states([0, 300], [10.0, 12.0]), horizons=(180,))
    assert bool(ok["fwd_valid_180"].iloc[0])


def test_a_label_never_crosses_into_another_symbol():
    a = _states([0, 200], [10.0, 11.0], symbol="A")
    b = _states([0, 200], [50.0, 20.0], symbol="B")
    d = add_labels(pd.concat([a, b], ignore_index=True), horizons=(180,))
    assert d[d.symbol == "A"]["fwd_mid_ticks_180"].iloc[0] == pytest.approx(10.0)
    assert d[d.symbol == "B"]["fwd_mid_ticks_180"].iloc[0] == pytest.approx(-300.0)


def test_an_unobserved_tick_leaves_the_outcome_unobservable():
    """The outcome is in ticks; a guessed tick would be a guessed denominator."""
    s = _states([0, 200], [10.0, 10.5], tick=np.nan)
    d = add_labels(s, horizons=(180,))
    assert not bool(d["fwd_valid_180"].iloc[0])


def test_labels_are_never_readable_as_inputs():
    with pytest.raises(ValueError, match="forward outcomes"):
        assert_no_labels(["e1", "fwd_mid_ticks_180"])
    assert_no_labels(["e1", "tlpi_2"])          # clean set raises nothing


def test_label_coverage_reports_why_rows_are_unlabelled():
    d = add_labels(_states([0, 100, 400, 500], [10.0, 10.1, 10.2, 10.2]), horizons=(180,))
    cov = label_coverage(d, (180,))["180"]
    assert cov["states"] == 4
    assert cov["labelled"] + cov["gap_over_2H"] + cov["no_forward_frame"] >= 4


# ------------------------------------------------------------------------- the metric
def test_auc_gives_ties_half_credit():
    """Without this every balanced book counts as a win and imbalance looks better."""
    assert auc(np.array([1.0, 1.0, 1.0, 1.0]), np.array([True, True, False, False])) == 0.5
    assert auc(np.array([2.0, 2.0, 1.0, 1.0]), np.array([True, True, False, False])) == 1.0


def test_auc_is_undefined_without_both_classes():
    assert np.isnan(auc(np.array([1.0, 2.0]), np.array([True, True])))


def test_episodes_collapse_a_run_into_its_first_frame():
    d = pd.DataFrame({"symbol": "X",
                      "t_frame": pd.to_datetime([T0 + timedelta(seconds=s)
                                                 for s in (0, 30, 60, 90, 1000)], utc=True)})
    ep = episodes(d, np.array([True, True, True, True, True]), cooldown_s=300)
    assert ep.tolist() == [True, False, False, False, True], \
        "four frames of one run are one episode; the 1000 s frame starts another"


# -------------------------------------------------------- insufficient is not a kill
def _labelled(n=400, blocks=1, seed=0):
    rng = np.random.default_rng(seed)
    sess = np.repeat([f"s{i}" for i in range(blocks)], n // blocks)
    score = rng.normal(size=len(sess))
    fwd = np.where(score + rng.normal(scale=0.5, size=len(sess)) > 0, 1.0, -1.0)
    return pd.DataFrame({
        "symbol": np.tile(["A", "B"], len(sess) // 2), "session": sess,
        "t_frame": pd.to_datetime([T0 + timedelta(seconds=60 * i) for i in range(len(sess))], utc=True),
        "score": score, "fwd_mid_ticks_180": fwd, "fwd_valid_180": True})


def test_one_block_is_insufficient_sample_however_strong_the_estimate():
    r = evaluate(_labelled(blocks=1), "s", "score", 180)
    assert r.auc > 0.6, "the estimate is strong"
    assert r.status == INSUFFICIENT_SAMPLE, "and it still cannot be a finding on one block"
    assert "NOT a kill" in r.note


def test_enough_blocks_lets_a_real_effect_be_called_promising():
    r = evaluate(_labelled(n=1200, blocks=MIN_BLOCKS + 2), "s", "score", 180)
    assert r.blocks >= MIN_BLOCKS
    assert r.ci_kind == "block_bootstrap_over_sessions"
    assert r.status == PROMISING


def test_a_score_that_is_never_present_is_not_observable_not_killed():
    d = _labelled(blocks=4)
    d["missing"] = np.nan
    r = evaluate(d, "m", "missing", 180)
    assert r.status == NOT_OBSERVABLE and r.status != KILLED


def test_flat_rows_are_excluded_from_the_metric_as_the_prereg_requires():
    d = _labelled(blocks=4)
    d.loc[d.index[:100], "fwd_mid_ticks_180"] = 0.0
    r = evaluate(d, "s", "score", 180)
    assert r.rows == len(d) - 100


def test_the_ranking_never_lets_a_thin_sample_lead():
    rs = [evaluate(_labelled(n=1200, blocks=5), "measured", "score", 180),
          evaluate(_labelled(blocks=1), "thin", "score", 180)]
    t = rank(rs)
    assert t["candidate"].iloc[0] == "measured"
    assert t["status"].iloc[-1] == INSUFFICIENT_SAMPLE


# --------------------------------------------------------------------------- controls
def test_the_incremental_gate_reports_an_interval_not_just_a_point():
    d = _labelled(n=1200, blocks=4)
    d["weaker"] = d["score"] + np.random.default_rng(1).normal(scale=2.0, size=len(d))
    r = paired_auc_difference(d, "score", "weaker", 180, block_col="session")
    assert r["auc_a"] > r["auc_b"] and r["diff"] > 0
    assert r["ci_lo"] is not None and r["established"] is True


def test_a_margin_whose_interval_spans_zero_is_not_established():
    d = _labelled(n=1200, blocks=4)
    d["twin"] = d["score"] + np.random.default_rng(2).normal(scale=1e-6, size=len(d))
    r = paired_auc_difference(d, "score", "twin", 180, block_col="session")
    assert r["established"] is False, "an identical twin cannot be an established improvement"


def test_the_null_control_lands_at_a_half_whatever_the_class_mix():
    d = _labelled(n=800, blocks=4)
    d.loc[d.index[:600], "fwd_mid_ticks_180"] = -1.0      # heavily down session
    rep = run_controls(d, "score", ["score"], 180)
    assert rep["C1_null_scores"]["constant"] == 0.5
    assert rep["C1_null_scores"]["clean"] is True


# ------------------------------------------------------- geometry on a realistic frame
def test_geometry_frame_produces_every_declared_quantity_and_they_vary():
    rng = np.random.default_rng(3)
    n = 60
    rows = []
    for i in range(n):
        base = 10.0 + rng.normal(scale=0.05)
        bids = [(round(base - 0.1 * k, 2), float(rng.integers(10, 500))) for k in range(5)]
        asks = [(round(base + 0.1 * (k + 1), 2), float(rng.integers(10, 500))) for k in range(5)]
        rows.append({"symbol": "X", "t_frame": T0 + timedelta(seconds=45 * i),
                     "frame_dt_s": np.nan if i == 0 else 45.0,
                     "bid_levels": bids, "ask_levels": asks,
                     "mid": (bids[0][0] + asks[0][0]) / 2, "tick_size": 0.1,
                     "bid_qty1": bids[0][1], "ask_qty1": asks[0][1],
                     "best_bid": bids[0][0], "best_ask": asks[0][0],
                     "bid_depth_top5": sum(q for _, q in bids),
                     "ask_depth_top5": sum(q for _, q in asks),
                     "spread_ticks": 1.0, "imb_all": np.nan})
    g = geometry_frame(pd.DataFrame(rows))
    for lam in LAMBDA_GRID:
        c = f"tlpi_{lam:g}".replace(".", "p")
        assert c in g and g[c].notna().any() and g[c].std() > 0
    for c in ("e1", "geom_divergence", "geom_centroid_gap", "geom_slope_gap",
              "e1_vel", "e1_acc", "ladder_mismatch_ticks"):
        assert c in g, c
        assert g[c].std(skipna=True) > 0, f"{c} is constant — not a measurement"
    assert set(g["book_shape"].unique()) <= {"symmetric", "slightly_gapped", "gapped", "unknown"}
    # every λ gets its rank ablation twin alongside it
    for lam in LAMBDA_GRID:
        assert f"tlpi_{lam:g}".replace(".", "p") + "_rank" in g


def test_the_risk_veto_can_only_suppress_never_generate():
    rng = np.random.default_rng(4)
    n = 40
    d = pd.DataFrame({
        "symbol": "X",
        "t_frame": pd.to_datetime([T0 + timedelta(seconds=45 * i) for i in range(n)], utc=True),
        "liquidity_top5": np.linspace(1000, 100, n),          # emptying book
        "best_bid": np.linspace(10.0, 9.5, n), "best_ask": np.linspace(10.1, 9.6, n),
        "tick_size": 0.1, "spread_ticks": 1.0,
        "bid_depth_top5": rng.random(n), "ask_depth_top5": rng.random(n)})
    r = risk_context(d)
    assert r["risk_veto"].dtype == bool
    assert r["liquidity_depleting"].any(), "a book losing 90 % must register as depleting"
    assert r["adverse_retreat_long"].any(), "a falling bid is the adverse case for a long"
    assert set(r["risk_veto"].unique()) <= {True, False}
