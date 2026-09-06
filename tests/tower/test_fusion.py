"""tower.fusion — synthetic multi-source scenarios (machinery) and the real closed-market fixture."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tower.events import Event, EventType
from tower.fusion import Fuser, QUOTE_FIELDS
from tower.normalize import normalize_store
from tower.state import MarketState

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
T0 = datetime(2026, 9, 6, 4, 15, 0, tzinfo=timezone.utc)

_SEQ = {}


def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def _seq(source: str) -> int:
    n = _SEQ.get(source, 0)
    _SEQ[source] = n + 1
    return n


def snap(source, symbol, s, bids, asks, ltp=None, trades=None, volume=None, value=None, dup=False, zero=()):
    payload = {"bids": bids, "asks": asks, "ltp": ltp, "day_trades": trades, "day_volume": volume,
               "day_value_mn": value, "zero_fields": list(zero)}
    observed = ["t_recv", "bid_levels", "ask_levels"]
    for k, canon in (("ltp", "ltp"), ("day_trades", "day_trades"), ("day_volume", "day_volume"),
                     ("day_value_mn", "day_value")):
        if payload[k] is not None:
            observed.append(canon)
    return Event(source=source, event_type=EventType.BOOK_SNAPSHOT, t_recv=_t(s), seq_local=_seq(source),
                 symbol=symbol, session_phase="CONTINUOUS", is_snapshot=True, price=ltp, payload=payload,
                 observed_fields=tuple(observed), flags={"duplicate": True} if dup else {})


def quote(source, symbol, s, ltp=None, trades=None, volume=None, value=None, t_exch=None, unchanged=False):
    payload = {"ltp": ltp, "day_trades": trades, "day_volume": volume, "day_value_mn": value}
    observed = ["t_recv"] + (["t_source"] if t_exch else [])
    for k, canon in (("ltp", "ltp"), ("day_trades", "day_trades"), ("day_volume", "day_volume"),
                     ("day_value_mn", "day_value")):
        if payload[k] is not None:
            observed.append(canon)
    return Event(source=source, event_type=EventType.QUOTE, t_recv=_t(s), seq_local=_seq(source), symbol=symbol,
                 t_exch=t_exch, session_phase="CONTINUOUS", price=ltp, payload=payload,
                 observed_fields=tuple(observed), flags={"unchanged": True} if unchanged else {})


def cum(source, symbol, s, price, trades, volume, value, t_exch_s=None):
    return Event(source=source, event_type=EventType.CUM_TOTALS, t_recv=_t(s), seq_local=_seq(source),
                 symbol=symbol, t_exch=_t(t_exch_s) if t_exch_s is not None else None, session_phase="CONTINUOUS",
                 price=price, payload={"cum_trades": trades, "cum_volume": volume, "cum_value_mn": value,
                                       "price": price},
                 observed_fields=("t_recv", "t_source", "day_trades", "day_volume", "day_value", "ltp"))


def gap(source, symbol, s):
    return Event(source=source, event_type=EventType.GAP, t_recv=_t(s), seq_local=_seq(source), symbol=symbol,
                 status="http_error", payload={"reason": "http_error"}, flags={"gap": True})


def unparsed(source, symbol, s):
    """The page answered but showed no book (layout change / error page): liveness, not an image."""
    return Event(source=source, event_type=EventType.STATUS, t_recv=_t(s), seq_local=_seq(source), symbol=symbol,
                 status="book_unparsed", payload={"kind": "book_unparsed", "sides_missing": ["bid", "ask"]},
                 flags={"parse_problem": True}, observed_fields=("t_recv",))


def update(source, symbol, s, side, price, qty):
    return Event(source=source, event_type=EventType.BOOK_UPDATE, t_recv=_t(s), seq_local=_seq(source),
                 symbol=symbol, session_phase="CONTINUOUS", side=side, price=price, qty=qty,
                 payload={"action": "CHANGE"}, observed_fields=("t_recv", "bid_levels", "ask_levels"))


def trade(source, symbol, s, price, qty, t_exch_s=None):
    return Event(source=source, event_type=EventType.TRADE, t_recv=_t(s), seq_local=_seq(source), symbol=symbol,
                 t_exch=_t(t_exch_s) if t_exch_s is not None else None, session_phase="CONTINUOUS",
                 price=price, qty=qty, payload={}, observed_fields=("t_recv", "trade_prints"))


def fill(f: Fuser, symbol: str, s: float) -> MarketState:
    ms = MarketState(symbol=symbol, t=_t(s))
    f.fill_state(ms, _t(s))
    return ms


BIDS = [(10.0, 100.0), (9.9, 300.0), (9.8, 250.0)]
ASKS = [(10.1, 80.0), (10.2, 400.0)]


# ---------------------------------------------------------------- two-source agreement
def test_machinery_two_source_agreement_book_and_quote():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.1, trades=50, volume=5000, value=0.05))
    # only one source yet: no comparison possible, agreement carries no 'book' key (not a silent True)
    bids, asks, src, agr, dis = f.fuse_book("SYN", _t(0))
    assert (bids, asks, src) == (BIDS, ASKS, "lankabd_depth") and agr == {} and dis == {}
    f.on_event(snap("dsebd_depth", "SYN", 2, BIDS, ASKS, ltp=10.1, trades=50, volume=5000, value=0.05))
    bids, asks, src, agr, dis = f.fuse_book("SYN", _t(2))
    assert src == "lankabd_depth"            # first primary is kept while both sensors are concurrent
    assert agr == {"book": True} and dis == {}
    values, prov, qagr, qdis = f.fuse_quote("SYN", _t(2))
    assert values == {"ltp": 10.1, "day_trades": 50.0, "day_volume": 5000.0, "day_value": 0.05}
    assert prov == {fld: "dsebd_depth" for fld in QUOTE_FIELDS}     # freshest receipt of each field
    assert qagr == {fld: True for fld in QUOTE_FIELDS} and qdis == {}
    ms = fill(f, "SYN", 2)
    assert ms.source_agreement == {"book": True, "ltp": True, "day_trades": True, "day_volume": True, "day_value": True}
    assert ms.source_disagreement == {}
    assert ms.book_source == "lankabd_depth" and ms.book_age_s == 2.0
    assert ms.provenance["book"] == "lankabd_depth" and ms.provenance["best_bid"] == "lankabd_depth"
    assert ms.provenance["ltp"] == "dsebd_depth"
    assert ms.bids == BIDS and ms.best_bid == 10.0 and ms.best_ask == 10.1 and ms.empty_book is False
    assert ms.ltp == 10.1 and ms.trade_count == 50 and ms.trade_volume == 5000 and ms.trade_value == 0.05
    for s in ("lankabd_depth", "dsebd_depth"):
        assert ms.sources[s].agreement == {"book": True, "ltp": True, "day_trades": True, "day_volume": True,
                                           "day_value": True}
        assert ms.sources[s].disagreement == {}
    assert ms.sources["lankabd_depth"].freshness_s == 2.0 and ms.sources["dsebd_depth"].freshness_s == 0.0
    # the state serialises (tuples inside disagreement / status are JSON-able)
    json.dumps(ms.to_dict())


def test_machinery_disagreement_exposes_both_values_and_sources():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.1, trades=50, volume=5000, value=0.05))
    assert f.primary_book_source("SYN", _t(0)) == "lankabd_depth"        # the engine asks at every frame
    other_bids = [(10.0, 100.0), (9.9, 280.0), (9.8, 250.0)]
    other_asks = [(10.1, 80.0)]
    # dsebd (fresher → quote primary): one bid qty differs, one ask level missing; ltp differs;
    # trades 60 vs lankabd 50 (other lags 17 % → beyond tolerance); volume 4900 vs 5000 (other LEADS);
    # value equal
    f.on_event(snap("dsebd_depth", "SYN", 3, other_bids, other_asks, ltp=10.2, trades=60, volume=4900, value=0.05))
    ms = fill(f, "SYN", 3)
    assert ms.book_source == "lankabd_depth"
    assert ms.source_agreement == {"book": False, "ltp": False, "day_trades": False, "day_volume": False,
                                   "day_value": True}
    b = ms.source_disagreement["book"]
    assert b["n_diff_levels"] == 2 and b["this_source"] == "lankabd_depth" and b["other_source"] == "dsebd_depth"
    assert b["this_best_bid"] == 10.0 and b["other_best_bid"] == 10.0
    assert b["this_n_levels"] == (3, 2) and b["other_n_levels"] == (3, 1) and b["dt_s"] == -3.0
    assert b["examples"] == [{"side": "bid", "level": 1, "this": (9.9, 300.0), "other": (9.9, 280.0)},
                             {"side": "ask", "level": 1, "this": (10.2, 400.0), "other": None}]
    assert b["others_compared"] == ["dsebd_depth"]
    # fused quote values are the primary's (dsebd), the other is listed — never a blend
    assert ms.ltp == 10.2 and ms.trade_count == 60 and ms.trade_volume == 4900 and ms.trade_value == 0.05
    d = ms.source_disagreement["ltp"]
    assert (d["this"], d["this_source"], d["other"], d["other_source"]) == (10.2, "dsebd_depth", 10.1, "lankabd_depth")
    assert d["rule"] == "ltp exact" and d["dt_s"] == 3.0
    d = ms.source_disagreement["day_trades"]
    assert (d["this"], d["other"]) == (60.0, 50.0) and d["rule"].startswith("other lags beyond")
    d = ms.source_disagreement["day_volume"]
    assert (d["this"], d["other"]) == (4900.0, 5000.0) and d["rule"].startswith("other leads")
    assert "day_value" not in ms.source_disagreement
    # both per-source views carry the disagreement, each from its own side
    assert ms.sources["dsebd_depth"].agreement == {"book": False, "ltp": False, "day_trades": False,
                                                   "day_volume": False, "day_value": True}
    assert ms.sources["lankabd_depth"].agreement == ms.sources["dsebd_depth"].agreement
    assert ms.sources["lankabd_depth"].disagreement["ltp"] == {"this": 10.1, "other": 10.2,
                                                                "other_source": "dsebd_depth",
                                                                "rule": "ltp exact", "dt_s": -3.0}
    bk = ms.sources["dsebd_depth"].disagreement["book"]
    assert bk["other_source"] == "lankabd_depth" and bk["n_diff_levels"] == 2 and bk["dt_s"] == 3.0
    assert bk["this"] == {"best_bid": 10.0, "best_ask": 10.1, "n_levels": (3, 1)}
    assert ms.provenance == {"book": "lankabd_depth", "best_bid": "lankabd_depth", "best_ask": "lankabd_depth",
                             "ltp": "dsebd_depth", "day_trades": "dsebd_depth", "day_volume": "dsebd_depth",
                             "day_value": "dsebd_depth"}
    json.dumps(ms.to_dict())


def test_machinery_total_tolerance_rules():
    f = Fuser(coalesce_s=6.0)
    # other lags within 5 %: agreement; other leads: disagreement; other lags beyond 5 %: disagreement
    f.on_event(quote("lankabd_watch", "SYN", 0, volume=9700))
    f.on_event(snap("lankabd_depth", "SYN", 1, BIDS, ASKS, volume=10000))
    _, prov, agr, dis = f.fuse_quote("SYN", _t(1))
    assert prov["day_volume"] == "lankabd_depth" and agr["day_volume"] is True and dis == {}
    f.on_event(quote("dsebd_latest", "SYN", 2, volume=10200))        # fresher, leads the depth page
    values, prov, agr, dis = f.fuse_quote("SYN", _t(2))
    assert prov["day_volume"] == "dsebd_latest" and values["day_volume"] == 10200
    assert agr["day_volume"] is True                                  # both others lag within 5 % of 10200
    f.on_event(snap("lankabd_depth", "SYN", 3, BIDS, ASKS, volume=10000, dup=False))
    values, prov, agr, dis = f.fuse_quote("SYN", _t(3))
    assert prov["day_volume"] == "lankabd_depth" and values["day_volume"] == 10000
    assert agr["day_volume"] is False
    d = dis["day_volume"]
    assert d["other_source"] == "dsebd_latest" and d["other"] == 10200 and "leads" in d["rule"]
    others = {o["other_source"]: o for o in d["others"]}
    assert others["lankabd_watch"]["agree"] is True and others["dsebd_latest"]["agree"] is False
    f.on_event(quote("lankabd_watch", "SYN", 4, volume=9000))         # lags 10 % behind → disagreement
    _, prov, agr, dis = f.fuse_quote("SYN", _t(4))
    # freshest is the watch (9000): the depth page (10000) and dsebd (10200) both LEAD it
    assert prov["day_volume"] == "lankabd_watch" and agr["day_volume"] is False
    assert {o["other_source"] for o in dis["day_volume"]["others"] if not o["agree"]} == {"lankabd_depth", "dsebd_latest"}


def test_machinery_zero_fields_and_unobserved_are_not_observations():
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 0, [], [], ltp=0.0, trades=0, volume=0, zero=("ltp",)))
    values, prov, _, _ = f.fuse_quote("SYN", _t(0))
    assert values["ltp"] is None and "ltp" not in prov
    assert values["day_trades"] == 0.0 and prov["day_trades"] == "lankabd_depth"   # a delivered 0 is observed
    assert values["day_value"] is None and "day_value" not in prov              # never delivered
    ms = fill(f, "SYN", 0)
    assert ms.ltp is None and ms.trade_count == 0.0 and ms.trade_value is None
    assert ms.empty_book is True and ms.best_bid is None and "best_bid" not in ms.provenance
    assert ms.provenance["book"] == "lankabd_depth"


# ---------------------------------------------------------------- primary selection over time
def test_machinery_primary_sticky_then_switches_when_lagging_or_stale():
    f = Fuser(coalesce_s=6.0)
    for i in range(4):
        f.on_event(snap("lankabd_depth", "SYN", 6 * i, BIDS, ASKS, dup=i > 0))
        assert f.primary_book_source("SYN", _t(6 * i)) == "lankabd_depth"
        f.on_event(snap("dsebd_depth", "SYN", 6 * i + 2, BIDS, ASKS, dup=i > 0))
        # dsebd's first snapshot is a "change" too, but the images are identical: no switch
        assert f.primary_book_source("SYN", _t(6 * i + 2)) == "lankabd_depth"
    assert f.primary_book_source("SYN", _t(25)) == "lankabd_depth"
    # lankabd keeps polling but its image no longer changes; dsebd sees a change at 26 s
    new_bids = [(10.0, 150.0)] + BIDS[1:]
    f.on_event(snap("lankabd_depth", "SYN", 24, BIDS, ASKS, dup=True))
    f.on_event(snap("dsebd_depth", "SYN", 26, new_bids, ASKS))
    # within the coalesce window of that change lankabd is still primary, but the disagreement is exposed
    bids, _, src, agr, dis = f.fuse_book("SYN", _t(26))
    assert src == "lankabd_depth" and agr == {"book": False} and dis["book"]["n_diff_levels"] == 1
    assert bids == BIDS
    f.on_event(snap("lankabd_depth", "SYN", 30, BIDS, ASKS, dup=True))
    f.on_event(snap("dsebd_depth", "SYN", 32, new_bids, ASKS, dup=True))
    f.on_event(snap("lankabd_depth", "SYN", 36, BIDS, ASKS, dup=True))
    # lankabd's last content change (0 s) is > 6 s behind dsebd's (26 s): primary switches to dsebd
    bids, _, src, agr, dis = f.fuse_book("SYN", _t(36))
    assert src == "dsebd_depth" and bids == new_bids and agr == {"book": False}
    assert dis["book"]["this_source"] == "dsebd_depth" and dis["book"]["other_source"] == "lankabd_depth"
    # dsebd stops; lankabd continues → dsebd leaves the concurrent set (beyond coalesce), lankabd is primary
    for s in (42, 48, 54, 60):
        f.on_event(snap("lankabd_depth", "SYN", s, new_bids, ASKS, dup=s > 42))
    bids, _, src, agr, dis = f.fuse_book("SYN", _t(60))
    assert src == "lankabd_depth" and bids == new_bids and agr == {} and dis == {}
    ms = fill(f, "SYN", 60)
    # dsebd: 28 s old, cadence 6 s → threshold max(30, 18) = 30 s: out of the coalesce window but not yet stale
    assert ms.sources["dsebd_depth"].stale is False and ms.sources["dsebd_depth"].freshness_s == 28.0
    assert ms.sources["dsebd_depth"].cadence_s == 6.0
    ms = fill(f, "SYN", 70)
    assert ms.sources["dsebd_depth"].stale is True and ms.sources["dsebd_depth"].freshness_s == 38.0
    assert ms.sources["lankabd_depth"].stale is False and ms.book_source == "lankabd_depth"
    # everything silent for a long time: the freshest source is still named, and reported stale
    ms = fill(f, "SYN", 400)
    assert ms.book_source == "lankabd_depth" and ms.book_age_s == 340.0
    assert ms.sources["lankabd_depth"].stale is True and ms.sources["dsebd_depth"].stale is True


def test_machinery_book_fusion_is_causal_in_now():
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.0))
    f.on_event(snap("lankabd_depth", "SYN", 10, [(10.0, 1.0)], ASKS, ltp=10.5))
    # only the latest image is held; asked about an instant before it, the fuser answers "nothing
    # observable yet" rather than showing a book from the future
    assert f.fuse_book("SYN", _t(5)) == (None, None, None, {}, {})
    assert f.fuse_quote("SYN", _t(5)) == ({fld: None for fld in QUOTE_FIELDS}, {}, {}, {})
    bids, _, src, _, _ = f.fuse_book("SYN", _t(10))
    assert src == "lankabd_depth" and bids == [(10.0, 1.0)]
    assert f.fuse_quote("SYN", _t(-1)) == ({fld: None for fld in QUOTE_FIELDS}, {}, {}, {})
    assert f.fuse_book("SYN", _t(-1))[2] is None
    assert f.tape_source("SYN", _t(-1)) == (None, None)


# ---------------------------------------------------------------- freshness / stale evolution
def test_machinery_freshness_and_stale_follow_learned_cadence():
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS))
    st = fill(f, "SYN", 10).sources["lankabd_depth"]
    assert st.cadence_s is None and st.freshness_s == 10.0 and st.stale is False
    st = fill(f, "SYN", 31).sources["lankabd_depth"]
    assert st.stale is True                       # no cadence yet: the 30 s floor applies
    # a 20 s poller: threshold = max(30, 3 × 20) = 60 s
    for i in range(1, 5):
        f.on_event(snap("lankabd_depth", "SYN", 20 * i, BIDS, ASKS, dup=True))
    assert f.cadence_s("lankabd_depth", "SYN") == 20.0 and f.stale_threshold_s("lankabd_depth", "SYN") == 60.0
    assert fill(f, "SYN", 80 + 45).sources["lankabd_depth"].stale is False
    assert fill(f, "SYN", 80 + 61).sources["lankabd_depth"].stale is True
    # a fast poller (5 s) is protected by the floor: stale only beyond 30 s
    for i in range(6):
        f.on_event(snap("dsebd_depth", "SYN", 100 + 5 * i, BIDS, ASKS, dup=i > 0))
    assert f.cadence_s("dsebd_depth", "SYN") == 5.0 and f.stale_threshold_s("dsebd_depth", "SYN") == 30.0
    assert fill(f, "SYN", 125 + 20).sources["dsebd_depth"].stale is False
    assert fill(f, "SYN", 125 + 31).sources["dsebd_depth"].stale is True
    # cadence is a median: one long outage does not stretch it
    f.on_event(snap("dsebd_depth", "SYN", 125 + 300, BIDS, ASKS, dup=True))
    assert f.cadence_s("dsebd_depth", "SYN") == 5.0
    assert f.is_stale("nowhere", "SYN", _t(0)) is None


def test_machinery_status_counters_coverage_gaps_and_market_wide_sources():
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.0))
    f.on_event(snap("lankabd_depth", "SYN", 6, BIDS, ASKS, ltp=10.0, dup=True))
    f.on_event(gap("lankabd_depth", "SYN", 9))
    f.on_event(snap("lankabd_depth", "SYN", 12, BIDS, ASKS, ltp=10.0, trades=3))
    f.on_event(quote("lankabd_watch", "SYN", 13, ltp=10.0, t_exch=_t(12.5)))
    f.on_event(quote("lankabd_watch", "OTHER", 13, ltp=5.0, t_exch=_t(12.5)))
    f.on_event(Event(source="lankabd_market", event_type=EventType.MARKET_STATS, t_recv=_t(14), seq_local=0,
                     payload={"market_trades": 10.0}, observed_fields=("t_recv", "market_trades")))
    f.on_event(gap("heartbeat", None, 15))
    ms = fill(f, "SYN", 15)
    st = ms.sources["lankabd_depth"]
    assert (st.updates, st.duplicates, st.gaps) == (3, 1, 1)
    assert st.last_update == _t(12) and st.freshness_s == 3.0 and st.duplicate is False
    assert st.field_coverage == ("t_recv", "bid_levels", "ask_levels", "ltp", "day_trades")
    w = ms.sources["lankabd_watch"]
    assert w.updates == 1 and w.t_exch == _t(12.5) and "t_source" in w.field_coverage
    assert ms.sources["lankabd_market"].updates == 1 and ms.sources["lankabd_market"].freshness_s == 1.0
    assert ms.sources["heartbeat"].gaps == 1 and ms.sources["heartbeat"].last_update is None
    assert ms.sources["heartbeat"].freshness_s is None and ms.sources["heartbeat"].stale is False
    assert ms.ltp == 10.0 and ms.provenance["ltp"] == "lankabd_watch"      # freshest, exchange-stamped
    other = fill(f, "OTHER", 15)
    assert other.sources["lankabd_watch"].updates == 1 and "lankabd_depth" not in other.sources
    assert other.ltp == 5.0 and other.provenance == {"ltp": "lankabd_watch"}
    # a GAP is not an update: freshness is measured from the last accepted observation
    g = Fuser()
    g.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS))
    g.on_event(gap("lankabd_depth", "SYN", 9))
    st = g.source_status("lankabd_depth", "SYN", _t(9.5))
    assert st.freshness_s == 9.5 and st.gaps == 1 and st.updates == 1 and st.last_update == _t(0)
    assert g.source_status("lankabd_depth", None, _t(9.5)) is None      # a keyed gap creates no market-wide entry


# ---------------------------------------------------------------- quote comparison window & provenance
def test_machinery_quote_window_widens_to_the_slower_source_cadence():
    f = Fuser(coalesce_s=6.0)
    # watch polls every 60 s, depth every 6 s: the watch is compared within one of its own cycles
    for i in range(3):
        f.on_event(quote("lankabd_watch", "SYN", 60 * i, ltp=10.0 + i, trades=10 * i))
    for s in range(120, 160, 6):
        f.on_event(snap("lankabd_depth", "SYN", s, BIDS, ASKS, ltp=12.0, trades=20, dup=s > 120))
    _, prov, agr, dis = f.fuse_quote("SYN", _t(156))
    assert prov == {"ltp": "lankabd_depth", "day_trades": "lankabd_depth"}
    assert agr == {"ltp": True, "day_trades": True} and dis == {}
    # the watch's next poll carries a stale price: compared (dt 36 s ≤ 60 s window), disagreement exposed
    f.on_event(quote("lankabd_watch", "SYN", 180, ltp=12.0, trades=20))
    f.on_event(snap("lankabd_depth", "SYN", 186, BIDS, ASKS, ltp=12.5, trades=22))
    _, prov, agr, dis = f.fuse_quote("SYN", _t(186))
    assert prov["ltp"] == "lankabd_depth" and agr["ltp"] is False and dis["ltp"]["other_source"] == "lankabd_watch"
    assert dis["ltp"]["window_s"] == 60.0 and dis["ltp"]["dt_s"] == 6.0
    assert agr["day_trades"] is False                     # 20 lags 22 by 9 % > 5 %
    assert dis["day_trades"]["rule"] == "other lags beyond 5%"
    ms = fill(f, "SYN", 186)
    assert ms.sources["lankabd_watch"].agreement["ltp"] is False
    assert ms.sources["lankabd_watch"].disagreement["ltp"] == {"this": 12.0, "other": 12.5,
                                                                "other_source": "lankabd_depth",
                                                                "rule": "ltp exact", "dt_s": -6.0}
    # far outside every window (watch silent for > 60 s) the comparison stops instead of guessing
    f.on_event(snap("lankabd_depth", "SYN", 300, BIDS, ASKS, ltp=13.0, trades=30))
    _, _, agr, dis = f.fuse_quote("SYN", _t(300))
    assert "ltp" not in agr and dis == {}


def test_machinery_tape_source_and_provenance_from_cum_totals():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.1, trades=50, volume=5000, value=0.05))
    f.on_event(cum("lankabd_tape", "SYN", 2, price=10.1, trades=50, volume=5000, value=0.05, t_exch_s=1))
    ms = fill(f, "SYN", 3)
    assert ms.tape_source == "lankabd_tape" and ms.tape_age_s == 1.0 and ms.provenance["tape"] == "lankabd_tape"
    assert ms.provenance["ltp"] == "lankabd_tape"           # freshest observation of ltp
    assert ms.source_agreement == {"ltp": True, "day_trades": True, "day_volume": True, "day_value": True}
    assert ms.sources["lankabd_tape"].t_exch == _t(1)
    # a tape engine that already named its feed keeps it; the fuser records its own view in provenance
    ms2 = MarketState(symbol="SYN", t=_t(3), tape_source="lankabd_depth", tape_age_s=3.0)
    f.fill_state(ms2, _t(3))
    assert ms2.tape_source == "lankabd_depth" and ms2.tape_age_s == 3.0 and ms2.provenance["tape"] == "lankabd_tape"
    # upstream engines' values are never overwritten
    ms3 = MarketState(symbol="SYN", t=_t(3), ltp=99.0, trade_count=1.0)
    f.fill_state(ms3, _t(3))
    assert ms3.ltp == 99.0 and ms3.trade_count == 1.0 and ms3.trade_volume == 5000.0


def test_machinery_unchanged_flag_counts_as_no_content_change():
    f = Fuser()
    f.on_event(quote("lankabd_watch", "SYN", 0, ltp=10.0))
    f.on_event(quote("lankabd_watch", "SYN", 60, ltp=10.0, unchanged=True))
    st = fill(f, "SYN", 60).sources["lankabd_watch"]
    assert st.duplicate is True and st.duplicates == 0 and st.updates == 2   # 'unchanged' is not a raw duplicate


def test_machinery_previous_session_tape_rows_are_receipts_not_observations():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS, ltp=10.1, trades=50, volume=5000, value=0.05))
    # a pull before the day's first trade returns the previous session's rows (stamped 3 days earlier)
    prev = -3 * 86400
    f.on_event(cum("lankabd_tape", "SYN", 2, price=9.0, trades=3000, volume=900000, value=8.1, t_exch_s=prev + 4 * 3600))
    f.on_event(trade("lankabd_tape", "SYN", 2, price=9.0, qty=100, t_exch_s=prev + 4 * 3600))
    assert f.previous_session_rows == 2
    assert f.tape_source("SYN", _t(2)) == (None, None)
    values, prov, agr, dis = f.fuse_quote("SYN", _t(2))
    assert prov == {fld: "lankabd_depth" for fld in QUOTE_FIELDS} and agr == {} and dis == {}
    assert values["day_trades"] == 50.0 and values["ltp"] == 10.1          # never yesterday's 3000 / 9.0
    ms = fill(f, "SYN", 2)
    assert ms.tape_source is None and ms.tape_age_s is None and "tape" not in ms.provenance
    assert ms.trade_count == 50.0 and ms.ltp == 10.1
    st = ms.sources["lankabd_tape"]
    assert st.updates == 2 and st.freshness_s == 0.0 and st.last_update == _t(2)   # the feed itself is alive
    # today's first row: now it is the tape and (freshest) the quote primary
    f.on_event(cum("lankabd_tape", "SYN", 4, price=10.2, trades=51, volume=5010, value=0.051, t_exch_s=3.5))
    ms = fill(f, "SYN", 5)
    assert ms.tape_source == "lankabd_tape" and ms.tape_age_s == 1.0 and ms.provenance["tape"] == "lankabd_tape"
    assert ms.provenance["ltp"] == "lankabd_tape" and ms.ltp == 10.2 and ms.trade_count == 51.0
    assert ms.source_agreement["day_trades"] is True and ms.source_agreement["ltp"] is False
    assert f.previous_session_rows == 2


def test_machinery_same_pull_rows_share_a_receipt_and_the_newest_stamp_is_the_observation():
    # a many-row pull arrives with one t_recv; whichever order the rows come in, the observation
    # is the row with the newest exchange stamp
    for order in ("forward", "reverse"):
        f = Fuser()
        rows = [(1.0, 10.0, 50), (1.5, 10.5, 60)]
        if order == "reverse":
            rows = rows[::-1]
        for t_exch_s, price, trades in rows:
            f.on_event(cum("lankabd_tape", "SYN", 2, price=price, trades=trades, volume=100, value=1.0,
                           t_exch_s=t_exch_s))
        values, prov, _, _ = f.fuse_quote("SYN", _t(2))
        assert (values["ltp"], values["day_trades"], prov["ltp"]) == (10.5, 60.0, "lankabd_tape"), order
        st = f.source_status("lankabd_tape", "SYN", _t(2))
        assert st.updates == 2 and st.cadence_s is None            # one receipt: no 0 s cadence learned
    # a later pull (newer receipt) always wins over an earlier one, whatever its stamp
    f.on_event(cum("lankabd_tape", "SYN", 8, price=10.6, trades=61, volume=101, value=1.01, t_exch_s=7.0))
    assert f.fuse_quote("SYN", _t(8))[0]["day_trades"] == 61.0
    assert f.cadence_s("lankabd_tape", "SYN") == 6.0


def test_machinery_unparsed_page_keeps_a_source_alive_but_not_concurrent():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS))
    f.on_event(snap("dsebd_depth", "SYN", 2, BIDS, ASKS))
    assert f.fuse_book("SYN", _t(2))[3] == {"book": True}
    # dsebd's page stops showing a book (layout change): it keeps answering, but its image is old
    new_bids = [(10.0, 150.0)] + BIDS[1:]
    for s, ev in ((6, snap("lankabd_depth", "SYN", 6, BIDS, ASKS, dup=True)),
                  (8, unparsed("dsebd_depth", "SYN", 8)),
                  (12, snap("lankabd_depth", "SYN", 12, new_bids, ASKS)),
                  (14, unparsed("dsebd_depth", "SYN", 14)),
                  (18, snap("lankabd_depth", "SYN", 18, new_bids, ASKS, dup=True)),
                  (20, unparsed("dsebd_depth", "SYN", 20))):
        f.on_event(ev)
    bids, _, src, agr, dis = f.fuse_book("SYN", _t(20))
    # its 18 s old image is NOT compared against the live primary: no false disagreement
    assert src == "lankabd_depth" and bids == new_bids and agr == {} and dis == {}
    ms = fill(f, "SYN", 20)
    assert ms.sources["dsebd_depth"].freshness_s == 0.0 and ms.sources["dsebd_depth"].stale is False
    assert ms.sources["dsebd_depth"].updates == 4 and ms.sources["dsebd_depth"].agreement == {}
    assert ms.book_source == "lankabd_depth" and ms.book_age_s == 2.0          # age of the image, not of liveness
    # the reverse: the PRIMARY's page breaks while the other sensor keeps delivering images → switch
    for ev in (unparsed("lankabd_depth", "SYN", 24), snap("dsebd_depth", "SYN", 26, new_bids, ASKS),
               unparsed("lankabd_depth", "SYN", 30), snap("dsebd_depth", "SYN", 32, new_bids, ASKS, dup=True)):
        f.on_event(ev)
    bids, _, src, agr, dis = f.fuse_book("SYN", _t(32))
    assert src == "dsebd_depth" and bids == new_bids and agr == {} and dis == {}
    ms = fill(f, "SYN", 32)
    assert ms.book_source == "dsebd_depth" and ms.book_age_s == 0.0
    assert ms.sources["lankabd_depth"].freshness_s == 2.0 and ms.sources["lankabd_depth"].stale is False


def test_machinery_incremental_source_is_a_candidate_without_an_image():
    f = Fuser(coalesce_s=6.0)
    for s, side, px, q in ((0, "bid", 10.0, 100), (1, "ask", 10.1, 80), (2, "bid", 9.9, 300)):
        f.on_event(update("itch", "SYN", s, side, px, q))
    assert f.primary_book_source("SYN", _t(2)) == "itch"
    assert f.fuse_book("SYN", _t(2)) == (None, None, "itch", {}, {})
    ms = MarketState(symbol="SYN", t=_t(2), bids=list(BIDS), best_bid=10.0)         # its EvolvingBook displayed it
    f.fill_state(ms, _t(2))
    assert ms.book_source == "itch" and ms.book_age_s == 0.0 and ms.bids == BIDS and ms.best_bid == 10.0
    assert ms.provenance == {"book": "itch", "best_bid": "itch"} and ms.source_agreement == {}
    # a snapshot sensor joins: both concurrent; the incremental image cannot be compared level by level
    f.on_event(snap("lankabd_depth", "SYN", 3, BIDS, ASKS))
    assert f.primary_book_source("SYN", _t(3)) == "itch"                        # sticky while concurrent
    assert f.fuse_book("SYN", _t(3))[3] == {}                                    # nothing comparable: no verdict
    for k in range(3):
        assert f.primary_book_source("SYN", _t(3)) == "itch"                    # idempotent per frame
    # itch still receives (a heartbeat-like unchanged update) but sees no content change while the
    # snapshot sensor does, for more than a window: an image that cannot be compared counts as
    # differing → the primary switches
    f.on_event(update("itch", "SYN", 8, "bid", 9.9, 310))
    f.on_event(snap("lankabd_depth", "SYN", 9, [(10.0, 120.0)] + BIDS[1:], ASKS))
    assert f.primary_book_source("SYN", _t(9)) == "itch"                        # change 0 s old: not yet
    keep = update("itch", "SYN", 12, "bid", 9.9, 310)
    keep.flags["unchanged"] = True
    f.on_event(keep)
    assert f.primary_book_source("SYN", _t(12)) == "itch"                       # 3 s old: not yet
    f.on_event(snap("lankabd_depth", "SYN", 15.5, [(10.0, 120.0)] + BIDS[1:], ASKS, dup=True))
    assert f.primary_book_source("SYN", _t(15.5)) == "lankabd_depth"
    bids, _, src, agr, _ = f.fuse_book("SYN", _t(15.5))
    assert src == "lankabd_depth" and bids == [(10.0, 120.0)] + BIDS[1:] and agr == {}
    # and once the incremental source is silent beyond the window it is simply not concurrent
    f.on_event(snap("lankabd_depth", "SYN", 21, [(10.0, 120.0)] + BIDS[1:], ASKS, dup=True))
    assert f.primary_book_source("SYN", _t(21)) == "lankabd_depth"
    assert fill(f, "SYN", 21).sources["itch"].freshness_s == 9.0


def test_machinery_one_sided_and_empty_books_compare_level_by_level():
    f = Fuser(coalesce_s=6.0)
    f.on_event(snap("lankabd_depth", "SYN", 0, [], ASKS))                # no bids resting
    assert f.primary_book_source("SYN", _t(0)) == "lankabd_depth"        # the engine asks at every frame
    f.on_event(snap("dsebd_depth", "SYN", 1, [(10.0, 100.0)], ASKS))     # the other sensor shows one
    bids, asks, src, agr, dis = f.fuse_book("SYN", _t(1))
    assert (bids, asks, src) == ([], ASKS, "lankabd_depth") and agr == {"book": False}
    b = dis["book"]
    assert b["n_diff_levels"] == 1 and b["this_best_bid"] is None and b["other_best_bid"] == 10.0
    assert b["this_best_ask"] == 10.1 and b["other_best_ask"] == 10.1
    assert b["examples"] == [{"side": "bid", "level": 0, "this": None, "other": (10.0, 100.0)}]
    ms = fill(f, "SYN", 1)
    assert ms.best_bid is None and ms.bid_qty1 is None and ms.best_ask == 10.1 and ms.ask_qty1 == 80.0
    assert ms.empty_book is False and "best_bid" not in ms.provenance and ms.provenance["best_ask"] == "lankabd_depth"
    # both empty: identical → agreement, and nothing invented
    g = Fuser(coalesce_s=6.0)
    g.on_event(snap("lankabd_depth", "SYN", 0, [], []))
    g.on_event(snap("dsebd_depth", "SYN", 1, [], []))
    bids, asks, src, agr, dis = g.fuse_book("SYN", _t(1))
    assert (bids, asks, agr, dis) == ([], [], {"book": True}, {})
    ms = fill(g, "SYN", 1)
    assert ms.empty_book is True and ms.best_bid is None and ms.best_ask is None
    assert set(ms.provenance) == {"book"}
    # a level with a missing quantity is not a level (never a silent zero)
    h = Fuser()
    h.on_event(snap("lankabd_depth", "SYN", 0, [(10.0, None), (9.9, 5.0)], [(10.1, float("nan"))]))
    assert h.fuse_book("SYN", _t(0))[:2] == ([(9.9, 5.0)], [])


# ---------------------------------------------------------------- determinism / sentinels / display
def test_machinery_primary_is_a_function_of_events_not_of_query_history():
    # two fusers fed the same sorted stream must answer the same, whether or not earlier frames
    # were queried: the sticky memory advances at event receipt, queries are pure reads
    queried, silent = Fuser(coalesce_s=6.0), Fuser(coalesce_s=6.0)
    for f in (queried, silent):
        f.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS))
    assert queried.primary_book_source("SYN", _t(0)) == "lankabd_depth"
    for f in (queried, silent):
        f.on_event(snap("dsebd_depth", "SYN", 2, BIDS, ASKS))
    assert silent.fuse_book("SYN", _t(2))[2] == queried.fuse_book("SYN", _t(2))[2] == "lankabd_depth"
    assert silent.fuse_book("SYN", _t(2))[3] == {"book": True}
    # a query at an instant where the primary would already count as lagging does not mutate memory:
    # the next event decides, and both fusers keep agreeing
    new_bids = [(10.0, 150.0)] + BIDS[1:]
    for f in (queried, silent):
        f.on_event(snap("lankabd_depth", "SYN", 6, BIDS, ASKS, dup=True))
        f.on_event(snap("dsebd_depth", "SYN", 8, new_bids, ASKS))
    assert queried.primary_book_source("SYN", _t(20)) == "dsebd_depth"       # 12 s old change, image differs
    assert queried.primary_book_source("SYN", _t(9)) == "lankabd_depth"      # earlier instant: not yet lagging
    for f in (queried, silent):
        f.on_event(snap("lankabd_depth", "SYN", 10, new_bids, ASKS))         # lankabd caught up in time
        assert f.primary_book_source("SYN", _t(10)) == "lankabd_depth"
        assert f.primary_book_source("SYN", _t(30)) == "lankabd_depth"       # identical images never switch
    # equal t_recv: the sorted stream feeds the higher-priority sensor first and it becomes sticky
    g = Fuser()
    g.on_event(snap("lankabd_depth", "SYN", 0, BIDS, ASKS))
    g.on_event(snap("dsebd_depth", "SYN", 0, BIDS, ASKS))
    assert g.primary_book_source("SYN", _t(0)) == "lankabd_depth" and g.fuse_book("SYN", _t(0))[3] == {"book": True}


def test_machinery_sentinel_prices_and_impossible_values_are_not_observations():
    # tape row with a 0 price (the feed's 'not populated' sentinel): totals observed, ltp not
    f = Fuser()
    f.on_event(cum("lankabd_tape", "SYN", 2, price=0.0, trades=5, volume=100, value=1.0, t_exch_s=1))
    values, prov, _, _ = f.fuse_quote("SYN", _t(2))
    assert values["ltp"] is None and "ltp" not in prov and values["day_trades"] == 5.0
    assert fill(f, "SYN", 2).ltp is None
    # a print at price 0 is no price either; a negative day total is a parse artefact, not a count
    g = Fuser()
    g.on_event(trade("lankabd_tape", "SYN", 2, price=0.0, qty=10, t_exch_s=1))
    g.on_event(snap("lankabd_depth", "SYN", 3, BIDS, ASKS, trades=-1, volume=7))
    values, prov, _, _ = g.fuse_quote("SYN", _t(3))
    assert values["ltp"] is None and values["day_trades"] is None and values["day_volume"] == 7.0
    assert set(prov) == {"day_volume"}
    # levels follow the EvolvingBook's rule: a 0 / negative / missing quantity or a non-finite price
    # is not a resting level, so the image compared is the image displayed (never a phantom difference)
    h = Fuser(coalesce_s=6.0)
    h.on_event(snap("lankabd_depth", "SYN", 0, [(10.0, 0.0), (9.9, -5.0), (9.8, 20.0)], [(float("inf"), 5.0)]))
    h.on_event(snap("dsebd_depth", "SYN", 1, [(9.8, 20.0)], []))
    bids, asks, _, agr, dis = h.fuse_book("SYN", _t(1))
    assert (bids, asks, agr, dis) == ([(9.8, 20.0)], [], {"book": True}, {})


def test_machinery_fused_display_derives_l1_fields_from_the_same_image():
    # one-sided image: flags and derived fields follow the book engine's definitions
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 0, [], ASKS))
    ms = fill(f, "SYN", 0)
    assert ms.one_sided is True and ms.empty_book is False and ms.crossed is False and ms.locked is False
    assert ms.spread is None and ms.mid is None and ms.microprice is None and ms.best_bid is None
    # two-sided: spread / mid / microprice / spread_ticks from the primary's own levels
    g = Fuser()
    g.on_event(snap("lankabd_depth", "SYN", 0, [(10.0, 100.0)], [(10.2, 300.0)]))
    ms = MarketState(symbol="SYN", t=_t(0), tick_size=0.1)
    g.fill_state(ms, _t(0))
    assert ms.spread == pytest.approx(0.2) and ms.spread_ticks == pytest.approx(2.0) and ms.mid == pytest.approx(10.1)
    assert ms.microprice == pytest.approx((10.2 * 100.0 + 10.0 * 300.0) / 400.0)
    assert ms.one_sided is False and ms.crossed is False and ms.locked is False and ms.empty_book is False
    # crossed / locked images are reported as such, never left at the defaults
    h = Fuser()
    h.on_event(snap("lankabd_depth", "SYN", 0, [(10.2, 5.0)], [(10.1, 5.0)]))
    ms = fill(h, "SYN", 0)
    assert ms.crossed is True and ms.locked is False and ms.spread == pytest.approx(-0.1)
    k = Fuser()
    k.on_event(snap("lankabd_depth", "SYN", 0, [(10.1, 5.0)], [(10.1, 5.0)]))
    ms = fill(k, "SYN", 0)
    assert ms.locked is True and ms.crossed is False and ms.spread == 0.0 and ms.mid == pytest.approx(10.1)
    # an upstream-displayed book is left alone (its flags included)
    m = MarketState(symbol="SYN", t=_t(0), bids=[(9.0, 1.0)], best_bid=9.0, one_sided=True)
    k.fill_state(m, _t(0))
    assert m.bids == [(9.0, 1.0)] and m.best_bid == 9.0 and m.one_sided is True and m.spread is None


def test_machinery_out_of_order_receipt_never_replaces_a_newer_observation():
    f = Fuser()
    f.on_event(snap("lankabd_depth", "SYN", 10, [(10.0, 1.0)], ASKS, ltp=10.5))
    f.on_event(snap("lankabd_depth", "SYN", 4, BIDS, ASKS, ltp=10.0))        # a late, older frame
    assert f.out_of_order == 1
    bids, _, src, _, _ = f.fuse_book("SYN", _t(10))
    assert src == "lankabd_depth" and bids == [(10.0, 1.0)]
    assert f.fuse_quote("SYN", _t(10))[0]["ltp"] == 10.5
    st = f.source_status("lankabd_depth", "SYN", _t(10))
    assert st.updates == 2 and st.last_update == _t(10) and st.freshness_s == 0.0   # counted, not advanced
    g = Fuser()
    g.on_event(cum("lankabd_tape", "SYN", 10, price=10.5, trades=6, volume=60, value=0.6, t_exch_s=9))
    g.on_event(cum("lankabd_tape", "SYN", 4, price=10.0, trades=5, volume=50, value=0.5, t_exch_s=3))
    assert g.tape_source("SYN", _t(10)) == ("lankabd_tape", 0.0)
    assert g.fuse_quote("SYN", _t(10))[0]["day_trades"] == 6.0


# ---------------------------------------------------------------- real data
def test_realdata_fixture_two_depth_sensors_agree_on_closed_books():
    events, _ = normalize_store(FIXTURE)
    f = Fuser(coalesce_s=6.0)
    states = {}
    for ev in events:
        f.on_event(ev)
        if ev.symbol and ev.event_type in (EventType.BOOK_SNAPSHOT, EventType.CUM_TOTALS, EventType.QUOTE):
            ms = MarketState(symbol=ev.symbol, t=ev.t_recv, session_phase=ev.session_phase)
            f.fill_state(ms, ev.t_recv)
            states.setdefault(ev.symbol, []).append((ev, ms))
    assert set(states) >= {"MALEKSPIN", "SHARPIND", "FINEFOODS"}
    for sym in ("MALEKSPIN", "SHARPIND", "FINEFOODS"):
        # the first dsebd snapshot arrives within the coalesce window of the lankabd one
        ev, ms = next((e, m) for e, m in states[sym] if e.source == "dsebd_depth")
        assert ms.book_source == "lankabd_depth"                     # first sensor, kept while concurrent
        assert ms.source_agreement["book"] is True and "book" not in ms.source_disagreement
        assert ms.source_agreement["ltp"] is True and ms.source_agreement["day_trades"] is True
        assert ms.provenance["book"] == "lankabd_depth" and ms.provenance["ltp"] == "dsebd_depth"
        assert ms.ltp == ev.payload["ltp"] and ms.ltp is not None
        assert ms.trade_count == ev.payload["day_trades"] and ms.trade_value == ev.payload["day_value_mn"]
        assert ms.sources["lankabd_depth"].agreement["book"] is True
        assert ms.sources["dsebd_depth"].agreement["book"] is True
        assert 0.0 < ms.sources["lankabd_depth"].freshness_s < 6.0 and ms.sources["dsebd_depth"].freshness_s == 0.0
        json.dumps(ms.to_dict())
    _, ms = states["MALEKSPIN"][-1]
    # the fixture's tape pulls were made before the day's first trade: every one of their rows is
    # stamped on the PREVIOUS session (2026-09-03). The feed is alive (status), but nothing it
    # delivered is today's tape: no tape source, no tape provenance, no quote observation from it
    assert f.previous_session_rows == sum(1 for e in events if e.source == "lankabd_tape"
                                          and e.event_type == EventType.CUM_TOTALS) > 500
    assert ms.tape_source is None and ms.tape_age_s is None and "tape" not in ms.provenance
    assert "lankabd_tape" not in ms.provenance.values()
    # SHARPIND: both sides empty on both sensors → agreement, empty book, no best prices, no silent zero
    _, ms = states["SHARPIND"][-1]
    assert ms.bids == [] and ms.asks == [] and ms.best_bid is None and ms.empty_book is True
    assert ms.source_agreement["book"] is True and "best_bid" not in ms.provenance
    # MALEKSPIN: one ask level on both sensors, no bids; the last frame sees 3 snapshots per sensor, 2 duplicates
    _, ms = states["MALEKSPIN"][-1]
    assert ms.bids == [] and ms.asks == [(51.3, 1000.0)] and ms.best_ask == 51.3 and ms.provenance["best_ask"] == "lankabd_depth"
    for s in ("lankabd_depth", "dsebd_depth"):
        st = ms.sources[s]
        assert st.updates == 3 and st.duplicates == 2 and st.gaps == 0 and st.stale is False
        assert st.cadence_s is not None and 5.0 < st.cadence_s < 8.0
        assert st.field_coverage[:3] == ("t_recv", "bid_levels", "ask_levels") and "day_value" in st.field_coverage
    # every fused quote field comes from a named source and no disagreement exists on this closed market
    assert {"ltp", "day_trades", "day_volume", "day_value"} <= set(ms.provenance)
    assert ms.source_disagreement == {}
    # at the capture's final instant: the tape (many rows per pull, then a no_new_rows status) learned a
    # pull cadence rather than a 0 s row cadence, and nothing is reported from the future
    end = MarketState(symbol="MALEKSPIN", t=events[-1].t_recv)
    f.fill_state(end, events[-1].t_recv)
    tape = end.sources["lankabd_tape"]
    assert tape.cadence_s is not None and tape.cadence_s > 1.0 and tape.updates > 200
    assert tape.freshness_s >= 0.0 and tape.stale is False and tape.duplicates >= 1   # no_new_rows polls
    assert all(st.freshness_s is None or st.freshness_s >= 0.0 for st in end.sources.values())
    assert end.tape_source is None and end.tape_age_s is None
