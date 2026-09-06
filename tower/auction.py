"""Auction engine — the call-auction / pre-open state kept apart from the
continuous-session fields.

Rules
-----
phase
    taken from ``ms.session_phase`` (CLOSED | PRE_OPEN | CONTINUOUS | POST_CLOSE,
    labelled by ``seeing.clock.session_phase``); phase transitions are recorded
    with their times.
real auction data
    when a source delivers AUCTION events (payload ``indicative_price``,
    ``matched_qty``, ``imbalance_qty``, ``imbalance_side`` — or the event's
    ``price`` / ``qty`` / ``side``) the latest one supplies
    ``indicative_price``, ``matched_qty``, ``imbalance_qty``, ``imbalance_side``
    and ``auction_pressure`` = signed imbalance / (matched + |imbalance|)
    (positive = buy imbalance), ``source`` = the event's source. An AUCTION
    event is used for the trading date it was received on only.
pre-open proxy
    without AUCTION events, during PRE_OPEN the displayed book imbalance stands
    in: ``auction_pressure`` = imb_topk (falling back to imb_l1),
    ``source='pre_open_book_proxy'``, ``indicative_price`` None. Outside
    PRE_OPEN with no auction feed everything is None (``source`` None).
transition
    the first update in CONTINUOUS after a PRE_OPEN update is the auction →
    continuous transition: ``transition_time`` is that update's time and
    ``open_gap_ticks`` = (opening ltp − reference) / tick where the reference is
    the indicative price when one was delivered, else yclose
    (``open_reference`` names which). When no ltp is available at the transition
    the gap is filled by the first later update that carries one (the opening
    price is the first print of the continuous session).

``fill_state`` only ever writes ``ms.auction`` — no continuous-session field is
touched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from seeing.clock import trading_date

from .events import Event, EventType
from .state import MarketState

PROXY_SOURCE = "pre_open_book_proxy"


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _side_sign(side: Any) -> Optional[int]:
    if side is None:
        return None
    s = str(side).strip().lower()
    if s in ("b", "bid", "buy", "+", "1"):
        return 1
    if s in ("s", "a", "ask", "sell", "-", "-1"):
        return -1
    return None


@dataclass
class _Auction:
    t: datetime
    source: str
    indicative_price: Optional[float]
    matched_qty: Optional[float]
    imbalance_qty: Optional[float]
    imbalance_side: Optional[str]
    date: Any


class AuctionEngine:
    def __init__(self) -> None:
        self._last: Optional[_Auction] = None
        self._n_events = 0
        self._prev_phase: Optional[str] = None
        self._seen_pre_open_date: Any = None
        self._transition_time: Optional[datetime] = None
        self._transition_date: Any = None
        self._open_ltp: Optional[float] = None
        self._open_gap_ticks: Optional[float] = None
        self._open_reference: Optional[str] = None
        self._open_reference_price: Optional[float] = None
        self._phase_changes: List[Dict[str, Any]] = []
        self._status: Optional[str] = None

    # ------------------------------------------------------------------ events
    def on_event(self, ev: Event) -> None:
        if ev.event_type == EventType.AUCTION:
            p = ev.payload or {}
            side = p.get("imbalance_side", ev.side)
            self._last = _Auction(
                t=ev.t_recv, source=ev.source,
                indicative_price=_f(p.get("indicative_price", ev.price)),
                matched_qty=_f(p.get("matched_qty", ev.qty)),
                imbalance_qty=_f(p.get("imbalance_qty")),
                imbalance_side=(str(side) if side is not None else None),
                date=trading_date(ev.t_recv),
            )
            self._n_events += 1
        elif ev.event_type == EventType.STATUS and ev.status:
            self._status = ev.status

    # ------------------------------------------------------------------ state
    def fill_state(self, ms: MarketState) -> Dict[str, Any]:
        phase = ms.session_phase
        today = trading_date(ms.t)
        d: Dict[str, Any] = {
            "phase": phase, "source": None, "indicative_price": None, "matched_qty": None,
            "imbalance_qty": None, "imbalance_side": None, "auction_pressure": None, "auction_age_s": None,
            "auction_events": self._n_events, "transition_time": None, "open_ltp": None,
            "open_gap_ticks": None, "open_reference": None, "open_reference_price": None,
            "last_phase_change": None,
        }
        # phase transitions
        if self._prev_phase is not None and phase != self._prev_phase:
            self._phase_changes.append({"t": ms.t.isoformat(), "from": self._prev_phase, "to": phase})
            if self._prev_phase == "PRE_OPEN" and phase == "CONTINUOUS":
                self._transition_time = ms.t
                self._transition_date = today
                self._open_ltp = None
                self._open_gap_ticks = None
                self._open_reference = None
                self._open_reference_price = None
        if phase == "PRE_OPEN":
            self._seen_pre_open_date = today
        if self._phase_changes:
            d["last_phase_change"] = self._phase_changes[-1]

        # real auction data (for today's session only)
        a = self._last
        if a is not None and a.date == today:
            d["source"] = a.source
            d["indicative_price"] = a.indicative_price
            d["matched_qty"] = a.matched_qty
            d["imbalance_qty"] = a.imbalance_qty
            d["imbalance_side"] = a.imbalance_side
            d["auction_age_s"] = (ms.t - a.t).total_seconds()
            sgn = _side_sign(a.imbalance_side)
            if a.imbalance_qty is not None:
                imb = abs(a.imbalance_qty)
                if sgn is None:
                    sgn = 1 if a.imbalance_qty > 0 else (-1 if a.imbalance_qty < 0 else 0)
                denom = (a.matched_qty or 0.0) + imb
                d["auction_pressure"] = (sgn * imb / denom) if denom > 0 else None
        elif phase == "PRE_OPEN":
            proxy = ms.imb_topk if ms.imb_topk is not None else ms.imb_l1
            if proxy is not None:
                d["source"] = PROXY_SOURCE
                d["auction_pressure"] = float(proxy)
                d["proxy_basis"] = "imb_topk" if ms.imb_topk is not None else "imb_l1"
            else:
                d["source"] = PROXY_SOURCE
                d["missing"] = ["book imbalance"]

        # auction → continuous transition and the opening gap
        if self._transition_time is not None and self._transition_date == today:
            d["transition_time"] = self._transition_time
            if self._open_ltp is None and phase == "CONTINUOUS" and ms.ltp is not None:
                self._open_ltp = ms.ltp
                ref_px, ref_name = None, None
                if a is not None and a.date == today and a.indicative_price is not None:
                    ref_px, ref_name = a.indicative_price, "indicative"
                else:
                    yclose = _f(((ms.session_state or {}).get("quote") or {}).get("yclose"))
                    if yclose is not None and yclose > 0:
                        ref_px, ref_name = yclose, "yclose"
                self._open_reference, self._open_reference_price = ref_name, ref_px
                if ref_px is not None and ms.tick_size:
                    self._open_gap_ticks = (ms.ltp - ref_px) / ms.tick_size
            d["open_ltp"] = self._open_ltp
            d["open_gap_ticks"] = self._open_gap_ticks
            d["open_reference"] = self._open_reference
            d["open_reference_price"] = self._open_reference_price
        self._prev_phase = phase
        ms.auction = d
        return d
