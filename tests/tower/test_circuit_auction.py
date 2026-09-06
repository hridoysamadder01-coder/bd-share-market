"""tower.circuit / tower.auction — hand-built limit approach / lock sequences, band
fallback, exception detection, streak logic, auction proxy vs real AUCTION events
and the transition gap; real-data tests on the fixture circuit table and the
closed-market capture (empty books → distances from ltp)."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import pytest

from bdlib.config import CIRCUIT_BANDS_UNVERIFIED, LIMIT_BAND_TOLERANCE
from seeing.replay import _adapters, replay
from tower.auction import AuctionEngine, PROXY_SOURCE
from tower.book import EvolvingBook
from tower.circuit import (BANDS_RULE_SOURCE, CircuitEngine, band_for, day_history_from_tables,
                           limits_from_reference)
from tower.events import Event, EventType
from tower.mechanics.base import StateHistory
from tower.normalize import events_from_frames, normalize_store
from tower.pressure import fill_pressure
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)          # 10:00 Dhaka, Sunday → CONTINUOUS
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
CIRCUIT_HTML = os.path.join(ROOT, "tests", "fixtures", "lankabd_circuit_head.html")


def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


class Sim:
    """Drives one symbol through CircuitEngine exactly the way tower.engine does:
    book → MarketState → circuit.on_state/fill_state → pressure → history."""

    def __init__(self, symbol="SYN", tick=0.1, yclose=10.0, engine=None):
        self.symbol, self.tick, self.yclose = symbol, tick, yclose
        self.eng = engine or CircuitEngine()
        self.hist = StateHistory()
        self.seq = 0
        self.rows = 0

    def step(self, s, bids=(), asks=(), ltp=None, phase="CONTINUOUS", interval_volume=None, interval_value=None,
             open_px=None, yclose=None, t=None, rows_added=1, first_row=False, mono=False, cum_volume=None,
             cum_value=None, feed="lankabd_tape"):
        t = t or _t(s)
        self.seq += 1
        ms = MarketState(symbol=self.symbol, t=t, seq=self.seq, session_phase=phase, tick_size=self.tick)
        if bids or asks:
            b = EvolvingBook(self.tick)
            b.apply_snapshot(t, list(bids), list(asks))
            b.fill_state(ms)
        ms.tick_size = self.tick
        ms.ltp = ltp
        q = {"yclose": yclose if yclose is not None else self.yclose}
        if open_px is not None:
            q["open"] = open_px
        ms.session_state["quote"] = q
        if interval_volume is not None:
            self.rows += rows_added
            ms.interval_volume = interval_volume
            ms.trade_volume, ms.trade_value = cum_volume, cum_value
            ms.session_state["tape"] = {"feed": feed, "rows": self.rows, "last_interval_value": interval_value,
                                        "last_first_row": first_row, "last_monotone_break": mono}
        elif self.rows:
            prev = self.hist.last(1)[0] if len(self.hist) else None
            ms.interval_volume = prev.interval_volume if prev else None
            ms.trade_volume, ms.trade_value = (prev.trade_volume, prev.trade_value) if prev else (None, None)
            ms.session_state["tape"] = dict(prev.session_state.get("tape") or {}) if prev else {}
        self.eng.on_state(ms, self.hist)
        self.eng.fill_state(ms)
        fill_pressure(ms, self.hist)
        self.hist.push(ms)
        return ms


# ============================================================================ limits
def test_machinery_bands_fallback_when_no_reference_then_published_reference_wins():
    sim = Sim(yclose=51.3)
    ms = sim.step(0, bids=[(50.5, 100)], asks=[(50.7, 100)], ltp=50.6)
    c = ms.circuit
    # 51.3 ≤ 200 → ±10 %: upper floor(56.43/0.1)·0.1 = 56.4, lower ceil(46.17/0.1)·0.1 = 46.2
    assert c["rule_source"] == BANDS_RULE_SOURCE and c["unverified"] is True and c["band"] == 0.10
    assert abs(c["upper_limit"] - 56.4) < 1e-9 and abs(c["lower_limit"] - 46.2) < 1e-9
    assert ms.session_state["circuit_rule"]["rule_source"] == BANDS_RULE_SOURCE
    assert ms.session_state["circuit_rule"]["unverified"] is True
    assert c["price_basis"] == "mid" and abs(c["price"] - 50.6) < 1e-9
    assert abs(c["dist_up_ticks"] - 58.0) < 1e-6 and abs(c["dist_down_ticks"] - 44.0) < 1e-6
    assert abs(c["dist_up_pct"] - (56.4 - 50.6) / 50.6 * 100) < 1e-9
    assert c["nearer_limit"] == "down"
    # a higher-priced symbol lands in a narrower dated band
    sim2 = Sim(symbol="BIG", yclose=465.8)
    c2 = sim2.step(0, ltp=463.4).circuit
    assert c2["band"] == 0.0875 and abs(c2["upper_limit"] - 506.5) < 1e-9 and abs(c2["lower_limit"] - 425.1) < 1e-9
    assert c2["price_basis"] == "ltp"
    # published reference replaces the derived limits and is not flagged
    sim.eng.on_reference("SYN", 56.0, 46.5, 0.1, 10.0, "2026-09-03", rule_source="lankabd_circuit")
    c3 = sim.step(5, bids=[(50.5, 100)], asks=[(50.7, 100)], ltp=50.6).circuit
    assert c3["rule_source"] == "lankabd_circuit" and c3["unverified"] is False
    assert c3["upper_limit"] == 56.0 and c3["lower_limit"] == 46.5 and c3["reference_date"] == "2026-09-03"
    # nothing observable → nothing invented
    c4 = Sim(symbol="EMPTY", yclose=None).step(0).circuit
    assert c4["upper_limit"] is None and c4["dist_up_ticks"] is None and c4["hit_up"] is None
    assert c4["exception"] is None and c4["rule_source"] is None


def test_machinery_band_schedule_helpers_match_config():
    assert band_for(200.0) == 0.10 and band_for(200.01) == 0.0875 and band_for(6000.0) == 0.0375
    assert band_for(0.0) is None and band_for(None) is None
    assert [b for _, b in CIRCUIT_BANDS_UNVERIFIED][0] == band_for(1.0)
    up, lo, rounded = limits_from_reference(3.6, 0.10, 0.1)
    assert rounded and abs(up - 3.9) < 1e-9 and abs(lo - 3.3) < 1e-9        # real 1JANATAMF row
    up, lo, rounded = limits_from_reference(21.9, 0.10, None)
    assert not rounded and abs(up - 24.09) < 1e-9


# ============================================================================ approach / lock dynamics
def test_machinery_approach_velocity_and_acceleration_toward_upper():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    mids = []
    for i in range(13):                      # every 30 s for 6 min, mid climbs 10.0 → 10.6 accelerating
        px = 10.0 + 0.6 * (i / 12) ** 2
        px = round(px / 0.1) * 0.1
        bid, ask = round(px - 0.05, 2), round(px + 0.05, 2)
        ms = sim.step(30 * i, bids=[(bid, 500)], asks=[(ask, 300)], ltp=px)
        mids.append(ms.circuit)
    c0 = mids[0]
    assert c0["approach_velocity"] is None                              # no point 120 s back yet
    late = mids[-1]
    assert late["nearer_limit"] == "up" and late["approach_velocity"] is not None
    # distance to the upper limit fell over the last 120 s → positive ticks/min toward the door
    d_now = late["dist_up_ticks"]; d_prev = mids[-5]["dist_up_ticks"]
    assert abs(late["approach_velocity"] - (d_prev - d_now) / 2.0) < 1e-6
    assert late["approach_velocity"] > 0 and late["approach_acceleration"] is not None
    assert late["approach_acceleration"] > 0                           # quadratic climb: velocity rising
    assert late["hit_up"] is False and late["locked_up"] is False and late["exception"] is None


def test_machinery_hit_lock_unlock_relock_queue_and_volume_accounting():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    # t=0 far away; t=30 within 2 % of the door, a tape row arrives (approaching volume)
    sim.step(0, bids=[(10.5, 100)], asks=[(10.6, 100), (11.0, 400)], ltp=10.5, interval_volume=1000, interval_value=10500)
    m1 = sim.step(30, bids=[(10.8, 200)], asks=[(10.9, 150), (11.0, 400)], ltp=10.8, interval_volume=700, interval_value=7500)
    c1 = m1.circuit
    assert c1["dist_up_pct"] < 2.0 and c1["hit_up"] is False
    assert c1["shares_to_door"] == 550.0 and c1["door_visible"] is True
    assert c1["volume_approaching"] == 700.0 and c1["turnover_approaching"] == 7500.0
    assert c1["volume_while_locked"] == 0.0
    # t=60: the ask at the limit is hit (ltp == upper, asks still displayed): hit but not locked
    m2 = sim.step(60, bids=[(10.9, 300)], asks=[(11.0, 100)], ltp=11.0, interval_volume=300, interval_value=3300)
    c2 = m2.circuit
    assert c2["hit_up"] is True and c2["locked_up"] is False and c2["first_hit_time"] == _t(60)
    assert c2["first_hit_side"] == "up"
    ph = c2["pre_hit_state"]
    assert ph["t"] == _t(30).isoformat() and ph["shares_to_door"] == 550.0
    assert ph["imb_topk"] == m1.imb_topk and ph["pressure_strength"] == m1.pressure_strength
    assert c2["pressure_before_hit"] == m1.pressure_strength and c2["liquidity_before_hit"] == m1.visible_ask_liq
    assert c2["volume_approaching"] == 1000.0                              # 700 + 300 (hit row, not locked)
    # t=90: locked — best bid at the limit, no asks left
    m3 = sim.step(90, bids=[(11.0, 5000), (10.9, 300)], asks=[], ltp=11.0)
    c3 = m3.circuit
    assert c3["locked_up"] is True and c3["hit_up"] is True and c3["first_lock_time"] == _t(90)
    assert c3["queue_at_upper"] == 5000.0 and c3["queue_side"] == "up"
    assert c3["shares_to_door"] == 0.0 and c3["door_visible"] is True
    assert c3["price_basis"] == "ltp" and c3["dist_up_ticks"] == 0.0
    assert c3["time_locked_s"] == 0.0                                       # just locked: nothing elapsed yet
    # t=150: still locked, queue grew; a tape row arrives while locked
    m4 = sim.step(150, bids=[(11.0, 8000), (10.9, 300)], asks=[], ltp=11.0, interval_volume=2000, interval_value=22000)
    c4 = m4.circuit
    assert c4["time_locked_s"] == 60.0 and c4["queue_delta_60s"] == 3000.0
    assert c4["queue_growth"] == 3000.0 and c4["queue_decay"] == 0.0 and c4["queue_persistence_s"] == 60.0
    assert c4["volume_while_locked"] == 2000.0 and c4["turnover_while_locked"] == 22000.0
    assert c4["volume_approaching"] == 1000.0                              # unchanged while locked
    # t=210: unlock — an ask reappears at the limit; the queue is decaying
    m5 = sim.step(210, bids=[(11.0, 6000), (10.9, 300)], asks=[(11.0, 200)], ltp=11.0)
    c5 = m5.circuit
    assert c5["locked_up"] is False and c5["unlock_count"] == 1 and c5["relock_count"] == 0
    assert c5["time_locked_s"] == 120.0 and c5["last_unlock_time"] == _t(210)
    assert c5["queue_delta_60s"] == -2000.0 and c5["queue_decay"] == 2000.0
    assert c5["hit_up"] is True                                             # still at the limit price
    # t=240: relock 30 s later
    m6 = sim.step(240, bids=[(11.0, 6500)], asks=[], ltp=11.0)
    c6 = m6.circuit
    assert c6["locked_up"] is True and c6["relock_count"] == 1 and c6["time_between_unlock_relock_s"] == 30.0
    assert c6["time_locked_s"] == 120.0                                     # unlocked interval not counted
    m7 = sim.step(300, bids=[(11.0, 6500)], asks=[], ltp=11.0)
    assert m7.circuit["time_locked_s"] == 180.0 and m7.circuit["max_queue_at_limit"] == 8000.0
    assert m7.circuit["ever_locked_up"] is True and m7.circuit["first_hit_time"] == _t(60)
    # a repeated update carries the same tape row: not double counted
    m8 = sim.step(330, bids=[(11.0, 6500)], asks=[], ltp=11.0)
    assert m8.circuit["volume_while_locked"] == 2000.0
    # day summary for the next session
    d = sim.eng.day_summary("SYN")
    assert d["locked_up_close"] is True and d["unlock_count"] == 1 and d["relock_count"] == 1
    assert d["date"] == "2026-09-06" and d["upper"] == 11.0 and d["time_locked_s"] == 210.0
    assert abs(d["locked_share"] - 210.0 / 330.0) < 1e-9 and d["close"] == 11.0
    assert d["session_observed_s"] == 330.0


def test_machinery_locked_down_mirror():
    sim = Sim(symbol="DN", yclose=10.0)
    sim.eng.on_reference("DN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    c0 = sim.step(0, bids=[(9.1, 100)], asks=[(9.2, 100)], ltp=9.2).circuit
    assert c0["nearer_limit"] == "down" and c0["hit_down"] is False and c0["shares_to_floor"] == 100.0
    assert c0["floor_visible"] is False                                      # displayed bids stop at 9.1 > 9.0
    m = sim.step(30, bids=[], asks=[(9.0, 7000), (9.1, 100)], ltp=9.0)
    c = m.circuit
    assert c["locked_down"] is True and c["hit_down"] is True and c["queue_at_lower"] == 7000.0
    assert c["queue_side"] == "down" and c["locked_up"] is False and c["hit_up"] is False
    assert c["shares_to_floor"] == 0.0 and c["floor_visible"] is True
    assert c["first_hit_side"] == "down" and c["first_hit_time"] == _t(30) and c["first_lock_time"] == _t(30)
    assert c["pre_hit_state"]["liquidity_door_side"] == 100.0                # bid liquidity that was consumed
    assert c["liquidity_before_hit"] == 100.0 and c["dist_down_ticks"] == 0.0
    m2 = sim.step(90, bids=[], asks=[(9.0, 7000)], ltp=9.0)
    assert m2.circuit["time_locked_down_s"] == 60.0 and m2.circuit["time_locked_up_s"] == 0.0
    assert m2.circuit["time_locked_s"] == 60.0 and m2.circuit["queue_persistence_s"] == 60.0
    # unlock: a bid reappears
    m3 = sim.step(120, bids=[(9.0, 50)], asks=[(9.0, 6000)], ltp=9.0)
    assert m3.circuit["locked_down"] is False and m3.circuit["unlock_count"] == 1
    assert m3.circuit["queue_delta_60s"] == -1000.0 and m3.circuit["queue_decay"] == 1000.0
    d = sim.eng.day_summary("DN")
    assert d["locked_down_close"] is False and d["ever_locked_down"] is True and d["first_hit_side"] == "down"


def test_machinery_pre_open_residual_book_is_not_session_bookkeeping():
    """A capture starts hours before the open with the previous session's residual book (bids at
    the upper limit, no asks) and the carried ltp = previous close. The flags describe that book,
    but nothing of TODAY's session (lock time, first lock, unlocks, open price, session clock,
    streak "locked now", break day) may come from it."""
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    sim.eng.set_day_history("SYN", _hist(n_up=2, upper=11.0, lower=9.0, locked_share=0.9, unlock_count=0))
    t_closed = T0 - timedelta(hours=3)                                      # 07:00 Dhaka → CLOSED
    t_pre = T0 - timedelta(minutes=20)                                      # 09:40 Dhaka → PRE_OPEN
    c0 = sim.step(0, bids=[(11.0, 4000), (10.9, 100)], asks=[], ltp=11.0, phase="CLOSED", t=t_closed).circuit
    assert c0["in_session"] is False and c0["locked_up"] is True and c0["hit_up"] is True    # the displayed book
    assert c0["first_lock_time"] is None and c0["first_hit_time"] is None and c0["time_locked_s"] == 0.0
    assert c0["open_price"] is None and c0["session_elapsed_s"] is None
    assert c0["consecutive_upper_streak"] == 2 and c0["break_day"] is None and c0["next_session"] is None
    assert c0["streak_continuation_strength"] is None and c0["streak_weakening"] is None
    c1 = sim.step(0, bids=[(11.0, 4000), (10.9, 100)], asks=[], ltp=11.0, phase="PRE_OPEN", t=t_pre).circuit
    assert c1["time_locked_s"] == 0.0 and c1["session_elapsed_s"] is None and c1["open_price"] is None
    assert c1["unlock_count"] == 0 and c1["ever_locked_up"] is False
    # the open: a fresh two-sided book below the limit — NOT an unlock, and the session clock starts here
    c2 = sim.step(0, bids=[(10.7, 300)], asks=[(10.8, 200)], ltp=10.75).circuit
    assert c2["in_session"] is True and c2["unlock_count"] == 0 and c2["relock_count"] == 0
    assert c2["session_elapsed_s"] == 0.0 and c2["open_price"] == 10.75 and c2["break_day"] is True
    assert c2["next_session"] == "reversal" and c2["streak_weakening"] is False
    assert c2["consecutive_upper_streak"] == 2
    c3 = sim.step(60, bids=[(11.0, 500)], asks=[], ltp=11.0).circuit
    assert c3["locked_up"] is True and c3["first_lock_time"] == _t(60) and c3["first_hit_time"] == _t(60)
    assert c3["pre_hit_state"]["t"] == _t(0).isoformat()                    # the update before the hit, today
    assert c3["session_elapsed_s"] == 60.0 and c3["time_locked_s"] == 0.0 and c3["consecutive_upper_streak"] == 3
    c4 = sim.step(120, bids=[(11.0, 500)], asks=[], ltp=11.0).circuit
    assert c4["time_locked_s"] == 60.0 and abs(c4["locked_share_today"] - 0.5) < 1e-9
    assert abs(c4["streak_continuation_strength"] - 0.5 / 0.9) < 1e-9
    # after the close the residual locked book is still displayed but the clocks stop
    t_after = T0 + timedelta(hours=5)                                       # 15:00 Dhaka → CLOSED
    c5 = sim.step(0, bids=[(11.0, 500)], asks=[], ltp=11.0, phase="CLOSED", t=t_after).circuit
    assert c5["time_locked_s"] == 60.0 and c5["session_elapsed_s"] == 120.0 and c5["locked_up"] is True
    assert c5["queue_delta_60s"] is None and c5["approach_velocity"] is None   # no session dynamics after the close
    d = sim.eng.day_summary("SYN")
    assert d["locked_up_close"] is True and d["session_observed_s"] == 120.0 and d["locked_share"] == 0.5
    assert d["time_locked_s"] == 60.0 and d["unlock_count"] == 0


def test_machinery_open_price_needs_the_session_and_prefers_the_published_open():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    t_pre = T0 - timedelta(minutes=10)
    assert sim.step(0, ltp=10.0, open_px=10.0, phase="PRE_OPEN", t=t_pre).circuit["open_price"] is None
    assert sim.step(0, ltp=10.4).circuit["open_price"] == 10.4              # first in-session ltp
    assert sim.step(5, ltp=10.6).circuit["open_price"] == 10.4              # frozen
    sim2 = Sim(symbol="P", yclose=10.0)
    assert sim2.step(0, ltp=10.6, open_px=10.3).circuit["open_price"] == 10.3   # a published open wins


def test_machinery_tape_rows_first_row_monotone_break_and_multi_row_pull():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    near = dict(bids=[(10.85, 200)], asks=[(10.9, 100), (11.0, 400)], ltp=10.9)   # within 2 % of the door
    # the first row of the day is the cumulative total: it carries no interval, so nothing is attributed
    c0 = sim.step(0, interval_volume=50000, interval_value=5e5, first_row=True, cum_volume=50000, cum_value=5e5, **near).circuit
    assert c0["volume_approaching"] == 0.0 and c0["turnover_approaching"] == 0.0
    c1 = sim.step(30, interval_volume=700, interval_value=7500, cum_volume=50700, cum_value=507500, **near).circuit
    assert c1["volume_approaching"] == 700.0 and c1["turnover_approaching"] == 7500.0
    # a monotone break (negative Δ, kept and flagged by the tape) is not traded volume
    c2 = sim.step(60, interval_volume=300, interval_value=3000, mono=True, cum_volume=50400, cum_value=504500, **near).circuit
    assert c2["volume_approaching"] == 700.0
    # one pull delivered three rows: the frame shows the last interval only, the cumulative totals bridge them
    c3 = sim.step(90, interval_volume=100, interval_value=1000, rows_added=3, cum_volume=51400, cum_value=514500, **near).circuit
    assert c3["volume_approaching"] == 700.0 + 1000.0 and c3["turnover_approaching"] == 7500.0 + 10000.0
    # a repeated frame with the same row count adds nothing
    c4 = sim.step(100, **near).circuit
    assert c4["volume_approaching"] == 1700.0
    # a feed switch restarts the row clock: its first row is not attributed
    c5 = sim.step(130, interval_volume=900, interval_value=9000, feed="other_tape", cum_volume=5, cum_value=5, **near).circuit
    assert c5["volume_approaching"] == 1700.0
    c6 = sim.step(160, interval_volume=200, interval_value=2000, feed="other_tape", cum_volume=205, cum_value=2005, **near).circuit
    assert c6["volume_approaching"] == 1900.0


def test_machinery_queue_persistence_is_per_side_and_fill_state_recomputes_for_a_new_update():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    a = sim.step(0, bids=[(11.0, 100), (10.9, 50)], asks=[(11.0, 200)], ltp=11.0).circuit
    assert a["queue_side"] == "up" and a["queue_at_upper"] == 100.0 and a["queue_persistence_s"] == 0.0
    b = sim.step(60, bids=[(11.0, 100), (10.9, 50)], asks=[(11.0, 200)], ltp=11.0).circuit
    assert b["queue_persistence_s"] == 60.0
    # the nearer limit flips to the lower side: its queue has just appeared — persistence restarts at 0
    c = sim.step(90, bids=[(9.0, 100)], asks=[(9.0, 300), (9.1, 50)], ltp=9.0).circuit
    assert c["queue_side"] == "down" and c["queue_at_lower"] == 300.0 and c["queue_persistence_s"] == 0.0
    d = sim.step(120, bids=[(9.0, 100)], asks=[(9.0, 300), (9.1, 50)], ltp=9.0).circuit
    assert d["queue_persistence_s"] == 30.0
    # fill_state for an update that was never passed to on_state computes it instead of serving the stale one
    ms = MarketState(symbol="SYN", t=_t(150), seq=99, session_phase="CONTINUOUS", tick_size=0.1, ltp=10.0)
    ms.session_state["quote"] = {"yclose": 10.0}
    sim.eng.fill_state(ms)
    assert ms.circuit["price"] == 10.0 and ms.circuit["nearer_limit"] == "up" and ms.circuit["queue_side"] is None


def test_machinery_pre_hit_state_never_reads_a_previous_session():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    sim.step(0, bids=[(10.5, 100)], asks=[(10.6, 100)], ltp=10.5)
    sim.step(60, bids=[(10.6, 100)], asks=[(10.7, 100)], ltp=10.6)
    # the next trading date opens straight at the limit: the last state in history is yesterday's
    t1 = T0 + timedelta(days=1)
    c = sim.step(0, bids=[(11.0, 900)], asks=[], ltp=11.0, yclose=10.6, t=t1).circuit
    assert c["first_hit_time"] == t1 and c["pre_hit_state"]["t"] is None
    assert c["pre_hit_state"]["missing"] == ["no update before the hit"] and c["pressure_before_hit"] is None


def test_machinery_day_history_from_tables_keys_published_limits_by_the_scrape_session():
    import pandas as pd
    # the circuit page scraped on 2026-09-06 carries reference_date 2026-09-03 (the previous close's
    # date); its limits are the ones in force on 09-06, not on 09-03
    books = pd.DataFrame([
        {"symbol": "X", "t_recv": pd.Timestamp("2026-09-06 04:10:00+00:00"), "ltp": 10.5, "close_published": None,
         "yclose": 10.0, "open": 10.2},
        {"symbol": "X", "t_recv": pd.Timestamp("2026-09-06 08:20:00+00:00"), "ltp": 10.9, "close_published": 10.9,
         "yclose": 10.0, "open": 10.2},
        {"symbol": "X", "t_recv": pd.Timestamp("2026-09-07 05:00:00+00:00"), "ltp": 11.9, "close_published": None,
         "yclose": 10.9, "open": 11.0},
    ])
    circ = pd.DataFrame([{"symbol": "X", "t_recv": pd.Timestamp("2026-09-06 01:06:00+00:00"), "reference_date": "2026-09-03",
                          "upper_limit": 10.9, "lower_limit": 9.1, "tick_size": 0.1}])
    recs = day_history_from_tables({"books": books, "circuit": circ}, "X")
    assert [r["date"] for r in recs] == ["2026-09-06", "2026-09-07"]
    assert recs[0]["upper"] == 10.9 and recs[0]["lower"] == 9.1 and recs[0]["close"] == 10.9
    assert recs[0]["locked_up_close"] is True                               # closed at the published limit
    # 09-07 has no circuit row: the band schedule on yclose 10.9 → 11.9 / 9.9
    assert recs[1]["upper"] == 11.9 and recs[1]["lower"] == 9.9 and recs[1]["close"] == 11.9
    assert recs[1]["locked_up_close"] is True and recs[1]["open"] == 11.0


# ============================================================================ exceptions
def test_machinery_exception_reference_reset_suspect():
    # (a) published limits inconsistent with yclose × (1 ± breaker): ex-date style reset
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 8.8, 7.2, 0.1, 10.0, rule_source="lankabd_circuit")     # limits around 8.0, yclose 10
    c = sim.step(0, bids=[(8.0, 100)], asks=[(8.1, 100)], ltp=8.0).circuit
    assert c["exception"] == "reference_reset_suspect"
    assert c["exception_detail"]["limits_vs_yclose"]["expected_upper"] == 11.0
    assert "price_beyond_band" not in c["exception_detail"]
    # (b) consistent limits but a price beyond the band
    sim2 = Sim(symbol="B", yclose=10.0)
    sim2.eng.on_reference("B", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    c2 = sim2.step(0, bids=[(11.4, 100)], asks=[(11.6, 100)], ltp=11.5).circuit
    assert c2["exception"] == "reference_reset_suspect"
    assert set(c2["exception_detail"]["price_beyond_band"]["prices"]) == {"ltp", "best_bid", "best_ask"}
    # tolerance: one tick + LIMIT_BAND_TOLERANCE·yclose (display rounding of the reference price)
    tol = 0.1 + LIMIT_BAND_TOLERANCE * 10.0
    c3 = sim2.step(10, ltp=round(11.0 + tol - 0.01, 4)).circuit
    assert c3["exception"] is None
    c4 = sim2.step(20, ltp=round(11.0 + tol + 0.01, 4)).circuit
    assert c4["exception"] == "reference_reset_suspect"
    # (c) at the limit exactly: no exception
    c5 = sim2.step(30, bids=[(11.0, 100)], asks=[], ltp=11.0).circuit
    assert c5["exception"] is None and c5["locked_up"] is True
    # (d) band-derived limits are consistent by construction; only a price beyond flags
    sim3 = Sim(symbol="C", yclose=10.0)
    assert sim3.step(0, ltp=10.5).circuit["exception"] is None
    assert sim3.step(5, ltp=11.3).circuit["exception"] == "reference_reset_suspect"


# ============================================================================ streaks
def _hist(n_up=0, n_down=0, upper=11.0, lower=9.0, **extra):
    recs = []
    for i in range(n_up + n_down):
        up = i < n_up
        recs.append({"date": f"2026-09-0{i + 1}", "close": upper if up else lower, "yclose": 10.0,
                     "upper": upper, "lower": lower, "locked_up_close": up, "locked_down_close": not up, **extra})
    return recs


def test_machinery_streak_continuation_strength_and_weakening():
    sim = Sim(yclose=11.0)
    sim.eng.on_reference("SYN", 12.1, 9.9, 0.1, 10.0, rule_source="lankabd_circuit")
    sim.eng.set_day_history("SYN", _hist(n_up=2, upper=11.0, lower=9.0, locked_share=0.8, unlock_count=0))
    # open at the prior upper limit and lock immediately
    m0 = sim.step(0, bids=[(12.1, 3000)], asks=[], ltp=12.1, open_px=11.0)
    c0 = m0.circuit
    assert c0["prior_upper_streak"] == 2 and c0["consecutive_upper_streak"] == 3 and c0["consecutive_lower_streak"] == 0
    assert c0["next_session"] == "continuation" and c0["break_day"] is False
    assert c0["streak_continuation_strength"] is None                      # no elapsed session yet
    m1 = sim.step(100, bids=[(12.1, 4000)], asks=[], ltp=12.1)
    c1 = m1.circuit
    # locked for the whole 100 s elapsed → share 1.0 vs previous 0.8 → capped at 1
    assert c1["locked_share_today"] == 1.0 and c1["streak_continuation_strength"] == 1.0
    assert c1["streak_weakening"] is False and c1["queue_delta_60s"] == 1000.0
    # unlock: unlocks exceed the previous session's 0 and the queue decays → weakening
    m2 = sim.step(200, bids=[(12.1, 2500)], asks=[(12.1, 100)], ltp=12.1)
    c2 = m2.circuit
    assert c2["unlock_count"] == 1 and c2["streak_weakening"] is True
    assert c2["consecutive_upper_streak"] == 2                             # not locked now: prior streak only
    assert c2["break_day"] is True and c2["break_behaviour"]["queue_decay"] is True
    assert abs(c2["streak_continuation_strength"] - min(1.0, (200.0 / 200.0) / 0.8)) < 1e-9
    m3 = sim.step(300, bids=[(12.1, 2500)], asks=[(12.1, 100)], ltp=12.1)
    assert abs(m3.circuit["locked_share_today"] - 200.0 / 300.0) < 1e-9
    assert abs(m3.circuit["streak_continuation_strength"] - (200.0 / 300.0) / 0.8) < 1e-9


def test_machinery_break_day_and_next_session_classification():
    # prior session locked up at 11.0; today opens below → reversal, break day with a gap and reversal evidence
    sim = Sim(yclose=11.0)
    sim.eng.on_reference("SYN", 12.1, 9.9, 0.1, 10.0, rule_source="lankabd_circuit")
    sim.eng.set_day_history("SYN", _hist(n_up=1, upper=11.0, lower=9.0))
    before = sim.step(0, phase="PRE_OPEN")                                  # no open yet
    assert before.circuit["next_session"] is None and before.circuit["break_day"] is None
    m = sim.step(10, bids=[(10.6, 300)], asks=[(10.8, 200)], ltp=10.7, open_px=10.7)
    c = m.circuit
    assert c["next_session"] == "reversal" and c["break_day"] is True
    assert abs(c["break_behaviour"]["gap_open_ticks"] - (-3.0)) < 1e-9 and c["break_behaviour"]["reversal"] is True
    assert c["consecutive_upper_streak"] == 1 and c["streak_weakening"] is False
    # lower streak mirror: open at/below the prior lower limit → continuation
    sim2 = Sim(symbol="L", yclose=9.0)
    sim2.eng.on_reference("L", 9.9, 8.1, 0.1, 10.0, rule_source="lankabd_circuit")
    sim2.eng.set_day_history("L", _hist(n_down=3, upper=11.0, lower=9.0))
    c2 = sim2.step(0, bids=[], asks=[(8.1, 9000)], ltp=8.1, open_px=8.1).circuit
    assert c2["prior_lower_streak"] == 3 and c2["consecutive_lower_streak"] == 4
    assert c2["next_session"] == "continuation" and c2["break_day"] is False
    c3 = sim2.step(60, bids=[(8.3, 100)], asks=[(8.4, 100)], ltp=8.4).circuit
    assert c3["break_day"] is True and c3["break_behaviour"]["reversal"] is False   # 8.35 < prior lower 9.0
    assert c3["unlock_count"] == 1 and c3["streak_weakening"] is True
    # no history ever supplied → streak fields not observable (None, never a silent 0)
    c4 = Sim(symbol="N").step(0, ltp=10.0).circuit
    assert c4["consecutive_upper_streak"] is None and c4["prior_upper_streak"] is None
    assert c4["streak_history_observed"] is False and c4["next_session"] is None and c4["break_day"] is None
    # an EMPTY history is an observation: no prior session locked → 0, and locked now → 1
    sim5 = Sim(symbol="E", yclose=10.0)
    sim5.eng.on_reference("E", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    sim5.eng.set_day_history("E", [])
    c5 = sim5.step(0, bids=[(11.0, 100)], asks=[], ltp=11.0).circuit
    assert c5["streak_history_observed"] is True and c5["prior_upper_streak"] == 0
    assert c5["consecutive_upper_streak"] == 1 and c5["consecutive_lower_streak"] == 0 and c5["break_day"] is None


def test_machinery_day_roll_appends_summary_and_carries_streak():
    sim = Sim(yclose=10.0)
    sim.eng.on_reference("SYN", 11.0, 9.0, 0.1, 10.0, rule_source="lankabd_circuit")
    sim.step(0, bids=[(10.9, 100)], asks=[(11.0, 100)], ltp=10.9)
    sim.step(60, bids=[(11.0, 5000)], asks=[], ltp=11.0)
    sim.step(120, bids=[(11.0, 5000)], asks=[], ltp=11.0)
    assert sim.eng.day_history("SYN") == []
    # next trading date (Monday 2026-09-07): published reference moves up with the new day
    sim.eng.on_reference("SYN", 12.1, 9.9, 0.1, 10.0, "2026-09-07", rule_source="lankabd_circuit")
    t1 = T0 + timedelta(days=1)
    m = sim.step(0, bids=[(11.5, 100)], asks=[(11.6, 100)], ltp=11.5, open_px=11.5, yclose=11.0, t=t1)
    h = sim.eng.day_history("SYN")
    assert len(h) == 1 and h[0]["date"] == "2026-09-06" and h[0]["locked_up_close"] is True
    assert h[0]["time_locked_s"] == 60.0 and h[0]["upper"] == 11.0
    c = m.circuit
    assert c["prior_upper_streak"] == 1 and c["next_session"] == "continuation" and c["break_day"] is True
    assert c["time_locked_s"] == 0.0 and c["unlock_count"] == 0 and c["first_hit_time"] is None
    assert c["upper_limit"] == 12.1


# ============================================================================ auction
def _ev(et, t, source="auction_feed", **kw):
    return Event(source=source, event_type=et, t_recv=t, seq_local=0, symbol="SYN", **kw)


def test_machinery_auction_proxy_then_real_events_then_transition_gap():
    a = AuctionEngine()
    t_pre = datetime(2026, 9, 6, 3, 50, tzinfo=timezone.utc)                # 09:50 Dhaka → PRE_OPEN
    ms = MarketState(symbol="SYN", t=t_pre, session_phase="PRE_OPEN", tick_size=0.1)
    b = EvolvingBook(0.1); b.apply_snapshot(t_pre, [(10.0, 800), (9.9, 200)], [(10.1, 200), (10.2, 100)]); b.fill_state(ms)
    ms.ltp = 9.95
    ms.session_state["quote"] = {"yclose": 10.0}
    d = a.fill_state(ms)
    assert d["phase"] == "PRE_OPEN" and d["source"] == PROXY_SOURCE and d["indicative_price"] is None
    assert d["auction_pressure"] == ms.imb_topk and d["proxy_basis"] == "imb_topk" and d["auction_pressure"] > 0
    assert ms.ltp == 9.95 and ms.mid is not None                            # continuous fields untouched
    # a real AUCTION event replaces the proxy: signed imbalance / (matched + imbalance)
    a.on_event(_ev(EventType.AUCTION, t_pre + timedelta(seconds=30),
                   payload={"indicative_price": 10.3, "matched_qty": 6000, "imbalance_qty": 2000, "imbalance_side": "sell"}))
    ms2 = MarketState(symbol="SYN", t=t_pre + timedelta(seconds=40), session_phase="PRE_OPEN", tick_size=0.1)
    ms2.session_state["quote"] = {"yclose": 10.0}
    d2 = a.fill_state(ms2)
    assert d2["source"] == "auction_feed" and d2["indicative_price"] == 10.3 and d2["matched_qty"] == 6000
    assert d2["imbalance_side"] == "sell" and abs(d2["auction_pressure"] + 0.25) < 1e-9
    assert d2["auction_age_s"] == 10.0 and d2["transition_time"] is None
    # transition PRE_OPEN → CONTINUOUS without an ltp yet: gap pending
    t_open = datetime(2026, 9, 6, 4, 0, tzinfo=timezone.utc)
    ms3 = MarketState(symbol="SYN", t=t_open, session_phase="CONTINUOUS", tick_size=0.1)
    ms3.session_state["quote"] = {"yclose": 10.0}
    d3 = a.fill_state(ms3)
    assert d3["transition_time"] == t_open and d3["open_gap_ticks"] is None and d3["open_ltp"] is None
    assert d3["last_phase_change"]["from"] == "PRE_OPEN" and d3["last_phase_change"]["to"] == "CONTINUOUS"
    # first continuous update with an ltp: opening gap vs the indicative price
    ms4 = MarketState(symbol="SYN", t=t_open + timedelta(seconds=5), session_phase="CONTINUOUS", tick_size=0.1, ltp=10.5)
    ms4.session_state["quote"] = {"yclose": 10.0}
    d4 = a.fill_state(ms4)
    assert d4["open_ltp"] == 10.5 and d4["open_reference"] == "indicative" and abs(d4["open_gap_ticks"] - 2.0) < 1e-9
    assert d4["source"] == "auction_feed"                                  # today's auction data stays visible
    ms5 = MarketState(symbol="SYN", t=t_open + timedelta(seconds=50), session_phase="CONTINUOUS", tick_size=0.1, ltp=10.9)
    assert a.fill_state(ms5)["open_ltp"] == 10.5                            # the opening print is frozen


def test_machinery_auction_transition_gap_vs_yclose_without_auction_feed_and_closed_market():
    a = AuctionEngine()
    t_pre = datetime(2026, 9, 6, 3, 55, tzinfo=timezone.utc)
    ms = MarketState(symbol="SYN", t=t_pre, session_phase="PRE_OPEN", tick_size=0.1)
    d = a.fill_state(ms)
    assert d["source"] == PROXY_SOURCE and d["auction_pressure"] is None and d["missing"] == ["book imbalance"]
    t_open = datetime(2026, 9, 6, 4, 0, 1, tzinfo=timezone.utc)
    ms2 = MarketState(symbol="SYN", t=t_open, session_phase="CONTINUOUS", tick_size=0.1, ltp=9.7)
    ms2.session_state["quote"] = {"yclose": 10.0}
    d2 = a.fill_state(ms2)
    assert d2["open_reference"] == "yclose" and abs(d2["open_gap_ticks"] + 3.0) < 1e-9 and d2["source"] is None
    assert d2["auction_events"] == 0
    # closed market with no pre-open ever seen: nothing but the phase
    b = AuctionEngine()
    ms3 = MarketState(symbol="SYN", t=datetime(2026, 9, 6, 1, 6, tzinfo=timezone.utc), session_phase="CLOSED", tick_size=0.1, ltp=50.6)
    d3 = b.fill_state(ms3)
    assert d3["phase"] == "CLOSED" and d3["source"] is None and d3["auction_pressure"] is None
    assert d3["transition_time"] is None and d3["open_gap_ticks"] is None
    # an AUCTION event from a previous trading date is not applied today
    b.on_event(_ev(EventType.AUCTION, datetime(2026, 9, 3, 3, 55, tzinfo=timezone.utc),
                   payload={"indicative_price": 50.0, "matched_qty": 100, "imbalance_qty": 50, "imbalance_side": "buy"}))
    assert b.fill_state(ms3)["indicative_price"] is None and b.fill_state(ms3)["auction_events"] == 1


def test_machinery_auction_transition_from_closed_same_date_and_late_tick():
    # no update fell inside PRE_OPEN: CLOSED (same trading date) → CONTINUOUS is still the open
    a = AuctionEngine()
    t_closed = datetime(2026, 9, 6, 1, 30, tzinfo=timezone.utc)             # 07:30 Dhaka → CLOSED
    d0 = a.fill_state(MarketState(symbol="SYN", t=t_closed, session_phase="CLOSED", tick_size=None, ltp=10.0))
    assert d0["source"] is None and d0["transition_time"] is None
    t_open = datetime(2026, 9, 6, 4, 0, 2, tzinfo=timezone.utc)
    ms1 = MarketState(symbol="SYN", t=t_open, session_phase="CONTINUOUS", tick_size=None, ltp=10.3)
    ms1.session_state["quote"] = {"yclose": 10.0}
    d1 = a.fill_state(ms1)
    assert d1["transition_time"] == t_open and d1["open_ltp"] == 10.3 and d1["open_reference"] == "yclose"
    assert d1["open_gap_ticks"] is None                                     # tick not known yet — not 0
    ms2 = MarketState(symbol="SYN", t=t_open + timedelta(seconds=30), session_phase="CONTINUOUS", tick_size=0.1, ltp=10.8)
    d2 = a.fill_state(ms2)
    assert d2["open_ltp"] == 10.3 and abs(d2["open_gap_ticks"] - 3.0) < 1e-9   # frozen open, gap once the tick exists
    # a CLOSED update of the PREVIOUS date followed by CONTINUOUS is a replay starting mid-session: no open
    b = AuctionEngine()
    b.fill_state(MarketState(symbol="SYN", t=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc), session_phase="CLOSED", tick_size=0.1))
    d3 = b.fill_state(MarketState(symbol="SYN", t=datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc), session_phase="CONTINUOUS",
                                  tick_size=0.1, ltp=10.0))
    assert d3["transition_time"] is None and d3["open_ltp"] is None and d3["last_phase_change"]["to"] == "CONTINUOUS"
    # imbalance sign conventions: a negative imbalance quantity without a side is a sell imbalance
    c = AuctionEngine()
    t_pre = datetime(2026, 9, 6, 3, 50, tzinfo=timezone.utc)
    c.on_event(_ev(EventType.AUCTION, t_pre, payload={"indicative_price": 10.0, "matched_qty": 1000, "imbalance_qty": -1000}))
    d4 = c.fill_state(MarketState(symbol="SYN", t=t_pre, session_phase="PRE_OPEN", tick_size=0.1))
    assert abs(d4["auction_pressure"] + 0.5) < 1e-9 and d4["imbalance_side"] is None
    c.on_event(_ev(EventType.AUCTION, t_pre, payload={"indicative_price": 10.0, "matched_qty": 0, "imbalance_qty": 0, "imbalance_side": "B"}))
    assert c.fill_state(MarketState(symbol="SYN", t=t_pre, session_phase="PRE_OPEN", tick_size=0.1))["auction_pressure"] is None


# ============================================================================ real data
def test_realdata_circuit_reference_rows_limits_ticks_and_band_consistency():
    with open(CIRCUIT_HTML, "rb") as f:
        parsed = _adapters()["lankabd_circuit"].parse(f.read())
    assert len(parsed.frames) >= 40 and not parsed.problems
    t = datetime(2026, 9, 6, 1, 6, 30, tzinfo=timezone.utc)
    evs = events_from_frames("lankabd_circuit", parsed.frames, t_recv=t, seq=0)
    assert all(e.event_type == EventType.REFERENCE for e in evs)
    eng = CircuitEngine()
    ticks, breakers, exceptions, exact_misses = set(), set(), [], []
    for e in evs:
        p = e.payload
        eng.on_reference(e.symbol, p["upper_limit"], p["lower_limit"], p["tick_size"], p["breaker_pct"],
                         p["reference_date"], rule_source=e.source)
        ticks.add(p["tick_size"]); breakers.add(p["breaker_pct"])
        # the published breaker % is the dated band schedule applied to the reference price
        assert abs(band_for(p["open_adj_price"]) - p["breaker_pct"] / 100.0) < 1e-9, e.symbol
        up, lo, _ = limits_from_reference(p["open_adj_price"], p["breaker_pct"] / 100.0, p["tick_size"])
        if abs(up - p["upper_limit"]) > 1e-9 or abs(lo - p["lower_limit"]) > 1e-9:
            exact_misses.append(e.symbol)
        # an update with the published reference price as yclose and the book empty:
        # limits parsed, distances from ltp, no exception within one tick + tolerance
        ms = MarketState(symbol=e.symbol, t=t, session_phase="CLOSED", tick_size=p["tick_size"], ltp=p["open_adj_price"])
        ms.session_state["quote"] = {"yclose": p["open_adj_price"]}
        eng.on_state(ms, StateHistory()); eng.fill_state(ms)
        c = ms.circuit
        assert c["rule_source"] == "lankabd_circuit" and c["unverified"] is False and c["reference_date"] == "2026-09-03"
        assert c["upper_limit"] == p["upper_limit"] and c["lower_limit"] == p["lower_limit"] and c["tick"] == p["tick_size"]
        assert c["price_basis"] == "ltp" and c["dist_up_ticks"] > 0 and c["dist_down_ticks"] > 0
        assert abs(c["dist_up_ticks"] - (p["upper_limit"] - p["open_adj_price"]) / p["tick_size"]) < 1e-6
        assert c["hit_up"] is False and c["locked_up"] is False and c["queue_at_upper"] is None
        assert c["shares_to_door"] is None and c["door_visible"] is None
        if c["exception"]:
            exceptions.append(e.symbol)
    assert ticks == {0.1, 0.5} and breakers == {10.0, 8.75, 7.5, 6.25, 5.0}
    assert exceptions == []
    # the displayed reference price is rounded to one decimal: exactly one row (APOLOISPAT, 3.0 → limits
    # 2.7/3.2 imply ≈2.95) is one tick off the floor/ceil rule, which is why the tolerance carries a tick
    assert exact_misses == ["APOLOISPAT"]


def test_realdata_closed_capture_empty_books_distances_from_ltp_with_band_fallback():
    evs, stats = normalize_store(FIXTURE)
    books = [e for e in evs if e.event_type == EventType.BOOK_SNAPSHOT]
    # closed market: no snapshot is two-sided (SHARPIND empty, MALEKSPIN a residual ask, FINEFOODS residual bids)
    assert books and all(not (e.payload["bids"] and e.payload["asks"]) for e in books)
    eng = CircuitEngine()
    hists = {}
    seen = {}
    for e in books:
        hist = hists.setdefault(e.symbol, StateHistory())
        ms = MarketState(symbol=e.symbol, t=e.t_recv, session_phase=e.session_phase, tick_size=0.1)
        b = EvolvingBook(0.1); b.apply_snapshot(e.t_recv, e.payload["bids"], e.payload["asks"]); b.fill_state(ms)
        ms.ltp = e.payload["ltp"]
        ms.session_state["quote"] = {k: e.payload.get(k) for k in ("yclose", "open", "high", "low", "close_published")}
        eng.on_state(ms, hist); eng.fill_state(ms)
        hist.push(ms)
        seen[e.symbol] = (ms, e.payload)
    assert set(seen) == {"FINEFOODS", "MALEKSPIN", "SHARPIND"}
    for sym, (ms, p) in seen.items():
        c = ms.circuit
        band = band_for(p["yclose"])
        up, lo, _ = limits_from_reference(p["yclose"], band, 0.1)
        assert ms.mid is None and c["rule_source"] == BANDS_RULE_SOURCE and c["unverified"] is True
        assert c["upper_limit"] == up and c["lower_limit"] == lo and c["band"] == band
        assert c["price_basis"] == "ltp" and c["price"] == p["ltp"]            # no two-sided book: ltp basis
        assert abs(c["dist_up_ticks"] - (up - p["ltp"]) / 0.1) < 1e-6
        assert abs(c["dist_down_pct"] - (p["ltp"] - lo) / p["ltp"] * 100) < 1e-9
        assert c["hit_up"] is False and c["hit_down"] is False and c["locked_up"] is False and c["locked_down"] is False
        assert c["volume_approaching"] is None                                   # no tape feed in this path
        assert c["exception"] is None and ms.session_state["circuit_rule"]["unverified"] is True
        # the day's high / low stayed inside the derived band (a closed-market sanity check on the schedule)
        assert p["high"] <= up + 1e-9 and p["low"] >= lo - 1e-9
    # SHARPIND: nothing displayed at all → queue / door not observable
    c = seen["SHARPIND"][0].circuit
    assert seen["SHARPIND"][0].empty_book and c["queue_at_upper"] is None and c["queue_at_lower"] is None
    assert c["shares_to_door"] is None and c["door_visible"] is None
    # MALEKSPIN: one residual ask 1000 @ 51.3 (= yclose), no bids: the ask side does not reach the
    # upper limit 56.4, so shares_to_door (1000) is only a lower bound; no ask sits at the lower limit
    c = seen["MALEKSPIN"][0].circuit
    assert seen["MALEKSPIN"][0].one_sided and c["shares_to_door"] == 1000.0 and c["door_visible"] is False
    assert c["queue_at_lower"] == 0.0 and c["queue_at_upper"] is None
    # FINEFOODS: residual bids 28 @ 461.8 + 53 @ 461.0, no asks: the bid side (81) does not reach 425.1
    c = seen["FINEFOODS"][0].circuit
    assert c["shares_to_floor"] == 81.0 and c["floor_visible"] is False and c["queue_at_upper"] == 0.0
    assert c["shares_to_door"] == 0.0 and c["door_visible"] is True              # no ask displayed at all
    # SHARPIND closed one tick above its derived lower limit (23.8 × 0.9 = 21.42 → ceil to 21.5; ltp 21.6):
    # the nearer limit is down and within 2 %
    c = seen["SHARPIND"][0].circuit
    assert c["nearer_limit"] == "down" and abs(c["dist_down_ticks"] - 1.0) < 1e-6 and c["dist_down_pct"] < 2.0
    # day-history records derived from the seeing tables of the same capture
    tables = replay(FIXTURE)
    recs = day_history_from_tables(tables, "SHARPIND")
    assert len(recs) == 1 and recs[0]["date"] == "2026-09-06" and recs[0]["close"] == 21.6 and recs[0]["yclose"] == 23.8
    assert recs[0]["upper"] == 26.1 and recs[0]["lower"] == 21.5 and recs[0]["locked_down_close"] is False
    assert day_history_from_tables(tables, "NOPE") == []
