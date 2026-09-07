"""tower.book — EvolvingBook machinery (synthetic) and real fixture capture."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from seeing.replay import replay
from tower.book import EvolvingBook
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")


def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def _ev(events, price, side=None):
    for e in events:
        if e["price"] == price and (side is None or e["side"] == side):
            return e
    return None


# ---------------------------------------------------------------- snapshots
def test_machinery_snapshot_replacement_keeps_first_seen_for_persisting_prices():
    b = EvolvingBook(tick=0.1)
    ev = b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 200)], [(10.1, 50)])
    assert ev == [] and b.ofi is None and b.added["bid"] is None and b.velocity is None
    lv99 = {l.price: l for l in b.levels("bid")}[9.9]
    assert lv99.first_seen == _t(0)
    # full replace: 10.0 gone, 9.9 persists (qty change), 9.8 new
    ev = b.apply_snapshot(_t(5), [(9.9, 250), (9.8, 10)], [(10.1, 50)])
    assert b.bids() == [(9.9, 250.0), (9.8, 10.0)] and b.asks() == [(10.1, 50.0)]
    lv = {l.price: l for l in b.levels("bid")}
    assert lv[9.9].first_seen == _t(0) and lv[9.9].last_changed == _t(5)
    assert lv[9.8].first_seen == _t(5)
    kinds = {(e["side"], e["price"]): e["kind"] for e in ev}
    assert kinds == {("bid", 10.0): "SWEEP", ("bid", 9.9): "ADD", ("bid", 9.8): "ADD"}
    assert b.added["bid"] == 60 and b.removed["bid"] == 100 and b.added["ask"] == 0 and b.removed["ask"] == 0
    # identical snapshot → no events, unchanged_run grows
    assert b.apply_snapshot(_t(6), [(9.9, 250), (9.8, 10)], [(10.1, 50)]) == []
    assert b.unchanged_run == 1
    b.apply_snapshot(_t(7), [(9.9, 250), (9.8, 10)], [(10.1, 50)])
    assert b.unchanged_run == 2
    b.apply_snapshot(_t(8), [(9.9, 251), (9.8, 10)], [(10.1, 50)])
    assert b.unchanged_run == 0


def test_machinery_snapshot_orders_and_triples():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100, 3)], [(10.1, 50)], ask_orders=[7])
    g = b.geometry()
    assert g["bid_orders"] == [3] and g["ask_orders"] == [7]
    b2 = EvolvingBook(tick=0.1)
    b2.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)], orders=([2], [None]))
    assert b2.geometry()["bid_orders"] == [2] and b2.geometry()["ask_orders"] == [None]
    b3 = EvolvingBook(tick=0.1)
    b3.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)])
    assert b3.geometry()["bid_orders"] is None       # never carried → not observable


# ---------------------------------------------------------------- incremental
def test_machinery_incremental_new_change_delete():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.2, 100)])
    ev = b.apply_update(_t(1), "bid", 9.9, 300, action="NEW")
    assert ev == [{"side": "bid", "price": 9.9, "kind": "ADD", "dq": 300.0, "q_prev": None, "q_cur": 300.0,
                   "at_touch": False, "through": False}]
    assert b.bids() == [(10.0, 100.0), (9.9, 300.0)]
    ev = b.apply_update(_t(2), "ask", 10.2, 40, action="CHANGE")
    assert ev[0]["kind"] == "REDUCE" and ev[0]["dq"] == -60 and ev[0]["at_touch"]
    assert b.removed["ask"] == 60 and b.added["ask"] == 0
    ev = b.apply_update(_t(3), "ask", 10.2, None, action="DELETE")
    assert ev[0]["kind"] == "REMOVE" and b.asks() == []
    ev = b.apply_update(_t(4), "bid", 9.9, 0)                       # qty 0 removes
    assert ev[0]["kind"] == "REMOVE" and b.bids() == [(10.0, 100.0)]
    # level-keyed change: level 1 of the bid side is 10.0
    ev = b.apply_update(_t(5), "B", None, 120, action="CHANGE", level=1)
    assert ev[0]["price"] == 10.0 and ev[0]["kind"] == "ADD" and ev[0]["dq"] == 20
    # order-count-only change leaves qty and records orders
    b.apply_update(_t(6), "bid", 10.0, None, order_count=4, action="CHANGE")
    assert b.bids() == [(10.0, 120.0)] and b.geometry()["bid_orders"] == [4]
    with pytest.raises(ValueError):
        b.apply_update(_t(7), "bid", None, 1)


def test_machinery_level_events_sweep_flags():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 100), (10.2, 200), (10.3, 300)])
    ev = b.apply_snapshot(_t(1), [(10.0, 100)], [(10.3, 300)])
    e1, e2 = _ev(ev, 10.1), _ev(ev, 10.2)
    assert e1["kind"] == "SWEEP" and e1["at_touch"] and e1["through"]
    assert e2["kind"] == "SWEEP" and not e2["at_touch"] and e2["through"]
    assert _ev(ev, 10.3) is None and b.removed["ask"] == 300
    # touch improves (ask steps down): new levels are ADD with through=True, not sweeps
    ev = b.apply_snapshot(_t(2), [(10.0, 100)], [(10.1, 10), (10.3, 300)])
    e = _ev(ev, 10.1)
    assert e["kind"] == "ADD" and e["through"] and not e["at_touch"]
    # bid side sweep: best bid falls through 10.0 and 9.9
    b2 = EvolvingBook(tick=0.1)
    b2.apply_snapshot(_t(0), [(10.0, 50), (9.9, 60), (9.8, 70)], [(10.1, 100)])
    ev = b2.apply_snapshot(_t(1), [(9.8, 70)], [(10.1, 100)])
    assert _ev(ev, 10.0)["kind"] == "SWEEP" and _ev(ev, 10.0)["at_touch"]
    assert _ev(ev, 9.9)["kind"] == "SWEEP" and not _ev(ev, 9.9)["at_touch"]
    # a plain cancel of a non-touch level while the touch is unchanged is REMOVE, not SWEEP
    ev = b2.apply_snapshot(_t(2), [(9.8, 70)], [(10.2, 100)])
    assert _ev(ev, 10.1)["kind"] == "SWEEP"                    # ask touch retreated through 10.1
    b2.apply_snapshot(_t(3), [(9.8, 70), (9.7, 5)], [(10.2, 100)])
    ev = b2.apply_snapshot(_t(4), [(9.8, 70)], [(10.2, 100)])
    assert _ev(ev, 9.7)["kind"] == "REMOVE" and not _ev(ev, 9.7)["through"]


# ---------------------------------------------------------------- OFI
def test_machinery_ofi_sign_and_magnitude_hand_computed():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.3, 100)])
    assert b.ofi is None
    # bid qty at same best price 100 → 150: e = 150 − 100 = +50
    b.apply_update(_t(1), "bid", 10.0, 150)
    assert b.ofi == 50
    # ask qty at same price 100 → 60: e = −60 + 100 = +40
    b.apply_update(_t(2), "ask", 10.3, 60)
    assert b.ofi == 40
    # best bid steps up to 10.1 with 30: Pb up → +qb_n = +30 only (no −qb_{n−1} term)
    b.apply_update(_t(3), "bid", 10.1, 30, action="NEW")
    assert b.ofi == 30
    # best ask steps down to 10.2 with 80: Pa down → −qa_n = −80
    b.apply_update(_t(4), "ask", 10.2, 80, action="NEW")
    assert b.ofi == -80
    # best bid removed (falls back to 10.0 @150): Pb down → −qb_{n−1} = −30
    b.apply_update(_t(5), "bid", 10.1, 0)
    assert b.ofi == -30
    # best ask removed (back to 10.3 @60): Pa up → +qa_{n−1} = +80
    b.apply_update(_t(6), "ask", 10.2, 0)
    assert b.ofi == 80
    # deep level change leaves the touch untouched: e = qb − qb + (−qa + qa) = 0 (computed, not missing)
    b.apply_update(_t(7), "bid", 9.5, 999, action="NEW")
    assert b.ofi == 0
    assert b.ofi_window() == 50 + 40 + 30 - 80 - 30 + 80 + 0
    # window rolls: only the last 60 s count
    b.apply_update(_t(65), "bid", 10.0, 160)          # +10 at t=65; t=1..5 drop out of (5, 65]
    assert b.ofi == 10 and b.ofi_window() == 80 + 0 + 10
    # a side missing at either instant → OFI not observable
    b.apply_update(_t(66), "ask", 10.3, 0)
    assert b.ofi is None and b.asks() == []
    b.apply_update(_t(67), "ask", 10.4, 5, action="NEW")
    assert b.ofi is None


# ---------------------------------------------------------------- geometry
def test_machinery_geometry_hand_built_book():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 100), (9.7, 300)], [(10.1, 300), (10.2, 100), (10.4, 100)])
    g = b.geometry()
    assert g["best_bid"] == 10.0 and g["best_ask"] == 10.1 and g["spread_ticks"] == 1.0 and g["mid"] == 10.05
    assert abs(g["microprice"] - (10.1 * 100 + 10.0 * 300) / 400) < 1e-9
    gb, ga = g["bid"], g["ask"]
    assert [l["dist_ticks"] for l in gb["levels"]] == [0, 1, 3] and [l["cum_qty"] for l in gb["levels"]] == [100, 200, 500]
    assert gb["visible"] == 500 and gb["topk"] == 500 and ga["visible"] == 500
    assert abs(gb["hhi"] - 0.44) < 1e-12 and abs(ga["hhi"] - 0.44) < 1e-12
    assert abs(gb["weighted"] - 225.0) < 1e-9                              # 100/1 + 100/2 + 300/4
    assert abs(ga["weighted"] - (300 + 50 + 25)) < 1e-9
    assert abs(gb["slope"] - 5700 / 42) < 1e-9 and gb["slope"] > 0 and ga["slope"] > 0
    assert gb["curvature"] > 0                    # back-loaded (wall deep) → convex cumulative depth
    assert ga["curvature"] < 0                    # front-loaded (wall at touch) → concave
    assert gb["hollow"] == 1 and ga["hollow"] == 1                         # 9.8 / 10.3 missing
    assert gb["wall"]["price"] == 9.7 and gb["wall"]["qty"] == 300 and abs(gb["wall"]["share"] - 0.6) < 1e-12
    assert gb["wall"]["dist_ticks"] == 3 and gb["wall"]["persistence_s"] == 0.0
    assert gb["wall"]["migrated_ticks"] is None                            # nothing 60 s old yet
    assert abs(gb["mean_dist"] - 2.0) < 1e-12 and abs(ga["mean_dist"] - 0.8) < 1e-12   # (0·300+1·100+3·100)/500
    assert gb["migration"] is None
    # imbalances: equal sizes overall, but the ask depth sits nearer the touch
    assert g["imb_l1"] == (100 - 300) / 400 and g["imb_topk"] == 0.0 and g["depth_ratio"] == 0.5
    assert g["imb_weighted"] == (225 - 375) / 600
    # asymmetry: liq share 0, weighted −0.25, hhi 0, proximity (0.8−2)/(2.8) → mean < 0 (ask side stronger)
    exp = (0.0 + (225 - 375) / 600 + 0.0 + (0.8 - 2.0) / 2.8) / 4
    assert abs(g["side_asymmetry"] - exp) < 1e-12 and g["side_asymmetry"] < 0
    assert not g["crossed"] and not g["locked"] and not g["one_sided"] and not g["empty_book"]


def test_machinery_geometry_flags_and_single_level():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 10)], [(10.0, 5)])
    g = b.geometry()
    assert g["locked"] and not g["crossed"] and g["spread"] == 0
    b.apply_snapshot(_t(1), [(10.1, 10)], [(10.0, 5)])
    assert b.geometry()["crossed"]
    b.apply_snapshot(_t(2), [(10.1, 10)], [])
    g = b.geometry()
    assert g["one_sided"] and not g["empty_book"] and g["spread"] is None and g["mid"] is None
    assert g["imb_l1"] == 1.0 and g["depth_ratio"] == 1.0
    assert g["bid"]["hollow"] == 0 and g["bid"]["slope"] is None and g["bid"]["curvature"] is None
    assert g["bid"]["hhi"] == 1.0 and g["ask"]["hhi"] is None and g["side_asymmetry"] == 1.0


def test_machinery_wall_persistence_and_migration_over_time():
    b = EvolvingBook(tick=0.1)
    bids = [(10.0, 100), (9.9, 100), (9.7, 300)]
    asks = [(10.1, 100)]
    b.apply_snapshot(_t(0), bids, asks)
    b.apply_snapshot(_t(30), [(10.0, 100), (9.9, 100), (9.7, 320)], asks)     # 300 ≥ 50 % of 320 → run continues
    w = b.geometry()["bid"]["wall"]
    assert w["price"] == 9.7 and w["persistence_s"] == 30.0
    b.apply_snapshot(_t(60), [(10.0, 100), (9.9, 100), (9.7, 320)], asks)
    w = b.geometry()["bid"]["wall"]
    assert w["persistence_s"] == 60.0 and w["migrated_ticks"] == 0.0        # same price as 60 s ago
    b.apply_snapshot(_t(70), [(10.0, 100), (9.9, 100), (9.7, 1000)], asks)   # 320 < 500 → run restarts
    w = b.geometry()["bid"]["wall"]
    assert w["persistence_s"] == 0.0 and w["qty"] == 1000
    b.apply_snapshot(_t(100), [(10.0, 100), (9.9, 100), (9.7, 1000)], asks)
    assert b.geometry()["bid"]["wall"]["persistence_s"] == 30.0
    # wall migrates two ticks deeper: 9.7 shrinks, 9.5 becomes the largest level
    b.apply_snapshot(_t(130), [(10.0, 100), (9.9, 100), (9.7, 50), (9.5, 900)], asks)
    g = b.geometry()
    w = g["bid"]["wall"]
    assert w["price"] == 9.5 and w["migrated_ticks"] == -2.0 and w["migrated_dist_ticks"] == 2.0
    assert w["persistence_s"] == 0.0 and w["first_seen"] == _t(130)
    # depth migration: qty-weighted mean distance now vs 60 s ago (t=70 book)
    md_then = (100 * 0 + 100 * 1 + 1000 * 3) / 1200
    md_now = (100 * 0 + 100 * 1 + 50 * 3 + 900 * 5) / 1150
    assert abs(g["bid"]["migration"] - (md_now - md_then)) < 1e-9 and g["bid"]["migration"] > 0
    assert g["ask"]["migration"] == 0.0
    # side empties then refills: no wall while empty; migration None against an empty past side
    b.apply_snapshot(_t(140), bids, [])
    assert b.geometry()["ask"]["wall"] is None and b.geometry()["ask"]["migration"] is None
    b.apply_snapshot(_t(210), bids, asks)
    ga = b.geometry()["ask"]
    assert ga["wall"]["migrated_ticks"] is None and ga["migration"] is None


# ---------------------------------------------------------------- dynamics
def test_machinery_velocity_and_acceleration():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 100)])
    assert b.velocity is None and b.acceleration is None
    b.apply_snapshot(_t(10), [(10.0, 150)], [(10.1, 70)])        # |Δ| = 50 + 30 = 80 over 10 s observed
    assert b.velocity == 80 / 10 and b.acceleration is None
    b.apply_snapshot(_t(30), [(10.0, 150)], [(10.1, 70)])        # no change: 80 over 30 s
    assert abs(b.velocity - 80 / 30) < 1e-12
    b.apply_snapshot(_t(60), [(10.0, 150), (9.9, 40)], [(10.1, 70)])   # +40 → 120 over 60 s
    assert b.velocity == 2.0 and b.acceleration is None            # no velocity point at or before t=0
    b.apply_snapshot(_t(70), [(10.0, 150), (9.9, 40)], [(10.1, 70)])
    # window (10, 70]: updates at 30 (0) and 60 (40) → 40/60
    assert abs(b.velocity - 40 / 60) < 1e-12
    # acceleration vs velocity at or before t=10 (8.0): (0.667 − 8)/60
    assert abs(b.acceleration - (40 / 60 - 8.0) / 60) < 1e-12
    b.apply_snapshot(_t(200), [(10.0, 150), (9.9, 40)], [(10.1, 70)])
    assert b.velocity == 0.0                                      # nothing changed in the last 60 s
    assert b.acceleration < 0


def test_machinery_none_on_empty_book_never_zero_imbalance():
    b = EvolvingBook(tick=0.1)
    g = b.geometry()
    for k in ("best_bid", "best_ask", "spread", "mid", "microprice", "imb_l1", "imb_topk", "imb_weighted",
              "visible_bid_liq", "visible_ask_liq", "depth_ratio", "side_asymmetry", "book_change_velocity",
              "book_change_acceleration", "ofi", "ofi_window", "depth_added_bid", "depth_removed_ask"):
        assert g[k] is None, k
    assert g["empty_book"] and not g["one_sided"] and not g["crossed"] and not g["locked"]
    assert g["bid"]["hhi"] is None and g["bid"]["hollow"] is None and g["bid"]["wall"] is None
    b.apply_snapshot(_t(0), [], [])
    b.apply_snapshot(_t(5), [], [])
    g = b.geometry()
    assert g["empty_book"] and g["imb_l1"] is None and g["imb_topk"] is None and g["imb_weighted"] is None
    assert g["depth_ratio"] is None and g["ofi"] is None and g["side_asymmetry"] is None
    assert g["book_change_velocity"] == 0.0 and g["depth_added_bid"] == 0.0   # two observations, no change
    ms = MarketState(symbol="X", t=_t(6))
    b.fill_state(ms)
    assert ms.empty_book and ms.imb_l1 is None and ms.imb_topk is None and ms.imb_weighted is None
    assert ms.depth_concentration_bid is None and ms.wall_bid is None and ms.hollow_ask is None
    assert ms.book_age_s == 1.0 and ms.tick_size == 0.1 and ms.bids == [] and ms.bid_orders is None


def test_machinery_fill_state_writes_every_field():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 100), (9.7, 300)], [(10.1, 300), (10.2, 100), (10.4, 100)])
    b.apply_update(_t(1), "bid", 10.0, 120)
    ms = MarketState(symbol="X", t=_t(1))
    g = b.fill_state(ms)
    assert ms.best_bid == 10.0 and ms.bid_qty1 == 120 and ms.spread_ticks == 1.0 and ms.mid == 10.05
    assert ms.bids == [(10.0, 120.0), (9.9, 100.0), (9.7, 300.0)] and ms.asks[0] == (10.1, 300.0)
    assert ms.imb_l1 == g["imb_l1"] and ms.imb_topk == g["imb_topk"] and ms.imb_weighted == g["imb_weighted"]
    assert ms.visible_bid_liq == 520 and ms.visible_ask_liq == 500 and abs(ms.depth_ratio - 520 / 1020) < 1e-12
    assert ms.depth_concentration_bid == g["bid"]["hhi"] and ms.depth_slope_ask == g["ask"]["slope"]
    assert ms.depth_curvature_bid > 0 and ms.depth_curvature_ask < 0
    assert ms.hollow_bid == 1 and ms.hollow_ask == 1
    assert ms.wall_bid["price"] == 9.7 and ms.wall_ask["price"] == 10.1 and ms.wall_bid["migrated_ticks"] is None
    assert ms.depth_migration_bid is None and ms.side_asymmetry == g["side_asymmetry"]
    assert ms.book_change_velocity == 20.0 and ms.book_change_acceleration is None
    assert ms.depth_added_bid == 20 and ms.depth_removed_bid == 0 and ms.depth_added_ask == 0
    assert ms.ofi == 20 and ms.ofi_window == 20 and ms.book_age_s == 0.0
    assert not ms.empty_book and not ms.one_sided
    ms.to_dict()                                   # wire format must serialise (datetimes in walls are dropped)


# ---------------------------------------------------------------- contract shape / input discipline
def test_machinery_contract_positional_orders_and_keyword_precedence():
    # CONTRACTS: apply_snapshot(t, bids, asks, orders=None) — the 4th positional is ``orders``
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)], ([3], [7]))
    g = b.geometry()
    assert g["bid_orders"] == [3] and g["ask_orders"] == [7]
    b2 = EvolvingBook(tick=0.1)
    b2.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)], {"bid": [4], "ask": None})
    assert b2.geometry()["bid_orders"] == [4] and b2.geometry()["ask_orders"] == [None]
    # explicit keyword lists win over the combined argument
    b3 = EvolvingBook(tick=0.1)
    b3.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)], ([3], [7]), bid_orders=[9])
    assert b3.geometry()["bid_orders"] == [9] and b3.geometry()["ask_orders"] == [7]


def test_machinery_side_codes_and_unknown_side_rejected():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 50)])
    b.apply_update(_t(1), "0", 9.9, 10, action="NEW")          # FIX MDEntryType 0 = bid
    b.apply_update(_t(2), "1", 10.2, 10, action="NEW")         # FIX MDEntryType 1 = offer
    b.apply_update(_t(3), "BUY", 9.8, 10, action="NEW")
    b.apply_update(_t(4), "Sell", 10.3, 10, action="NEW")
    assert b.bids() == [(10.0, 100.0), (9.9, 10.0), (9.8, 10.0)]
    assert b.asks() == [(10.1, 50.0), (10.2, 10.0), (10.3, 10.0)]
    # Event.side may be None — that must never silently become an ask update
    for bad in (None, "", "none", "x"):
        with pytest.raises(ValueError):
            b.apply_update(_t(5), bad, 9.7, 10, action="NEW")
    assert b.n_updates == 5 and b.asks()[-1] == (10.3, 10.0)     # nothing applied by the rejected calls
    # non-finite price / qty on an update is an error, not a level
    with pytest.raises(ValueError):
        b.apply_update(_t(6), "bid", float("nan"), 10)
    with pytest.raises(ValueError):
        b.apply_update(_t(6), "bid", 9.7, float("inf"))
    # an unrecognised action (FIX adapter emits "UNKNOWN") is governed by the qty rule
    b.apply_update(_t(7), "bid", 9.7, 5, action="UNKNOWN")
    assert (9.7, 5.0) in b.bids()
    b.apply_update(_t(8), "bid", 9.7, 0, action="UNKNOWN")
    assert (9.7, 5.0) not in b.bids()


def test_machinery_snapshot_drops_non_finite_rows_and_sums_duplicates():
    b = EvolvingBook(tick=0.1)
    nan, inf = float("nan"), float("inf")
    b.apply_snapshot(_t(0), [(nan, 100), (10.0, 60), (10.0, 40), (9.9, nan), (9.8, inf), (9.7, -5), (9.6, 0)],
                     [(None, 5), (10.1, 50)])
    assert b.bids() == [(10.0, 100.0)] and b.asks() == [(10.1, 50.0)]
    assert b.geometry()["bid"]["hollow"] == 0


def test_machinery_order_count_not_carried_forward_across_qty_change():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100, 3)], [(10.1, 50, 2)])
    assert b.geometry()["bid_orders"] == [3]
    # a later image with a new size and no order count: the count of the old size is not observed any more
    b.apply_snapshot(_t(1), [(10.0, 500)], [(10.1, 50)])
    assert b.geometry()["bid_orders"] == [None] and b.geometry()["ask_orders"] == [2]   # ask unchanged → kept
    b.apply_update(_t(2), "ask", 10.1, 80)                       # incremental change w/o count → None
    assert b.geometry()["ask_orders"] == [None]
    b.apply_update(_t(3), "ask", 10.1, 90, order_count=6)
    assert b.geometry()["ask_orders"] == [6]
    # an order-count-only change is a change of the displayed book: unchanged_run resets
    b.apply_snapshot(_t(4), [(10.0, 500)], [(10.1, 90, 6)])
    assert b.unchanged_run == 1
    b.apply_update(_t(5), "ask", 10.1, None, order_count=7, action="CHANGE")
    assert b.unchanged_run == 0 and b.last_events == [] and b.added["ask"] == 0.0


def test_machinery_wall_distance_migration_measured_against_touch_then():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.7, 300)], [(10.1, 10)])
    # the wall stays at 9.7 but the touch walks up two ticks: price migration 0, distance migration +2
    b.apply_snapshot(_t(70), [(10.2, 100), (9.7, 300)], [(10.3, 10)])
    w = b.geometry()["bid"]["wall"]
    assert w["price"] == 9.7 and w["migrated_ticks"] == 0.0 and w["migrated_dist_ticks"] == 2.0
    # the wall moves down one tick while the touch stays: both −1 in price, +1 in distance
    b.apply_snapshot(_t(140), [(10.2, 100), (9.6, 300)], [(10.3, 10)])
    w = b.geometry()["bid"]["wall"]
    assert w["migrated_ticks"] == -1.0 and w["migrated_dist_ticks"] == 1.0
    # ask side: wall at 10.5 while the ask touch steps down one tick → distance +1, price 0
    a = EvolvingBook(tick=0.1)
    a.apply_snapshot(_t(0), [(10.0, 10)], [(10.2, 50), (10.5, 900)])
    a.apply_snapshot(_t(61), [(10.0, 10)], [(10.1, 50), (10.5, 900)])
    w = a.geometry()["ask"]["wall"]
    assert w["migrated_ticks"] == 0.0 and w["migrated_dist_ticks"] == 1.0


def test_machinery_long_window_trackers_retain_lookback():
    # a window longer than the default 600 s tracker retention must still find its W-old points
    W = 1200.0
    b = EvolvingBook(tick=0.1, window_s=W)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.7, 300)], [(10.1, 10)])
    for i in range(1, 40):                                   # 39 observations spread over 1170 s
        b.apply_snapshot(_t(30 * i), [(10.0, 100), (9.7, 300)], [(10.1, 10)])
    b.apply_snapshot(_t(1230), [(10.0, 100), (9.5, 300)], [(10.1, 10)])
    g = b.geometry()
    assert g["bid"]["wall"]["migrated_ticks"] == -2.0 and g["bid"]["wall"]["migrated_dist_ticks"] == 2.0
    assert abs(g["bid"]["migration"] - (5 * 300 / 400 - 3 * 300 / 400)) < 1e-9
    assert b.acceleration is not None


def test_machinery_acceleration_uses_actual_elapsed_time():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 100)])
    b.apply_snapshot(_t(10), [(10.0, 160)], [(10.1, 100)])        # v = 60/10 = 6
    assert b.velocity == 6.0 and b.acceleration is None
    # a sparse feed: the next observation is 190 s later; the reference velocity is the one at t=10
    b.apply_snapshot(_t(200), [(10.0, 190)], [(10.1, 100)])       # v = 30/60 = 0.5
    assert b.velocity == 0.5
    assert abs(b.acceleration - (0.5 - 6.0) / 190) < 1e-12        # Δv over the true 190 s, not a nominal 60 s


def test_machinery_first_observation_via_update_and_duplicate_timestamps():
    b = EvolvingBook(tick=0.1)
    # first observation arrives as an incremental update: no predecessor → nothing diffed
    ev = b.apply_update(_t(0), "bid", 10.0, 100, action="NEW")
    assert ev == [] and b.ofi is None and b.added == {"bid": None, "ask": None} and b.velocity is None
    assert b.geometry()["one_sided"] and b.geometry()["imb_l1"] == 1.0
    # same-timestamp second observation: a diff exists but no time has elapsed → velocity unobservable
    ev = b.apply_update(_t(0), "ask", 10.2, 50, action="NEW")
    assert [e["kind"] for e in ev] == ["ADD"] and b.added["ask"] == 50 and b.ofi is None
    assert b.velocity is None and b.acceleration is None
    b.apply_update(_t(0), "bid", 10.0, 80)
    assert b.ofi == 80 - 100 and b.removed["bid"] == 20 and b.velocity is None
    # time advances: all three same-instant |Δqty| (50 + 20) count in the window
    b.apply_update(_t(5), "bid", 10.0, 80)
    assert b.last_events == [] and b.unchanged_run == 1
    assert b.velocity == 70 / 5
    # a deep negative delta is REDUCE with a negative dq, not an ADD of |dq|
    b.apply_update(_t(6), "ask", 10.2, 5)
    e = b.last_events[0]
    assert e["kind"] == "REDUCE" and e["dq"] == -45.0 and b.removed["ask"] == 45.0 and b.added["ask"] == 0.0
    assert b.ofi == -5 + 50                                     # Pa same: −qa_n + qa_{n−1}


def test_machinery_fill_state_is_deterministic_and_causal():
    def build(reverse):
        b = EvolvingBook(tick=0.1)
        bids = [(10.0, 100), (9.9, 100), (9.7, 300), (9.6, 20)]
        asks = [(10.1, 300), (10.2, 100), (10.4, 100)]
        frames = [(bids, asks), (bids[:3] + [(9.6, 25)], asks), (bids[:3] + [(9.6, 25)], asks[1:])]
        for s, (bb, aa) in zip((0, 30, 61), frames):
            if reverse:                                          # feed order must not matter
                bb, aa = list(reversed(bb)), list(reversed(aa))
            b.apply_snapshot(_t(s), bb, aa)
        ms = MarketState(symbol="X", t=_t(61))
        b.fill_state(ms)
        # ask wall: 10.1@300 at t=0; at t=61 a 100/100 tie between 10.2 and 10.4 → nearest the touch (10.2)
        assert ms.wall_ask["price"] == 10.2 and ms.wall_ask["migrated_ticks"] == 1.0     # 10.1 → 10.2 over 61 s
        assert ms.wall_ask["migrated_dist_ticks"] == 0.0                                # at the touch both times
        # bid unchanged (+100 −100), ask touch rose 10.1 → 10.2: +qa_prev = +300
        assert ms.depth_added_bid == 0.0 and ms.depth_removed_ask == 300.0 and ms.ofi == 300
        return ms.state_hash()
    assert build(False) == build(True)


def test_machinery_wall_persistence_exact_over_many_changes():
    # a busy level that changes far more often than any bounded history would hold: persistence must
    # still date back to the true run start (the old 512-entry deque reported 511 s here)
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.7, 1000)], [(10.1, 10)])
    for i in range(1, 601):
        b.apply_snapshot(_t(i), [(10.0, 100), (9.7, 1000 + (i % 2))], [(10.1, 10)])
    w = b.geometry()["bid"]["wall"]
    assert w["price"] == 9.7 and w["persistence_s"] == 600.0
    lv = {l.price: l for l in b.levels("bid")}[9.7]
    assert len(lv.history) <= 2                                   # suffix-minimum staircase, not 601 entries
    # a dip below 50 % of the eventual size restarts the run at the dip's end, exactly
    b.apply_snapshot(_t(700), [(10.0, 100), (9.7, 400)], [(10.1, 10)])
    b.apply_snapshot(_t(710), [(10.0, 100), (9.7, 1000)], [(10.1, 10)])
    for i in range(711, 1311):
        b.apply_snapshot(_t(i), [(10.0, 100), (9.7, 1000 + (i % 3))], [(10.1, 10)])
    w = b.geometry()["bid"]["wall"]
    assert w["persistence_s"] == 1310.0 - 710.0
    # the run start is the earliest instant the bound held: 1000 → 300 → 1000 → 1000 → threshold 500 → since the
    # recovery; 1000 → 600 → 1000 (600 ≥ 500) → since the first observation
    a = EvolvingBook(tick=0.1)
    a.apply_snapshot(_t(0), [(10.0, 1000)], [(10.1, 10)])
    a.apply_snapshot(_t(10), [(10.0, 600)], [(10.1, 10)])
    a.apply_snapshot(_t(20), [(10.0, 1000)], [(10.1, 10)])
    a.apply_snapshot(_t(50), [(10.0, 1000)], [(10.1, 10)])
    assert a.geometry()["bid"]["wall"]["persistence_s"] == 50.0
    a.apply_snapshot(_t(60), [(10.0, 300)], [(10.1, 10)])
    a.apply_snapshot(_t(70), [(10.0, 1000)], [(10.1, 10)])
    a.apply_snapshot(_t(90), [(10.0, 1000)], [(10.1, 10)])
    assert a.geometry()["bid"]["wall"]["persistence_s"] == 20.0


def test_machinery_level_keyed_new_without_price_does_not_overwrite():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 50)], [(10.1, 10)])
    # an insertion at rank 1 whose price was not delivered cannot be placed: the book must not change
    ev = b.apply_update(_t(1), "bid", None, 77, action="NEW", level=1)
    assert ev == [] and b.bids() == [(10.0, 100.0), (9.9, 50.0)] and b.n_updates == 2 and b.unchanged_run == 1
    # a level-keyed DELETE / CHANGE acts on the level displayed at that rank
    ev = b.apply_update(_t(2), "bid", None, None, action="DELETE", level=2)
    assert ev[0]["price"] == 9.9 and ev[0]["kind"] == "REMOVE" and b.bids() == [(10.0, 100.0)]
    ev = b.apply_update(_t(3), "bid", None, 120, action="CHANGE", level=1)
    assert ev[0]["price"] == 10.0 and b.bids() == [(10.0, 120.0)]
    # a rank beyond the displayed range with no price: counted, nothing changes
    assert b.apply_update(_t(4), "bid", None, 5, action="CHANGE", level=9) == [] and b.bids() == [(10.0, 120.0)]
    # with a price the NEW is placed normally (level is informational)
    b.apply_update(_t(5), "bid", 10.05, 7, action="NEW", level=1)
    assert b.bids() == [(10.05, 7.0), (10.0, 120.0)]


def test_machinery_lookback_references_survive_long_gaps_and_fast_feeds():
    # sparse feed: the acceleration / migration references are the last points at or before t − W, however
    # old — they must not be dropped by a retention cap (the old 600 s cap returned None here)
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100), (9.7, 300)], [(10.1, 100)])
    b.apply_snapshot(_t(10), [(10.0, 160), (9.7, 300)], [(10.1, 100)])       # v = 60 / 10 = 6
    b.apply_snapshot(_t(2000), [(10.0, 190), (9.5, 300)], [(10.1, 100)])     # |Δ| = 30 + 300 + 300 → v = 630 / 60
    assert b.velocity == 10.5 and abs(b.acceleration - (10.5 - 6.0) / 1990) < 1e-15
    g = b.geometry()
    assert g["bid"]["wall"]["migrated_ticks"] == -2.0 and g["bid"]["wall"]["migrated_dist_ticks"] == 2.0
    assert abs(g["bid"]["migration"] - (5 * 300 / 490 - 3 * 300 / 460)) < 1e-12
    # fast feed: 6000 updates inside one 60 s window — the window sums must cover all of them
    # (a 5000-point ring would silently shorten the window)
    f = EvolvingBook(tick=0.1)
    f.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 100)])
    for i in range(1, 6001):
        f.apply_update(_t(i * 0.005), "bid", 10.0, 100 + (i % 2))            # |Δ| = 1 per update, e_n = ±1
    assert f.velocity == 6000 / 30.0                                          # 6000 over the 30 s observed
    assert f.ofi_window() == 0.0 and f.geometry()["ofi_window_n"] == 6000
    # after the window rolls, only the updates inside (t − 60, t] remain
    f.apply_update(_t(65), "bid", 10.0, 100)                                  # unchanged (|Δ| = 0, e = 0)
    assert f.geometry()["ofi_window_n"] == 6000 - 1000 + 1 and abs(f.velocity - 5000 / 60) < 1e-12
    # the running window sum shows no drift after roll-off: nothing changed in the last 60 s → exactly 0
    f.apply_update(_t(200), "bid", 10.0, 100)
    assert f.velocity == 0.0 and f.ofi_window() == 0.0 and f.geometry()["ofi_window_n"] == 1


def test_machinery_ofi_window_reports_unobservable_steps():
    b = EvolvingBook(tick=0.1)
    b.apply_snapshot(_t(0), [(10.0, 100)], [(10.1, 100)])
    b.apply_update(_t(1), "bid", 10.0, 150)                                  # e = +50
    g = b.geometry()
    assert g["ofi_window"] == 50 and g["ofi_window_n"] == 1 and g["ofi_window_missing"] == 0
    b.apply_snapshot(_t(2), [(10.0, 150)], [])                              # ask side vanishes: step unobservable
    b.apply_snapshot(_t(3), [(10.0, 150)], [(10.2, 30)])                    # returns: still unobservable
    g = b.geometry()
    assert b.ofi is None and g["ofi_window"] == 50 and g["ofi_window_missing"] == 2 and g["ofi_window_n"] == 1
    # once the only observable step rolls out of the window the sum is None, not 0 (the t=2 step rolls out too)
    b.apply_snapshot(_t(62), [(10.0, 150)], [])
    g = b.geometry()
    assert g["ofi_window"] is None and g["ofi_window_n"] == 0 and g["ofi_window_missing"] == 2
    # the closed-market case: never observable → None throughout, missing counts every step
    c = EvolvingBook(tick=0.1)
    c.apply_snapshot(_t(0), [(10.0, 1)], [])
    c.apply_snapshot(_t(1), [(10.0, 1)], [])
    assert c.ofi_window() is None and c.geometry()["ofi_window_missing"] == 1


def test_machinery_flat_orders_argument_is_rejected():
    b = EvolvingBook(tick=0.1)
    with pytest.raises(ValueError):
        b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 50)], [(10.1, 10)], orders=[3, 4])
    assert b.n_updates == 0 and b.bids() == []                                # nothing applied
    b.apply_snapshot(_t(0), [(10.0, 100), (9.9, 50)], [(10.1, 10)], orders=([3, 4], None))
    assert b.geometry()["bid_orders"] == [3, 4] and b.geometry()["ask_orders"] == [None]


# ---------------------------------------------------------------- real data
def test_realdata_fixture_depth_snapshots():
    tables = replay(FIXTURE)
    books = tables["books"]
    assert len(books) == 14
    seen = {}
    for src in ("lankabd_depth", "dsebd_depth"):
        for sym, g in books[books["source"] == src].groupby("symbol"):
            b = EvolvingBook(tick=0.1)
            for _, r in g.sort_values(["t_recv", "seq"]).iterrows():
                b.apply_snapshot(r["t_recv"].to_pydatetime(), r["bid_levels"], r["ask_levels"])
                ms = MarketState(symbol=sym, t=r["t_recv"].to_pydatetime())
                b.fill_state(ms)
                # closed market: no two-sided book anywhere → no spread / mid / OFI
                assert ms.spread is None and ms.mid is None and ms.microprice is None and ms.ofi is None
                assert ms.bid_orders is None and ms.ask_orders is None        # never carried by these sources
                if not r["bid_levels"] and not r["ask_levels"]:
                    assert ms.empty_book and not ms.one_sided
                    assert ms.imb_l1 is None and ms.imb_topk is None and ms.imb_weighted is None
                    assert ms.depth_ratio is None and ms.side_asymmetry is None
                    assert ms.visible_bid_liq is None and ms.wall_bid is None and ms.hollow_bid is None
                else:
                    assert ms.one_sided and not ms.empty_book
                    assert ms.imb_l1 in (1.0, -1.0)
                ms.to_dict()
            seen[(src, sym)] = b
    # SHARPIND is empty on both sensors; FINEFOODS bid-only; MALEKSPIN ask-only
    assert seen[("lankabd_depth", "SHARPIND")].geometry()["empty_book"]
    assert seen[("dsebd_depth", "SHARPIND")].geometry()["empty_book"]
    ff = seen[("lankabd_depth", "FINEFOODS")].geometry()
    assert ff["bids"] == [(461.8, 28.0), (461.0, 53.0)] and ff["asks"] == [] and ff["imb_l1"] == 1.0
    assert ff["bid"]["wall"]["price"] == 461.0 and ff["bid"]["hollow"] == 7      # 461.7 … 461.1 missing
    assert ff["unchanged_run"] == 1 and ff["book_change_velocity"] == 0.0
    mk = seen[("dsebd_depth", "MALEKSPIN")].geometry()
    assert mk["asks"] == [(51.3, 1000.0)] and mk["imb_l1"] == -1.0 and mk["depth_ratio"] == 0.0
    assert mk["unchanged_run"] == 2
