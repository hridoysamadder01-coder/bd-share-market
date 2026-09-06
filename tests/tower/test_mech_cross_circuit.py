"""tower.mechanics.cross_family + circuit_family — mechanisms 17, 18 / 44, 45, 46, 47, 48, 49.

test_machinery_* build deterministic MarketState scenarios (5-s cadence, tick 0.1) carrying the
``circuit`` / ``cross`` / ``sector`` dicts the circuit and cross engines write, and feed them
through a StateHistory exactly as the engine does (history holds the states *before* the current
one): per mechanism a scenario that drives the score ≥ active_threshold, a null scenario that keeps
it < build_threshold, missing-input readings, evidence computed from the inputs (changes when the
inputs change) and the lifecycle building → active → confirmed → resolved / failed through
``Mechanism.update``.  test_realdata_* run the committed closed-market capture (and the live
capture when present) through the engine.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import pytest

from tower.mechanics import REGISTRY, all_mechanisms
from tower.mechanics.base import Mechanism, StateHistory
from tower.mechanics import circuit_family as cf
from tower.mechanics import cross_family as xf
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)          # 10:00 Dhaka, continuous open
T_CLOSE = datetime(2026, 9, 6, 7, 0, 0, tzinfo=timezone.utc)     # 13:00 Dhaka (close 14:00)
TICK = 0.1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
LIVE = "/home/user/bd-share-market/evidence/capture/2026-09-06"

BIDS = [(10.0, 1000.0), (9.9, 800.0), (9.8, 600.0), (9.7, 500.0), (9.6, 400.0)]
ASKS = [(10.1, 1000.0), (10.2, 800.0), (10.3, 600.0), (10.4, 500.0), (10.5, 400.0)]

CROSS_NAMES = ["basket_rebalance", "cross_lead_lag"]
CIRCUIT_NAMES = ["circuit_regime", "circuit_streak", "circuit_prehit_pressure", "circuit_lock_strength",
                 "circuit_break_weakness", "circuit_next_session"]
ALL_NAMES = CROSS_NAMES + CIRCUIT_NAMES
BASELINE_KEYS = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio", "price_only_response", "volume_only_response")


# ----------------------------------------------------------------------------- builders
def _t(s: float, base: datetime = T0) -> datetime:
    return base + timedelta(seconds=s)


def shift(levels, ticks: float):
    return [(round(p + ticks * TICK, 6), q) for p, q in levels]


def C(price: Optional[float] = 10.05, upper: float = 11.0, lower: float = 9.0, tick: float = TICK, **over) -> Dict[str, Any]:
    """A circuit dict as ``CircuitEngine.on_state`` writes it (limits, distances from ``price``,
    flags / counters at their quiet defaults), overridden by ``over``."""
    c: Dict[str, Any] = {"upper_limit": upper, "lower_limit": lower, "tick": tick, "rule_source": "lankabd_circuit",
                         "unverified": False, "price": price, "price_basis": "mid" if price is not None else None,
                         "dist_up_ticks": None, "dist_down_ticks": None, "dist_up_pct": None, "dist_down_pct": None,
                         "nearer_limit": None, "approach_velocity": None, "approach_acceleration": None,
                         "hit_up": None, "hit_down": None, "locked_up": None, "locked_down": None,
                         "time_locked_s": 0.0, "unlock_count": 0, "relock_count": 0, "time_between_unlock_relock_s": None,
                         "queue_at_upper": None, "queue_at_lower": None, "queue_side": None, "queue_delta_60s": None,
                         "queue_growth": None, "queue_decay": None, "queue_persistence_s": None, "max_queue_at_limit": None,
                         "volume_approaching": None, "volume_while_locked": None, "pre_hit_state": None,
                         "shares_to_door": None, "door_visible": None, "shares_to_floor": None, "floor_visible": None,
                         "exception": None, "prior_upper_streak": 0, "prior_lower_streak": 0,
                         "consecutive_upper_streak": 0, "consecutive_lower_streak": 0,
                         "streak_continuation_strength": None, "streak_weakening": None, "break_day": None,
                         "break_behaviour": None, "next_session": None, "open_price": None, "session_elapsed_s": 600.0,
                         "first_hit_time": None}
    if price is not None:
        c["dist_up_pct"] = (upper - price) / price * 100.0
        c["dist_down_pct"] = (price - lower) / price * 100.0
        c["dist_up_ticks"] = (upper - price) / tick
        c["dist_down_ticks"] = (price - lower) / tick
        c["nearer_limit"] = "up" if c["dist_up_pct"] <= c["dist_down_pct"] else "down"
        c["hit_up"] = c["hit_down"] = c["locked_up"] = c["locked_down"] = False
    c.update(over)
    return c


def S(s: float, bids=BIDS, asks=ASKS, *, base: datetime = T0, circuit: Optional[Dict[str, Any]] = None,
      cross: Optional[Dict[str, Any]] = None, sector: Optional[Dict[str, Any]] = None, tv=None, iv=None,
      pd=None, ps=None, cp=None, vel=None, phase: str = "CONTINUOUS", tick: Optional[float] = TICK,
      seq: int = 0) -> MarketState:
    """A MarketState carrying a displayed book plus what the circuit / cross / pressure engines write."""
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
    ms.trade_volume, ms.interval_volume = tv, iv
    ms.pressure_direction, ms.pressure_strength, ms.combined_pressure = pd, ps, cp
    ms.price_velocity = vel
    ms.circuit = dict(circuit) if circuit is not None else {}
    ms.cross = dict(cross) if cross is not None else {}
    ms.sector = dict(sector) if sector is not None else {}
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


def last(mech: Mechanism, states: Sequence[MarketState]):
    return run(mech, states)[-1]


def _check_common(r, expect_direction: Optional[int] = None):
    assert set(BASELINE_KEYS) <= set(r.baseline)
    assert "direction" in r.evidence and r.evidence["direction"] in (-1, 0, 1)
    assert isinstance(r.evidence.get("inputs"), dict)
    if expect_direction is not None:
        assert r.evidence["direction"] == expect_direction


def states_with(circuit: Dict[str, Any], n: int = 12, **kw) -> List[MarketState]:
    return [S(5 * i, circuit=circuit, **kw) for i in range(n)]


# ----------------------------------------------------------------------------- registry / nulls
def test_machinery_registry_has_all_8():
    all_mechanisms()
    for n in ALL_NAMES:
        assert n in REGISTRY, n
    assert all(REGISTRY[n].family == "cross" for n in CROSS_NAMES)
    assert all(REGISTRY[n].family == "circuit" for n in CIRCUIT_NAMES)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_null_quiet_state_stays_below_build(name):
    """A static book mid-band with a quiet cross context at 10:00 Dhaka never builds any of the 8."""
    cross = {"leaders": [], "laggers": [], "lead_lag_pairs_evaluated": 4, "basket_sync": 0.5, "basket_sync_n": 4,
             "simultaneous_liquidity_change": {"share": 0.0, "n": 6, "count": 0, "sign": None},
             "synchronized_expansion": {"share": 0.0, "n": 6, "count": 0}}
    states = [S(5 * i, circuit=C(), cross=cross, tv=1000.0 + 100 * i) for i in range(421)]
    rs = run(REGISTRY[name](), states)
    assert max(r.score for r in rs) < REGISTRY[name].build_threshold
    _check_common(rs[-1])
    assert "missing" not in rs[-1].evidence, name


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_no_context_is_missing_not_zero(name):
    """No circuit dict, no cross context, no tape: score 0 with the missing inputs named."""
    states = [S(5 * i, [], [], base=T_CLOSE + timedelta(seconds=2000)) for i in range(12)]
    rs = run(REGISTRY[name](), states)
    assert rs[-1].score == 0.0
    assert rs[-1].evidence.get("missing"), name
    _check_common(rs[-1], 0)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_machinery_update_never_raises_on_sparse_states(name):
    mech = REGISTRY[name]()
    hist = StateHistory()
    states = [S(0, [], ASKS, tick=None), S(5, BIDS, [], tick=None, circuit=C(price=None)),
              S(10, BIDS[:1], ASKS[:1], circuit=C(locked_up=True, hit_up=True, price=11.0)),
              S(15, tv=500.0, iv=100.0, cross={"leaders": [["B", 30.0, 0.7]], "laggers": None, "lead_lag_pairs_evaluated": 1}),
              S(16, circuit={"upper_limit": 11.0, "lower_limit": 9.0}, cross={"simultaneous_liquidity_change": {"share": None}}),
              S(20, [], [], tick=None, phase="CLOSED", circuit=C(prior_upper_streak=2, next_session="reversal",
                                                                  break_behaviour={"gap_open_ticks": None}))]
    for ms in states:
        r = mech.update(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert isinstance(r.evidence, dict) and isinstance(r.baseline, dict)
        hist.push(ms)


# ============================================================================= #17 basket_rebalance
def _basket_cross(liq_share: Optional[float] = 0.7, exp_share: Optional[float] = 0.6, bsync: Optional[float] = 1.0,
                  bsn: Optional[int] = 4) -> Dict[str, Any]:
    return {"simultaneous_liquidity_change": ({"share": liq_share, "n": 10, "count": round(10 * liq_share), "sign": 1,
                                               "own_rel_change": 0.4} if liq_share is not None else None),
            "synchronized_expansion": ({"share": exp_share, "n": 10, "count": round(10 * exp_share), "own_in_top_decile": True}
                                       if exp_share is not None else None),
            "basket_sync": bsync, "basket_sync_n": bsn, "leaders": None, "laggers": None, "lead_lag_pairs_evaluated": 0}


def _basket_scenario(base: datetime = T_CLOSE, burst: bool = True, phase_late: str = "CONTINUOUS", n: int = 421,
                     pressure: float = 0.6, cross: Optional[Dict[str, Any]] = None, sector: Optional[Dict[str, Any]] = None,
                     with_tape: bool = True) -> List[MarketState]:
    out, tv = [], 1000.0
    cross = _basket_cross() if cross is None else cross
    for i in range(n):                                                # 5-s cadence: 0..2100 s
        t = 5 * i
        late = t >= 1800
        tv += 500.0 if (late and burst) else 100.0
        cp = pressure if late else (0.05 if i % 2 else -0.05)
        out.append(S(t, base=base, tv=(tv if with_tape else None), cp=cp, cross=cross, sector=sector,
                     phase=(phase_late if late else "CONTINUOUS")))
    return out


def test_machinery_basket_rebalance_activates():
    r = last(xf.BasketRebalance(), _basket_scenario())
    assert r.score >= 0.6
    assert r.evidence["minutes_to_close"] == pytest.approx(25.0) and r.evidence["window_factor"] == 1.0
    assert r.evidence["rate_now_per_min"] == pytest.approx(6000.0) and r.evidence["base_rate_per_min"] == pytest.approx(1200.0)
    assert r.evidence["volume_rel"] == pytest.approx(5.0) and r.evidence["burst"] == 1.0
    assert r.evidence["simultaneity"] == 1.0 and r.evidence["sync"] == 1.0 and r.evidence["blend"] == pytest.approx(1.0)
    assert r.evidence["direction_basis"] == "combined_pressure"
    _check_common(r, 1)
    post = last(xf.BasketRebalance(), _basket_scenario(phase_late="POST_CLOSE"))
    assert post.evidence["window_factor"] == 1.0 and post.score >= 0.6
    sell = last(xf.BasketRebalance(), _basket_scenario(pressure=-0.6))
    assert sell.evidence["direction"] == -1


def test_machinery_basket_rebalance_null_window_and_missing():
    quiet = last(xf.BasketRebalance(), _basket_scenario(burst=False, cross=_basket_cross(0.05, 0.05, 0.3, 4)))
    assert quiet.evidence["volume_rel"] == pytest.approx(1.0) and quiet.evidence["burst"] == 0.0
    assert quiet.evidence["simultaneity"] == 0.0 and quiet.score == 0.0 and quiet.evidence["direction"] == 0
    early = last(xf.BasketRebalance(), _basket_scenario(base=T_CLOSE - timedelta(hours=1)))
    assert early.evidence["in_close_window"] is False and early.score == 0.0 and "missing" not in early.evidence
    closed = last(xf.BasketRebalance(), _basket_scenario(phase_late="CLOSED"))
    assert closed.score == 0.0 and closed.evidence["phase"] == "CLOSED"
    no_cross = last(xf.BasketRebalance(), _basket_scenario(cross=_basket_cross(None, None, None, None)))
    assert no_cross.score == 0.0 and no_cross.evidence["missing"] == ["cross.simultaneous_liquidity_change"]
    assert no_cross.evidence["burst"] == 1.0                       # the own burst was still computed
    no_tape = last(xf.BasketRebalance(), _basket_scenario(with_tape=False))
    assert no_tape.score == 0.0 and no_tape.evidence["missing"] == ["trade_volume"]
    short = last(xf.BasketRebalance(), [S(1800 + 5 * i, base=T_CLOSE, tv=1000.0 + 500 * i, cross=_basket_cross()) for i in range(30)])
    assert short.score == 0.0 and any("baseline" in m for m in short.evidence["missing"])


def test_machinery_basket_rebalance_evidence_tracks_inputs():
    half = last(xf.BasketRebalance(), _basket_scenario(cross=_basket_cross(0.4, None, 1.0, 4)))
    assert half.evidence["simultaneity"] == pytest.approx(0.5) and half.evidence["simultaneity_basis"] == "liquidity_change"
    assert half.evidence["blend"] == pytest.approx(0.4 + 0.175 + 0.25) and half.score == pytest.approx(0.825)
    no_sector = last(xf.BasketRebalance(), _basket_scenario(cross=_basket_cross(0.4, None, None, None)))
    assert no_sector.evidence["unverified"] == ["basket_sync"] and no_sector.evidence["sync"] is None
    assert no_sector.score == pytest.approx((0.4 + 0.175) / 0.75)
    weak_sync = last(xf.BasketRebalance(), _basket_scenario(cross=_basket_cross(0.7, 0.6, 0.8, 5)))
    assert weak_sync.evidence["sync"] == pytest.approx(0.5) and weak_sync.score == pytest.approx(0.875)
    sec = last(xf.BasketRebalance(), _basket_scenario(pressure=0.0, sector={"sector": "BANK", "sector_pressure": -0.5,
                                                                            "sector_breadth": {"net": 0.2}}))
    assert sec.evidence["direction"] == -1 and sec.evidence["direction_basis"] == "sector_pressure"
    br = last(xf.BasketRebalance(), _basket_scenario(pressure=0.0, sector={"sector": "BANK", "sector_pressure": None,
                                                                           "sector_breadth": {"net": 0.8}}))
    assert br.evidence["direction"] == 1 and br.evidence["direction_basis"] == "sector_breadth_net"


# ============================================================================= #18 cross_lead_lag
def _ll_cross(leaders=(("B", 30.0, 0.75), ("C", 15.0, 0.5)), laggers=(), evaluated: int = 5, own: float = 0.0,
              market: Optional[float] = 0.01) -> Dict[str, Any]:
    return {"leaders": None if leaders is None else [tuple(x) for x in leaders],
            "laggers": None if laggers is None else [tuple(x) for x in laggers],
            "lead_lag_pairs_evaluated": evaluated, "symbol_return_60s": own, "market_return_60s": market}


def test_machinery_cross_lead_lag_activates():
    r = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross()) for i in range(25)])
    assert r.score >= 0.6
    assert r.evidence["mode"] == "led" and r.evidence["top_leader"] == {"symbol": "B", "lag_s": 30.0, "corr": 0.75}
    assert r.evidence["strength"] == pytest.approx(0.9) and r.evidence["breadth"] == pytest.approx(0.85)
    assert r.evidence["persistence"] == 1.0 and r.score == pytest.approx(0.765)
    assert r.evidence["return_gap"] == pytest.approx(0.01) and r.evidence["direction_basis"] == "market_return_gap"
    _check_common(r, 1)
    down = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(market=-0.01)) for i in range(25)])
    assert down.evidence["direction"] == -1
    peer = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(market=0.01), sector={"peer_return_60s": -0.02}) for i in range(25)])
    assert peer.evidence["direction"] == -1 and peer.evidence["direction_basis"] == "peer_return_gap"
    leading = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(leaders=(), laggers=(("D", 15.0, 0.8),))) for i in range(25)])
    assert leading.evidence["mode"] == "leading" and leading.score == pytest.approx(1.0 * 0.7) and leading.evidence["direction"] == 0


def test_machinery_cross_lead_lag_null_missing_and_evidence():
    none = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(leaders=(), laggers=())) for i in range(25)])
    assert none.score == 0.0 and none.evidence["mode"] == "none" and "missing" not in none.evidence
    miss = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(leaders=None, laggers=None, evaluated=0)) for i in range(5)])
    assert miss.score == 0.0 and miss.evidence["missing"] and miss.evidence["pairs_evaluated"] == 0
    weak = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(leaders=(("B", 30.0, 0.5),))) for i in range(25)])
    assert weak.evidence["strength"] == pytest.approx(0.4) and weak.evidence["breadth"] == pytest.approx(0.7)
    assert weak.score == pytest.approx(0.28) and weak.score < 0.35
    single = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(leaders=(("B", 30.0, 0.75),))) for i in range(25)])
    assert single.score == pytest.approx(0.63)
    # a relation that only just appeared has no persistence yet
    st = [S(5 * i, cross=_ll_cross(leaders=())) for i in range(24)] + [S(120, cross=_ll_cross())]
    brief = last(xf.CrossLeadLag(), st)
    assert brief.evidence["persistence"] == pytest.approx(1 / 25) and brief.score == pytest.approx(0.765 * 0.5)
    # below half a tick of log price the gap implies no direction
    small_gap = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(market=0.001)) for i in range(25)])
    assert small_gap.evidence["direction"] == 0 and small_gap.evidence["gap_threshold"] == pytest.approx(0.05 / 10.05)
    no_ret = last(xf.CrossLeadLag(), [S(5 * i, cross=_ll_cross(market=None)) for i in range(25)])
    assert no_ret.evidence["direction"] == 0 and no_ret.evidence["direction_basis"] is None and no_ret.score >= 0.6


# ============================================================================= #44 circuit_regime
def test_machinery_circuit_regime_locked_hit_near_free():
    locked = last(cf.CircuitRegime(), states_with(C(price=11.0, locked_up=True, hit_up=True, time_locked_s=300.0), bids=[(11.0, 5000.0)], asks=[]))
    assert locked.score == pytest.approx(0.9) and locked.evidence["regime"] == "locked_up"
    assert locked.evidence["lock_time_factor"] == pytest.approx(0.5)
    _check_common(locked, 1)
    hit = last(cf.CircuitRegime(), states_with(C(price=10.95, hit_up=True, queue_at_upper=3000.0, shares_to_door=1000.0)))
    assert hit.evidence["regime"] == "hit_up" and hit.evidence["lock_pressure"] == pytest.approx(0.75)
    assert hit.score == pytest.approx(0.9)
    near = last(cf.CircuitRegime(), states_with(C(price=10.95, approach_velocity=3.0)))
    assert near.evidence["regime"] == "near_up" and near.evidence["proximity"] == pytest.approx(1 - (5 / 10.95) / 3)
    assert near.evidence["approach"] == 1.0 and near.score == pytest.approx(near.evidence["proximity"]) and near.score >= 0.6
    idle = last(cf.CircuitRegime(), states_with(C(price=10.95)))
    assert idle.evidence["unverified"] == ["approach_velocity"] and idle.score == pytest.approx(near.score / 2)
    assert 0.35 <= idle.score < 0.6
    free = last(cf.CircuitRegime(), states_with(C(price=10.0)))
    assert free.evidence["regime"] == "free" and free.score == 0.0 and free.evidence["direction"] == 0
    down = last(cf.CircuitRegime(), states_with(C(price=9.05, approach_velocity=2.0)))
    assert down.evidence["regime"] == "near_down" and down.evidence["direction"] == -1
    assert down.evidence["approach"] == pytest.approx(2 / 3)


def test_machinery_circuit_regime_phase_gate_and_missing():
    closed = last(cf.CircuitRegime(), states_with(C(price=11.0, locked_up=True, hit_up=True, time_locked_s=300.0), phase="CLOSED"))
    assert closed.score == 0.0 and closed.evidence["score_ungated"] == pytest.approx(0.9) and closed.evidence["regime"] == "locked_up"
    assert closed.evidence["direction"] == 0 and closed.evidence["phase_factor"] == 0.0
    pre = last(cf.CircuitRegime(), states_with(C(price=11.0, locked_up=True, hit_up=True, time_locked_s=300.0), phase="PRE_OPEN"))
    assert pre.score == pytest.approx(0.45)
    no_limits = last(cf.CircuitRegime(), states_with({"upper_limit": None, "lower_limit": None, "rule_source": None}))
    assert no_limits.score == 0.0 and "upper_limit" in no_limits.evidence["missing"][0]
    no_price = last(cf.CircuitRegime(), states_with(C(price=None), bids=[], asks=[]))
    assert no_price.score == 0.0 and "dist_up_pct" in no_price.evidence["missing"][0] and no_price.evidence["regime"] == "unknown"


# ============================================================================= #45 circuit_streak
def _streak(length: int = 3, prior: int = 2, locked: bool = True, cont: Optional[float] = 1.0, weak: Optional[bool] = False,
            side: str = "up") -> Dict[str, Any]:
    kw = {"streak_continuation_strength": cont, "streak_weakening": weak}
    if side == "up":
        kw.update(consecutive_upper_streak=length, prior_upper_streak=prior, locked_up=locked, hit_up=locked)
        return C(price=11.0 if locked else 10.8, **kw)
    kw.update(consecutive_lower_streak=length, prior_lower_streak=prior, locked_down=locked, hit_down=locked)
    return C(price=9.0 if locked else 9.2, **kw)


def test_machinery_circuit_streak_activates():
    r = last(cf.CircuitStreak(), states_with(_streak()))
    assert r.score == pytest.approx(1.0) and r.evidence["regime"] == "streak_up" and r.evidence["length"] == 3
    assert r.evidence["length_factor"] == 1.0 and r.evidence["continuation_factor"] == 1.0 and r.evidence["weakening_factor"] == 1.0
    _check_common(r, 1)
    low = last(cf.CircuitStreak(), states_with(_streak(side="down")))
    assert low.evidence["regime"] == "streak_down" and low.evidence["direction"] == -1 and low.score == pytest.approx(1.0)


def test_machinery_circuit_streak_null_weakening_and_evidence():
    none = last(cf.CircuitStreak(), states_with(_streak(length=0, prior=0, locked=False, cont=None, weak=None)))
    assert none.score == 0.0 and none.evidence["regime"] == "none" and "missing" not in none.evidence
    single = last(cf.CircuitStreak(), states_with(_streak(length=1, prior=0)))
    assert single.evidence["length_factor"] == 0.0 and single.score == 0.0
    weak = last(cf.CircuitStreak(), states_with(_streak(weak=True)))
    assert weak.evidence["weakening_factor"] == 0.7 and weak.score == pytest.approx(0.7)
    two = last(cf.CircuitStreak(), states_with(_streak(length=2, prior=1, cont=0.5)))
    assert two.evidence["length_factor"] == 0.5 and two.evidence["continuation_factor"] == 0.75 and two.score == pytest.approx(0.375)
    off = last(cf.CircuitStreak(), states_with(_streak(length=2, prior=2, locked=False, cont=0.0, weak=True)))
    assert off.score == pytest.approx(0.5 * 0.5 * 0.7) and off.score < 0.35
    no_cont = last(cf.CircuitStreak(), states_with(_streak(cont=None, weak=None)))
    assert no_cont.evidence["continuation_basis"] == "locked_now" and no_cont.evidence["continuation"] == 1.0
    assert no_cont.evidence["unverified"] == ["streak_weakening"]
    unknown = last(cf.CircuitStreak(), states_with(C(consecutive_upper_streak=None, consecutive_lower_streak=None)))
    assert unknown.score == 0.0 and unknown.evidence["missing"]


# ============================================================================= #46 circuit_prehit_pressure
def _prehit_scenario(price: float = 10.95, vel_last: float = 3.0, vel_hist: Optional[Sequence[float]] = None,
                     pd: Optional[int] = 1, ps: Optional[float] = 0.7, door: Optional[float] = 2000.0,
                     visible: Optional[bool] = True, vol_step: Optional[float] = 1000.0, n: int = 40,
                     hit: bool = False, bids=BIDS, asks=ASKS) -> List[MarketState]:
    out = []
    for i in range(n):
        v = vel_last if i == n - 1 else (vel_hist[i % len(vel_hist)] if vel_hist else 0.5 + 0.1 * (i % 5))
        c = C(price=price, approach_velocity=v, shares_to_door=door, door_visible=visible)
        if hit and i == n - 1:
            c.update(hit_up=True, price=11.0, dist_up_pct=0.0, dist_up_ticks=0.0,
                     pre_hit_state={"t": None, "side": "up", "approach_velocity": 3.0, "pressure_direction": 1,
                                    "pressure_strength": 0.7, "shares_to_door": 500.0, "imb_topk": 0.6})
        out.append(S(5 * i, bids, asks, circuit=c, tv=(1000.0 + vol_step * i) if vol_step is not None else None, pd=pd, ps=ps))
    return out


def test_machinery_circuit_prehit_pressure_activates():
    r = last(cf.CircuitPrehitPressure(), _prehit_scenario())
    assert r.score == pytest.approx(1.0) and r.evidence["regime"] == "pre_hit" and r.evidence["gate"] == 1.0
    assert r.evidence["velocity_z"] > 5 and r.evidence["velocity_z_factor"] == 1.0 and r.evidence["velocity_abs_factor"] == 1.0
    assert r.evidence["pressure_toward"] == pytest.approx(0.7) and r.evidence["pressure_basis"] == "pressure_layer"
    assert r.evidence["volume_rate_per_min"] == pytest.approx(12000.0)
    assert r.evidence["minutes_to_door"] == pytest.approx(2000 / 12000) and r.evidence["door_factor"] == 1.0
    _check_common(r, 1)
    down = _prehit_scenario(price=9.05, pd=-1)
    for s in down:
        s.circuit["shares_to_floor"], s.circuit["floor_visible"] = s.circuit.pop("shares_to_door"), s.circuit.pop("door_visible")
    rd = last(cf.CircuitPrehitPressure(), down)
    assert rd.score == pytest.approx(1.0) and rd.evidence["side"] == "down" and rd.evidence["direction"] == -1


def test_machinery_circuit_prehit_pressure_null_post_hit_and_missing():
    null = last(cf.CircuitPrehitPressure(), _prehit_scenario(vel_last=0.2, vel_hist=[0.2], pd=-1, door=100000.0))
    assert null.evidence["velocity_z"] is None and null.evidence["velocity_factor"] == 0.0     # constant baseline: z unknown
    assert null.evidence["pressure_toward"] == pytest.approx(-0.7) and null.evidence["pressure_factor"] == 0.0
    assert null.evidence["door_factor"] == pytest.approx(1 - (100000 / 12000 - 2) / 28)
    assert null.score == pytest.approx(0.25 * null.evidence["door_factor"]) and null.score < 0.35
    far = last(cf.CircuitPrehitPressure(), _prehit_scenario(price=10.4))
    assert far.evidence["gate"] == 0.0 and far.score == 0.0 and far.evidence["direction"] == 0
    post = last(cf.CircuitPrehitPressure(), _prehit_scenario(hit=True))
    assert post.score == 0.0 and post.evidence["regime"] == "post_hit"
    assert post.evidence["pre_hit_factors"]["velocity_factor"] == 1.0 and post.evidence["pre_hit_factors"]["blend"] == pytest.approx(1.0)
    no_vel_no_p = last(cf.CircuitPrehitPressure(), _prehit_scenario(vel_last=None, vel_hist=[None], pd=None, ps=None, bids=[], asks=[]))
    assert no_vel_no_p.score == 0.0 and set(no_vel_no_p.evidence["missing"]) == {"circuit.approach_velocity", "pressure"}


def test_machinery_circuit_prehit_pressure_evidence_tracks_inputs():
    base_r = last(cf.CircuitPrehitPressure(), _prehit_scenario())
    hidden = last(cf.CircuitPrehitPressure(), _prehit_scenario(visible=False))
    assert hidden.evidence["door_factor"] == pytest.approx(0.85) and hidden.score == pytest.approx(0.4 + 0.35 + 0.25 * 0.85)
    no_tape = last(cf.CircuitPrehitPressure(), _prehit_scenario(vol_step=None))
    assert no_tape.evidence["unverified"] == ["door"] and no_tape.evidence["door_factor"] is None and no_tape.score == pytest.approx(1.0)
    slow_door = last(cf.CircuitPrehitPressure(), _prehit_scenario(door=200000.0))
    assert slow_door.evidence["minutes_to_door"] == pytest.approx(200000 / 12000)
    assert slow_door.evidence["door_factor"] == pytest.approx(1 - (200000 / 12000 - 2) / 28) and slow_door.score < base_r.score
    weak_p = last(cf.CircuitPrehitPressure(), _prehit_scenario(ps=0.3))
    assert weak_p.evidence["pressure_factor"] == pytest.approx(0.2) and weak_p.score == pytest.approx(0.4 + 0.07 + 0.25)
    book_p = last(cf.CircuitPrehitPressure(), _prehit_scenario(pd=None, ps=None, bids=[(p, q * 4) for p, q in BIDS]))
    assert book_p.evidence["pressure_basis"] == "combined_pressure" and book_p.evidence["pressure_toward"] > 0.5
    mid_vel = last(cf.CircuitPrehitPressure(), _prehit_scenario(vel_last=1.0))
    assert mid_vel.evidence["velocity_abs_factor"] == pytest.approx(0.2) and mid_vel.evidence["velocity_z_factor"] > 0.2
    assert mid_vel.evidence["velocity_factor"] == mid_vel.evidence["velocity_z_factor"]
    mid_gate = last(cf.CircuitPrehitPressure(), _prehit_scenario(price=10.75))
    assert mid_gate.evidence["gate"] == pytest.approx(1 - ((25 / 10.75) - 1) / 3) and mid_gate.score == pytest.approx(mid_gate.evidence["gate"])


# ============================================================================= #47 circuit_lock_strength
def _lock(q: float = 5000.0, delta: Optional[float] = 2500.0, persist: Optional[float] = 300.0, unlocks: int = 0,
          relocks: int = 0, locked: bool = True, qmax: Optional[float] = None) -> Dict[str, Any]:
    return C(price=11.0 if locked else 10.95, locked_up=locked, hit_up=True, queue_at_upper=q, queue_side="up",
             queue_delta_60s=delta, queue_growth=(max(delta, 0.0) if delta is not None else None),
             queue_decay=(max(-delta, 0.0) if delta is not None else None), queue_persistence_s=persist,
             unlock_count=unlocks, relock_count=relocks, max_queue_at_limit=qmax, shares_to_door=0.0 if locked else 800.0)


def _lock_states(c: Dict[str, Any], vol_step: Optional[float] = 10.0, n: int = 12) -> List[MarketState]:
    bids, asks = ([(11.0, c["queue_at_upper"] or 0.0)], []) if c.get("locked_up") else (BIDS, ASKS)
    return [S(5 * i, bids, asks, circuit=c, tv=(1000.0 + vol_step * i) if vol_step is not None else None) for i in range(n)]


def test_machinery_circuit_lock_strength_activates():
    r = last(cf.CircuitLockStrength(), _lock_states(_lock()))
    assert r.score == pytest.approx(1.0) and r.evidence["regime"] == "locked_up"
    assert r.evidence["queue_rel_change"] == pytest.approx(1.0) and r.evidence["growth"] == 1.0
    assert r.evidence["persistence"] == 1.0 and r.evidence["queue_minutes_of_volume"] == pytest.approx(5000 / 120)
    assert r.evidence["size"] == 1.0 and r.evidence["integrity"] == 1.0 and r.evidence["lock_base"] == 1.0
    _check_common(r, 1)
    relocked = last(cf.CircuitLockStrength(), _lock_states(_lock(unlocks=2, relocks=2)))
    assert relocked.evidence["open_unlocks"] == 0 and relocked.evidence["integrity"] == pytest.approx(1 - 0.2 / 3)
    assert relocked.score == pytest.approx(1 - 0.2 / 3)


def test_machinery_circuit_lock_strength_null_hit_missing_and_evidence():
    off = last(cf.CircuitLockStrength(), states_with(C(price=10.5, unlock_count=1)))
    assert off.score == 0.0 and off.evidence["regime"] == "off_limit" and "missing" not in off.evidence
    decaying = last(cf.CircuitLockStrength(), _lock_states(_lock(q=2500.0, delta=-2500.0, persist=30.0)))
    assert decaying.evidence["queue_rel_change"] == pytest.approx(-0.5) and decaying.evidence["growth"] == pytest.approx(0.25)
    assert decaying.evidence["persistence"] == pytest.approx(0.1)
    assert decaying.evidence["queue_minutes_of_volume"] == pytest.approx(2500 / 120)
    assert decaying.evidence["size"] == pytest.approx((2500 / 120 - 1) / 29)
    assert decaying.score == pytest.approx(0.4 * 0.25 + 0.3 * 0.1 + 0.3 * decaying.evidence["size"]) and decaying.score < 0.6
    hit_only = last(cf.CircuitLockStrength(), _lock_states(_lock(locked=False, unlocks=1)))
    assert hit_only.evidence["regime"] == "hit_up" and hit_only.evidence["lock_base"] == 0.6
    assert hit_only.evidence["open_unlocks"] == 1 and hit_only.evidence["integrity"] == pytest.approx(0.5)
    assert hit_only.score == pytest.approx(0.6 * 1.0 * 0.5)
    no_tape = last(cf.CircuitLockStrength(), _lock_states(_lock(qmax=10000.0), vol_step=None))
    assert no_tape.evidence["size_basis"] == "share_of_day_max" and no_tape.evidence["size"] == pytest.approx(0.5)
    assert no_tape.score == pytest.approx(0.4 + 0.3 + 0.15)
    blind = last(cf.CircuitLockStrength(), _lock_states(_lock(qmax=None), vol_step=None))
    assert blind.evidence["unverified"] == ["size"] and blind.score == pytest.approx(1.0)
    no_delta = last(cf.CircuitLockStrength(), _lock_states(_lock(delta=None, persist=None)))
    assert set(no_delta.evidence["unverified"]) == {"growth", "persistence"} and no_delta.score == pytest.approx(1.0)
    no_queue = last(cf.CircuitLockStrength(), _lock_states(_lock(q=None)))
    assert no_queue.score == 0.0 and "queue_at_upper" in no_queue.evidence["missing"][0]
    unknown = last(cf.CircuitLockStrength(), states_with(C(price=None), bids=[], asks=[]))
    assert unknown.score == 0.0 and unknown.evidence["missing"]


# ============================================================================= #48 circuit_break_weakness
def _break(prior: int = 3, bd: Optional[bool] = True, gap: Optional[float] = -2.0, open_: Optional[float] = 10.8,
           price: Optional[float] = 10.5, q: Optional[float] = 1000.0, qmax: Optional[float] = 10000.0,
           delta: Optional[float] = -1000.0, unlocks: int = 2, beh_bools=(True, True)) -> Dict[str, Any]:
    beh = {"gap_open_ticks": gap, "queue_decay": beh_bools[0], "reversal": beh_bools[1]}
    return C(price=price, prior_upper_streak=prior, consecutive_upper_streak=prior, break_day=bd, break_behaviour=beh,
             open_price=open_, queue_at_upper=q, queue_side="up" if q is not None else None, queue_delta_60s=delta,
             max_queue_at_limit=qmax, unlock_count=unlocks)


def test_machinery_circuit_break_weakness_activates():
    r = last(cf.CircuitBreakWeakness(), states_with(_break(), vel=-3.0))
    assert r.score == pytest.approx(1.0) and r.evidence["regime"] == "break_day" and r.evidence["prior_streak"] == 3
    assert r.evidence["follow_ticks"] == pytest.approx(-3.0) and r.evidence["ticks_inside_prior_limit"] == pytest.approx(5.0)
    assert r.evidence["reversal"] == 1.0 and r.evidence["reversal_basis"] == "ticks_inside_prior_limit"
    assert r.evidence["queue_decay"] == 1.0 and r.evidence["queue_decay_basis"] == "queue_series"
    assert r.evidence["velocity_away"] == 1.0 and r.evidence["unlocks"] == 1.0
    _check_common(r, -1)


def test_machinery_circuit_break_weakness_null_missing_and_evidence():
    holding = last(cf.CircuitBreakWeakness(), states_with(_break(bd=False, price=11.0)))
    assert holding.score == 0.0 and holding.evidence["regime"] == "streak_holding"
    none = last(cf.CircuitBreakWeakness(), states_with(_break(prior=0, bd=None)))
    assert none.score == 0.0 and none.evidence["regime"] == "no_prior_streak" and "missing" not in none.evidence
    undecided = last(cf.CircuitBreakWeakness(), states_with(_break(bd=None, price=None), bids=[], asks=[]))
    assert undecided.score == 0.0 and "break_day" in undecided.evidence["missing"][0]
    mild = last(cf.CircuitBreakWeakness(), states_with(_break(gap=-1.0, price=10.7, q=8000.0, delta=-500.0, unlocks=0), vel=0.0))
    assert mild.evidence["ticks_inside_prior_limit"] == pytest.approx(2.0) and mild.evidence["reversal"] == pytest.approx(0.4)
    assert mild.evidence["queue_decay"] == pytest.approx(max(0.2, 500 / 8500 / 0.5))
    assert mild.score == pytest.approx(0.35 * mild.evidence["queue_decay"] + 0.35 * 0.4) and mild.score < 0.35
    # engine booleans when the queue and price series are not available
    bools = last(cf.CircuitBreakWeakness(), states_with(_break(gap=None, q=None, qmax=None, delta=None, beh_bools=(False, True)), vel=None))
    assert bools.evidence["reversal_basis"] == "engine_bool" and bools.evidence["queue_decay_basis"] == "engine_bool"
    # a displayed static book still yields a (zero) velocity from the window: nothing dropped
    assert "unverified" not in bools.evidence and bools.evidence["velocity_away"] == 0.0
    assert bools.score == pytest.approx(0.35 + 0.1)
    no_book = last(cf.CircuitBreakWeakness(), states_with(_break(gap=None, q=None, qmax=None, delta=None, beh_bools=(False, True)),
                                                          vel=None, bids=[], asks=[]))
    assert no_book.evidence["unverified"] == ["velocity_away"] and no_book.score == pytest.approx((0.35 + 0.1) / 0.8)
    blind = last(cf.CircuitBreakWeakness(), states_with(_break(gap=None, q=None, qmax=None, delta=None, beh_bools=(None, None)), vel=None))
    assert blind.score == 0.0 and blind.evidence["missing"]
    lower = last(cf.CircuitBreakWeakness(), states_with(C(price=9.5, prior_lower_streak=2, break_day=True, open_price=9.2,
                                                          break_behaviour={"gap_open_ticks": 2.0, "queue_decay": None, "reversal": True}), vel=3.0))
    assert lower.evidence["ticks_inside_prior_limit"] == pytest.approx(5.0) and lower.evidence["direction"] == 1


# ============================================================================= #49 circuit_next_session
def _next(ns: Optional[str] = "continuation", gap: Optional[float] = 1.0, price: float = 10.9, open_: float = 10.8,
          locked: bool = True, lshare: Optional[float] = None, elapsed: float = 600.0, side: str = "up") -> Dict[str, Any]:
    kw = {"next_session": ns, "break_behaviour": {"gap_open_ticks": gap}, "open_price": open_, "locked_share_today": lshare,
          "session_elapsed_s": elapsed}
    if side == "up":
        kw.update(prior_upper_streak=2, locked_up=locked, hit_up=locked)
    else:
        kw.update(prior_lower_streak=2, locked_down=locked, hit_down=locked)
    return C(price=price, **kw)


def test_machinery_circuit_next_session_activates():
    r = last(cf.CircuitNextSession(), states_with(_next()))
    assert r.evidence["regime"] == "continuation" and r.evidence["gap_ticks"] == 1.0 and r.evidence["follow_ticks"] == pytest.approx(1.0)
    assert r.evidence["gap_factor"] == pytest.approx(0.6) and r.evidence["lock_factor"] == 1.0
    assert r.evidence["follow_factor"] == pytest.approx(1 / 3) and r.evidence["time_factor"] == 1.0
    assert r.score == pytest.approx(0.5 * 0.6 + 0.3 + 0.2 / 3) and r.score >= 0.6
    _check_common(r, 1)
    rev = last(cf.CircuitNextSession(), states_with(_next(ns="reversal", gap=-4.0, price=10.5, open_=10.8, locked=False)))
    assert rev.evidence["regime"] == "reversal" and rev.evidence["gap_factor"] == pytest.approx(3.5 / 4.5)
    assert rev.evidence["follow_factor"] == 1.0 and rev.score == pytest.approx(0.6 * 3.5 / 4.5 + 0.4)
    _check_common(rev, -1)
    low = last(cf.CircuitNextSession(), states_with(_next(gap=-1.0, price=8.9, open_=9.0, side="down")))
    assert low.evidence["gap_ticks"] == 1.0 and low.evidence["follow_ticks"] == pytest.approx(1.0) and low.evidence["direction"] == -1


def test_machinery_circuit_next_session_null_fade_and_missing():
    none = last(cf.CircuitNextSession(), states_with(C(price=10.5)))
    assert none.score == 0.0 and none.evidence["regime"] == "no_prior_lock" and "missing" not in none.evidence
    no_open = last(cf.CircuitNextSession(), states_with(_next(ns=None)))
    assert no_open.score == 0.0 and "next_session" in no_open.evidence["missing"][0]
    no_gap = last(cf.CircuitNextSession(), states_with(_next(gap=None)))
    assert no_gap.score == 0.0 and "gap_open_ticks" in no_gap.evidence["missing"][0]
    faded = last(cf.CircuitNextSession(), states_with(_next(elapsed=5400.0)))
    assert faded.evidence["time_factor"] == 0.5 and faded.score == pytest.approx(0.5 * (0.5 * 0.6 + 0.3 + 0.2 / 3))
    weak_cont = last(cf.CircuitNextSession(), states_with(_next(gap=-0.5, price=10.7, open_=10.7, locked=False, lshare=0.1)))
    assert weak_cont.evidence["gap_factor"] == 0.0 and weak_cont.evidence["lock_factor"] == pytest.approx(0.2)
    assert weak_cont.evidence["follow_factor"] == 0.0 and weak_cont.score == pytest.approx(0.06) and weak_cont.score < 0.35
    mild_rev = last(cf.CircuitNextSession(), states_with(_next(ns="reversal", gap=-1.0, price=10.7, open_=10.7, locked=False)))
    assert mild_rev.evidence["gap_factor"] == pytest.approx(0.5 / 4.5) and mild_rev.score < 0.35


# ============================================================================= lifecycle
def _prehit_lifecycle(outcome: str) -> List[MarketState]:
    """far (inactive) → 1.85 % away with modest pressure (building) → 0.46 % away with strong
    approach (active → confirmed) → the limit is hit (resolved) or the price retreats (failed)."""
    out: List[MarketState] = []
    for i in range(40):                                               # 0..195 s: far away, baseline velocities
        out.append(S(5 * i, shift(BIDS, 4), shift(ASKS, 4), circuit=C(price=10.45, approach_velocity=0.5 + 0.1 * (i % 5),
                                                                       shares_to_door=30000.0, door_visible=True),
                     tv=1000.0 + 1000.0 * i, pd=1, ps=0.3))
    for j in range(20):                                               # 200..295 s: building
        out.append(S(200 + 5 * j, shift(BIDS, 7), shift(ASKS, 7), circuit=C(price=10.75, approach_velocity=1.0,
                                                                             shares_to_door=30000.0, door_visible=True),
                     tv=41000.0 + 1000.0 * j, pd=1, ps=0.3))
    for k in range(20):                                               # 300..395 s: active → confirmed
        out.append(S(300 + 5 * k, [(10.9, 3000.0)], [(11.0, 500.0)], circuit=C(price=10.95, approach_velocity=3.0,
                                                                                shares_to_door=500.0, door_visible=True),
                     tv=61000.0 + 1000.0 * k, pd=1, ps=0.7))
    for m in range(10):                                               # 400..445 s: release
        if outcome == "resolved":
            c = C(price=11.0, hit_up=True, locked_up=True, shares_to_door=0.0, door_visible=True, time_locked_s=5.0 * m)
            out.append(S(400 + 5 * m, [(11.0, 8000.0)], [], circuit=c, tv=81000.0 + 500.0 * m, pd=1, ps=0.9))
            out[-1].mid = 11.0
        else:
            c = C(price=10.45, approach_velocity=-3.0, shares_to_door=30000.0, door_visible=True)
            out.append(S(400 + 5 * m, shift(BIDS, 4), shift(ASKS, 4), circuit=c, tv=81000.0 + 500.0 * m, pd=-1, ps=0.5))
    return out


@pytest.mark.parametrize("outcome", ["resolved", "failed"])
def test_machinery_lifecycle_prehit_building_active_confirmed_release(outcome):
    mech = cf.CircuitPrehitPressure()
    rs = run(mech, _prehit_lifecycle(outcome), use_update=True)
    seq = [r.state for r in rs]
    assert seq[0] == "inactive" and seq[39] == "inactive"
    assert "building" in seq and "active" in seq and "confirmed" in seq
    i_b, i_a, i_c = seq.index("building"), seq.index("active"), seq.index("confirmed")
    assert i_b < i_a < i_c
    assert 0.35 <= rs[i_b].score < 0.6 and rs[i_a].score >= 0.6
    assert rs[i_c].start_time is not None and rs[i_c].duration_s >= mech.confirm_s
    assert outcome in seq and seq.index(outcome) > i_c
    i_t = seq.index(outcome)
    assert rs[i_t].score < mech.release_threshold and rs[i_t].evidence["episode_direction"] == 1
    assert rs[i_c].evidence["direction"] == 1 and rs[i_c].evidence["peak_score"] >= 0.6
    assert (rs[i_t].evidence["mid_change_since_start"] > 0) == (outcome == "resolved")
    if outcome == "resolved":
        assert rs[i_t].evidence["regime"] == "post_hit"


def test_machinery_lifecycle_lock_strength_resolves_without_mid():
    """A lock is one-sided (no mid): the episode resolves on release (no direction verdict possible)."""
    mech = cf.CircuitLockStrength()
    st = _lock_states(_lock(), n=30) + _lock_states(_lock(q=500.0, delta=-4500.0, persist=0.0, unlocks=1, locked=False), n=5)
    for i, s in enumerate(st[30:]):
        s.t = _t(150 + 5 * i)
    rs = run(mech, st, use_update=True)
    seq = [r.state for r in rs]
    assert seq[0] == "active" and "confirmed" in seq
    assert "resolved" in seq and "failed" not in seq and seq.index("resolved") == 30 and seq[-1] == "inactive"


# ============================================================================= real data
def _run_capture(root: str, **norm_kw):
    from tower.engine import Engine, EngineConfig
    from tower.normalize import normalize_store
    events, _ = normalize_store(root, **norm_kw)
    eng = Engine(EngineConfig(strict=False))
    states = [ms for ms in (eng.process(ev) for ev in events) if ms is not None]
    return events, eng, states


def test_realdata_fixture_capture_through_engine():
    """The closed-market fixture: all 8 mechanisms compute on every state; the closed market
    never builds any of them (session gate / no cross context); readings name their missing
    inputs; no engine error mentions one of these mechanisms."""
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
            assert "direction" in m.evidence and isinstance(m.evidence.get("inputs"), dict)
            if m.evidence.get("missing"):
                with_missing += 1
    assert seen == set(ALL_NAMES)
    assert with_missing > 0
    assert not [e for e in eng.metrics["errors"] if any(n in e for n in ALL_NAMES)], eng.metrics["errors"]


def test_realdata_fixture_circuit_readings_reflect_limits_and_closed_gate():
    """States carrying limits (from the circuit table on the closing price) get a computed regime
    (proximity from the real distance, session gate 0 in CLOSED, ungated core reported); the
    cross lead/lag context is missing on every closed state (no returns to correlate)."""
    _, _, states = _run_capture(FIXTURE)
    assert any((ms.circuit or {}).get("upper_limit") is not None for ms in states)
    # the engine throttles the mechanics (readings are carried between recomputes), so readings are
    # classified by what they were computed from, not by the state that carries them
    regimes = [ms.mechanisms["circuit_regime"] for ms in states if "circuit_regime" in ms.mechanisms]
    assert regimes
    computed = [m for m in regimes if "proximity" in m.evidence]
    no_lim = [m for m in regimes if m.evidence.get("missing")]
    assert computed and no_lim and len(computed) + len(no_lim) == len(regimes)
    assert all(m.evidence["phase"] == "CLOSED" and m.evidence["phase_factor"] == 0.0 for m in computed)
    assert all("upper_limit" in m.evidence["missing"][0] for m in no_lim)
    near = [m for m in computed if m.evidence.get("regime", "").startswith("near_")]
    assert near, "no closed state near a limit in the fixture"
    for m in near:
        d = m.evidence["inputs"]["dist_pct"]
        assert m.evidence["proximity"] == pytest.approx(1 - min(d, 3.0) / 3.0)
        assert m.evidence["score_ungated"] > 0 and m.score == 0.0
        assert m.evidence["inputs"]["rule_source"] is not None
    streaks = [ms.mechanisms["circuit_streak"].evidence.get("regime") for ms in states if "circuit_streak" in ms.mechanisms]
    assert "none" in streaks and set(streaks) <= {None, "none", "unknown", "streak_up", "streak_down"}
    ll = [ms.mechanisms["cross_lead_lag"] for ms in states if "cross_lead_lag" in ms.mechanisms]
    assert ll and all(m.evidence.get("missing") for m in ll)


def test_realdata_live_capture_when_present():
    """The live session capture (read-only, optional): every reading computes without error and,
    when displayed books exist, the circuit regime is measured from real distances."""
    if not os.path.isdir(os.path.join(LIVE, "segments")):
        pytest.skip("live capture not present")
    from tower.events import EventType
    events, eng, states = _run_capture(LIVE)
    assert not [e for e in eng.metrics["errors"] if any(n in e for n in ALL_NAMES)], eng.metrics["errors"]
    books = [ev for ev in events if ev.event_type == EventType.BOOK_SNAPSHOT and (ev.payload.get("bids") or ev.payload.get("asks"))]
    if not books:
        pytest.skip("live capture carries no displayed books yet")
    measured = [ms.mechanisms["circuit_regime"] for ms in states
                if "circuit_regime" in ms.mechanisms and ms.mechanisms["circuit_regime"].evidence.get("proximity") is not None]
    assert measured, "books present but no circuit regime measured"
    for m in measured:
        assert 0.0 <= m.score <= 1.0 and m.evidence["regime"] != "unknown"
