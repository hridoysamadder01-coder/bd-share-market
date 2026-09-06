"""tower.mechanics.queue_family + sweep_family — mechanisms 1, 2, 30, 31, 33 and 3, 4, 5, 14, 15, 20, 21, 34.

test_machinery_* build deterministic MarketState scenarios (5-s cadence, tick 0.1) and feed them
through a StateHistory exactly as the engine does (history holds the states *before* the current
one): for every mechanism a scenario that drives the score ≥ active_threshold, a null scenario that
keeps it < build_threshold, a check that the evidence is computed from the inputs (changes when the
inputs change), and the lifecycle building → active → confirmed → resolved / failed through
``Mechanism.update``.  test_realdata_* run the committed closed-market capture through the engine.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from tower.mechanics import REGISTRY, all_mechanisms
from tower.mechanics.base import Mechanism, StateHistory
from tower.mechanics import queue_family as qf
from tower.mechanics import sweep_family as sf
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)
TICK = 0.1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")

BIDS = [(10.0, 1000.0), (9.9, 800.0), (9.8, 600.0), (9.7, 500.0), (9.6, 400.0)]
ASKS = [(10.1, 1000.0), (10.2, 800.0), (10.3, 600.0), (10.4, 500.0), (10.5, 400.0)]

QUEUE_NAMES = ["queue_pull_stack", "quote_refresh_churn", "layering_like", "hidden_replenishment", "order_splitting"]
SWEEP_NAMES = ["liquidity_sweep", "failed_sweep", "exhaustion", "liquidity_vacuum", "vacuum_snapback",
               "liquidity_run", "ignition", "liquidity_depletion"]
BASELINE_KEYS = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio", "price_only_response", "volume_only_response")


# ----------------------------------------------------------------------------- builders
def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def S(s: float, bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], *,
      tv: Optional[float] = None, iv: Optional[float] = None, itr: Optional[float] = None,
      dirn: Optional[float] = None, intensity: Optional[float] = None, vel: Optional[float] = None,
      acc: Optional[float] = None, tacc: Optional[float] = None, lp: Optional[Dict[str, Any]] = None,
      clock: Optional[str] = None, queue: Optional[Dict[str, Any]] = None, tick: Optional[float] = TICK,
      seq: int = 0) -> MarketState:
    """A MarketState carrying what the book / tape engines write for a displayed book."""
    ms = MarketState(symbol="SYN", t=_t(s), seq=seq, tick_size=tick)
    ms.bids = [(float(p), float(q)) for p, q in bids]
    ms.asks = [(float(p), float(q)) for p, q in asks]
    if bids:
        ms.best_bid, ms.bid_qty1 = ms.bids[0]
    if asks:
        ms.best_ask, ms.ask_qty1 = ms.asks[0]
    if bids and asks:
        ms.spread = round(ms.best_ask - ms.best_bid, 6)
        ms.spread_ticks = round(ms.spread / TICK, 6)
        ms.mid = (ms.best_ask + ms.best_bid) / 2.0
    ms.empty_book = not (bids or asks)
    ms.one_sided = bool(bids) != bool(asks)
    ms.trade_volume = tv
    ms.interval_volume = iv
    ms.interval_trades = itr
    ms.trade_flow_direction = dirn
    ms.trade_intensity = intensity
    ms.price_velocity = vel
    ms.price_acceleration = acc
    ms.trade_acceleration = tacc
    ms.last_print = lp
    if clock is not None:
        ms.session_state["tape"] = {"tape_clock": clock}
    if queue is not None:
        ms.session_state["queue"] = queue
    return ms


def run(mech: Mechanism, states: Sequence[MarketState], use_update: bool = False):
    """Feed states in order the way the engine does (history holds only the states before the
    current one). Returns the list of readings (MechanismReading or MechanismState)."""
    hist = StateHistory()
    out = []
    for ms in states:
        r = mech.update(ms, hist) if use_update else mech.compute(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert not (isinstance(r.score, float) and math.isnan(r.score))
        hist.push(ms)
        out.append(r)
    return out


def last_score(mech: Mechanism, states: Sequence[MarketState]) -> float:
    return run(mech, states)[-1].score


def static(n: int = 40, step: float = 5.0, **kw) -> List[MarketState]:
    return [S(step * i, BIDS, ASKS, **kw) for i in range(n)]


def scale(levels, factor: float, only_first: bool = False):
    if only_first:
        return [(levels[0][0], levels[0][1] * factor)] + list(levels[1:])
    return [(p, q * factor) for p, q in levels]


def shift(levels, ticks: int):
    return [(round(p + ticks * TICK, 6), q) for p, q in levels]


def _check_common(r, expect_direction: Optional[int] = None):
    assert set(BASELINE_KEYS) <= set(r.baseline)
    assert "direction" in r.evidence
    assert r.evidence["direction"] in (-1, 0, 1)
    if expect_direction is not None:
        assert r.evidence["direction"] == expect_direction


# ----------------------------------------------------------------------------- registry
def test_machinery_registry_has_all_13():
    all_mechanisms()
    for n in QUEUE_NAMES + SWEEP_NAMES:
        assert n in REGISTRY, n
    assert all(REGISTRY[n].family == "queue" for n in QUEUE_NAMES)
    assert all(REGISTRY[n].family == "sweep" for n in SWEEP_NAMES)


@pytest.mark.parametrize("name", QUEUE_NAMES + SWEEP_NAMES)
def test_machinery_null_static_book_stays_below_build(name):
    """A static, fully displayed book with a flat tape never builds any of the 13 mechanisms."""
    tv = 1000.0
    states = static(60, tv=tv)
    rs = run(REGISTRY[name](), states)
    assert max(r.score for r in rs) < REGISTRY[name].build_threshold
    _check_common(rs[-1])


@pytest.mark.parametrize("name", QUEUE_NAMES + SWEEP_NAMES)
def test_machinery_empty_book_is_missing_not_zero(name):
    """An empty book yields score 0 with the missing inputs named — never a silent zero."""
    states = [S(5 * i, [], []) for i in range(12)]
    rs = run(REGISTRY[name](), states)
    assert rs[-1].score == 0.0
    assert rs[-1].evidence.get("missing"), name


# ============================================================================= #1 queue_pull_stack
def _pull_scenario(fall_to: float = 200.0, with_trades: bool = False, side: str = "bid") -> List[MarketState]:
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(12)]              # 0..55 s static
    steps = 6
    for k in range(1, steps + 1):
        q = 1000.0 - (1000.0 - fall_to) * k / steps
        tv = 1000.0 + ((1000.0 - q) if with_trades else 0.0)
        b = [(10.0, q)] + BIDS[1:] if side == "bid" else BIDS
        a = [(10.1, q)] + ASKS[1:] if side == "ask" else ASKS
        st.append(S(60 + 5 * k, b, a, tv=tv))
    return st


def test_machinery_queue_pull_stack_pull_without_trades_activates():
    rs = run(qf.QueuePullStack(), _pull_scenario())
    r = rs[-1]
    assert r.score >= 0.6
    assert r.evidence["kind"] == "pull" and r.evidence["side"] == "bid"
    assert r.evidence["share"] == pytest.approx(0.8)
    assert r.evidence["consistency"] == 1.0
    _check_common(r, -1)
    # ask-side pull implies the opposite direction
    ra = run(qf.QueuePullStack(), _pull_scenario(side="ask"))[-1]
    assert ra.score >= 0.6 and ra.evidence["direction"] == 1


def test_machinery_queue_pull_stack_consumption_is_not_a_pull():
    """The same fall fully accounted for by traded volume is consumption, not a pull."""
    r = run(qf.QueuePullStack(), _pull_scenario(with_trades=True))[-1]
    assert r.evidence["sides"]["bid"]["pull_qty"] == pytest.approx(0.0)
    assert r.score < 0.35


def test_machinery_queue_pull_stack_stack_activates_and_evidence_changes():
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(12)]
    for k in range(1, 7):
        st.append(S(60 + 5 * k, [(10.0, 1000.0 + 600.0 * k)] + BIDS[1:], ASKS, tv=1000.0))
    r = run(qf.QueuePullStack(), st)[-1]
    assert r.score >= 0.6 and r.evidence["kind"] == "stack" and r.evidence["direction"] == 1
    # evidence responds to the input: a smaller fall → smaller share
    r_small = run(qf.QueuePullStack(), _pull_scenario(fall_to=600.0))[-1]
    r_big = run(qf.QueuePullStack(), _pull_scenario(fall_to=200.0))[-1]
    assert r_small.evidence["share"] == pytest.approx(0.4) and r_big.evidence["share"] == pytest.approx(0.8)
    assert r_small.score < r_big.score


def test_machinery_queue_pull_stack_uses_engine_pull_counter():
    """With no usable series the queue engine's tape-budgeted pull counter is the estimate."""
    q = {"bid": {"pulled_qty_120s": 800.0, "touch_qty": 200.0, "added_qty_120s": 0.0}, "ask": {}}
    st = [S(0, BIDS, ASKS, tv=1000.0), S(5, [(10.0, 200.0)] + BIDS[1:], ASKS, tv=1000.0, queue=q)]
    r = run(qf.QueuePullStack(), st)[-1]
    assert r.evidence["sides"]["bid"]["counter_pull_share"] == pytest.approx(0.8)
    assert r.evidence["share"] == pytest.approx(0.8)


