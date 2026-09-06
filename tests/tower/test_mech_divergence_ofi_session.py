"""tower.mechanics.divergence_family + ofi_shape_family + session_family —
mechanisms 19, 22, 23, 24, 25, 26, 27, 35, 37 / 40, 41, 42 / 36, 16.

test_machinery_* build deterministic MarketState scenarios (5-s cadence, tick 0.1) and feed them
through a StateHistory exactly as the engine does (history holds the states *before* the current
one): per mechanism a scenario that drives the score ≥ active_threshold, a null scenario that keeps
it < build_threshold, a check that the evidence is computed from the inputs (changes when the inputs
change), and the lifecycle building → active → confirmed → resolved / failed through
``Mechanism.update``.  test_realdata_* run the committed closed-market capture (and the live capture
when it carries books) through the engine.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from tower.mechanics import REGISTRY, all_mechanisms
from tower.mechanics.base import Mechanism, StateHistory
from tower.mechanics import divergence_family as df
from tower.mechanics import ofi_shape_family as of
from tower.mechanics import session_family as sf
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)          # 10:00 Dhaka, continuous open
T_CLOSE = datetime(2026, 9, 6, 7, 0, 0, tzinfo=timezone.utc)     # 13:00 Dhaka (close 14:00)
TICK = 0.1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
LIVE = "/home/user/bd-share-market/evidence/capture/2026-09-06"

BIDS = [(10.0, 1000.0), (9.9, 800.0), (9.8, 600.0), (9.7, 500.0), (9.6, 400.0)]
ASKS = [(10.1, 1000.0), (10.2, 800.0), (10.3, 600.0), (10.4, 500.0), (10.5, 400.0)]
FLAT_B = [(10.0, 500.0), (9.9, 500.0), (9.8, 500.0), (9.7, 500.0), (9.6, 500.0)]
FLAT_A = [(10.1, 500.0), (10.2, 500.0), (10.3, 500.0), (10.4, 500.0), (10.5, 500.0)]

DIV_NAMES = ["churn_anomaly", "book_trade_divergence", "depth_price_divergence", "flow_impact_divergence",
             "resilience_asymmetry", "compression_expansion", "false_breakout", "trap_pressure", "trade_churn_repetition"]
OFI_NAMES = ["ofi_state", "deep_book_shape", "recovery_curve_state"]
SESSION_NAMES = ["close_session_pressure", "auction_imbalance"]
ALL_NAMES = DIV_NAMES + OFI_NAMES + SESSION_NAMES
BASELINE_KEYS = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio", "price_only_response", "volume_only_response")


# ----------------------------------------------------------------------------- builders
def _t(s: float, base: datetime = T0) -> datetime:
    return base + timedelta(seconds=s)


def S(s: float, bids=BIDS, asks=ASKS, *, base: datetime = T0, tv=None, iv=None, itr=None, dirn=None,
      intensity=None, vel=None, bp=None, tp=None, cp=None, prev_rev=None, ofi_w=None, ofi=None, sfw=None,
      vol=None, impact=None, asym=None, speed=None, sb=None, sa=None, curve=None, shock=None, auction=None,
      phase: str = "CONTINUOUS", clock: Optional[str] = None, tick: Optional[float] = TICK, mig=None,
      sasym=None, seq: int = 0, tape_vol=None) -> MarketState:
    """A MarketState carrying what the book / tape / pressure / resilience / auction engines write."""
    ms = MarketState(symbol="SYN", t=_t(s, base), seq=seq, tick_size=tick, session_phase=phase)
    ms.bids = [(float(p), float(q)) for p, q in bids]
    ms.asks = [(float(p), float(q)) for p, q in asks]
    if bids:
        ms.best_bid, ms.bid_qty1 = ms.bids[0]
    if asks:
        ms.best_ask, ms.ask_qty1 = ms.asks[0]
    if bids and asks:
        ms.spread = round(ms.best_ask - ms.best_bid, 6)
        ms.spread_ticks = round(ms.spread / TICK, 6)
        ms.mid = round((ms.best_ask + ms.best_bid) / 2.0, 6)
    ms.empty_book = not (bids or asks)
    ms.one_sided = bool(bids) != bool(asks)
    ms.trade_volume = tv
    ms.interval_volume = iv
    ms.interval_trades = itr
    ms.trade_flow_direction = dirn
    ms.trade_intensity = intensity
    ms.price_velocity = vel
    ms.book_pressure, ms.trade_pressure, ms.combined_pressure = bp, tp, cp
    ms.pressure_reversal = prev_rev
    ms.ofi_window, ms.ofi = ofi_w, ofi
    ms.signed_flow_window, ms.volume_only_response, ms.price_impact = sfw, vol, impact
    ms.recovery_asymmetry, ms.recovery_speed, ms.recovery_curve = asym, speed, curve
    if sb is not None or sa is not None or shock is not None:
        ms.session_state["resilience"] = {"recovery_speed_bid": sb, "recovery_speed_ask": sa, "curves_completed": 1,
                                          "shock": shock or {}, "side": (shock or {}).get("side"), "elapsed_s": s,
                                          "state": "recovering"}
    if auction is not None:
        ms.auction = auction
    if clock is not None:
        ms.session_state.setdefault("tape", {})["tape_clock"] = clock
    if tape_vol is not None:                                          # the tape's own 300-s window volume
        ms.session_state.setdefault("tape", {})["volume_300s"] = tape_vol
    if mig is not None:
        ms.depth_migration_bid, ms.depth_migration_ask = mig
    ms.side_asymmetry = sasym
    return ms


def run(mech: Mechanism, states: Sequence[MarketState], use_update: bool = False):
    hist = StateHistory()
    out = []
    for ms in states:
        r = mech.update(ms, hist) if use_update else mech.compute(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert not (isinstance(r.score, float) and math.isnan(r.score))
        hist.push(ms)
        out.append(r)
    return out


def shift(levels, ticks: int):
    return [(round(p + ticks * TICK, 6), q) for p, q in levels]


def scale(levels, factor: float):
    return [(p, q * factor) for p, q in levels]


def l1_only(ms: MarketState) -> MarketState:
    """Strip the displayed size: best prices and mid stay, levels / touch quantities go (an L1-price feed)."""
    ms.bids, ms.asks = [], []
    ms.bid_qty1 = ms.ask_qty1 = None
    ms.empty_book = False
    return ms


def _check_common(r, expect_direction: Optional[int] = None):
    assert set(BASELINE_KEYS) <= set(r.baseline)
    assert "direction" in r.evidence and r.evidence["direction"] in (-1, 0, 1)
    if expect_direction is not None:
        assert r.evidence["direction"] == expect_direction


# ----------------------------------------------------------------------------- registry / nulls
def test_machinery_registry_has_all_14():
    all_mechanisms()
    for n in ALL_NAMES:
        assert n in REGISTRY, n
    assert all(REGISTRY[n].family == "divergence" for n in DIV_NAMES)
    assert all(REGISTRY[n].family == "ofi_shape" for n in OFI_NAMES)
    assert all(REGISTRY[n].family == "session" for n in SESSION_NAMES)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_null_static_book_stays_below_build(name):
    """A static, fully displayed book with a flat tape at 10:00 Dhaka never builds any of the 14."""
    states = [S(5 * i, tv=1000.0) for i in range(60)]
    rs = run(REGISTRY[name](), states)
    assert max(r.score for r in rs) < REGISTRY[name].build_threshold
    _check_common(rs[-1])


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_empty_book_is_missing_not_zero(name):
    states = [S(5 * i, [], []) for i in range(12)]
    rs = run(REGISTRY[name](), states)
    assert rs[-1].score == 0.0
    assert rs[-1].evidence.get("missing"), name


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_update_never_raises_on_sparse_states(name):
    mech = REGISTRY[name]()
    hist = StateHistory()
    states = [S(0, [], ASKS, tick=None), S(5, BIDS, [], tick=None), S(10, BIDS[:1], ASKS[:1]),
              S(15, tv=500.0, iv=100.0, itr=1, dirn=0.3, intensity=2.0, ofi=5.0, bp=0.2),
              S(16, tv=500.0, curve=[(0.0, 0.5)], auction={"auction_pressure": None, "source": None}),
              S(20, [], [], tick=None, phase="CLOSED")]
    for ms in states:
        r = mech.update(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert isinstance(r.evidence, dict) and isinstance(r.baseline, dict)
        hist.push(ms)


# ============================================================================= #19 churn_anomaly
def _churn_scenario(final_intensity: float = 30.0, move_ticks: int = 0, flat_baseline: bool = False,
                    n_base: int = 40) -> List[MarketState]:
    st = [S(5 * i, tv=1000.0 + 10 * i, intensity=(6.0 if flat_baseline else 5.0 + (i % 3))) for i in range(n_base)]
    for j in range(1, 13):                                            # last 60 s: optional drift
        k = round(move_ticks * j / 12)
        st.append(S(5 * (n_base - 1) + 5 * j, shift(BIDS, k), shift(ASKS, k), tv=1000.0 + 10 * n_base + 40 * j,
                    intensity=final_intensity))
    return st


def test_machinery_churn_anomaly_activates():
    r = run(df.ChurnAnomaly(), _churn_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["intensity"] == 30.0 and r.evidence["intensity_z"] > 3.5
    assert r.evidence["burst_basis"] == "z" and r.evidence["progress_ticks_per_min"] == 0.0
    _check_common(r, 0)


def test_machinery_churn_anomaly_null_and_evidence_changes():
    assert run(df.ChurnAnomaly(), _churn_scenario(final_intensity=7.0))[-1].score < 0.35
    moved = run(df.ChurnAnomaly(), _churn_scenario(move_ticks=6))[-1]
    assert moved.evidence["progress_ticks_per_min"] >= 2.5 and moved.score == 0.0
    r20, r30 = (run(df.ChurnAnomaly(), _churn_scenario(final_intensity=x))[-1] for x in (20.0, 30.0))
    assert r20.evidence["intensity_z"] < r30.evidence["intensity_z"]
    flat = run(df.ChurnAnomaly(), _churn_scenario(flat_baseline=True))[-1]
    assert flat.evidence["intensity_z"] is None and flat.evidence["burst_basis"] == "ratio_flat_baseline"
    assert flat.evidence["intensity_ratio"] == pytest.approx(5.0) and flat.score >= 0.6
    short = run(df.ChurnAnomaly(), _churn_scenario(n_base=3))[-1]
    assert short.score == 0.0 and short.evidence["missing"]


def test_machinery_churn_anomaly_silent_baseline_is_not_a_silent_zero():
    """No trades at all for the whole baseline (intensity 0 everywhere) then a burst: the ratio to a
    zero mean is unbounded, so the intensity is compared against a 1 trade/min floor."""
    st = [S(5 * i, tv=1000.0, intensity=0.0) for i in range(40)] + [S(200 + 5 * j, tv=1000.0, intensity=30.0) for j in range(12)]
    r = run(df.ChurnAnomaly(), st)[-1]
    assert r.evidence["burst_basis"] == "ratio_silent_baseline" and r.evidence["intensity_z"] is None
    assert r.evidence["intensity_ratio"] == pytest.approx(30.0) and r.score >= 0.6
    faint = run(df.ChurnAnomaly(), st[:40] + [S(200 + 5 * j, tv=1000.0, intensity=1.0) for j in range(12)])[-1]
    assert faint.evidence["burst_basis"] == "ratio_silent_baseline" and faint.score == 0.0


# ============================================================================= #22 book_trade_divergence
def _btd_scenario(bp: float, tp: float, n: int = 25, conflict_last: Optional[int] = None) -> List[MarketState]:
    out = []
    for i in range(n):
        t_p = tp if (conflict_last is None or i >= n - conflict_last) else bp
        out.append(S(5 * i, tv=1000.0, bp=bp, tp=t_p, cp=(bp + t_p) / 2))
    return out


def test_machinery_book_trade_divergence_activates():
    r = run(df.BookTradeDivergence(), _btd_scenario(0.5, -0.5))[-1]
    assert r.score >= 0.6
    assert r.evidence["conflict"] and r.evidence["strength"] == pytest.approx(0.5)
    assert r.evidence["conflict_share"] == pytest.approx(1.0)
    _check_common(r, -1)
    assert run(df.BookTradeDivergence(), _btd_scenario(-0.5, 0.5))[-1].evidence["direction"] == 1


def test_machinery_book_trade_divergence_null_persistence_and_fallback():
    assert run(df.BookTradeDivergence(), _btd_scenario(0.5, 0.5))[-1].score == 0.0
    assert run(df.BookTradeDivergence(), _btd_scenario(0.1, -0.1))[-1].score == 0.0
    brief = run(df.BookTradeDivergence(), _btd_scenario(0.5, -0.5, conflict_last=5))[-1]
    full = run(df.BookTradeDivergence(), _btd_scenario(0.5, -0.5))[-1]
    assert brief.evidence["conflict_share"] == pytest.approx(0.2) and brief.score < full.score
    # no pressure layer: book pressure from the displayed book, trade pressure = signed flow over the
    # tape's volume of the same 300-s window
    st = [S(5 * i, scale(BIDS, 3.0), ASKS, tv=1000.0, sfw=-800.0, tape_vol=1000.0) for i in range(25)]
    r = run(df.BookTradeDivergence(), st)[-1]
    assert r.evidence["book_pressure"] > 0.3 and r.evidence["trade_pressure"] == pytest.approx(-0.8)
    assert r.score >= 0.6 and "missing" not in r.evidence
    miss = run(df.BookTradeDivergence(), [S(5 * i, bp=0.5) for i in range(5)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"] == ["trade_pressure"]


def test_machinery_trade_pressure_fallback_needs_the_window_volume():
    """A signed flow without the tape's 300-s volume is not a pressure: None, never ±1 (the 120-s
    response volume is a different window and is not used as the denominator)."""
    assert df.trade_pressure_of(S(0, sfw=-800.0)) is None
    assert df.trade_pressure_of(S(0, sfw=-800.0, vol=1000.0)) is None
    assert df.trade_pressure_of(S(0, sfw=-800.0, tape_vol=2000.0)) == pytest.approx(-0.4)
    assert df.trade_pressure_of(S(0, sfw=-800.0, tape_vol=500.0)) == -1.0          # clipped, never beyond ±1
    assert df.trade_pressure_of(S(0, sfw=-800.0, tape_vol=1000.0, tp=0.3)) == pytest.approx(0.3)
    miss = run(df.BookTradeDivergence(), [S(5 * i, scale(BIDS, 3.0), ASKS, sfw=-800.0, vol=1000.0) for i in range(5)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"] == ["trade_pressure"]


def test_machinery_book_trade_divergence_persistence_unknown_with_few_points():
    """One or two states carrying both pressures cannot show persistence: the share is None (not
    1.0 from the current point alone), the factor stays neutral and the reading is unverified."""
    st = [S(5 * i, tv=1000.0, bp=0.5) for i in range(24)] + [S(120, tv=1000.0, bp=0.5, tp=-0.5)]
    r = run(df.BookTradeDivergence(), st)[-1]
    assert r.evidence["points"] == 1 and r.evidence["conflict_share"] is None
    assert r.evidence["unverified"] == ["persistence"]
    assert r.score == pytest.approx(0.75) and r.evidence["direction"] == -1
    full = run(df.BookTradeDivergence(), _btd_scenario(0.5, -0.5))[-1]
    assert full.score > r.score and "unverified" not in full.evidence
    three = run(df.BookTradeDivergence(), _btd_scenario(0.5, -0.5, n=3))[-1]
    assert three.evidence["conflict_share"] == pytest.approx(1.0) and "unverified" not in three.evidence


# ============================================================================= #23 depth_price_divergence
def _dpd_scenario(price_ticks: int = -4, depth_factor: float = 3.0, n: int = 25) -> List[MarketState]:
    out = []
    for i in range(n):
        f = 1.0 + (depth_factor - 1.0) * i / (n - 1)
        k = round(price_ticks * i / (n - 1))
        out.append(S(5 * i, shift(scale(BIDS, f), k), shift(ASKS, k), tv=1000.0))
    return out


def test_machinery_depth_price_divergence_activates():
    r = run(df.DepthPriceDivergence(), _dpd_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["depth_ratio_change"] == pytest.approx(0.25)
    assert r.evidence["mid_change_ticks"] == pytest.approx(-4.0) and r.evidence["conflict"]
    _check_common(r, 1)
    ra = run(df.DepthPriceDivergence(), _dpd_scenario(price_ticks=4, depth_factor=1 / 3.0))[-1]
    assert ra.score >= 0.6 and ra.evidence["direction"] == -1


def test_machinery_depth_price_divergence_null_and_migration_evidence():
    assert run(df.DepthPriceDivergence(), _dpd_scenario(price_ticks=4))[-1].score == 0.0      # same way
    assert run(df.DepthPriceDivergence(), _dpd_scenario(price_ticks=0))[-1].score == 0.0      # flat price
    st = _dpd_scenario(price_ticks=-4, depth_factor=1.0)
    st[-1].depth_migration_bid, st[-1].depth_migration_ask = -2.0, 2.0
    r = run(df.DepthPriceDivergence(), st)[-1]
    assert r.evidence["migration_net_ticks"] == pytest.approx(4.0)
    assert r.evidence["depth_signal"] == pytest.approx(0.5) and r.score == pytest.approx(0.5)
    mild = run(df.DepthPriceDivergence(), _dpd_scenario(price_ticks=-2))[-1]
    assert mild.evidence["price_signal"] == pytest.approx(-0.5) and mild.score == pytest.approx(1 / 3)


# ============================================================================= #24 flow_impact_divergence
def _fid_scenario(final_flow: float = 2000.0, move_ticks: int = 0, with_impact: bool = True) -> List[MarketState]:
    st = [S(5 * i, tv=1000.0, sfw=500.0 + 50 * (i % 4), vol=1000.0, impact=(0.01 + 0.002 * (i % 3)) if with_impact else None)
          for i in range(40)]
    st.append(S(200, shift(BIDS, move_ticks), shift(ASKS, move_ticks), tv=1000.0, sfw=final_flow, vol=2500.0))
    return st


def test_machinery_flow_impact_divergence_flow_without_impact_activates():
    r = run(df.FlowImpactDivergence(), _fid_scenario())[-1]
    assert r.score >= 0.6 and r.evidence["mode"] == "flow_without_impact"
    assert r.evidence["base_impact"] == pytest.approx(0.012) and r.evidence["expected_move_ticks"] == pytest.approx(24.0)
    assert r.evidence["impact_ratio"] == pytest.approx(0.0) and r.evidence["flow_rel"] > 2.5
    _check_common(r, -1)
    rs = run(df.FlowImpactDivergence(), _fid_scenario(final_flow=-2000.0))[-1]
    assert rs.evidence["direction"] == 1


def test_machinery_flow_impact_divergence_impact_without_flow_null_and_missing():
    rb = run(df.FlowImpactDivergence(), _fid_scenario(final_flow=100.0, move_ticks=30))[-1]
    assert rb.score >= 0.6 and rb.evidence["mode"] == "impact_without_flow" and rb.evidence["direction"] == -1
    null = run(df.FlowImpactDivergence(), _fid_scenario(final_flow=2000.0, move_ticks=24))[-1]
    assert null.evidence["impact_ratio"] == pytest.approx(1.0) and null.score == 0.0
    half = run(df.FlowImpactDivergence(), _fid_scenario(final_flow=2000.0, move_ticks=12))[-1]
    assert 0.0 < half.evidence["impact_ratio"] < 1.0 and half.score < 0.6
    miss = run(df.FlowImpactDivergence(), _fid_scenario(with_impact=False))[-1]
    assert miss.score == 0.0 and any("price_impact" in m for m in miss.evidence["missing"])


def test_machinery_flow_impact_divergence_silent_flow_baseline():
    """Median |flow| of the baseline = 0: flow_rel is None (flagged), a non-zero flow takes the
    flow ramps to their limits, and a zero flow scores nothing."""
    st = [S(5 * i, tv=1000.0, sfw=0.0, vol=1000.0, impact=0.01 + 0.002 * (i % 3)) for i in range(40)]
    st.append(S(200, tv=1000.0, sfw=2000.0, vol=2500.0))
    r = run(df.FlowImpactDivergence(), st)[-1]
    assert r.evidence["base_flow_silent"] is True and r.evidence["flow_rel"] is None
    assert r.evidence["mode"] == "flow_without_impact" and r.score == 1.0 and r.evidence["direction"] == -1
    quiet = run(df.FlowImpactDivergence(), st[:40] + [S(200, tv=1000.0, sfw=0.0, vol=1000.0)])[-1]
    assert quiet.evidence["base_flow_silent"] is True and quiet.score == 0.0
    # a move without any flow against a silent flow baseline: impact without flow at its limit
    jump = run(df.FlowImpactDivergence(), st[:40] + [S(200, shift(BIDS, 5), shift(ASKS, 5), tv=1000.0, sfw=0.0, vol=1000.0)])[-1]
    assert jump.evidence["mode"] == "impact_without_flow" and jump.score == 1.0 and jump.evidence["direction"] == -1


# ============================================================================= #25 resilience_asymmetry
def _ra_scenario(sb: float = 0.012, sa: float = 0.002, alternate: bool = False, n: int = 25) -> List[MarketState]:
    out = []
    for i in range(n):
        sign_ = -1.0 if (alternate and i % 2) else 1.0
        out.append(S(5 * i, tv=1000.0, asym=sign_ * (sb - sa), speed=(sb + sa) / 2, sb=sign_ * sb, sa=sign_ * sa))
    return out


def test_machinery_resilience_asymmetry_activates():
    r = run(df.ResilienceAsymmetry(), _ra_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["normalised_now"] == pytest.approx(10 / 14)
    assert r.evidence["run_share"] == 1.0 and r.evidence["run_span_s"] == pytest.approx(120.0)
    _check_common(r, 1)
    ra = run(df.ResilienceAsymmetry(), _ra_scenario(sb=0.002, sa=0.012))[-1]
    assert ra.score >= 0.6 and ra.evidence["direction"] == -1


def test_machinery_resilience_asymmetry_null_and_evidence():
    alt = run(df.ResilienceAsymmetry(), _ra_scenario(alternate=True))[-1]
    assert alt.evidence["run_share"] == pytest.approx(1 / 25) and alt.score == 0.0
    weak = run(df.ResilienceAsymmetry(), _ra_scenario(sb=0.007, sa=0.005))[-1]
    assert weak.evidence["normalised_now"] == pytest.approx(2 / 12) and weak.score == 0.0
    # without the record the state's own speed normalises
    st = [S(5 * i, tv=1000.0, asym=0.01, speed=0.012) for i in range(25)]
    r = run(df.ResilienceAsymmetry(), st)[-1]
    assert r.evidence["normalised_now"] == pytest.approx(0.01 / 0.012) and r.score >= 0.6
    miss = run(df.ResilienceAsymmetry(), [S(5 * i, tv=1000.0) for i in range(10)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"]
    # an asymmetry without any recovery speed cannot be normalised: skipped (was ±1 whatever its size)
    bare = run(df.ResilienceAsymmetry(), [S(5 * i, tv=1000.0, asym=1e-6) for i in range(25)])[-1]
    assert bare.score == 0.0 and bare.evidence["missing"]


# ============================================================================= #26 compression_expansion
def _ce_scenario(ref_amp: int = 3, comp_amp: int = 1, exp_ticks: int = 4) -> List[MarketState]:
    out = []
    for i in range(181):                                              # 0..900 s
        t = 5 * i
        if t < 600:
            k = [0, ref_amp, 0, -ref_amp][i % 4]
        elif t < 840:
            k = [0, comp_amp][i % 2]
        else:
            k = round(exp_ticks * (t - 840) / 60)
        out.append(S(t, shift(BIDS, k), shift(ASKS, k), tv=1000.0))
    return out


def test_machinery_compression_expansion_activates():
    r = run(df.CompressionExpansion(), _ce_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["range_reference_ticks"] == pytest.approx(6.0)
    assert r.evidence["range_compression_ticks"] == pytest.approx(1.0)
    assert r.evidence["components"]["mid_range"] == pytest.approx(1 - 1 / 6)
    assert r.evidence["move_expansion_ticks"] == pytest.approx(4.0)
    _check_common(r, 1)
    rd = run(df.CompressionExpansion(), _ce_scenario(exp_ticks=-4))[-1]
    assert rd.score >= 0.6 and rd.evidence["direction"] == -1


def test_machinery_compression_expansion_null_and_evidence_changes():
    no_comp = run(df.CompressionExpansion(), _ce_scenario(comp_amp=6))[-1]        # C range == R range
    assert no_comp.evidence["compression"] == pytest.approx(0.0) and no_comp.score == 0.0
    half_comp = run(df.CompressionExpansion(), _ce_scenario(comp_amp=3))[-1]
    assert half_comp.evidence["compression"] == pytest.approx(0.5) and half_comp.score < 0.6
    no_exp = run(df.CompressionExpansion(), _ce_scenario(exp_ticks=0))[-1]
    assert no_exp.evidence["expansion"] == pytest.approx(0.0) and no_exp.score == 0.0
    half = run(df.CompressionExpansion(), _ce_scenario(exp_ticks=2))[-1]
    assert half.evidence["expansion"] == pytest.approx(2.0) and 0 < half.score < 0.6
    # velocity / spread components when the state carries them
    st = _ce_scenario()
    for i, s in enumerate(st):
        s.price_velocity = (3.0 if i % 2 else -3.0) if s.t < _t(600) else (0.5 if i % 2 else -0.5)
    r = run(df.CompressionExpansion(), st)[-1]
    assert r.evidence["components"]["velocity_std"] == pytest.approx(1 - 0.5 / 3.0, rel=2e-2)
    flat = run(df.CompressionExpansion(), [S(5 * i, tv=1000.0) for i in range(181)])[-1]
    assert flat.score == 0.0 and flat.evidence["missing"]


# ============================================================================= #27 false_breakout
def _fb_scenario(exc_ticks: int = 5, reenter: bool = True, p_peak: Optional[float] = 0.6,
                 p_now: Optional[float] = -0.4) -> List[MarketState]:
    out = []
    for i in range(121):                                              # 0..600 s
        t = 5 * i
        if t < 420:
            k, cp = [0, 1, 0, -1][i % 4], (0.0 if p_peak is not None else None)
        elif t < 540:
            k, cp = 0, (0.0 if p_peak is not None else None)
        elif t == 540:
            k, cp = exc_ticks, p_peak
        else:
            k, cp = (0 if reenter else exc_ticks), p_now
        out.append(S(t, shift(BIDS, k), shift(ASKS, k), tv=1000.0, cp=cp))
    return out


def test_machinery_false_breakout_activates():
    r = run(df.FalseBreakout(), _fb_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["excursion_ticks"] == pytest.approx(4.0) and r.evidence["breakout_direction"] == 1
    assert r.evidence["reentry"] == 1.0 and r.evidence["reversal"] == 1.0
    assert r.evidence["reversal_basis"] == "pressure_delta" and r.evidence["seconds_since_peak"] == pytest.approx(60.0)
    _check_common(r, -1)
    rd = run(df.FalseBreakout(), _fb_scenario(exc_ticks=-5, p_peak=-0.6, p_now=0.4))[-1]
    assert rd.score >= 0.6 and rd.evidence["direction"] == 1


def test_machinery_false_breakout_null_and_pressure_evidence():
    assert run(df.FalseBreakout(), _fb_scenario(exc_ticks=0))[-1].score == 0.0
    stay = run(df.FalseBreakout(), _fb_scenario(reenter=False))[-1]
    assert stay.evidence["reentry"] == 0.0 and stay.score == 0.0
    # a displayed book always yields a book pressure: no pressure fields → book blend (0 here, no reversal)
    blend = run(df.FalseBreakout(), _fb_scenario(p_peak=None, p_now=None))[-1]
    assert blend.evidence["reversal_basis"] == "pressure_delta" and blend.evidence["pressure_at_peak"] == 0.0
    # L1 prices only (no displayed size anywhere): the pressure is unverifiable
    unv = run(df.FalseBreakout(), [l1_only(s) for s in _fb_scenario(p_peak=None, p_now=None)])[-1]
    assert unv.evidence["reversal_basis"] == "unverified" and unv.evidence["unverified"] == ["pressure"]
    assert unv.score == pytest.approx(0.7)
    same = run(df.FalseBreakout(), _fb_scenario(p_peak=0.6, p_now=0.6))[-1]
    assert same.evidence["reversal"] == 0.0 and same.score == pytest.approx(0.4)
    small = run(df.FalseBreakout(), _fb_scenario(exc_ticks=2))[-1]
    assert small.evidence["excursion_ticks"] == pytest.approx(1.0) and small.score == 0.0


# ============================================================================= #35 trap_pressure
def _trap_scenario(side: str = "bid", pull: bool = True, toward: bool = True, traded: bool = False) -> List[MarketState]:
    out = []
    for k in range(13):
        s = (k // 4) * (1 if toward else -1)
        wall = 8000.0 - (7500.0 * k / 12 if pull else 0.0)
        tv = 1000.0 + ((8000.0 - wall) if traded else 0.0)
        if side == "bid":
            bids = [(round(10.0 - 0.1 * s, 6), 500.0), (round(9.9 - 0.1 * s, 6), 500.0), (round(9.8 - 0.1 * s, 6), 500.0), (9.4, wall)]
            asks = shift(FLAT_A, -s)
        else:
            asks = [(round(10.1 + 0.1 * s, 6), 500.0), (round(10.2 + 0.1 * s, 6), 500.0), (round(10.3 + 0.1 * s, 6), 500.0), (10.7, wall)]
            bids = shift(FLAT_B, s)
        out.append(S(5 * k, bids, asks, tv=tv))
    return out


def test_machinery_trap_pressure_activates():
    r = run(df.TrapPressure(), _trap_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "bid" and r.evidence["imb_ref"] > 0.5
    assert r.evidence["withdrawal"] == pytest.approx(7500 / 9500)
    assert r.evidence["approach_ticks"] == pytest.approx(3.0) and r.evidence["comovement"] > 0.8
    assert r.evidence["centre_ref"] == pytest.approx(90050 / 9500)
    _check_common(r, -1)
    ra = run(df.TrapPressure(), _trap_scenario(side="ask"))[-1]
    assert ra.score >= 0.6 and ra.evidence["side"] == "ask" and ra.evidence["direction"] == 1


def test_machinery_trap_pressure_null_and_evidence():
    hold = run(df.TrapPressure(), _trap_scenario(pull=False))[-1]
    assert hold.evidence["withdrawal"] == 0.0 and hold.score == 0.0
    away = run(df.TrapPressure(), _trap_scenario(toward=False))[-1]
    assert away.evidence["approach_ticks"] < 0 and away.score == 0.0
    consumed = run(df.TrapPressure(), _trap_scenario(traded=True))[-1]
    assert consumed.evidence["withdrawal"] == pytest.approx(0.0) and consumed.score == 0.0
    st = _trap_scenario()
    for s in st:
        s.trade_volume = None
    unv = run(df.TrapPressure(), st)[-1]
    assert unv.evidence["unverified"] == ["tape"] and unv.score == pytest.approx(0.75 * run(df.TrapPressure(), _trap_scenario())[-1].score)


# ============================================================================= #37 trade_churn_repetition
def _tcr_scenario(identical: bool = True, gaps: Optional[Sequence[float]] = None, drift: int = 0, n: int = 10) -> List[MarketState]:
    out, t, tv = [], 0.0, 1000.0
    for i in range(n):
        vol = 500.0 if identical else 500.0 + 37.0 * i
        tv += vol
        k = round(drift * i / (n - 1))
        out.append(S(t, shift(BIDS, k), shift(ASKS, k), tv=tv, iv=vol, itr=1, clock=_t(t).isoformat()))
        t += gaps[i % len(gaps)] if gaps else 60.0
    return out


def test_machinery_trade_churn_repetition_activates():
    r = run(df.TradeChurnRepetition(), _tcr_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["largest_group"] == 10 and r.evidence["max_share"] == 1.0
    assert r.evidence["regularity"] == pytest.approx(1.0) and r.evidence["flat"] == 1.0
    _check_common(r, 0)


def test_machinery_trade_churn_repetition_null_and_evidence():
    distinct = run(df.TradeChurnRepetition(), _tcr_scenario(identical=False))[-1]
    assert distinct.evidence["largest_group"] == 1 and distinct.score == 0.0
    drift = run(df.TradeChurnRepetition(), _tcr_scenario(drift=4))[-1]
    assert drift.evidence["flat"] == 0.0 and drift.score == 0.0
    irregular = run(df.TradeChurnRepetition(), _tcr_scenario(gaps=[20.0, 100.0, 40.0]))[-1]
    regular = run(df.TradeChurnRepetition(), _tcr_scenario())[-1]
    assert irregular.evidence["regularity"] < regular.evidence["regularity"] and irregular.score < regular.score
    miss = run(df.TradeChurnRepetition(), [S(5 * i) for i in range(10)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"] == ["interval_volume"]


# ============================================================================= #40 ofi_state
def _ofi_scenario(burst: float = 800.0, n_burst: int = 25, mid_ticks: int = 0) -> List[MarketState]:
    st = [S(5 * i, tv=1000.0, ofi_w=(100.0 if i % 2 else -100.0), ofi=(20.0 if i % 2 else -20.0)) for i in range(40)]
    for j in range(n_burst):
        st.append(S(200 + 5 * j, shift(BIDS, mid_ticks), shift(ASKS, mid_ticks), tv=1000.0, ofi_w=burst, ofi=burst / 10))
    return st


def test_machinery_ofi_state_activates():
    r = run(of.OfiState(), _ofi_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["ofi_z"] > 2.5 and r.evidence["magnitude_basis"] == "z"
    assert r.evidence["sign_share_120s"] == 1.0 and r.evidence["sign_run_s"] == pytest.approx(120.0)
    _check_common(r, 1)
    rn = run(of.OfiState(), _ofi_scenario(burst=-800.0))[-1]
    assert rn.score >= 0.6 and rn.evidence["direction"] == -1


def test_machinery_ofi_state_null_fallbacks_and_evidence():
    null = run(of.OfiState(), _ofi_scenario(n_burst=0))[-1]        # ±100 against ±100: |z| ≈ 1, no persistence
    assert abs(null.evidence["ofi_z"]) <= 1.1 and null.evidence["sign_share_120s"] < 0.6 and null.score < 0.05
    brief = run(of.OfiState(), _ofi_scenario(n_burst=5))[-1]        # 5 burst + 10 alternating positives of 25
    full = run(of.OfiState(), _ofi_scenario())[-1]
    assert brief.evidence["sign_share_120s"] == pytest.approx(0.6) and 0 < brief.score < 0.6 < full.score
    # short history: depth-normalised magnitude
    small = run(of.OfiState(), [S(5 * i, ofi_w=800.0) for i in range(5)])[-1]
    assert small.evidence["magnitude_basis"] == "depth_normalised" and small.evidence["ofi_z"] is None
    assert small.evidence["ofi_depth_normalised"] == pytest.approx(800 / 6600)
    big = run(of.OfiState(), [S(5 * i, ofi_w=3000.0) for i in range(5)])[-1]
    assert big.score > small.score and big.score >= 0.6
    # per-update OFI only: the 60-s sum
    st = [S(5 * i, ofi=50.0) for i in range(15)]
    r = run(of.OfiState(), st)[-1]
    assert r.evidence["ofi_basis"] == "sum_ofi_60s" and r.evidence["ofi_window"] == pytest.approx(650.0)
    miss = run(of.OfiState(), [S(5 * i) for i in range(5)])[-1]
    assert miss.evidence["missing"] == ["ofi"]


# ============================================================================= #41 deep_book_shape
HEAVY_FRONT_B = [(10.0, 6000.0), (9.9, 1000.0), (9.8, 800.0), (9.7, 600.0), (9.6, 500.0)]
THIN_BACK_A = [(10.1, 400.0), (10.2, 500.0), (10.3, 600.0), (10.4, 800.0), (10.5, 2500.0)]


def test_machinery_deep_book_shape_activates():
    st = [S(5 * i, HEAVY_FRONT_B, THIN_BACK_A, tv=1000.0) for i in range(25)]
    r = run(of.DeepBookShape(), st)[-1]
    assert r.score >= 0.6
    assert r.evidence["convexity_bid"] < -0.5 and r.evidence["convexity_ask"] > 0.4
    assert r.evidence["asymmetry"] == pytest.approx((8900 - 4800) / (8900 + 4800))
    assert r.evidence["regime"] == "bid_heavy/bid_front_loaded/ask_back_loaded"
    assert r.evidence["persistence"] == 1.0 and r.evidence["slope_bid"] is not None
    _check_common(r, 1)
    mirror = [S(5 * i, [(round(20.1 - p, 6), q) for p, q in THIN_BACK_A], [(round(20.1 - p, 6), q) for p, q in HEAVY_FRONT_B], tv=1000.0)
              for i in range(25)]
    rm = run(of.DeepBookShape(), mirror)[-1]
    assert rm.score >= 0.6 and rm.evidence["direction"] == -1


def test_machinery_deep_book_shape_null_persistence_and_state_fields():
    lin = run(of.DeepBookShape(), [S(5 * i, FLAT_B, FLAT_A, tv=1000.0) for i in range(25)])[-1]
    assert lin.evidence["signal"] == pytest.approx(0.0) and lin.evidence["regime"] == "balanced/bid_linear/ask_linear"
    assert lin.score == 0.0
    st = [S(5 * i, FLAT_B, FLAT_A, tv=1000.0) for i in range(24)] + [S(120, HEAVY_FRONT_B, THIN_BACK_A, tv=1000.0)]
    brief = run(of.DeepBookShape(), st)[-1]
    assert brief.evidence["persistence"] == pytest.approx(1 / 25) and brief.score < 0.6
    # the book engine's side_asymmetry overrides the visible-depth share
    st2 = [S(5 * i, FLAT_B, FLAT_A, tv=1000.0, sasym=-0.6) for i in range(25)]
    r = run(of.DeepBookShape(), st2)[-1]
    assert r.evidence["asymmetry"] == -0.6 and r.evidence["signal"] == pytest.approx(-0.3) and r.evidence["direction"] == -1
    miss = run(of.DeepBookShape(), [S(5 * i, BIDS[:2], ASKS[:2]) for i in range(5)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"]


# ============================================================================= #42 recovery_curve_state
def _curve(tau: float, n: int = 13, d0: float = 0.8) -> List[Tuple[float, float]]:
    return [(5.0 * k, 1.0 - d0 * math.exp(-5.0 * k / tau)) for k in range(n)]


def test_machinery_recovery_curve_state_activates():
    st = [S(5 * i, tv=1000.0, curve=_curve(40.0, n=i + 1), shock={"move_ticks": -3.0, "side": "bid"}) for i in range(13)]
    r = run(of.RecoveryCurveState(), st)[-1]
    assert r.score >= 0.6
    assert r.evidence["tau_s"] == pytest.approx(40.0, rel=1e-6) and r.evidence["r2"] == pytest.approx(1.0)
    assert r.evidence["progress"] == pytest.approx(1 - math.exp(-1.5)) and r.evidence["speed_class"] == "fast"
    _check_common(r, 1)
    ra = run(of.RecoveryCurveState(), [S(0, curve=_curve(40.0), shock={"move_ticks": 2.0, "side": "ask"})])[-1]
    assert ra.evidence["direction"] == -1


def test_machinery_recovery_curve_state_null_and_evidence():
    flat = run(of.RecoveryCurveState(), [S(0, curve=[(5.0 * k, 0.3) for k in range(13)])])[-1]
    assert flat.evidence["tau_s"] is None and flat.score == 0.0
    short = run(of.RecoveryCurveState(), [S(0, curve=[(0.0, 0.2), (5.0, 0.3)])])[-1]
    assert short.score == 0.0 and short.evidence["points"] == 2 and "missing" not in short.evidence
    slow = run(of.RecoveryCurveState(), [S(0, curve=_curve(200.0))])[-1]
    assert slow.evidence["tau_s"] == pytest.approx(200.0, rel=1e-6) and slow.evidence["speed_class"] == "moderate"
    assert 0 < slow.score < 0.35
    miss = run(of.RecoveryCurveState(), [S(0, tv=1000.0)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"]


# ============================================================================= #36 close_session_pressure
def _close_scenario(base: datetime = T_CLOSE, burst: bool = True, phase_late: str = "CONTINUOUS",
                    n: int = 421, pressure: float = 0.6) -> List[MarketState]:
    out, tv = [], 1000.0
    for i in range(n):                                                # 5-s cadence: 0..2100 s
        t = 5 * i
        late = t >= 1800
        tv += 500.0 if (late and burst) else 100.0
        cp = pressure if (late and burst) else (0.05 if i % 2 else -0.05)
        out.append(S(t, base=base, tv=tv, cp=cp, phase=(phase_late if late else "CONTINUOUS")))
    return out


def test_machinery_close_session_pressure_activates():
    r = run(sf.CloseSessionPressure(), _close_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["minutes_to_close"] == pytest.approx(25.0) and r.evidence["window_factor"] == 1.0
    assert r.evidence["base_rate_per_min"] == pytest.approx(1200.0) and r.evidence["rate_now_per_min"] == pytest.approx(6000.0)
    assert r.evidence["volume_rel"] == pytest.approx(5.0) and r.evidence["pressure_excess"] == pytest.approx(0.55)
    _check_common(r, 1)
    post = run(sf.CloseSessionPressure(), _close_scenario(phase_late="POST_CLOSE"))[-1]
    assert post.evidence["window_factor"] == 1.0 and post.score >= 0.6
    sell = run(sf.CloseSessionPressure(), _close_scenario(pressure=-0.6))[-1]
    assert sell.evidence["direction"] == -1


def test_machinery_close_session_pressure_null_phase_and_missing():
    early = run(sf.CloseSessionPressure(), _close_scenario(base=T_CLOSE - timedelta(hours=1)))[-1]
    assert early.evidence["in_close_window"] is False and early.score == 0.0
    quiet = run(sf.CloseSessionPressure(), _close_scenario(burst=False))[-1]
    assert quiet.evidence["window_factor"] == 1.0 and quiet.evidence["volume_rel"] == pytest.approx(1.0) and quiet.score == 0.0
    closed = run(sf.CloseSessionPressure(), _close_scenario(phase_late="CLOSED"))[-1]
    assert closed.score == 0.0 and closed.evidence["phase"] == "CLOSED" and "missing" not in closed.evidence
    # only in-window history: no day baseline
    st = [S(1800 + 5 * i, base=T_CLOSE, tv=1000.0 + 500 * i, cp=0.6) for i in range(30)]
    miss = run(sf.CloseSessionPressure(), st)[-1]
    assert miss.score == 0.0 and any("baseline" in m for m in miss.evidence["missing"])
    vol_states = [S(t, base=T_CLOSE, tv=1000.0 + (100 * i if t < 1800 else 100 * 360 + 500 * (i - 360)))
                  for i, t in enumerate(range(0, 2105, 5))]
    vol_only = run(sf.CloseSessionPressure(), vol_states)[-1]        # book pressure from the symmetric book = 0
    assert vol_only.evidence["pressure"] == 0.0 and "unverified" not in vol_only.evidence
    assert vol_only.score == pytest.approx(0.5) and vol_only.evidence["direction"] == 0
    l1 = run(sf.CloseSessionPressure(), [l1_only(s) for s in vol_states])[-1]
    assert l1.evidence["unverified"] == ["pressure"] and l1.score == pytest.approx(0.5)


def test_machinery_close_session_pressure_silent_day_baseline():
    """A day without any volume before the close window: a positive close rate is unboundedly
    large relative to it (factor 1), a zero rate is no ratio at all (None, factor 0) — never inf/0."""
    quiet_day = [S(t, base=T_CLOSE, tv=1000.0) for t in range(0, 1800, 5)]
    burst = quiet_day + [S(1800 + 5 * i, base=T_CLOSE, tv=1000.0 + 500.0 * (i + 1)) for i in range(61)]   # to 25 min
    r = run(sf.CloseSessionPressure(), burst)[-1]
    assert r.evidence["window_factor"] == 1.0
    assert r.evidence["base_rate_per_min"] == 0.0 and r.evidence["volume_rel"] == float("inf")
    assert r.evidence["volume_factor"] == 1.0 and r.score == pytest.approx(0.5)
    still = quiet_day + [S(1800 + 5 * i, base=T_CLOSE, tv=1000.0) for i in range(61)]
    z = run(sf.CloseSessionPressure(), still)[-1]
    assert z.evidence["rate_now_per_min"] == 0.0 and z.evidence["volume_rel"] is None
    assert z.evidence["volume_factor"] == 0.0 and z.score == 0.0


# ============================================================================= #16 auction_imbalance
def _auction(p: Optional[float], source: str = "fix_md", age: float = 10.0, **kw) -> Dict[str, Any]:
    d = {"auction_pressure": p, "source": source, "auction_age_s": age, "indicative_price": 10.2, "matched_qty": 1000.0,
         "imbalance_qty": 4000.0 if p is not None else None, "imbalance_side": "B", "open_gap_ticks": None}
    d.update(kw)
    return d


def test_machinery_auction_imbalance_activates():
    st = [S(5 * i, phase="PRE_OPEN", auction=_auction(0.8)) for i in range(25)]
    r = run(sf.AuctionImbalance(), st)[-1]
    assert r.score >= 0.6 and r.evidence["proxy"] is False and r.evidence["size"] == 4000.0
    assert r.evidence["sign_share_120s"] == 1.0 and r.evidence["freshness"] == 1.0
    _check_common(r, 1)
    proxy = [S(5 * i, phase="PRE_OPEN", auction=_auction(0.8, source=sf.PROXY_SOURCE, proxy_basis="imb_topk")) for i in range(25)]
    rp = run(sf.AuctionImbalance(), proxy)[-1]
    assert rp.evidence["proxy"] is True and rp.score == pytest.approx(0.7 * r.score) and rp.score >= 0.6
    sell = run(sf.AuctionImbalance(), [S(5 * i, phase="PRE_OPEN", auction=_auction(-0.8, imbalance_side="S")) for i in range(25)])[-1]
    assert sell.evidence["direction"] == -1


def test_machinery_auction_imbalance_null_persistence_freshness_missing():
    weak = run(sf.AuctionImbalance(), [S(5 * i, phase="PRE_OPEN", auction=_auction(0.1)) for i in range(25)])[-1]
    assert weak.score == 0.0 and weak.evidence["direction"] == 0
    flip = run(sf.AuctionImbalance(), [S(5 * i, phase="PRE_OPEN", auction=_auction(0.8 if i % 2 else -0.8)) for i in range(25)])[-1]
    assert flip.evidence["sign_share_120s"] < 0.6 and flip.score < 0.6
    stale = run(sf.AuctionImbalance(), [S(5 * i, auction=_auction(0.8, age=1800.0)) for i in range(25)])[-1]
    assert stale.evidence["freshness"] == 0.0 and stale.score == 0.0
    miss = run(sf.AuctionImbalance(), [S(5 * i, auction={"auction_pressure": None, "source": None, "phase": "CONTINUOUS"})
                                       for i in range(5)])[-1]
    assert miss.score == 0.0 and miss.evidence["missing"] == ["auction_pressure"]


# ============================================================================= lifecycle
def _ofi_lifecycle(mid_move: int) -> List[MarketState]:
    st = _ofi_scenario(n_burst=30, mid_ticks=0)                      # 0..345 s
    for s in st:
        if s.t >= _t(300):
            s.bids, s.asks = shift(BIDS, mid_move), shift(ASKS, mid_move)
            s.best_bid, s.best_ask = s.bids[0][0], s.asks[0][0]
            s.mid = round((s.best_bid + s.best_ask) / 2, 6)
    for j in range(30):                                               # 350..495 s: imbalance gone
        st.append(S(350 + 5 * j, shift(BIDS, mid_move), shift(ASKS, mid_move), tv=1000.0, ofi_w=(100.0 if j % 2 else -100.0)))
    return st


@pytest.mark.parametrize("mid_move,terminal", [(3, "resolved"), (-3, "failed")])
def test_machinery_lifecycle_building_active_confirmed_release(mid_move, terminal):
    mech = of.OfiState()
    rs = run(mech, _ofi_lifecycle(mid_move), use_update=True)
    seq = [r.state for r in rs]
    assert seq[0] == "inactive"
    assert "building" in seq and "active" in seq and "confirmed" in seq
    i_b, i_a, i_c = seq.index("building"), seq.index("active"), seq.index("confirmed")
    assert i_b < i_a < i_c
    assert rs[i_c].start_time is not None and rs[i_c].duration_s >= mech.confirm_s
    assert terminal in seq and seq.index(terminal) > i_c
    assert rs[seq.index(terminal)].score < mech.release_threshold
    assert rs[i_c].evidence["direction"] == 1 and rs[i_c].evidence["peak_score"] >= 0.6
    assert "mid_change_since_start" in rs[i_c].evidence
    i_t = seq.index(terminal)
    assert rs[i_t].evidence["direction"] == 0 and rs[i_t].evidence["episode_direction"] == 1
    assert (rs[i_t].evidence["mid_change_since_start"] > 0) == (terminal == "resolved")


class _Scripted(df.DirectedMechanism):
    """A DirectedMechanism fed scripted (score, direction) readings — tests the lifecycle contract
    itself (not registered: it is not a mechanism of the tower)."""
    name, family = "scripted_directed", "test"

    def __init__(self, script: Sequence[Tuple[float, int]]) -> None:
        super().__init__()
        self.script = list(script)

    def compute(self, ms: MarketState, hist: StateHistory):
        score, d = self.script.pop(0)
        return df._reading(self, score, {"direction": d}, {}, "scripted")


def test_machinery_lifecycle_episode_direction_does_not_leak_between_episodes():
    """Episode 1 carries direction +1 and resolves on a rising mid.  Episode 2 is built from readings
    whose direction is 0 and ends on a falling mid: it must be judged as undirected (resolved,
    never failed) — the previous episode's +1 must not leak into it."""
    # exactly one releasing reading between the episodes: episode 2 starts straight from "resolved"
    # (an inactive reading in between would reset the direction on its own)
    script = [(0.9, 1)] * 12 + [(0.0, 0)] + [(0.9, 0)] * 12 + [(0.0, 0)]
    mids = [k for k in range(12)] + [11] + [11 - k for k in range(12)] + [-1]
    st = [S(5 * i, shift(BIDS, k), shift(ASKS, k), tv=1000.0) for i, k in enumerate(mids)]
    rs = run(_Scripted(script), st, use_update=True)
    seq = [r.state for r in rs]
    ends = [i for i, s in enumerate(seq) if s in ("resolved", "failed")]
    assert len(ends) == 2, seq
    first, second = ends
    assert seq[first] == "resolved" and rs[first].evidence["episode_direction"] == 1
    assert rs[first].evidence["mid_change_since_start"] > 0
    i_b2 = next(i for i in range(first + 1, second) if seq[i] in ("building", "active"))
    assert rs[i_b2].evidence["direction"] == 0 and rs[i_b2].evidence["episode_direction"] == 0
    assert "confirmed" in seq[i_b2:second]
    assert rs[second].evidence["mid_change_since_start"] < 0
    assert seq[second] == "resolved" and rs[second].evidence["episode_direction"] == 0
    # the mirror: episode 2 directed (−1) on a falling mid resolves, on a rising mid fails
    for mid2, want in ((-1, "resolved"), (1, "failed")):
        script = [(0.9, 1)] * 12 + [(0.0, 0)] + [(0.9, -1)] * 12 + [(0.0, 0)]
        mids = [k for k in range(12)] + [11] + [11 + mid2 * k for k in range(12)] + [11 + mid2 * 12]
        st = [S(5 * i, shift(BIDS, k), shift(ASKS, k), tv=1000.0) for i, k in enumerate(mids)]
        seq = [r.state for r in run(_Scripted(script), st, use_update=True)]
        assert [s for s in seq if s in ("resolved", "failed")] == ["resolved", want]


