"""tower.tape / tower.queue — machinery (synthetic, hand-computed) and real fixture tape rows."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import pytest

from seeing.replay import replay
from tower.book import EvolvingBook
from tower.queue import QueueState
from tower.state import MarketState
from tower.tape import TapeState, classify_direction

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")


def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def _ms(s: float, **kw) -> MarketState:
    ms = MarketState(symbol="SYN", t=_t(s), tick_size=0.1)
    for k, v in kw.items():
        setattr(ms, k, v)
    return ms


def _book(bids, asks, t=None):
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(t or T0, bids, asks)
    return b


# ============================================================================ tape
def test_machinery_cum_totals_deltas_first_row_monotone_break_and_repeat():
    tape = TapeState(tick=0.1)
    book = _book([(10.0, 100)], [(10.1, 50)])
    # first row of the day: the interval IS the cumulative value (flagged)
    r = tape.on_cum_totals(_t(10), _t(12), 5, 1000, 0.0101, 10.1, book=book, source="lankabd_tape")
    assert r["first_row"] and r["interval_trades"] == 5 and r["interval_volume"] == 1000
    assert abs(r["interval_vwap"] - 10.1) < 1e-9
    ms = _ms(12)
    tape.fill_state(ms, book)
    assert ms.trade_count == 5 and ms.trade_volume == 1000 and abs(ms.trade_value - 10100) < 1e-6
    assert ms.interval_trades == 5 and ms.session_state["tape"]["last_first_row"] is True
    assert ms.trade_intensity is None                                     # one instant: no rate yet
    assert ms.tape_source == "lankabd_tape" and ms.tape_age_s == 0.0
    # second row: Δ = (3, 500, 5000) → vwap 10.0 == bid → −1 by the quote rule (book before the interval)
    r = tape.on_cum_totals(_t(70), _t(72), 8, 1500, 0.0151, 10.0, book=book, source="lankabd_tape")
    assert r["interval_trades"] == 3 and r["interval_volume"] == 500 and abs(r["interval_vwap"] - 10.0) < 1e-6
    assert r["direction"] == -1.0 and not r["first_row"]
    ms = _ms(80)
    tape.fill_state(ms, book)
    assert ms.trade_flow_direction == -1.0 and ms.signed_flow_window == -500.0
    assert abs(ms.tape_age_s - 8.0) < 1e-9
    # intensity: rows in (max(now−120, t_first), now] = (10, 70] → 3 trades over 60 s → 3/min
    assert abs(ms.trade_intensity - 3.0) < 1e-9
    assert ms.last_print is None                                          # 3 trades ≠ one print
    # negative Δ: kept, flagged, excluded from windows, no direction
    r = tape.on_cum_totals(_t(130), _t(132), 7, 1400, 0.0140, 10.0, book=book, source="lankabd_tape")
    assert r["monotone_break"] and r["interval_trades"] == -1 and r["interval_volume"] == -100
    assert r["direction"] is None
    ms = _ms(132)
    tape.fill_state(ms, book)
    assert ms.interval_trades == -1 and ms.session_state["tape"]["last_monotone_break"] is True
    assert ms.session_state["tape"]["monotone_breaks"] == 1
    assert ms.trade_count == 7                                            # totals as reported, never repaired
    assert ms.signed_flow_window == -500.0                                # break row not in the window
    # a repeated row only advances the tape clock (no interval, no trades affirmed through the stamp)
    assert tape.on_cum_totals(_t(190), _t(192), 7, 1400, 0.0140, 10.0, book=book, source="lankabd_tape") is None
    ms = _ms(192)
    tape.fill_state(ms, book)
    st = ms.session_state["tape"]
    assert st["repeat_rows"] == 1 and st["tape_clock"] == _t(190).isoformat()
    assert ms.interval_trades == -1                                       # last interval still the last row
    assert ms.trade_intensity == 0.0                                      # (70, 190] holds no trades


def test_machinery_one_trade_interval_becomes_an_inferred_print():
    tape = TapeState(tick=0.1)
    book = _book([(10.0, 100)], [(10.1, 50)])
    tape.on_cum_totals(_t(10), _t(10), 5, 1000, 0.0101, 10.1, book=book, source="lankabd_tape")
    tape.on_cum_totals(_t(20), _t(20), 6, 1200, 0.01212, 10.1, book=book, source="lankabd_tape")   # +200 @ 10.1
    ms = _ms(20)
    tape.fill_state(ms, book)
    lp = ms.last_print
    assert lp["inferred_from_delta"] and lp["qty"] == 200 and abs(lp["price"] - 10.1) < 1e-6 and lp["direction"] == 1.0


def test_machinery_direction_rules_including_locked_book():
    # aggressor beats the quote rule
    assert classify_direction(10.0, 10.0, 10.1, aggressor="B")[0] == 1.0
    assert classify_direction(10.1, 10.0, 10.1, aggressor="S")[0] == -1.0
    # quote rule
    assert classify_direction(10.1, 10.0, 10.1)[0] == 1.0
    assert classify_direction(10.0, 10.0, 10.1)[0] == -1.0
    assert abs(classify_direction(10.05, 10.0, 10.1)[0]) < 1e-9          # mid-spread → 0 (position inside spread)
    assert abs(classify_direction(10.075, 10.0, 10.1)[0] - 0.5) < 1e-9
    # one-sided book: only bids displayed (typical at an upper limit), print at the bid → −1
    assert classify_direction(11.0, 11.0, None)[0] == -1.0
    assert classify_direction(11.2, 11.0, None)[0] is None               # away from the displayed side: unknown
    # no quote → None, never 0
    d, rule, conf = classify_direction(10.0, None, None)
    assert d is None and conf == "none"
    # locked book: exact ±1 from the resting (larger) queue
    d, rule, conf = classify_direction(11.0, 11.0, 11.0, 5000, 100)
    assert d == -1.0 and conf == "exact"
    d, rule, conf = classify_direction(11.0, 11.0, 11.0, 100, 5000)
    assert d == 1.0 and conf == "exact"
    assert classify_direction(11.0, 11.0, 11.0, 100, 100)[0] is None
    # through the engine with a real locked EvolvingBook
    tape = TapeState(tick=0.1)
    locked = _book([(11.0, 5000)], [(11.0, 100)])
    tape.on_trade(_t(1), 11.0, 300, book=locked, source="fix_md")
    ms = _ms(1)
    tape.fill_state(ms, locked)
    assert ms.trade_flow_direction == -1.0 and ms.session_state["tape"]["direction_confidence"] == "exact"
    assert ms.last_print["qty"] == 300 and ms.last_print["inferred_from_delta"] is False
    assert ms.trade_count == 1 and ms.trade_volume == 300 and abs(ms.trade_value - 3300) < 1e-9
    # the quote at the LAST update before the print decides, not a later book
    tape2 = TapeState(tick=0.1)
    early = _book([(10.0, 100)], [(10.1, 50)], t=_t(0))
    tape2.observe_quote(_t(0), early)
    late = _book([(10.2, 100)], [(10.3, 50)], t=_t(10))
    tape2.observe_quote(_t(10), late)
    tape2.on_trade(_t(12), 10.1, 10, book=late, t_exch=_t(5), source="fix_md")   # printed at t=5: vs early book
    assert tape2.preferred_feed().last_row().direction == 1.0                   # 10.1 = early ask → +1


def test_machinery_intensity_and_acceleration_on_synthetic_burst():
    tape = TapeState(tick=0.1)
    book = _book([(10.0, 100)], [(10.1, 50)])
    for s in range(0, 241, 30):                                           # 9 prints, one per 30 s
        tape.on_trade(_t(s), 10.1, 10, book=book, source="fix_md")
    ms = _ms(240)
    tape.fill_state(ms, book)
    # (120, 240] holds prints at 150, 180, 210, 240 → 4 trades / 2 min
    assert abs(ms.trade_intensity - 2.0) < 1e-9
    assert abs(ms.trade_acceleration - 0.0) < 1e-9                        # same rate 120 s earlier
    for s in range(250, 260):                                             # burst: 10 prints in 10 s
        tape.on_trade(_t(s), 10.1, 10, book=book, source="fix_md")
    ms = _ms(259)
    tape.fill_state(ms, book)
    # (139, 259] holds 4 + 10 = 14 trades / 2 min = 7/min; intensity at t=120 was 2/min → +5
    assert abs(ms.trade_intensity - 7.0) < 1e-9
    assert abs(ms.trade_acceleration - 5.0) < 1e-9
    assert ms.volume_only_response == 140.0 and ms.signed_flow_window == 190.0   # (−41, 259]: 19 prints × 10


def test_machinery_price_velocity_acceleration_and_response():
    tape = TapeState(tick=0.1)
    tape.on_mid(_t(0), 10.0)
    ms = _ms(0)
    tape.fill_state(ms)
    assert ms.price_velocity is None and ms.trade_intensity is None and ms.tape_source is None
    tape.on_mid(_t(60), 10.3)
    assert abs(tape.price_velocity() - 3.0) < 1e-9                        # 3 ticks over 60 s
    tape.on_mid(_t(120), 10.3)
    ms = _ms(120)
    tape.fill_state(ms)
    assert abs(ms.price_velocity - 0.0) < 1e-9
    assert abs(ms.price_acceleration - (-3.0)) < 1e-9                     # 0 − 3 over the last 60 s
    assert abs(ms.price_only_response - 3.0) < 1e-9                       # 120-s mid change in ticks
    assert ms.session_state["tape"]["feed"] is None


def test_machinery_price_impact_sign_and_flow_floor():
    def run(direction_price: float, mid_end: float, mixed: bool = False):
        tape = TapeState(tick=0.1)
        book = _book([(10.0, 1000)], [(10.1, 1000)])
        tape.on_mid(_t(0), 10.05)
        for i in range(10):
            px = direction_price if not mixed else (10.1 if i % 2 == 0 else 10.0)
            tape.on_trade(_t(310 + 10 * i), px, 100, book=book, source="fix_md")
        tape.on_mid(_t(400), mid_end)
        ms = _ms(400)
        tape.fill_state(ms, book)
        return ms
    up = run(10.1, 10.25)                       # buys (+1000) and the mid rose 2 ticks → +0.002 ticks/share
    assert abs(up.signed_flow_window - 1000.0) < 1e-9 and abs(up.price_impact - 0.002) < 1e-12
    down = run(10.0, 9.85)                      # sells (−1000) and the mid fell 2 ticks → +0.002 (with the flow)
    assert abs(down.signed_flow_window + 1000.0) < 1e-9 and abs(down.price_impact - 0.002) < 1e-12
    against = run(10.0, 10.25)                  # sells while the mid rose → negative
    assert abs(against.price_impact + 0.002) < 1e-12
    balanced = run(10.1, 10.25, mixed=True)     # net flow 0 < 20 % of volume → not attributable
    assert balanced.signed_flow_window == 0.0 and balanced.price_impact is None


def test_machinery_failed_response_flow_without_price():
    def run(mid_end: float):
        tape = TapeState(tick=0.1)
        book = _book([(10.0, 1000)], [(10.1, 1000)])
        tape.on_mid(_t(0), 10.05)
        for i in range(10):                                               # alternating 10-share prints: tiny |flow|
            tape.on_trade(_t(30 * i), 10.1 if i % 2 == 0 else 10.0, 10, book=book, source="fix_md")
        tape.on_mid(_t(280), 10.05)
        ms = _ms(280)
        tape.fill_state(ms, book)
        assert ms.failed_response is False                                # baseline exists, flow not large
        for i in range(5):                                                # one-sided burst of 500-share buys
            tape.on_trade(_t(300 + 5 * i), 10.1, 500, book=book, source="fix_md")
        tape.on_mid(_t(320), mid_end)
        ms = _ms(320)
        tape.fill_state(ms, book)
        return ms
    flat = run(10.05)
    # (20, 320]: nine alternating prints net −10 plus the 2500 burst
    assert flat.signed_flow_window == 2490.0 and flat.price_only_response == 0.0
    assert flat.failed_response is True
    moved = run(10.35)                                                    # +3 ticks: the flow was followed
    assert abs(moved.price_only_response - 3.0) < 1e-9 and moved.failed_response is False
    # no baseline yet → None, never False
    tape = TapeState(tick=0.1)
    book = _book([(10.0, 1000)], [(10.1, 1000)])
    tape.on_mid(_t(0), 10.05)
    tape.on_trade(_t(10), 10.1, 500, book=book, source="fix_md")
    tape.on_mid(_t(130), 10.05)
    ms = _ms(130)
    tape.fill_state(ms, book)
    assert ms.failed_response is None and ms.price_only_response == 0.0


def test_machinery_day_totals_fallback_and_feed_preference():
    tape = TapeState(tick=0.1)
    book = _book([(10.0, 100)], [(10.1, 50)])
    tape.on_day_totals(_t(0), 10, 1000.0, 0.0101, source="lankabd_depth", book=book)
    tape.on_day_totals(_t(5), 10, 1000.0, 0.0101, source="lankabd_depth", book=book)     # unchanged: no interval
    tape.on_day_totals(_t(10), 12, 1300.0, 0.013130, source="lankabd_depth", book=book)
    ms = _ms(10)
    tape.fill_state(ms, book)
    assert ms.tape_source == "lankabd_depth" and ms.trade_count == 12 and ms.interval_trades == 2
    assert ms.interval_volume == 300 and abs(ms.interval_vwap - 10.1) < 1e-6 and ms.trade_flow_direction == 1.0
    assert ms.session_state["tape"]["repeat_rows"] == 1
    # an exchange-stamped feed appears → it is preferred; the snapshot feed keeps running separately
    tape.on_cum_totals(_t(8), _t(11), 12, 1300.0, 0.013130, 10.1, book=book, source="lankabd_tape")
    ms = _ms(11)
    tape.fill_state(ms, book)
    assert ms.tape_source == "lankabd_tape" and ms.session_state["tape"]["last_first_row"] is True
    assert sorted(ms.session_state["tape"]["feeds"]) == ["cum:lankabd_tape", "snap:lankabd_depth"]
    # nothing observed → None
    assert tape.on_day_totals(_t(12), None, None, None, source="lankabd_depth") is None


def test_machinery_ltp_fallback_from_tape_price():
    tape = TapeState(tick=0.1)
    tape.on_cum_totals(_t(0), _t(0), 1, 10, 0.0001, 10.0, source="lankabd_tape")
    ms = _ms(0)
    tape.fill_state(ms)
    assert ms.ltp == 10.0 and ms.trade_flow_direction is None            # no book: NOT_OBSERVABLE, never 0


# ============================================================================ queue
def _dict_book(bids, asks, bid_orders=None, ask_orders=None):
    return {"bids": bids, "asks": asks, "bid_orders": bid_orders, "ask_orders": ask_orders}


def test_machinery_queue_pull_vs_consumed_split_is_bounded_by_volume():
    q = QueueState()
    q.on_book(_t(0), _dict_book([(10.0, 500), (9.9, 300)], [(10.1, 400)]))
    s = q.on_book(_t(5), _dict_book([(10.0, 300), (9.9, 300)], [(10.1, 400)]))
    assert s["bid"]["kind"] == "drop" and s["ask"]["kind"] == "none"
    ms = _ms(5)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["pending_qty"] == 200 and qb["traded_qty"] == 0 and qb["pulls"] == 0      # provisional
    # tape volume 150 arrives → 150 of the drop was consumed, 50 stays pending
    q.on_book(_t(10), _dict_book([(10.0, 300), (9.9, 300)], [(10.1, 400)]), interval_volume=150)
    q.on_book(_t(12), _dict_book([(10.0, 300), (9.9, 300)], [(10.1, 400)]), interval_volume=150)   # same interval
    ms = _ms(12)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["traded_qty"] == 150 and qb["pending_qty"] == 50 and ms.session_state["queue"]["volume_arrivals"] == 1
    # beyond the lag the remainder is a pull (cancel-like)
    q.on_book(_t(100), _dict_book([(10.0, 300), (9.9, 300)], [(10.1, 400)]))
    ms = _ms(100)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["pulls"] == 1 and qb["pulled_qty"] == 50 and qb["pending_qty"] == 0
    # stack
    s = q.on_book(_t(105), _dict_book([(10.0, 350), (9.9, 300)], [(10.1, 400)]))
    assert s["bid"]["kind"] == "stack"
    # volume that arrived before the drop covers it (bounded by the volume)
    q.on_book(_t(110), _dict_book([(10.0, 350), (9.9, 300)], [(10.1, 400)]), interval_volume=200)
    s = q.on_book(_t(115), _dict_book([(10.0, 350), (9.9, 300)], [(10.1, 100)]))
    assert s["ask"]["kind"] == "drop"
    q.on_book(_t(210), _dict_book([(10.0, 350), (9.9, 300)], [(10.1, 100)]))
    ms = _ms(210)
    q.fill_state(ms)
    qq = ms.session_state["queue"]
    assert qq["ask"]["traded_qty"] == 200 and qq["ask"]["pulled_qty"] == 100 and qq["ask"]["pulls"] == 1
    assert qq["bid"]["stacks"] == 1 and qq["bid"]["stacked_qty"] == 50
    assert qq["bid"]["traded_qty"] + qq["ask"]["traded_qty"] == qq["volume_budgeted"] == 350
    # a retreat of the best is a full drop of the old touch
    s = q.on_book(_t(215), _dict_book([(9.9, 300)], [(10.1, 100)]))
    assert s["bid"]["kind"] == "retreat" and q.sides["bid"].retreats == 1
    assert sum(d["remaining"] for d in q.sides["bid"].pending) == 350
    # depth added / removed of the last update (book engine left them None)
    ms = _ms(215)
    q.fill_state(ms)
    assert ms.depth_removed_bid == 350 and ms.depth_added_bid == 0 and ms.depth_removed_ask == 0


def test_machinery_replenishment_detection():
    q = QueueState()
    q.on_book(_t(0), _dict_book([(10.0, 1000)], [(10.1, 1000)]))
    q.on_book(_t(5), _dict_book([(10.0, 300)], [(10.1, 1000)]))          # ≤ 50 %: episode opens (pre 1000)
    ms = _ms(5)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["depletion_episodes"] == 1 and qb["episode_open"] and ms.liquidity_replenishment == 0.0
    q.on_book(_t(15), _dict_book([(10.0, 500)], [(10.1, 1000)]))
    ms = _ms(15)
    q.fill_state(ms)
    assert abs(ms.liquidity_replenishment - (500 - 300) / (1000 - 300)) < 1e-9
    q.on_book(_t(30), _dict_book([(10.0, 850)], [(10.1, 1000)]))         # ≥ 80 % within 120 s → replenished
    ms = _ms(30)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["replenished"] == 1 and qb["last_time_to_replenish_s"] == 25.0 and not qb["episode_open"]
    assert abs(ms.liquidity_replenishment - (850 - 300) / 700) < 1e-9
    # a second depletion that is not rebuilt within 120 s
    q.on_book(_t(200), _dict_book([(10.0, 100)], [(10.1, 1000)]))
    q.on_book(_t(330), _dict_book([(10.0, 200)], [(10.1, 1000)]))
    ms = _ms(330)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["depletion_episodes"] == 2 and qb["replenished"] == 1 and not qb["episode_open"]
    assert ms.liquidity_replenishment is None                             # no episode within 120 s
    # a retreat counts as a fall to zero and a better price rebuilt counts as replenishment
    q.on_book(_t(400), _dict_book([(9.9, 50)], [(10.1, 1000)]))
    assert q.sides["bid"].episodes == 3 and q.sides["bid"].episode.low == 0.0
    q.on_book(_t(410), _dict_book([(10.0, 900)], [(10.1, 1000)]))
    assert q.sides["bid"].replenished == 2


def test_machinery_depletion_and_retreat():
    q = QueueState()
    book = _dict_book([(10.0, 1000)], [(10.1, 1000)])
    for s in range(0, 121, 10):
        q.on_book(_t(s), book)
    ms = _ms(120)
    q.fill_state(ms)
    assert ms.liquidity_depletion == 0.0 and ms.liquidity_retreat is False and ms.liquidity_vacuum is False
    q.on_book(_t(130), _dict_book([(10.0, 400)], [(10.1, 600)]))         # both touches thinned, no tape volume
    ms = _ms(130)
    q.fill_state(ms)
    assert abs(ms.liquidity_depletion - 0.5) < 1e-9                       # (2000 − 1000) / 2000
    assert ms.liquidity_retreat is False                                  # drops still provisional
    q.on_book(_t(230), _dict_book([(10.0, 400)], [(10.1, 600)]))         # > 90 s: finalised as pulls
    ms = _ms(230)
    q.fill_state(ms)
    qq = ms.session_state["queue"]
    assert qq["bid"]["pulled_qty"] == 600 and qq["ask"]["pulled_qty"] == 400
    assert ms.liquidity_retreat is True
    assert abs(ms.liquidity_depletion - 0.5) < 1e-9
    # the same thinning explained by tape volume is consumption, not retreat
    q2 = QueueState()
    for s in range(0, 121, 10):
        q2.on_book(_t(s), book)
    q2.on_book(_t(125), book, interval_volume=1000)
    q2.on_book(_t(130), _dict_book([(10.0, 400)], [(10.1, 600)]))
    q2.on_book(_t(230), _dict_book([(10.0, 400)], [(10.1, 600)]))
    ms = _ms(230)
    q2.fill_state(ms)
    qq = ms.session_state["queue"]
    assert qq["bid"]["traded_qty"] == 600 and qq["ask"]["traded_qty"] == 400 and qq["bid"]["pulls"] == 0
    assert ms.liquidity_retreat is False
    # before any observation: None everywhere
    ms = _ms(0)
    QueueState().fill_state(ms)
    assert ms.liquidity_depletion is None and ms.liquidity_retreat is None and ms.liquidity_vacuum is None


def test_machinery_vacuum_detection():
    q = QueueState()
    full = _dict_book([(10.0, 1000), (9.9, 1000)], [(10.1, 1000), (10.2, 1000)])
    thin = _dict_book([(10.0, 100)], [(10.1, 100)])
    q.on_book(_t(0), full)
    ms = _ms(0)
    q.fill_state(ms)
    assert ms.liquidity_vacuum is None                                    # no 300-s median yet
    for s in range(10, 301, 10):
        q.on_book(_t(s), full)
    ms = _ms(300)
    q.fill_state(ms)
    assert ms.liquidity_vacuum is False and ms.session_state["queue"]["bid"]["visible_median_300s"] == 2000
    for s in range(310, 361, 10):                                         # collapsed to 5 % on both sides
        q.on_book(_t(s), thin)
        ms = _ms(s)
        q.fill_state(ms)
        assert ms.liquidity_vacuum is False                               # < 60 s of collapse
    q.on_book(_t(370), thin)
    ms = _ms(370)
    q.fill_state(ms)
    assert ms.liquidity_vacuum is True and ms.session_state["queue"]["vacuum_since"] == _t(370).isoformat()
    # a stack at the touch is replenishment: the vacuum verdict is lifted for 60 s
    q.on_book(_t(380), _dict_book([(10.0, 150)], [(10.1, 100)]))
    ms = _ms(380)
    q.fill_state(ms)
    assert ms.liquidity_vacuum is False and ms.session_state["queue"]["vacuum_since"] is None
    for s in range(390, 431, 10):
        q.on_book(_t(s), _dict_book([(10.0, 150)], [(10.1, 100)]))
    assert q.vacuum() is False
    q.on_book(_t(440), _dict_book([(10.0, 150)], [(10.1, 100)]))        # 60 s after the add, still 7.5 % of median
    assert q.vacuum() is True
    # one-sided collapse is not a vacuum
    q.on_book(_t(450), _dict_book([(10.0, 150)], [(10.1, 1000), (10.2, 1000)]))
    assert q.vacuum() is False


def test_machinery_refresh_churn_and_queue_position():
    q = QueueState()
    for i, s in enumerate(range(0, 61, 5)):                               # touch qty flips 100/120 every 5 s
        q.on_book(_t(s), _dict_book([(10.0, 100 if i % 2 == 0 else 120), (9.9, 300)], [(10.1, 50)],
                                    bid_orders=[2, 5], ask_orders=[1]))
    ms = _ms(60)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert qb["best_changes_per_min"] == 12.0 and qb["net_drift_ticks"] == 0.0 and qb["refresh_churn_per_min"] == 12.0
    assert ms.session_state["queue"]["ask"]["refresh_churn_per_min"] == 0.0     # no change at all
    # order counts when carried: queue position and average order size at the touch
    assert qb["touch_orders"] == 2 and qb["avg_order_size_touch"] == 50.0 and qb["orders_ahead_at_touch"] == 2
    assert q.queue_position("bid", 9.9) == {"qty_ahead": 400.0, "orders_ahead": 7, "levels_ahead": 2}
    assert q.queue_position("bid", 9.95) == {"qty_ahead": 100.0, "orders_ahead": 2, "levels_ahead": 1}
    assert q.queue_position("bid", 10.05) == {"qty_ahead": 0.0, "orders_ahead": None, "levels_ahead": 0}   # new best
    assert q.queue_position("ask", 10.1) == {"qty_ahead": 50.0, "orders_ahead": 1, "levels_ahead": 1}
    assert q.queue_position("ask", 10.05) == {"qty_ahead": 0.0, "orders_ahead": None, "levels_ahead": 0}
    # a price move is drift, not churn
    q.on_book(_t(65), _dict_book([(10.1, 100), (10.0, 100), (9.9, 300)], [(10.2, 50)]))
    ms = _ms(65)
    q.fill_state(ms)
    qb = ms.session_state["queue"]["bid"]
    assert abs(qb["net_drift_ticks"] - 1.0) < 1e-9 and qb["refresh_churn_per_min"] == 0.0 and qb["best_changes_per_min"] > 0
    assert qb["touch_orders"] is None and qb["avg_order_size_touch"] is None      # counts no longer carried
    # EvolvingBook levels are read the same way
    q2 = QueueState()
    q2.on_book(_t(0), _book([(10.0, 100, 3)], [(10.1, 50)]))
    assert q2.queue_position("bid", 10.0) == {"qty_ahead": 100.0, "orders_ahead": 3, "levels_ahead": 1}


def test_machinery_queue_accepts_marketstate_and_detects_new_intervals_by_tape_age():
    q = QueueState()
    q.on_book(_ms(0, bids=[(10.0, 500)], asks=[(10.1, 400)]), None)
    q.on_book(_ms(5, bids=[(10.0, 200)], asks=[(10.1, 400)]), None)                         # drop 300
    q.on_book(_ms(10, bids=[(10.0, 200)], asks=[(10.1, 400)], interval_volume=100.0, interval_trades=1.0,
                  interval_vwap=10.0, tape_age_s=1.0), None)
    q.on_book(_ms(20, bids=[(10.0, 200)], asks=[(10.1, 400)], interval_volume=100.0, interval_trades=1.0,
                  interval_vwap=10.0, tape_age_s=11.0), None)                                # same interval, older
    assert q.sides["bid"].traded_qty == 100 and q.volume_arrivals == 1
    q.on_book(_ms(30, bids=[(10.0, 200)], asks=[(10.1, 400)], interval_volume=100.0, interval_trades=1.0,
                  interval_vwap=10.0, tape_age_s=0.5), None)                                 # identical, fresher
    assert q.sides["bid"].traded_qty == 200 and q.volume_arrivals == 2


# ============================================================================ real data
def _fixture_tape():
    r = replay(FIXTURE)
    t = r["tape"]
    assert len(t), "fixture tape is empty"
    return t, r["books"]


def _day_range(books, symbol, rows):
    """Day low / high from the depth snapshots' day summary when carried, else the tape's own prices."""
    b = books[books.symbol == symbol] if len(books) and "symbol" in books.columns else books.iloc[0:0]
    if len(b) and b["low"].notna().any() and b["high"].notna().any():
        return float(b["low"].dropna().min()), float(b["high"].dropna().max()), "depth_day_summary"
    return float(rows.price.min()), float(rows.price.max()), "tape_prices"


@pytest.mark.parametrize("symbol", ["SHARPIND", "MALEKSPIN", "FINEFOODS"])
def test_realdata_fixture_lankabd_tape_cum_totals(symbol):
    """The fixture's exchange-stamped cumulative rows replayed through TapeState: intervals are
    non-negative (no monotone break), the day VWAP and every interval VWAP lie inside the day's
    price range (interval VWAPs up to the 0.001-mn rounding of the source value), the tape clock
    follows the exchange stamps and — with no book (closed market) — direction is None, never 0."""
    t, books = _fixture_tape()
    rows = t[t.symbol == symbol].sort_values(["t_source_ms", "row_index"], kind="mergesort")
    assert len(rows) > 10
    tape = TapeState(tick=0.1)
    lo, hi, range_src = _day_range(books, symbol, rows)
    assert range_src == "depth_day_summary"                                # the fixture carries the day range
    states = []
    for i, r in enumerate(rows.itertuples()):
        t_exch = r.t_source.to_pydatetime()
        t_recv = r.t_recv.to_pydatetime()
        res = tape.on_cum_totals(t_exch, t_recv, r.cum_trades, r.cum_volume, r.cum_value_mn, r.price,
                                 source="lankabd_tape")
        ms = MarketState(symbol=symbol, t=t_recv, tick_size=0.1)
        tape.fill_state(ms)
        states.append(ms)
        assert ms.trade_count == r.cum_trades and ms.trade_volume == r.cum_volume
        assert abs(ms.trade_value - r.cum_value_mn * 1e6) < 1e-3
        assert ms.session_state["tape"]["tape_clock"] == t_exch.isoformat()
        assert ms.trade_flow_direction is None and ms.signed_flow_window is None
        if res is None:
            continue                                                       # repeated totals: clock only
        assert res["first_row"] == (i == 0)
        assert not res["monotone_break"] and res["interval_trades"] >= 0 and res["interval_volume"] >= 0
        if res["interval_vwap"] is not None:
            tol = 0.001 * 1e6 / res["interval_volume"]                     # Δ of two 0.001-mn-rounded totals
            assert lo - tol - 1e-6 <= res["interval_vwap"] <= hi + tol + 1e-6, (i, res)
    last = states[-1]
    day_vwap = last.trade_value / last.trade_volume
    assert lo <= day_vwap <= hi
    # the depth snapshots' day volume agrees with the tape's final cumulative volume (two sources, one truth)
    b = books[books.symbol == symbol]
    if len(b) and b["day_volume"].notna().any():
        assert float(b["day_volume"].dropna().iloc[-1]) == last.trade_volume
    assert last.session_state["tape"]["monotone_breaks"] == 0
    assert last.trade_intensity is not None and last.trade_intensity >= 0
    assert all(s.trade_intensity is None or s.trade_intensity >= 0 for s in states)
    # rate sanity: 120-s intensity never exceeds the day's trades per minute × a burst factor of 100
    n_min = (rows.t_source.max() - rows.t_source.min()).total_seconds() / 60.0
    assert max(s.trade_intensity or 0 for s in states) <= 100 * last.trade_count / max(n_min, 1.0)
    # tape lags the receipt clock by days in this closed-market capture and that is reported, not hidden
    assert last.tape_age_s == 0.0 or last.tape_age_s >= 0
    assert (last.t - t_exch).total_seconds() > 0 and last.session_state["tape"]["rows"] == sum(
        1 for s in states if s is not None) - last.session_state["tape"]["repeat_rows"]


def test_realdata_fixture_tape_is_deterministic_and_windows_use_exchange_clock():
    t, _ = _fixture_tape()
    rows = t[t.symbol == "SHARPIND"].sort_values(["t_source_ms", "row_index"], kind="mergesort")

    def run():
        tape = TapeState(tick=0.1)
        out = []
        for r in rows.itertuples():
            tape.on_cum_totals(r.t_source.to_pydatetime(), r.t_recv.to_pydatetime(), r.cum_trades, r.cum_volume,
                               r.cum_value_mn, r.price, source="lankabd_tape")
            ms = MarketState(symbol="SHARPIND", t=r.t_recv.to_pydatetime(), tick_size=0.1)
            tape.fill_state(ms)
            out.append((ms.trade_intensity, ms.trade_acceleration, ms.interval_volume, ms.volume_only_response))
        return out
    a, b = run(), run()
    assert a == b
    # hand check of one window: intensity at the last row = trades in the trailing 120 s of exchange time
    last_t = rows.t_source.iloc[-1]
    w = rows[(rows.t_source > last_t - timedelta(seconds=120)) & (rows.t_source <= last_t)]
    prev_cum = rows[rows.t_source <= last_t - timedelta(seconds=120)].cum_trades.iloc[-1]
    expected = (w.cum_trades.iloc[-1] - prev_cum) / 2.0
    assert abs(a[-1][0] - expected) < 1e-9