# ============================================================================= #2 quote_refresh_churn
def _churn_scenario(drift_ticks: int = 0, n: int = 14) -> List[MarketState]:
    st = []
    for i in range(n):
        bq = 1000.0 if i % 2 == 0 else 1100.0
        aq = 900.0 if i % 2 == 0 else 1000.0
        d = drift_ticks if i == n - 1 else 0
        st.append(S(5 * i, shift([(10.0, bq)] + BIDS[1:], d), [(10.1, aq)] + ASKS[1:], tv=1000.0))
    return st


def test_machinery_quote_refresh_churn_activates():
    r = run(qf.QuoteRefreshChurn(), _churn_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["rate_per_min"] == pytest.approx(24.0, rel=0.01)   # 22 changes in 55 s
    assert r.evidence["drift_ticks"] == 0.0
    _check_common(r, 0)


def test_machinery_quote_refresh_churn_drift_damps_and_evidence_changes():
    r0 = run(qf.QuoteRefreshChurn(), _churn_scenario(0))[-1]
    r3 = run(qf.QuoteRefreshChurn(), _churn_scenario(3))[-1]
    assert r3.evidence["drift_ticks"] == pytest.approx(3.0)
    assert r3.score < r0.score
    assert r3.score == pytest.approx(r0.score / 4.0, rel=0.05)
    # a slow book: two changes in a minute is the ramp floor
    slow = [S(5 * i, [(10.0, 1000.0 + (i % 6 == 0) * 50)] + BIDS[1:], ASKS, tv=1000.0) for i in range(14)]
    assert run(qf.QuoteRefreshChurn(), slow)[-1].score < 0.35


def test_machinery_quote_refresh_churn_falls_back_to_counters():
    q = {"bid": {"best_changes_per_min": 12.0, "net_drift_ticks": 0.0}, "ask": {"best_changes_per_min": 8.0,
                                                                                "net_drift_ticks": 0.0}}
    st = [S(0, BIDS, ASKS, queue=q)]
    r = run(qf.QuoteRefreshChurn(), st)[-1]
    assert r.evidence["estimate_source"] == "counters" and r.evidence["rate_per_min"] == 20.0
    assert r.score >= 0.6


# ============================================================================= #30 layering_like
def _layer_scenario(cycles: int = 4, side: str = "bid", layer_qty: float = 5000.0) -> List[MarketState]:
    base_b, base_a = BIDS[:3], ASKS[:3]
    st = [S(5 * i, base_b, base_a, tv=1000.0) for i in range(4)]
    s = 20.0
    for _ in range(cycles):
        if side == "bid":
            st.append(S(s, base_b + [(9.6, layer_qty)], base_a, tv=1000.0))
        else:
            st.append(S(s, base_b, base_a + [(10.5, layer_qty)], tv=1000.0))
        st.append(S(s + 5, base_b, base_a, tv=1000.0))
        s += 10
    return st


def test_machinery_layering_like_activates():
    r = run(qf.LayeringLike(), _layer_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "bid" and r.evidence["cycles"] == 4
    assert r.evidence["cancel_away_share"] == pytest.approx(1.0)
    assert r.evidence["cancel_away"] == pytest.approx(20000.0)
    _check_common(r, -1)
    ra = run(qf.LayeringLike(), _layer_scenario(side="ask"))[-1]
    assert ra.score >= 0.6 and ra.evidence["direction"] == 1


def test_machinery_layering_like_touch_activity_is_not_layering():
    """Qty flickering at the touch (trades / pulls at the best) gives no away cycles."""
    st = [S(5 * i, [(10.0, 1000.0 if i % 2 else 400.0)] + BIDS[1:3], ASKS[:3], tv=1000.0) for i in range(14)]
    r = run(qf.LayeringLike(), st)[-1]
    assert r.evidence["cycles"] == 0 and r.score < 0.35


def test_machinery_layering_like_evidence_changes_with_cycles():
    r2 = run(qf.LayeringLike(), _layer_scenario(cycles=2))[-1]
    r4 = run(qf.LayeringLike(), _layer_scenario(cycles=4))[-1]
    assert r2.evidence["cycles"] == 2 and r4.evidence["cycles"] == 4
    assert r2.score < r4.score


# ============================================================================= #31 hidden_replenishment
def _refill_scenario(refills: int = 3, traded: bool = True, sizes: Optional[Sequence[float]] = None,
                     side: str = "bid") -> List[MarketState]:
    st = []
    s = 0.0
    tv = 1000.0
    ref = 1000.0
    sizes = list(sizes) if sizes else [1000.0] * refills
    for k in range(refills):
        st.append(S(s, [(10.0, ref)] + BIDS[1:] if side == "bid" else BIDS,
                    [(10.1, ref)] + ASKS[1:] if side == "ask" else ASKS, tv=tv))
        s += 5
        low = 0.3 * ref
        if traded:
            tv += ref - low
        st.append(S(s, [(10.0, low)] + BIDS[1:] if side == "bid" else BIDS,
                    [(10.1, low)] + ASKS[1:] if side == "ask" else ASKS, tv=tv))
        s += 5
        ref = sizes[k]
    st.append(S(s, [(10.0, ref)] + BIDS[1:] if side == "bid" else BIDS,
                [(10.1, ref)] + ASKS[1:] if side == "ask" else ASKS, tv=tv))
    return st


def test_machinery_hidden_replenishment_activates():
    r = run(qf.HiddenReplenishment(), _refill_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["cycles"] == 3 and r.evidence["side"] == "bid"
    assert r.evidence["traded_frac"] == pytest.approx(1.0)
    assert r.evidence["similarity"] == pytest.approx(1.0)
    _check_common(r, 1)
    ra = run(qf.HiddenReplenishment(), _refill_scenario(side="ask"))[-1]
    assert ra.score >= 0.6 and ra.evidence["direction"] == -1


def test_machinery_hidden_replenishment_pull_refills_and_missing_tape():
    r = run(qf.HiddenReplenishment(), _refill_scenario(traded=False))[-1]
    assert r.evidence["traded_frac"] == 0.0 and r.score < 0.35
    # no tape at all: consumption cannot be verified → missing
    st = [S(ms_.t and (ms_.t - T0).total_seconds(), ms_.bids, ms_.asks) for ms_ in _refill_scenario()]
    rn = run(qf.HiddenReplenishment(), st)[-1]
    assert rn.score == 0.0 and "trade_volume" in rn.evidence["missing"]


def test_machinery_hidden_replenishment_evidence_changes_with_size_similarity():
    same = run(qf.HiddenReplenishment(), _refill_scenario(sizes=[1000.0, 1000.0, 1000.0]))[-1]
    diff = run(qf.HiddenReplenishment(), _refill_scenario(sizes=[1000.0, 2000.0, 400.0]))[-1]
    assert diff.evidence["similarity"] < same.evidence["similarity"]
    assert diff.score < same.score


# ============================================================================= #33 order_splitting
def _prints_scenario(sizes: Sequence[float], gap_s: float = 10.0, direction: float = 1.0) -> List[MarketState]:
    st = [S(0, BIDS, ASKS, tv=1000.0)]
    tv = 1000.0
    for i, q in enumerate(sizes):
        t = gap_s * (i + 1)
        tv += q
        lp = {"t": _t(t).isoformat(), "price": 10.1, "qty": q, "trade_id": str(i), "direction": direction}
        st.append(S(t, BIDS, ASKS, tv=tv, iv=q, itr=1, dirn=direction, lp=lp, clock=_t(t).isoformat()))
        st.append(S(t + 2.5, BIDS, ASKS, tv=tv, iv=q, itr=1, dirn=direction, lp=lp, clock=_t(t).isoformat()))
    return st


def test_machinery_order_splitting_activates():
    r = run(qf.OrderSplitting(), _prints_scenario([500.0] * 8))[-1]
    assert r.score >= 0.6
    assert r.evidence["prints"] == 8 and r.evidence["modal_repeats"] == 8
    assert r.evidence["mode_share"] == 1.0 and r.evidence["cadence_cv"] == pytest.approx(0.0)
    assert r.evidence["size_source"] == "prints"
    _check_common(r, 1)
    rs = run(qf.OrderSplitting(), _prints_scenario([500.0] * 8, direction=-1.0))[-1]
    assert rs.evidence["direction"] == -1


def test_machinery_order_splitting_varied_sizes_stay_null():
    r = run(qf.OrderSplitting(), _prints_scenario([100.0, 260.0, 410.0, 730.0, 1300.0, 90.0, 2200.0, 560.0]))[-1]
    assert r.evidence["modal_repeats"] == 1 and r.score < 0.35


def test_machinery_order_splitting_evidence_changes_and_touch_fallback():
    half = run(qf.OrderSplitting(), _prints_scenario([500.0] * 4 + [130.0, 970.0, 2100.0, 3300.0]))[-1]
    full = run(qf.OrderSplitting(), _prints_scenario([500.0] * 8))[-1]
    assert half.evidence["mode_share"] == pytest.approx(0.5) and half.score < full.score
    # irregular cadence lowers the regularity component
    reg = run(qf.OrderSplitting(), _prints_scenario([500.0] * 8, gap_s=10.0))[-1]
    st = _prints_scenario([500.0] * 8, gap_s=10.0)
    # squeeze prints unevenly: same sizes, irregular arrival times
    irregular = [S((1.0 + 3.0 * (i % 3)) * i, m.bids, m.asks, tv=m.trade_volume, lp=m.last_print,
                   iv=m.interval_volume, itr=m.interval_trades, dirn=m.trade_flow_direction)
                 for i, m in enumerate(st)]
    for i, m in enumerate(irregular):
        if m.last_print:
            m.last_print = dict(m.last_print, t=m.t.isoformat(), trade_id=str(i))
    ri = run(qf.OrderSplitting(), irregular)[-1]
    assert ri.evidence["regularity"] < reg.evidence["regularity"]
    # touch-consumption fallback when no prints / single-trade intervals are carried
    st2 = []
    tv = 1000.0
    for i in range(10):
        q = 1000.0 if i % 2 == 0 else 700.0
        if i % 2 == 1:
            tv += 300.0
        st2.append(S(5 * i, [(10.0, q)] + BIDS[1:], ASKS, tv=tv))
    r2 = run(qf.OrderSplitting(), st2)[-1]
    assert r2.evidence["size_source"] == "touch_consumption" and r2.evidence["modal_repeats"] == 5
    assert r2.evidence["direction"] == -1


# ============================================================================= #3 liquidity_sweep
def _sweep_scenario(levels: int = 3, burst_vol: float = 2400.0) -> List[MarketState]:
    st = []
    tv = 1000.0
    for i in range(13):
        tv += 10.0
        st.append(S(5 * i, BIDS, ASKS, tv=tv))
    st.append(S(65, BIDS, ASKS[levels:], tv=tv + burst_vol))
    return st


def test_machinery_liquidity_sweep_activates():
    r = run(sf.LiquiditySweep(), _sweep_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "ask" and r.evidence["levels_consumed"] == 3
    assert r.evidence["retreat_ticks"] == pytest.approx(3.0)
    assert r.evidence["qty_consumed"] == pytest.approx(2400.0)
    assert r.evidence["volume_ratio"] > 5
    _check_common(r, 1)
    # bid sweep mirrors the direction
    st = _sweep_scenario()
    st[-1] = S(65, BIDS[3:], ASKS, tv=st[-1].trade_volume)
    rb = run(sf.LiquiditySweep(), st)[-1]
    assert rb.evidence["side"] == "bid" and rb.evidence["direction"] == -1 and rb.score >= 0.6


def test_machinery_liquidity_sweep_evidence_changes_and_no_tape():
    r1 = run(sf.LiquiditySweep(), _sweep_scenario(levels=1))[-1]
    r3 = run(sf.LiquiditySweep(), _sweep_scenario(levels=3))[-1]
    assert r1.evidence["levels_consumed"] == 1 and r1.score < r3.score
    # without a tape the volume component is replaced by the taken share and named as missing
    st = [S((m.t - T0).total_seconds(), m.bids, m.asks) for m in _sweep_scenario()]
    rn = run(sf.LiquiditySweep(), st)[-1]
    assert "trade_volume" in rn.evidence["missing"] and rn.evidence["volume_burst"] is None
    assert rn.evidence["taken_share"] == pytest.approx(2400.0 / 3300.0)
    assert rn.score >= 0.6


# ============================================================================= #4 failed_sweep
def _failed_sweep_scenario(return_book: bool = True) -> List[MarketState]:
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(7)]                 # 0..30 s
    swept_b, swept_a = [(9.5, 500.0)], [(9.6, 300.0), (9.7, 300.0)]
    for i in range(7, 11):                                                    # 35..50 s at the trough
        st.append(S(5 * i, swept_b, swept_a, tv=3000.0))
    for i in range(11, 16):                                                   # 55..75 s back
        st.append(S(5 * i, BIDS if return_book else swept_b, ASKS if return_book else swept_a, tv=3000.0))
    return st


def test_machinery_failed_sweep_activates():
    r = run(sf.FailedSweep(), _failed_sweep_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "bid"
    assert r.evidence["excursion_ticks"] == pytest.approx(5.0)
    assert r.evidence["return_share"] == pytest.approx(1.0)
    assert r.evidence["retreat_ticks_at_extreme"] == pytest.approx(5.0)
    _check_common(r, 1)


def test_machinery_failed_sweep_without_return_is_null_and_evidence_changes():
    rs = run(sf.FailedSweep(), _failed_sweep_scenario(return_book=False))
    assert rs[-1].evidence["return_share"] == 0.0 and rs[-1].score < 0.35
    # a partial return scores between the two
    st = _failed_sweep_scenario()
    st[-1] = S(75, [(9.8, 500.0)] + BIDS[2:], [(9.9, 300.0)] + ASKS[1:], tv=3000.0)   # mid 9.85: 60 % back
    rp = run(sf.FailedSweep(), st)[-1]
    assert 0 < rp.evidence["return_share"] < 1
    assert rs[-1].score < rp.score < run(sf.FailedSweep(), _failed_sweep_scenario())[-1].score
    # an ask-side sweep (ask retreats 6 ticks → mid +3 ticks) that comes back is the mirror image
    up = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(7)]
    up += [S(5 * i, BIDS, [(10.7, 100.0)], tv=1000.0) for i in range(7, 11)]        # ask retreats, bid still
    up += [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(11, 16)]
    ru = run(sf.FailedSweep(), up)[-1]
    assert ru.evidence["side"] == "ask" and ru.evidence["excursion_ticks"] == pytest.approx(3.0)
    assert ru.score >= 0.6 and ru.evidence["direction"] == -1
    # the mid moving without the swept side's best retreating (a quote drift) is gated out
    drift = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(7)]
    drift += [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(7, 11)]
    for m in drift[7:11]:
        m.mid = 9.55                                                                  # carried mid only
    drift += [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(11, 16)]
    rd = run(sf.FailedSweep(), drift)[-1]
    assert rd.evidence["retreat_ticks_at_extreme"] == 0.0 and rd.score == 0.0


# ============================================================================= #5 exhaustion
def _exhaustion_scenario(rebuild: bool = True) -> List[MarketState]:
    st = []
    for i in range(61):                                  # 0..300 s
        s = 5 * i
        if s < 240:
            inten, vel, asks = 5.0, 0.0, ASKS
        elif s <= 270:
            inten, vel, asks = 30.0, 5.0, scale(ASKS, 0.2)
        else:
            k = (s - 270) / 30.0
            inten, vel = 30.0, 5.0 * (1.0 - k)
            asks = scale(ASKS, 0.2 + 0.8 * k) if rebuild else scale(ASKS, 0.2)
        st.append(S(s, BIDS, asks, tv=1000.0 + s, intensity=inten, vel=vel))
    return st


def test_machinery_exhaustion_activates():
    r = run(sf.Exhaustion(), _exhaustion_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["velocity_decay"] == pytest.approx(1.0)
    assert r.evidence["against_side"] == "ask"
    assert r.evidence["rebuild_share"] > 0.6
    assert r.evidence["intensity_z"] is None or r.evidence["intensity_z"] > 2.5 or r.evidence["intensity_ratio"] > 3
    _check_common(r, -1)


def test_machinery_exhaustion_without_rebuild_is_weaker_and_flat_is_null():
    full = run(sf.Exhaustion(), _exhaustion_scenario(rebuild=True))[-1]
    none = run(sf.Exhaustion(), _exhaustion_scenario(rebuild=False))[-1]
    assert none.evidence["rebuild_share"] == pytest.approx(0.0) and none.score == 0.0
    assert full.score > none.score
    flat = [S(5 * i, BIDS, ASKS, tv=1000.0, intensity=5.0, vel=0.0) for i in range(61)]
    assert run(sf.Exhaustion(), flat)[-1].score < 0.35


# ============================================================================= #14 liquidity_vacuum
def _vacuum_scenario(bid_factor: float = 0.05, ask_factor: float = 0.05, refill: bool = False) -> List[MarketState]:
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(41)]                # 0..200 s
    for i in range(41, 49):                                                  # 205..240 s collapsed
        st.append(S(5 * i, scale(BIDS, bid_factor), scale(ASKS, ask_factor), tv=1000.0))
    if refill:
        st.append(S(245, scale(BIDS, 0.6), scale(ASKS, 0.6), tv=1000.0))
    return st


def test_machinery_liquidity_vacuum_activates_both_sides():
    r = run(sf.LiquidityVacuum(), _vacuum_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["collapse_max"] == pytest.approx(0.95) and r.evidence["collapse_min"] == pytest.approx(0.95)
    assert r.evidence["replenish_share"] == 0.0
    _check_common(r, 0)


def test_machinery_liquidity_vacuum_one_sided_direction_and_refill_damps():
    rb = run(sf.LiquidityVacuum(), _vacuum_scenario(bid_factor=0.05, ask_factor=1.0))[-1]
    assert rb.evidence["direction"] == -1 and rb.evidence["side"] == "bid" and rb.score >= 0.6
    ra = run(sf.LiquidityVacuum(), _vacuum_scenario(bid_factor=1.0, ask_factor=0.05))[-1]
    assert ra.evidence["direction"] == 1
    rr = run(sf.LiquidityVacuum(), _vacuum_scenario(refill=True))[-1]
    assert rr.evidence["replenish_share"] > 0.5 and rr.score < rb.score
    mild = run(sf.LiquidityVacuum(), _vacuum_scenario(bid_factor=0.7, ask_factor=0.7))[-1]
    assert mild.evidence["collapse_max"] == pytest.approx(0.3) and mild.score < 0.35


# ============================================================================= #15 vacuum_snapback
def _snapback_scenario(recover: bool = True, late: bool = False) -> List[MarketState]:
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(21)]                # 0..100 s
    for i in range(21, 25):                                                  # 105..120 s vacuum + mid down
        st.append(S(5 * i, [(9.5, 300.0)], [(9.6, 300.0)], tv=2000.0))
    if recover:
        n = 4 if not late else 40
        for i in range(25, 25 + n):                                          # back
            st.append(S(5 * i, BIDS, ASKS, tv=2000.0))
    else:
        for i in range(25, 29):
            st.append(S(5 * i, [(9.5, 300.0)], [(9.6, 300.0)], tv=2000.0))
    return st


def test_machinery_vacuum_snapback_activates():
    r = run(sf.VacuumSnapback(), _snapback_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["collapse"] > 0.85
    assert r.evidence["revert_share"] == pytest.approx(1.0)
    assert r.evidence["depth_return"] == pytest.approx(1.0)
    assert r.evidence["time_factor"] == 1.0
    _check_common(r, 1)


def test_machinery_vacuum_snapback_null_without_recovery_and_late_recovery_fades():
    rn = run(sf.VacuumSnapback(), _snapback_scenario(recover=False))[-1]
    assert rn.score < 0.35
    rl = run(sf.VacuumSnapback(), _snapback_scenario(late=True))[-1]
    assert rl.evidence["time_factor"] == 0.0 and rl.score == 0.0
    r = run(sf.VacuumSnapback(), _snapback_scenario())[-1]
    assert rl.evidence["seconds_since_trough"] > r.evidence["seconds_since_trough"]


# ============================================================================= #20 liquidity_run
def _run_scenario(ticks: int = 6, flow_dir: float = 1.0, stall: bool = True) -> List[MarketState]:
    st = []
    tv = 1000.0
    for k in range(ticks + 1):                                               # 0..60 s: one tick per 10 s
        s = 10 * k
        asks = shift(scale(ASKS, 0.1), k) if k < ticks else shift(ASKS, k)
        bids = shift(BIDS, k)
        tv += 100.0
        st.append(S(s, bids, asks, tv=tv, iv=100.0, itr=1, dirn=flow_dir, clock=_t(s).isoformat()))
        st.append(S(s + 5, bids, asks, tv=tv, iv=100.0, itr=1, dirn=flow_dir, clock=_t(s).isoformat()))
    if stall:
        for s in range(70, 105, 5):
            st.append(S(s, shift(BIDS, ticks), shift(ASKS, ticks), tv=tv, iv=0.0, itr=0, dirn=None,
                        clock=_t(s).isoformat()))
    else:
        for j, s in enumerate(range(70, 105, 5)):
            st.append(S(s, shift(BIDS, ticks + j + 1), shift(ASKS, ticks + j + 1), tv=tv + 100 * (j + 1), iv=100.0,
                        itr=1, dirn=flow_dir, clock=_t(s).isoformat()))
    return st


def test_machinery_liquidity_run_activates():
    r = run(sf.LiquidityRun(), _run_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["run_ticks"] == pytest.approx(6.0)
    assert r.evidence["run_direction"] == 1 and r.evidence["consumed_side"] == "ask"
    assert r.evidence["flow_consistency"] == pytest.approx(1.0)
    assert r.evidence["thin_share"] > 0.6
    assert r.evidence["stall"] == pytest.approx(1.0)
    _check_common(r, -1)


def test_machinery_liquidity_run_no_stall_is_null_and_flow_changes_evidence():
    rn = run(sf.LiquidityRun(), _run_scenario(stall=False))[-1]
    assert rn.evidence["stall"] < 0.35 and rn.score < 0.35
    against = run(sf.LiquidityRun(), _run_scenario(flow_dir=-1.0))[-1]
    along = run(sf.LiquidityRun(), _run_scenario(flow_dir=1.0))[-1]
    assert against.evidence["flow_along_run"] == 0.0 and along.evidence["flow_along_run"] == pytest.approx(1.0)
    assert against.score < along.score
    short = run(sf.LiquidityRun(), _run_scenario(ticks=1))[-1]
    assert short.evidence["run_ticks"] == pytest.approx(1.0) and short.score < 0.35


# ============================================================================= #21 ignition
def _ignition_states(v: float, a: float, ta: float, widen: float = 2.0) -> List[MarketState]:
    st = [S(5 * i, BIDS, ASKS, tv=1000.0, intensity=10.0, vel=0.0, acc=0.0, tacc=0.0) for i in range(30)]
    st.append(S(150, BIDS, shift(ASKS, int(widen)), tv=1200.0, intensity=10.0 + ta, vel=v, acc=a, tacc=ta))
    return st


def test_machinery_ignition_activates():
    r = run(sf.Ignition(), _ignition_states(6.0, 4.0, 20.0))[-1]
    assert r.score >= 0.6
    assert r.evidence["velocity"] == 6.0 and r.evidence["acceleration"] == 4.0
    assert r.evidence["spread_expansion"] == pytest.approx(2.0)
    assert r.evidence["trade_acceleration_rel"] > 1.5
    _check_common(r, 1)
    rd = run(sf.Ignition(), _ignition_states(-6.0, -4.0, 20.0))[-1]
    assert rd.evidence["direction"] == -1 and rd.score >= 0.6


def test_machinery_ignition_null_and_evidence_changes():
    assert run(sf.Ignition(), _ignition_states(0.0, 0.0, 0.0, widen=0))[-1].score < 0.35
    decel = run(sf.Ignition(), _ignition_states(6.0, -4.0, 20.0))[-1]          # velocity fading: no ignition
    assert decel.score == 0.0
    narrow = run(sf.Ignition(), _ignition_states(6.0, 4.0, 20.0, widen=0))[-1]
    wide = run(sf.Ignition(), _ignition_states(6.0, 4.0, 20.0, widen=2))[-1]
    assert narrow.evidence["spread_expansion"] == pytest.approx(0.0) and narrow.score < wide.score
    # fallback: no velocity fields → computed from the mid series
    st = [S(5 * i, shift(BIDS, i // 2), shift(ASKS, i // 2), tv=1000.0 + 50 * i, intensity=5.0 + i) for i in range(30)]
    r = run(sf.Ignition(), st)[-1]
    assert r.evidence["velocity"] == pytest.approx(6.0, rel=0.2) and "missing" not in r.evidence


# ============================================================================= #34 liquidity_depletion
def _depletion_scenario(final: float = 0.2, price_move_ticks: int = 0, side: str = "both") -> List[MarketState]:
    st = []
    for i in range(25):                                                      # 0..120 s
        k = i / 24.0
        f = 1.0 - (1.0 - final) * k
        b = scale(BIDS, f if side in ("both", "bid") else 1.0)
        a = scale(ASKS, f if side in ("both", "ask") else 1.0)
        d = price_move_ticks if i == 24 else 0
        st.append(S(5 * i, shift(b, d), shift(a, d), tv=1000.0))
    return st


def test_machinery_liquidity_depletion_activates():
    r = run(sf.LiquidityDepletion(), _depletion_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["depletion"] == pytest.approx(0.8)
    assert r.evidence["consistency"] == 1.0 and r.evidence["mid_move_ticks"] == 0.0
    _check_common(r, 0)
    rb = run(sf.LiquidityDepletion(), _depletion_scenario(side="bid"))[-1]
    assert rb.evidence["direction"] == -1 and rb.score >= 0.6
    ra = run(sf.LiquidityDepletion(), _depletion_scenario(side="ask"))[-1]
    assert ra.evidence["direction"] == 1


def test_machinery_liquidity_depletion_price_move_and_evidence():
    moved = run(sf.LiquidityDepletion(), _depletion_scenario(price_move_ticks=3))[-1]
    assert moved.evidence["price_factor"] == 0.0 and moved.score == 0.0
    mild = run(sf.LiquidityDepletion(), _depletion_scenario(final=0.9))[-1]
    assert mild.evidence["depletion"] == pytest.approx(0.1) and mild.score < 0.35
    # the queue engine's own estimate is taken when larger
    st = _depletion_scenario(final=0.9)
    st[-1].liquidity_depletion = 0.75
    r = run(sf.LiquidityDepletion(), st)[-1]
    assert r.evidence["engine_depletion"] == 0.75 and r.evidence["depletion"] == 0.75


# ============================================================================= lifecycle
def _lifecycle_states(mid_move_ticks: int) -> List[MarketState]:
    """Static → bid pull (t 100..130) → hold (t 135..250) → window rolls past the fall (share → 0).
    The asks move by ``mid_move_ticks`` at t = 200 so the realised mid move sets the outcome."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(20)]
    for k in range(1, 7):
        st.append(S(100 + 5 * k, [(10.0, 1000.0 - 800.0 * k / 6)] + BIDS[1:], ASKS, tv=1000.0))
    for s in range(135, 300, 5):
        asks = shift(ASKS, mid_move_ticks) if s >= 200 else ASKS
        st.append(S(s, [(10.0, 200.0)] + BIDS[1:], asks, tv=1000.0))
    return st


@pytest.mark.parametrize("mid_move,terminal", [(-1, "resolved"), (1, "failed")])
def test_machinery_lifecycle_building_active_confirmed_release(mid_move, terminal):
    mech = qf.QueuePullStack()
    rs = run(mech, _lifecycle_states(mid_move), use_update=True)
    seq = [r.state for r in rs]
    assert seq[0] == "inactive"
    assert "building" in seq and "active" in seq and "confirmed" in seq
    i_b, i_a, i_c = seq.index("building"), seq.index("active"), seq.index("confirmed")
    assert i_b < i_a < i_c
    # confirmed only after confirm_s of activity
    assert (rs[i_c].start_time is not None)
    assert rs[i_c].duration_s >= mech.confirm_s
    assert (_t(0) + timedelta(seconds=0)).tzinfo is not None
    assert terminal in seq
    i_t = seq.index(terminal)
    assert i_t > i_c
    assert rs[i_t].score < mech.release_threshold
    assert rs[i_c].evidence["direction"] == -1
    assert rs[i_c].evidence["peak_score"] >= 0.6
    assert "mid_change_since_start" in rs[i_c].evidence


def test_machinery_lifecycle_direction_zero_resolves():
    """A mechanism that implies no price direction resolves (never fails) on release."""
    mech = qf.QuoteRefreshChurn()
    st = _churn_scenario(n=30)                                   # 145 s of churn
    st += [S(150 + 5 * i, BIDS, ASKS, tv=1000.0) for i in range(20)]
    rs = run(mech, st, use_update=True)
    seq = [r.state for r in rs]
    assert "confirmed" in seq and "resolved" in seq and "failed" not in seq


@pytest.mark.parametrize("name", QUEUE_NAMES + SWEEP_NAMES)
def test_machinery_update_never_raises_on_sparse_states(name):
    """Partial states (one side, no tick, no tape, no history) never raise inside update()."""
    mech = REGISTRY[name]()
    hist = StateHistory()
    states = [S(0, [], ASKS, tick=None), S(5, BIDS, [], tick=None), S(10, BIDS[:1], ASKS[:1]),
              S(15, BIDS, ASKS, tv=500.0, iv=100.0, itr=1, dirn=0.3, intensity=2.0),
              S(16, BIDS, ASKS, tv=500.0, lp={"t": None, "qty": 100.0, "price": 10.1}),
              S(20, [], [], tick=None)]
    for ms in states:
        r = mech.update(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert isinstance(r.evidence, dict) and isinstance(r.baseline, dict)
        hist.push(ms)


# ============================================================================= real data
def test_realdata_fixture_capture_through_engine():
    """The closed-market fixture: every mechanism computes on every state without error; the closed
    (empty / one-sided, static) books never build any of the 13 mechanisms and the readings name
    their missing inputs; baselines are present."""
    from tower.engine import Engine, EngineConfig
    from tower.normalize import normalize_store
    events, _ = normalize_store(FIXTURE)
    assert events
    eng = Engine(EngineConfig(strict=True))
    n = 0
    seen = set()
    with_missing = 0
    for ev in events:
        ms = eng.process(ev)
        if ms is None:
            continue
        n += 1
        for name in QUEUE_NAMES + SWEEP_NAMES:
            m = ms.mechanisms.get(name)
            if m is None:
                continue
            seen.add(name)
            assert 0.0 <= m.score <= 1.0
            assert m.state in ("inactive", "building", "active", "confirmed", "failed", "resolved")
            assert m.score < REGISTRY[name].build_threshold, (name, ms.symbol, ms.t, m.evidence)
            if m.evidence.get("missing"):
                with_missing += 1
            assert set(BASELINE_KEYS) <= set(m.baseline) or m.evidence.get("missing")
    assert n > 0 and seen == set(QUEUE_NAMES + SWEEP_NAMES)
    assert with_missing > 0
    assert not eng.metrics["errors"]