def test_machinery_lifecycle_direction_zero_resolves():
    """churn implies no direction: the episode resolves (never fails) when the burst fades."""
    mech = df.ChurnAnomaly()
    st = [S(5 * i, tv=1000.0, intensity=5.0 + (i % 3)) for i in range(40)]
    st += [S(200 + 5 * j, tv=1000.0, intensity=30.0) for j in range(30)]
    st += [S(350 + 5 * j, tv=1000.0, intensity=6.0) for j in range(40)]
    rs = run(mech, st, use_update=True)
    seq = [r.state for r in rs]
    assert "confirmed" in seq and "resolved" in seq and "failed" not in seq


# ============================================================================= real data
def _run_capture(root: str, **norm_kw):
    from tower.engine import Engine, EngineConfig
    from tower.normalize import normalize_store
    events, _ = normalize_store(root, **norm_kw)
    eng = Engine(EngineConfig(strict=False))
    states = [ms for ms in (eng.process(ev) for ev in events) if ms is not None]
    return events, eng, states


def test_realdata_fixture_capture_through_engine():
    """The closed-market fixture: all 14 mechanisms compute on every state; the closed (empty /
    static) books never build any of them; readings name their missing inputs; no engine error
    mentions one of these mechanisms."""
    events, eng, states = _run_capture(FIXTURE)
    assert events and states
    seen, with_missing = set(), 0
    for ms in states:
        for name in ALL_NAMES:
            m = ms.mechanisms.get(name)
            if m is None:
                continue
            seen.add(name)
            assert 0.0 <= m.score <= 1.0
            assert m.state in ("inactive", "building", "active", "confirmed", "failed", "resolved")
            assert m.score < REGISTRY[name].build_threshold, (name, ms.symbol, ms.t, m.evidence)
            assert "direction" in m.evidence
            if m.evidence.get("missing"):
                with_missing += 1
    assert seen == set(ALL_NAMES)
    assert with_missing > 0
    assert not [e for e in eng.metrics["errors"] if any(n in e for n in ALL_NAMES)], eng.metrics["errors"]


