"""tower.resilience — shock detection and recovery curves.

test_machinery_* feed hand-built book states with known depth / spread / mid paths and check the
rules of the module docstring number by number; test_realdata_* replay the committed closed-market
fixture (one-sided, static pre-open books) through the book engine into the resilience engine.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from seeing.replay import replay
from tower.book import EvolvingBook
from tower.resilience import ResilienceEngine, TIMEOUT_S, VACUUM_S
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
TICK = 0.1

BIDS = [(10.0, 1000.0), (9.9, 800.0), (9.8, 600.0), (9.7, 500.0), (9.6, 400.0)]
ASKS = [(10.1, 1000.0), (10.2, 800.0), (10.3, 600.0), (10.4, 500.0), (10.5, 400.0)]


def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def _ms(s: float, bids, asks, symbol: str = "SYN", seq: int = 0) -> MarketState:
    """A MarketState carrying exactly what the book engine writes for a displayed book."""
    ms = MarketState(symbol=symbol, t=_t(s), seq=seq, tick_size=TICK)
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
    return ms


def _scale(levels, factor: float, only_first: bool = False):
    if only_first:
        return [(levels[0][0], levels[0][1] * factor)] + list(levels[1:])
    return [(p, q * factor) for p, q in levels]


def _run(eng: ResilienceEngine, steps):
    """steps: iterable of (seconds, bids, asks) → list of filled MarketStates (in order)."""
    out = []
    for i, (s, b, a) in enumerate(steps):
        ms = _ms(s, b, a, seq=i)
        eng.on_state(ms, None)
        eng.fill_state(ms)
        out.append(ms)
    return out


def _calm(until_s: float, step_s: float = 5.0, bids=BIDS, asks=ASKS):
    s = 0.0
    while s <= until_s:
        yield (s, bids, asks)
        s += step_s


# ============================================================================ machinery
def test_machinery_calm_book_never_shocks():
    eng = ResilienceEngine()
    states = _run(eng, _calm(900))
    assert all(ms.resilience_state == "none" for ms in states)
    assert all(ms.recovery_curve is None and ms.recovery_speed is None for ms in states)
    assert eng.curves("SYN") == []
    assert states[-1].session_state["resilience"] == {"state": "none", "observed": True, "curves_completed": 0}


def test_machinery_unobservable_book_is_none_not_zero():
    eng = ResilienceEngine()
    states = _run(eng, [(s, [], []) for s in range(0, 100, 5)])
    assert all(ms.resilience_state is None for ms in states)
    assert all(ms.liquidity_response is None for ms in states)
    assert states[-1].session_state["resilience"]["observed"] is False
    # a state filled without any prior on_state is explicitly unobservable
    ms = _ms(0, BIDS, ASKS, symbol="OTHER")
    eng.fill_state(ms)
    assert ms.resilience_state is None and ms.session_state["resilience"]["observed"] is False


def test_machinery_shock_then_full_recovery_sequence_and_time_to_recovery():
    eng = ResilienceEngine()
    steps = list(_calm(300))
    # touch bid depth falls to 30 % of its 300-s median (1000) inside one 5-s burst
    steps += [(305, _scale(BIDS, 0.30, only_first=True), ASKS),
              (310, _scale(BIDS, 0.50, only_first=True), ASKS),
              (315, _scale(BIDS, 0.75, only_first=True), ASKS),
              (320, _scale(BIDS, 0.95, only_first=True), ASKS),
              (325, BIDS, ASKS)]
    states = _run(eng, steps)
    seq = [ms.resilience_state for ms in states[-6:]]
    assert seq == ["none", "shocked", "recovering", "recovering", "recovered", "none"]
    curves = eng.curves("SYN")
    assert len(curves) == 1
    c = curves[0]
    assert c["side"] == "bid" and c["measure"] == "qty1" and c["state"] == "recovered"
    assert c["triggers"][0]["kind"] == "depth" and abs(c["triggers"][0]["drop_share"] - 0.70) < 1e-9
    assert c["baseline"]["bid"] == 1000.0 and c["baseline"]["spread_ticks"] == 1.0
    assert abs(c["share_at_shock"] - 0.30) < 1e-9
    assert c["time_to_recovery_s"] == 15.0 and c["updates_to_recovery"] == 3
    assert c["curve"] == [(0.0, 0.3), (5.0, 0.5), (10.0, 0.75), (15.0, 0.95)]
    assert not c["partial"] and not c["overshoot"] and not c["snapback"] and not c["vacuum"]
    # the shock did not move the mid: direction falls back to the side (bid → −1), no mid share
    assert c["direction"] == -1 and c["shock"]["move_ticks"] == 0.0 and c["samples"][-1]["mid_share"] is None
    # recovery speed = (0.95 − 0.30) / 15 s on the recovered update; ask side did not move → asymmetry = bid speed
    rec_ms = states[-2]
    assert abs(rec_ms.recovery_speed - (0.95 - 0.30) / 15.0) < 1e-9
    assert abs(rec_ms.recovery_asymmetry - rec_ms.recovery_speed) < 1e-9
    assert rec_ms.recovery_curve == c["curve"]
    assert abs(rec_ms.liquidity_response - 0.65) < 1e-9
    # after the curve closed: state none, curve still visible as the last one, speed cleared
    last = states[-1]
    assert last.resilience_state == "none" and last.recovery_curve == c["curve"] and last.recovery_speed is None
    assert abs(last.liquidity_response - 0.70) < 1e-9                     # depth is back: 1.0 − 0.3
    assert last.session_state["resilience"]["active"] is False
    assert last.session_state["resilience"]["curves_completed"] == 1
    # the session_state record is a snapshot, not the live list
    assert states[-5].session_state["resilience"]["curve"] == [(0.0, 0.3)]


def test_machinery_partial_recovery_closes_at_timeout():
    eng = ResilienceEngine()
    steps = list(_calm(300))
    s = 305.0
    while s <= 305 + TIMEOUT_S + 5:
        f = 0.30 if s == 305 else 0.60
        steps.append((s, _scale(BIDS, f, only_first=True), ASKS))
        s += 5
    states = _run(eng, steps)
    st = [ms.resilience_state for ms in states]
    assert st[61] == "shocked" and st[62] == "recovering"                # share 0.3 → 0.6 is an improvement
    c = eng.curves("SYN")
    assert len(c) == 1 and c[0]["state"] == "partial" and c[0]["partial"] is True
    assert c[0]["time_to_recovery_s"] is None and c[0]["updates_to_recovery"] is None
    assert abs(c[0]["final_share"] - 0.60) < 1e-9 and c[0]["duration_s"] == TIMEOUT_S
    closing = [ms for ms in states if ms.resilience_state == "partial"]
    assert len(closing) == 1 and closing[0].t == _t(305 + TIMEOUT_S)
    assert states[-1].resilience_state == "none"


def test_machinery_vacuum_after_120s_without_recovery():
    eng = ResilienceEngine()
    steps = list(_calm(300))
    s = 305.0
    while s <= 305 + TIMEOUT_S:
        steps.append((s, _scale(BIDS, 0.20, only_first=True), ASKS))
        s += 5
    states = _run(eng, steps)
    by_t = {ms.t: ms.resilience_state for ms in states}
    assert by_t[_t(305)] == "shocked"
    assert by_t[_t(305 + VACUUM_S - 5)] == "shocked"
    assert by_t[_t(305 + VACUUM_S)] == "vacuum"
    assert by_t[_t(305 + TIMEOUT_S)] == "vacuum"
    c = eng.curves("SYN")
    assert len(c) == 1 and c[0]["state"] == "vacuum" and c[0]["vacuum"] is True and not c[0]["partial"]
    assert abs(c[0]["final_share"] - 0.20) < 1e-9
    assert states[-1].liquidity_response is not None and abs(states[-1].liquidity_response) < 1e-9


def test_machinery_overshoot_depth_above_130pct():
    eng = ResilienceEngine()
    steps = list(_calm(300)) + [(305, BIDS, _scale(ASKS, 0.30, only_first=True)),
                                (310, BIDS, _scale(ASKS, 1.50, only_first=True))]
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-2:]] == ["shocked", "overshoot"]
    c = eng.curves("SYN")[0]
    assert c["side"] == "ask" and c["overshoot"] is True and c["overshoot_kind"] == "depth"
    assert c["state"] == "overshoot" and c["time_to_recovery_s"] == 5.0
    assert abs(c["final_share"] - 1.5) < 1e-9


def test_machinery_sweep_snapback_and_mid_overshoot():
    eng = ResilienceEngine()
    steps = list(_calm(300))
    # sweep: the two best bid levels are taken out → best bid 9.8 (−2 ticks), mid 9.95 (−1 tick)
    swept = BIDS[2:]
    steps.append((305, swept, ASKS))
    steps.append((320, BIDS, ASKS))                                       # book rebuilt, mid back at 10.05
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-2:]] == ["shocked", "recovered"]
    c = eng.curves("SYN")[0]
    kinds = [t["kind"] for t in c["triggers"]]
    assert c["triggers"][0]["kind"] == "sweep" and "depth" in kinds       # sweep is primary, top-K depth also fell
    assert c["side"] == "bid" and c["measure"] == "topk" and c["direction"] == -1
    assert abs(c["shock"]["move_ticks"] + 1.0) < 1e-9
    assert abs(c["share_at_shock"] - 1500.0 / 3300.0) < 1e-9
    assert c["snapback"] is True and c["snapback_s"] == 15.0
    assert abs(c["samples"][-1]["mid_share"] - 1.0) < 1e-9 and c["overshoot"] is False
    # mid overshoot: the rebuilt book sits one tick ABOVE the pre-shock level (mid share 2.0)
    eng2 = ResilienceEngine()
    up = [(p + 0.1, q) for p, q in BIDS], [(p + 0.1, q) for p, q in ASKS]
    states2 = _run(eng2, list(_calm(300)) + [(305, swept, ASKS), (320, up[0], up[1])])
    c2 = eng2.curves("SYN")[0]
    assert states2[-1].resilience_state == "overshoot"
    assert c2["overshoot"] is True and c2["overshoot_kind"] == "mid" and abs(c2["samples"][-1]["mid_share"] - 2.0) < 1e-9
    assert c2["snapback"] is True


def test_machinery_spread_widening_shock_recovers_when_spread_is_back_within_one_tick():
    eng = ResilienceEngine()
    wide_asks = [(10.4, 1000.0), (10.5, 800.0), (10.6, 600.0), (10.7, 500.0), (10.8, 400.0)]   # +3 ticks
    steps = list(_calm(300)) + [(305, BIDS, wide_asks),
                                (310, BIDS, [(p - 0.1, q) for p, q in wide_asks]),          # 2 ticks over baseline
                                (315, BIDS, [(p - 0.2, q) for p, q in wide_asks])]          # 1 tick over → within 1
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-3:]] == ["shocked", "recovering", "recovered"]
    c = eng.curves("SYN")[0]
    assert c["triggers"][0]["kind"] == "sweep" and c["side"] == "ask"      # the ask retreated 3 ticks
    assert any(t["kind"] == "spread" and abs(t["ticks"] - 3.0) < 1e-9 for t in c["triggers"])
    # depth never changed (top-K share stays 1) — the curve is carried by the spread
    assert all(abs(x["share"] - 1.0) < 1e-9 for x in c["samples"])
    assert [round(x["spread_share"], 6) for x in c["samples"]] == [0.0, round(1 / 3, 6), round(2 / 3, 6)]
    assert c["time_to_recovery_s"] == 10.0


def test_machinery_asymmetry_sign_follows_the_faster_side():
    def scenario(bid_f, ask_f):
        eng = ResilienceEngine()
        both = (_scale(BIDS, 0.30, only_first=True), _scale(ASKS, 0.30, only_first=True))
        steps = list(_calm(300)) + [(305, both[0], both[1]),
                                    (310, _scale(BIDS, bid_f, only_first=True), _scale(ASKS, ask_f, only_first=True))]
        states = _run(eng, steps)
        return states[-1], eng
    ms, eng = scenario(1.0, 0.4)
    c = eng.active_curve("SYN")
    assert c is not None and c["side"] == "both" and ms.resilience_state == "recovering"
    assert ms.recovery_asymmetry is not None and ms.recovery_asymmetry > 0
    assert abs(ms.recovery_asymmetry - ((1.0 - 0.3) - (0.4 - 0.3)) / 5.0) < 1e-9
    assert abs(ms.recovery_speed - (0.4 - 0.3) / 5.0) < 1e-9              # overall share = the slower side
    ms2, _ = scenario(0.4, 1.0)
    assert ms2.recovery_asymmetry is not None and ms2.recovery_asymmetry < 0
    assert abs(ms2.recovery_asymmetry + ms.recovery_asymmetry) < 1e-9


def test_machinery_no_false_shock_without_a_baseline_and_slow_drift_is_not_a_burst():
    # fewer than 3 baseline points: the depth rule cannot fire (no median), the spread/sweep rules need `pre`
    eng = ResilienceEngine()
    states = _run(eng, [(0, BIDS, ASKS), (5, _scale(BIDS, 0.2, only_first=True), ASKS)])
    assert [ms.resilience_state for ms in states] == ["none", "none"]
    # a slow, monotone bleed of 2 % per 5 s (12 % per 30-s burst) never falls by 50 % of the median inside
    # one burst — depth ends at 10 % of its start without a shock (a bleed, not a shock)
    eng = ResilienceEngine()
    steps = list(_calm(300))
    f = 1.0
    s = 305.0
    while f > 0.1:
        f -= 0.02
        steps.append((s, _scale(BIDS, f, only_first=True), ASKS))
        s += 5
    states = _run(eng, steps)
    assert eng.curves("SYN") == [] and all(ms.resilience_state == "none" for ms in states)
    assert states[-1].bid_qty1 < 0.12 * BIDS[0][1]
    # the same total fall inside one burst IS a shock
    eng = ResilienceEngine()
    states = _run(eng, list(_calm(300)) + [(310, _scale(BIDS, 0.45, only_first=True), ASKS)])
    assert states[-1].resilience_state == "shocked"
    assert abs(eng.active_curve("SYN")["triggers"][0]["drop_share"] - 0.55) < 1e-9


def test_machinery_determinism_same_input_same_curve():
    def build():
        steps = list(_calm(300))
        swept = BIDS[2:]
        steps += [(305, swept, ASKS), (312, [(9.9, 500.0)] + swept, ASKS), (330, BIDS, ASKS), (400, BIDS, ASKS)]
        return steps
    a, b = ResilienceEngine(), ResilienceEngine()
    sa = _run(a, build())
    sb = _run(b, build())
    assert a.curves("SYN") == b.curves("SYN") and len(a.curves("SYN")) == 1
    assert [m.state_hash() for m in sa] == [m.state_hash() for m in sb]
    assert [m.to_dict()["session_state"]["resilience"] for m in sa] == \
           [m.to_dict()["session_state"]["resilience"] for m in sb]


def test_machinery_engine_keeps_symbols_apart():
    eng = ResilienceEngine()
    for s in range(0, 305, 5):
        for sym in ("A", "B"):
            ms = _ms(s, BIDS, ASKS, symbol=sym)
            eng.on_state(ms, None)
    a = _ms(305, _scale(BIDS, 0.3, only_first=True), ASKS, symbol="A")
    b = _ms(305, BIDS, ASKS, symbol="B")
    eng.on_state(a, None); eng.fill_state(a)
    eng.on_state(b, None); eng.fill_state(b)
    assert a.resilience_state == "shocked" and b.resilience_state == "none"
    assert eng.active_curve("A") is not None and eng.active_curve("B") is None


def test_machinery_pulled_wall_above_baseline_is_not_a_depth_shock():
    """A 2.5× wall appears and is pulled back to 1.5× the median: the burst fall is 1.0 median (≥ 0.5) but the
    depth is still above baseline — not a depletion, no curve (before the fix this opened a curve that closed
    as 'overshoot' on the same update).  Pulling it further, to 0.8×, IS a shock with share 0.8 at the shock."""
    eng = ResilienceEngine()
    wall = _scale(BIDS, 2.5, only_first=True)
    steps = list(_calm(275)) + [(s, wall, ASKS) for s in (280, 285, 290, 295, 300, 305)]
    steps.append((310, _scale(BIDS, 1.5, only_first=True), ASKS))
    states = _run(eng, steps)
    assert states[-1].resilience_state == "none" and eng.curves("SYN") == [] and eng.active_curve("SYN") is None
    states = _run(eng, [(315, _scale(BIDS, 0.8, only_first=True), ASKS)])
    c = eng.active_curve("SYN")
    assert states[-1].resilience_state == "shocked" and c is not None
    assert c["triggers"][0]["kind"] == "depth" and c["measure"] == "qty1"
    assert abs(c["baseline"]["bid"] - 1000.0) < 1e-9 and abs(c["share_at_shock"] - 0.8) < 1e-9
    assert c["overshoot"] is False


def test_machinery_repricing_with_intact_depth_is_not_a_sweep_shock():
    """Both bests move down 2 ticks with every level intact (a repricing): the sweep rule matches the bid
    but nothing was consumed — the would-be curve is already 'recovered' at its first sample, so no curve is
    opened.  The reversion 30 s later (bests back up) must not fire either (the bid-sweep rule would see the
    old, lower bid as `pre`)."""
    eng = ResilienceEngine()
    down = [(round(p - 0.2, 6), q) for p, q in BIDS], [(round(p - 0.2, 6), q) for p, q in ASKS]
    steps = list(_calm(300)) + [(s, down[0], down[1]) for s in (305, 310, 315, 320, 325, 330, 335, 340)]
    steps += [(s, BIDS, ASKS) for s in (345, 350, 355, 360, 365, 370, 375, 380)]
    states = _run(eng, steps)
    assert all(ms.resilience_state == "none" for ms in states[-16:])
    assert eng.curves("SYN") == [] and eng.active_curve("SYN") is None
    assert all(ms.recovery_curve is None and ms.liquidity_response is None for ms in states)


def test_machinery_overshoot_needs_a_depleted_side_and_is_not_read_on_the_shock_sample():
    """Sweep of two thin touch levels exposing much bigger deep levels: top-K share at the shock is > 1.3 while
    the spread is 3 ticks wide (not recovered).  The shock sample never counts as overshoot and a side that
    was never below baseline cannot overshoot — the curve closes as plain 'recovered' once the spread is back."""
    eng = ResilienceEngine()
    thin = [(10.0, 100.0), (9.9, 100.0), (9.8, 2000.0), (9.7, 2000.0), (9.6, 2000.0)]      # top-K 6200
    swept = thin[2:] + [(9.5, 3000.0), (9.4, 3000.0)]                                       # top-K 12000, +3 ticks
    steps = list(_calm(300, bids=thin)) + [(305, swept, ASKS), (310, swept, ASKS), (320, thin, ASKS)]
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-3:]] == ["shocked", "shocked", "recovered"]
    c = eng.curves("SYN")[0]
    assert c["triggers"][0]["kind"] == "sweep" and c["side"] == "bid"
    assert c["share_at_shock"] > 1.3 and c["overshoot"] is False and c["overshoot_kind"] is None
    assert c["state"] == "recovered" and c["time_to_recovery_s"] == 15.0


def test_machinery_book_vanishes_during_curve_closes_at_timeout_with_consistent_flags():
    eng = ResilienceEngine()
    steps = list(_calm(300)) + [(305, _scale(BIDS, 0.20, only_first=True), ASKS)]
    steps += [(s, [], []) for s in range(310, 305 + int(TIMEOUT_S) + 10, 5)]                 # the book disappears
    states = _run(eng, steps)
    by_t = {ms.t: ms for ms in states}
    assert by_t[_t(305)].resilience_state == "shocked"
    assert by_t[_t(310)].resilience_state == "shocked"                                      # clock keeps running
    closing = by_t[_t(305 + TIMEOUT_S)]
    assert closing.resilience_state == "vacuum"                                             # last seen share 0.2
    c = eng.curves("SYN")
    assert len(c) == 1 and c[0]["state"] == "vacuum" and c[0]["vacuum"] is True and c[0]["partial"] is False
    assert c[0]["t_end"] == _t(305 + TIMEOUT_S) and c[0]["duration_s"] == TIMEOUT_S
    assert c[0]["time_to_recovery_s"] is None and abs(c[0]["final_share"] - 0.2) < 1e-9
    assert closing.liquidity_response is None                                               # unobservable now
    assert states[-1].resilience_state == "none" and states[-1].session_state["resilience"]["active"] is False
    # last seen share 0.6 → partial, with the partial flag set
    eng2 = ResilienceEngine()
    steps2 = list(_calm(300)) + [(305, _scale(BIDS, 0.20, only_first=True), ASKS),
                                 (310, _scale(BIDS, 0.60, only_first=True), ASKS)]
    steps2 += [(s, [], []) for s in range(315, 305 + int(TIMEOUT_S) + 10, 5)]
    _run(eng2, steps2)
    c2 = eng2.curves("SYN")[0]
    assert c2["state"] == "partial" and c2["partial"] is True and c2["vacuum"] is False


def test_machinery_one_sided_book_shock_and_recovery_without_a_spread():
    """Bids only (no ask, no spread, no mid): a bid depth drop is a shock, the spread terms stay None (never 0)
    and the curve recovers on depth alone."""
    eng = ResilienceEngine()
    steps = list(_calm(300, asks=[])) + [(305, _scale(BIDS, 0.25, only_first=True), []),
                                         (310, _scale(BIDS, 0.95, only_first=True), [])]
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-3:]] == ["none", "shocked", "recovered"]
    c = eng.curves("SYN")[0]
    assert c["side"] == "bid" and c["direction"] == -1 and c["shock"]["move_ticks"] is None
    assert c["baseline"]["spread_ticks"] is None and c["shock"]["spread_ticks"] is None
    assert all(x["spread_share"] is None and x["mid_share"] is None for x in c["samples"])
    assert c["spread_share_at_shock"] is None and c["time_to_recovery_s"] == 5.0
    assert c["recovery_speed_ask"] is None and c["asymmetry"] is None


def test_machinery_duplicate_timestamps_do_not_break_speeds_or_counts():
    eng = ResilienceEngine()
    shocked = _scale(BIDS, 0.30, only_first=True)
    steps = list(_calm(300)) + [(305, shocked, ASKS), (305, shocked, ASKS), (305, _scale(BIDS, 0.5, only_first=True), ASKS),
                                (310, BIDS, ASKS)]
    states = _run(eng, steps)
    assert [ms.resilience_state for ms in states[-4:]] == ["shocked", "shocked", "recovering", "recovered"]
    assert states[-3].recovery_speed is None and states[-2].recovery_speed is None      # no time has passed
    c = eng.curves("SYN")[0]
    assert c["updates_to_recovery"] == 3 and c["time_to_recovery_s"] == 5.0
    assert c["curve"] == [(0.0, 0.3), (0.0, 0.3), (0.0, 0.5), (5.0, 1.0)]
    assert abs(states[-1].recovery_speed - (1.0 - 0.3) / 5.0) < 1e-9


def test_machinery_session_record_is_a_bounded_snapshot():
    """The per-state record must not carry the full sample list (state-store lines would grow quadratically
    over a curve); it carries the count and the last sample, and the curve itself."""
    eng = ResilienceEngine()
    steps = list(_calm(300)) + [(s, _scale(BIDS, 0.5, only_first=True), ASKS) for s in range(305, 400, 5)]
    states = _run(eng, steps)
    rec = states[-1].session_state["resilience"]
    assert "samples" not in rec and rec["samples_n"] == len(eng.active_curve("SYN")["samples"]) == 19
    assert rec["last_sample"]["s"] == 90.0 and abs(rec["last_sample"]["share"] - 0.5) < 1e-9
    assert rec["curve"] == eng.active_curve("SYN")["curve"] and rec["active"] is True
    assert states[-1].to_dict()["session_state"]["resilience"]["samples_n"] == 19


# ============================================================================ real data
def test_realdata_fixture_closed_market_books_produce_no_shock_and_no_silent_zero():
    """The committed capture is a closed market: static one-sided or empty pre-open books from two
    depth sensors. Replayed through the book engine into the resilience engine nothing can be a shock,
    unobservable spreads stay None, and the same replay twice is identical."""
    r = replay(FIXTURE)
    books = r["books"]
    assert len(books) > 0

    def run():
        eng = ResilienceEngine()
        out = {}
        for (src, sym), g in books.groupby(["source", "symbol"], sort=True):
            book = EvolvingBook(tick=TICK)
            key = f"{sym}@{src}"
            for i, row in enumerate(g.sort_values(["t_recv", "seq"], kind="mergesort").itertuples()):
                t = row.t_recv.to_pydatetime()
                book.apply_snapshot(t, list(row.bid_levels or []), list(row.ask_levels or []))
                ms = MarketState(symbol=key, t=t, seq=i, tick_size=TICK)
                book.fill_state(ms)
                eng.on_state(ms, None)
                eng.fill_state(ms)
                out.setdefault(key, []).append(ms)
        return eng, out

    eng, out = run()
    assert out
    for key, states in out.items():
        assert eng.curves(key) == []
        for ms in states:
            if ms.empty_book:
                assert ms.resilience_state is None or ms.resilience_state == "none"
            else:
                assert ms.resilience_state == "none", (key, ms.t, ms.resilience_state)
            assert ms.recovery_speed is None and ms.recovery_curve is None and ms.liquidity_response is None
            rec = ms.session_state["resilience"]
            assert rec["curves_completed"] == 0 and rec["state"] == ms.resilience_state
    # the fixture has one-sided books: they are observed (state none), not unobservable
    assert any(ms.resilience_state == "none" and ms.one_sided for states in out.values() for ms in states)
    eng2, out2 = run()
    assert {k: [m.state_hash() for m in v] for k, v in out.items()} == \
           {k: [m.state_hash() for m in v] for k, v in out2.items()}