def test_realdata_fixture_session_and_auction_readings_reflect_closed_phase():
    """CLOSED phase: close_session_pressure reports the phase (not a missing input) and
    auction_imbalance reports the missing auction pressure (no feed, not PRE_OPEN)."""
    _, _, states = _run_capture(FIXTURE)
    last = [ms for ms in states if "close_session_pressure" in ms.mechanisms][-1]
    cs = last.mechanisms["close_session_pressure"]
    assert cs.evidence["phase"] == "CLOSED" and cs.score == 0.0 and cs.evidence["in_close_window"] is False
    ai = last.mechanisms["auction_imbalance"]
    assert ai.evidence["missing"] == ["auction_pressure"]


def test_realdata_live_capture_books_when_present():
    """The live session capture (read-only, optional): when it carries displayed books, the
    shape mechanism computes a real regime on them and no reading raises."""
    if not os.path.isdir(os.path.join(LIVE, "segments")):
        pytest.skip("live capture not present")
    from tower.events import EventType
    # bounded to the session's first 20 minutes so the test stays O(minutes) while the capture grows
    events, eng, states = _run_capture(LIVE, t_from="2026-09-06T03:55:00+00:00", t_to="2026-09-06T04:15:00+00:00")
    books = [ev for ev in events if ev.event_type == EventType.BOOK_SNAPSHOT and (ev.payload.get("bids") or ev.payload.get("asks"))]
    if not books:
        pytest.skip("live capture carries no displayed books yet")
    shaped = [ms.mechanisms["deep_book_shape"] for ms in states
              if "deep_book_shape" in ms.mechanisms and not ms.mechanisms["deep_book_shape"].evidence.get("missing")]
    assert shaped, "books present but no shape reading computed"
    assert any(m.evidence.get("regime") for m in shaped)
    assert not [e for e in eng.metrics["errors"] if any(n in e for n in ALL_NAMES)], eng.metrics["errors"]
