"""ResilienceEngine — book shocks and the recovery curve that follows them.

``on_state(ms, hist)`` is called once per MarketState after the book, tape,
queue and fusion engines have written their fields (``hist`` holds the states
*before* ``ms``; the engine keeps its own causal observation ring so it does
not depend on the history's retention).  ``fill_state(ms)`` then writes the
resilience fields computed for that same update.  One engine serves any number
of symbols (state is keyed by ``ms.symbol``); ``curves(symbol)`` returns the
completed curve records.

Observables per update (None when the book does not carry them):
  qty1 per side            ``bid_qty1`` / ``ask_qty1`` (else the first level);
  top-K depth per side     Σ qty of the first K = 5 displayed levels;
  spread in ticks          ``spread_ticks`` (else spread / tick, else
                           (best_ask − best_bid) / tick);
  mid                      ``mid`` (else the average of the two bests).

Shock detection (only while no curve is active), evaluated at every update
against the **burst window** of the last 30 s (``pre`` = the last observation
at or before now − 30 s) and the **baseline window** [now − 300 s, now − 30 s]:
  depth drop   a side's qty1 or top-K depth fell inside the burst by at least
               50 % of its baseline-window median (≥ 3 observations):
               depth(pre) − depth(now) ≥ 0.5 × median, and the depth now is
               below 90 % of the median (a wall that is pulled back to
               above-baseline depth is not a depletion) — a slow bleed whose
               30-s fall is small never qualifies, however deep it goes;
  spread       spread_ticks(now) − spread_ticks(pre) ≥ 2 ticks; the side is
               the best that retreated more (bid down / ask up), "both" on
               a tie;
  sweep        the best bid fell ≥ 2 ticks (side bid) or the best ask rose
               ≥ 2 ticks (side ask) inside the burst — price moved through
               displayed depth.
All triggers that fire are recorded; the primary one (sweep > depth > spread)
sets ``side`` and the depth ``measure`` used by the curve ("qty1" or "topk";
sweeps and spread shocks use "topk" because they consume whole levels).
A candidate whose first sample already satisfies the recovered condition
(depth share ≥ 90 % and spread within 1 tick of baseline — e.g. both bests
repriced together with the displayed depth intact) is not a shock: nothing
was consumed, so there is nothing to recover, and no curve is opened.

Pre-shock baseline: depth per side and per measure = the baseline-window
median (≥ 3 points) else the ``pre`` value; spread, mid and bests = ``pre``.
The shock move is mid(now) − mid(pre) in ticks; its sign is the shock
direction (falling back to −1 for a bid-side and +1 for an ask-side shock
when the mid did not move).

Recovery curve — one sample per update while the curve is active:
  depth share per side     depth / baseline depth (unclamped; > 1 = above
                           baseline);  ``share`` = the shocked side's share
                           (min over both sides for a two-sided shock);
  spread share             1 − (spread − base) / (spread_at_shock − base),
                           clamped to [0, 1]; when the spread did not widen at
                           the shock: 1 if it is within 1 tick of the
                           baseline, else 0;
  mid share                (mid_at_shock − mid) / (mid_at_shock − mid_pre):
                           1 = back at the pre-shock mid, > 1 = beyond it;
                           None when the shock did not move the mid;
  recovered                share ≥ 90 % and spread within 1 tick of baseline;
  timeout                  600 s after the shock;
  time_to_recovery_s       seconds from the shock to the recovered sample;
  updates_to_recovery      number of updates after the shock until recovered;
  recovery_speed           (share − share_at_shock) / seconds since shock
                           (shares per second); per side likewise;
  asymmetry                bid speed − ask speed;
  partial                  the final share lies in [30 %, 90 %) at timeout
                           (or depth is back but the spread is not);
  vacuum                   share < 30 % once 120 s have passed;
  overshoot                a shocked side's depth > 130 % of baseline (only a
                           side whose share was < 1 at the shock — it has to
                           have been depleted to overshoot on the way back),
                           or the mid reverted past its pre-shock level by
                           ≥ 1 tick (mid share > 1);  evaluated on the samples
                           after the shock sample; sticky once seen;
  snapback                 mid share ≥ 80 % within 60 s of the shock; sticky.

State per update:  none (no active curve; the terminal state is reported on the
update that closes the curve) | shocked | recovering (share improved ≥ 0.05
over the share at the shock, or spread share improved ≥ 0.25) | partial
(≥ 120 s, no improvement, share ≥ 30 %) | vacuum (≥ 120 s, share < 30 %) |
recovered | overshoot (recovered with the overshoot flag).  The terminal
states are recovered / overshoot (recovered condition), partial / vacuum (at
timeout).  A book that disappears during a curve keeps the clock running: the
curve closes at the timeout on what was last seen (vacuum if the last share
was < 30 %, else partial).  Before the first book observation the state is
None (not observable).

``fill_state`` writes ``resilience_state``, ``recovery_speed``,
``recovery_asymmetry``, ``recovery_curve`` = [(s, share)] of the active or
last curve, ``liquidity_response`` = share(now) − share(at shock) on the
shocked side while the last shock is < 600 s old, and
``session_state["resilience"]`` = a snapshot of the current (active or last)
curve record (the full per-update ``samples`` list is kept on the engine's
record only — ``curves()`` / ``active_curve()`` — and the snapshot carries
``last_sample`` and ``samples_n`` so state-store lines stay bounded).
All times are the event times carried by the states; nothing reads a clock.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

from .state import MarketState
from .windows import safe_div

SHOCK_W_S = 30.0               # burst window
MEDIAN_W_S = 300.0             # baseline-median window
MEDIAN_MIN_POINTS = 3
DEPTH_DROP_SHARE = 0.50        # fall inside the burst ≥ 50 % of the median
SPREAD_WIDEN_TICKS = 2.0
SWEEP_TICKS = 2.0
RECOVERED_SHARE = 0.90
SPREAD_RECOVERED_TICKS = 1.0
PARTIAL_LO = 0.30
OVERSHOOT_SHARE = 1.30
SNAPBACK_SHARE = 0.80
SNAPBACK_W_S = 60.0
VACUUM_S = 120.0
TIMEOUT_S = 600.0
TOP_K = 5
RECOVERING_DEPTH_MARGIN = 0.05
RECOVERING_SPREAD_MARGIN = 0.25
KEEP_S = MEDIAN_W_S + SHOCK_W_S + 5.0

TERMINAL = ("recovered", "overshoot", "partial", "vacuum")


@dataclass
class _Obs:
    t: datetime
    tick: Optional[float]
    bid1: Optional[float]
    ask1: Optional[float]
    topk_bid: Optional[float]
    topk_ask: Optional[float]
    spread_ticks: Optional[float]
    mid: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]

    def depth(self, side: str, measure: str) -> Optional[float]:
        if measure == "qty1":
            return self.bid1 if side == "bid" else self.ask1
        return self.topk_bid if side == "bid" else self.topk_ask

    def observable(self) -> bool:
        return any(v is not None for v in (self.bid1, self.ask1, self.topk_bid, self.topk_ask, self.mid))


def _observe(ms: MarketState) -> _Obs:
    """Read the book observables of one state; None for whatever it does not carry."""
    bids = list(ms.bids or [])
    asks = list(ms.asks or [])
    bid1 = ms.bid_qty1 if ms.bid_qty1 is not None else (float(bids[0][1]) if bids else None)
    ask1 = ms.ask_qty1 if ms.ask_qty1 is not None else (float(asks[0][1]) if asks else None)
    topk_bid = float(sum(float(q) for _, q in bids[:TOP_K])) if bids else bid1
    topk_ask = float(sum(float(q) for _, q in asks[:TOP_K])) if asks else ask1
    tick = ms.tick_size if ms.tick_size else None
    best_bid = ms.best_bid if ms.best_bid is not None else (float(bids[0][0]) if bids else None)
    best_ask = ms.best_ask if ms.best_ask is not None else (float(asks[0][0]) if asks else None)
    spread_ticks = ms.spread_ticks
    if spread_ticks is None and tick:
        if ms.spread is not None:
            spread_ticks = ms.spread / tick
        elif best_bid is not None and best_ask is not None:
            spread_ticks = (best_ask - best_bid) / tick
    mid = ms.mid
    if mid is None and best_bid is not None and best_ask is not None:
        mid = (best_bid + best_ask) / 2.0
    return _Obs(ms.t, tick, bid1, ask1, topk_bid, topk_ask, spread_ticks, mid, best_bid, best_ask)


def _median(vals: List[float]) -> Optional[float]:
    return statistics.median(vals) if len(vals) >= MEDIAN_MIN_POINTS else None


def _sign(x: Optional[float]) -> int:
    if x is None or x == 0:
        return 0
    return 1 if x > 0 else -1


class _Track:
    """Per-symbol state: the observation ring, the active curve and the completed ones."""

    def __init__(self) -> None:
        self.obs: Deque[_Obs] = deque()
        self.active: Optional[Dict[str, Any]] = None
        self.last: Optional[Dict[str, Any]] = None
        self.completed: List[Dict[str, Any]] = []
        self.state: Optional[str] = None
        self.updates: int = 0
        self.out: Dict[str, Any] = {}

    def push(self, o: _Obs) -> None:
        self.obs.append(o)
        cutoff = o.t - timedelta(seconds=KEEP_S)
        while len(self.obs) > 1 and self.obs[0].t < cutoff:
            self.obs.popleft()

    def at_or_before(self, t: datetime) -> Optional[_Obs]:
        for o in reversed(self.obs):
            if o.t <= t:
                return o
        return None

    def baseline_window(self, now: datetime) -> List[_Obs]:
        lo = now - timedelta(seconds=MEDIAN_W_S)
        hi = now - timedelta(seconds=SHOCK_W_S)
        return [o for o in self.obs if lo <= o.t <= hi]


class ResilienceEngine:
    """Shock detection and recovery-curve tracking for every symbol it is fed (see module docstring)."""

    def __init__(self) -> None:
        self._tracks: Dict[str, _Track] = {}

    # ------------------------------------------------------------------ public
    def curves(self, symbol: str) -> List[Dict[str, Any]]:
        """Completed curve records of ``symbol`` (oldest first); [] when none."""
        tr = self._tracks.get(symbol)
        return list(tr.completed) if tr else []

    def active_curve(self, symbol: str) -> Optional[Dict[str, Any]]:
        tr = self._tracks.get(symbol)
        return tr.active if tr else None

    def on_state(self, ms: MarketState, hist: Any = None) -> Dict[str, Any]:
        """Consume one state (in event order); returns the fields ``fill_state`` will write."""
        tr = self._tracks.setdefault(ms.symbol, _Track())
        tr.updates += 1
        o = _observe(ms)
        observed = o.observable()
        if observed:
            tr.push(o)
        state: Optional[str]
        rec = tr.active
        if rec is not None:
            if observed:
                self._sample(tr, rec, o)
            elif (o.t - rec["shock_t"]).total_seconds() >= TIMEOUT_S:
                # the book vanished and never came back inside the window: close on what was last seen
                self._close_at_timeout(tr, rec, o.t, rec["final_share"])
            state = tr.state
        else:
            state = None if tr.state is None and not tr.obs else "none"
            if observed:
                shock = self._detect(tr, o, ms)
                if shock is not None and self._recovered(shock, self._shares(shock, o)):
                    # nothing was consumed (e.g. both bests repriced with the depth intact): no curve to track
                    shock = None
                if shock is not None:
                    tr.active = shock
                    tr.last = shock
                    self._sample(tr, shock, o)
                    state = tr.state
                else:
                    tr.state = "none"
                    state = "none"
        tr.out = self._fields(tr, o, state)
        return tr.out

    def fill_state(self, ms: MarketState) -> None:
        """Write the resilience fields computed by the preceding ``on_state`` for this symbol."""
        tr = self._tracks.get(ms.symbol)
        if tr is None or not tr.out:
            ms.resilience_state = None
            ms.recovery_speed = None
            ms.recovery_asymmetry = None
            ms.recovery_curve = None
            ms.liquidity_response = None
            ms.session_state["resilience"] = {"state": None, "observed": False, "curves_completed": 0}
            return
        f = tr.out
        ms.resilience_state = f["state"]
        ms.recovery_speed = f["recovery_speed"]
        ms.recovery_asymmetry = f["recovery_asymmetry"]
        ms.recovery_curve = f["recovery_curve"]
        ms.liquidity_response = f["liquidity_response"]
        ms.session_state["resilience"] = f["record"]

    # ------------------------------------------------------------------ detection
    def _detect(self, tr: _Track, o: _Obs, ms: MarketState) -> Optional[Dict[str, Any]]:
        pre = tr.at_or_before(o.t - timedelta(seconds=SHOCK_W_S))
        base = tr.baseline_window(o.t)
        triggers: List[Dict[str, Any]] = []
        medians: Dict[Tuple[str, str], Optional[float]] = {}
        for side in ("bid", "ask"):
            for measure in ("qty1", "topk"):
                vals = [v for v in (b.depth(side, measure) for b in base) if v is not None]
                med = _median(vals)
                medians[(side, measure)] = med
                cur = o.depth(side, measure)
                prev = pre.depth(side, measure) if pre is not None else None
                if med is None or med <= 0 or cur is None or prev is None:
                    continue
                fall = (prev - cur) / med                                 # the fall inside the burst, in medians
                depleted = cur / med < RECOVERED_SHARE - 1e-9            # a pulled wall is not a depletion
                if fall >= DEPTH_DROP_SHARE - 1e-9 and depleted:
                    triggers.append({"kind": "depth", "side": side, "measure": measure,
                                     "drop_share": fall, "median": med, "pre": prev, "now": cur})
        bid_move = ask_move = None
        if pre is not None and o.tick:
            if pre.best_bid is not None and o.best_bid is not None:
                bid_move = (pre.best_bid - o.best_bid) / o.tick          # + = bid retreated
            if pre.best_ask is not None and o.best_ask is not None:
                ask_move = (o.best_ask - pre.best_ask) / o.tick          # + = ask retreated
        if bid_move is not None and bid_move >= SWEEP_TICKS - 1e-9:
            triggers.append({"kind": "sweep", "side": "bid", "measure": "topk", "ticks": bid_move})
        if ask_move is not None and ask_move >= SWEEP_TICKS - 1e-9:
            triggers.append({"kind": "sweep", "side": "ask", "measure": "topk", "ticks": ask_move})
        if pre is not None and pre.spread_ticks is not None and o.spread_ticks is not None:
            widen = o.spread_ticks - pre.spread_ticks
            if widen >= SPREAD_WIDEN_TICKS - 1e-9:
                bm = bid_move if bid_move is not None else 0.0
                am = ask_move if ask_move is not None else 0.0
                side = "bid" if bm > am else ("ask" if am > bm else "both")
                triggers.append({"kind": "spread", "side": side, "measure": "topk", "ticks": widen})
        if not triggers:
            return None
        order = {"sweep": 0, "depth": 1, "spread": 2}
        triggers.sort(key=lambda d: (order[d["kind"]], -(d.get("drop_share") or d.get("ticks") or 0.0)))
        primary = triggers[0]
        side = primary["side"]
        measure = primary["measure"]
        depth_sides = {d["side"] for d in triggers if d["kind"] == "depth"}
        if side != "both" and len(depth_sides) == 2 and primary["kind"] == "depth":
            side = "both"

        def baseline(sd: str, m: str) -> Optional[float]:
            med = medians.get((sd, m))
            if med is not None and med > 0:
                return med
            return pre.depth(sd, m) if pre is not None else None

        shock_move = None
        if pre is not None and pre.mid is not None and o.mid is not None and o.tick:
            shock_move = (o.mid - pre.mid) / o.tick
        direction = _sign(shock_move)
        if direction == 0:
            direction = -1 if side == "bid" else (1 if side == "ask" else 0)
        rec: Dict[str, Any] = {
            "symbol": ms.symbol, "state": "shocked", "shock_t": o.t, "shock_seq": ms.seq, "shock_update": tr.updates,
            "side": side, "measure": measure, "direction": direction, "triggers": triggers,
            "pre_t": pre.t if pre is not None else None,
            "baseline": {"spread_ticks": pre.spread_ticks if pre is not None else None,
                         "mid": pre.mid if pre is not None else None,
                         "best_bid": pre.best_bid if pre is not None else None,
                         "best_ask": pre.best_ask if pre is not None else None,
                         "bid": baseline("bid", measure), "ask": baseline("ask", measure),
                         "bid_qty1": baseline("bid", "qty1"), "ask_qty1": baseline("ask", "qty1"),
                         "bid_topk": baseline("bid", "topk"), "ask_topk": baseline("ask", "topk"),
                         "median_points": len(base)},
            "shock": {"spread_ticks": o.spread_ticks, "mid": o.mid, "best_bid": o.best_bid, "best_ask": o.best_ask,
                      "bid": o.depth("bid", measure), "ask": o.depth("ask", measure),
                      "move_ticks": shock_move},
            "tick": o.tick,
            "share_at_shock": None, "share_bid_at_shock": None, "share_ask_at_shock": None,
            "spread_share_at_shock": None,
            "samples": [], "curve": [],
            "updates": 0, "elapsed_s": 0.0, "final_share": None, "max_share": None, "min_share": None,
            "time_to_recovery_s": None, "updates_to_recovery": None,
            "recovery_speed": None, "recovery_speed_bid": None, "recovery_speed_ask": None, "asymmetry": None,
            "partial": False, "overshoot": False, "overshoot_kind": None, "snapback": False, "snapback_s": None,
            "vacuum": False, "t_end": None,
        }
        return rec

    # ------------------------------------------------------------------ curve
    @staticmethod
    def _shares(rec: Dict[str, Any], o: _Obs) -> Dict[str, Optional[float]]:
        m = rec["measure"]
        b = rec["baseline"]
        share_bid = safe_div(o.depth("bid", m), b["bid"]) if b["bid"] else None
        share_ask = safe_div(o.depth("ask", m), b["ask"]) if b["ask"] else None
        side = rec["side"]
        if side == "bid":
            share = share_bid
        elif side == "ask":
            share = share_ask
        else:
            both = [s for s in (share_bid, share_ask) if s is not None]
            share = min(both) if both else None
        base_spread = b["spread_ticks"]
        shock_spread = rec["shock"]["spread_ticks"]
        spread_share: Optional[float]
        spread_ok: Optional[bool]
        if base_spread is None or o.spread_ticks is None:
            spread_share, spread_ok = None, None
        else:
            widened = (shock_spread - base_spread) if shock_spread is not None else 0.0
            excess = o.spread_ticks - base_spread
            spread_ok = excess <= SPREAD_RECOVERED_TICKS + 1e-9
            if widened > 1e-9:
                spread_share = max(0.0, min(1.0, 1.0 - excess / widened))
            else:
                spread_share = 1.0 if spread_ok else 0.0
        mid_share = None
        move = rec["shock"]["move_ticks"]
        if move and o.mid is not None and b["mid"] is not None and rec["shock"]["mid"] is not None:
            mid_share = (rec["shock"]["mid"] - o.mid) / (rec["shock"]["mid"] - b["mid"])
        return {"share": share, "share_bid": share_bid, "share_ask": share_ask,
                "spread_share": spread_share, "spread_ok": spread_ok, "mid_share": mid_share}

    @staticmethod
    def _recovered(rec: Dict[str, Any], sh: Dict[str, Optional[float]]) -> bool:
        """The recovered condition: shocked-side share ≥ 90 % and the spread within 1 tick of baseline.

        When the spread is unobservable now (or had no baseline) it counts as back only if the shock
        never widened it."""
        spread_ok = sh["spread_ok"]
        if spread_ok is None:
            shock_sp, base_sp = rec["shock"]["spread_ticks"], rec["baseline"]["spread_ticks"]
            widened = shock_sp is not None and base_sp is not None and shock_sp - base_sp > 1e-9
            spread_ok = not widened
        share = sh["share"]
        return share is not None and share >= RECOVERED_SHARE - 1e-9 and bool(spread_ok)

    def _sample(self, tr: _Track, rec: Dict[str, Any], o: _Obs) -> None:
        s = (o.t - rec["shock_t"]).total_seconds()
        first = not rec["samples"]
        if not first:
            rec["updates"] += 1
        sh = self._shares(rec, o)
        share = sh["share"]
        if first:
            rec["share_at_shock"] = share
            rec["share_bid_at_shock"] = sh["share_bid"]
            rec["share_ask_at_shock"] = sh["share_ask"]
            rec["spread_share_at_shock"] = sh["spread_share"]
        rec["elapsed_s"] = s
        rec["samples"].append({"s": s, "share": share, "share_bid": sh["share_bid"], "share_ask": sh["share_ask"],
                               "spread_share": sh["spread_share"], "mid_share": sh["mid_share"],
                               "spread_ticks": o.spread_ticks, "mid": o.mid})
        if share is not None:
            rec["curve"].append((s, share))
            rec["final_share"] = share
            rec["max_share"] = share if rec["max_share"] is None else max(rec["max_share"], share)
            rec["min_share"] = share if rec["min_share"] is None else min(rec["min_share"], share)
        # speeds (shares per second since the shock)
        if s > 0:
            rec["recovery_speed"] = _speed(share, rec["share_at_shock"], s)
            rec["recovery_speed_bid"] = _speed(sh["share_bid"], rec["share_bid_at_shock"], s)
            rec["recovery_speed_ask"] = _speed(sh["share_ask"], rec["share_ask_at_shock"], s)
            if rec["recovery_speed_bid"] is not None and rec["recovery_speed_ask"] is not None:
                rec["asymmetry"] = rec["recovery_speed_bid"] - rec["recovery_speed_ask"]
        # snapback: mid reverted ≥ 80 % of the shock move within 60 s
        ms_ = sh["mid_share"]
        if ms_ is not None and s <= SNAPBACK_W_S and ms_ >= SNAPBACK_SHARE and not rec["snapback"]:
            rec["snapback"] = True
            rec["snapback_s"] = s
        # overshoot: depth beyond 130 % on a shocked side, or mid past its pre-shock level by ≥ 1 tick.
        # It describes the recovery, so the shock sample itself is not eligible.
        if not first:
            sides = ("bid", "ask") if rec["side"] == "both" else (rec["side"],)
            for sd in sides:
                v = sh["share_" + sd]
                v0 = rec["share_" + sd + "_at_shock"]
                # only a side that was actually depleted (share < 1 at the shock) can overshoot on its way back
                if v is not None and v0 is not None and v0 < 1.0 - 1e-9 and v > OVERSHOOT_SHARE:
                    rec["overshoot"] = True
                    rec["overshoot_kind"] = rec["overshoot_kind"] or "depth"
            move = rec["shock"]["move_ticks"]
            if move and ms_ is not None and (ms_ - 1.0) * abs(move) >= 1.0 - 1e-9:
                rec["overshoot"] = True
                rec["overshoot_kind"] = rec["overshoot_kind"] or "mid"
        # state
        if self._recovered(rec, sh):
            rec["time_to_recovery_s"] = s
            rec["updates_to_recovery"] = rec["updates"]
            self._close(tr, rec, o.t, "overshoot" if rec["overshoot"] else "recovered")
            return
        if s >= TIMEOUT_S:
            self._close_at_timeout(tr, rec, o.t, share)
            return
        improving = False
        if share is not None and rec["share_at_shock"] is not None and \
                share - rec["share_at_shock"] >= RECOVERING_DEPTH_MARGIN - 1e-9:
            improving = True
        if sh["spread_share"] is not None and rec["spread_share_at_shock"] is not None and \
                sh["spread_share"] - rec["spread_share_at_shock"] >= RECOVERING_SPREAD_MARGIN - 1e-9:
            improving = True
        if s >= VACUUM_S and share is not None and share < PARTIAL_LO:
            rec["vacuum"] = True
            st = "vacuum"
        elif improving:
            st = "recovering"
        elif s >= VACUUM_S:
            st = "partial"
        else:
            st = "shocked"
        rec["state"] = st
        tr.state = st

    @classmethod
    def _close_at_timeout(cls, tr: _Track, rec: Dict[str, Any], t: datetime, share: Optional[float]) -> None:
        """Timeout close: vacuum when the (last seen) share is < 30 %, else partial; the flags follow the state."""
        if share is not None and share < PARTIAL_LO:
            rec["vacuum"] = True
            cls._close(tr, rec, t, "vacuum")
        else:
            rec["partial"] = True
            cls._close(tr, rec, t, "partial")

    @staticmethod
    def _close(tr: _Track, rec: Dict[str, Any], t: datetime, terminal: str) -> None:
        rec["state"] = terminal
        rec["t_end"] = t
        rec["duration_s"] = (t - rec["shock_t"]).total_seconds()
        tr.completed.append(rec)
        tr.active = None
        tr.last = rec
        tr.state = terminal

    # ------------------------------------------------------------------ output
    def _fields(self, tr: _Track, o: _Obs, state: Optional[str]) -> Dict[str, Any]:
        rec = tr.active or tr.last
        speed = asym = None
        curve: Optional[List[Tuple[float, float]]] = None
        liq: Optional[float] = None
        if rec is not None:
            curve = list(rec["curve"])
            if tr.active is not None or state in TERMINAL:
                speed = rec["recovery_speed"]
                asym = rec["asymmetry"]
            age = (o.t - rec["shock_t"]).total_seconds()
            if 0 <= age <= TIMEOUT_S and rec["share_at_shock"] is not None and o.observable():
                share_now = self._shares(rec, o)["share"]
                if share_now is not None:
                    liq = share_now - rec["share_at_shock"]
        record: Dict[str, Any]
        if rec is None:
            record = {"state": state, "observed": bool(tr.obs), "curves_completed": len(tr.completed)}
        else:
            record = dict(rec)                      # snapshot: the live lists keep growing
            samples = rec["samples"]
            del record["samples"]                   # per-update samples stay on the engine record (curves())
            record["samples_n"] = len(samples)
            record["last_sample"] = dict(samples[-1]) if samples else None
            record["curve"] = list(rec["curve"])
            record["curves_completed"] = len(tr.completed)
            record["active"] = tr.active is not None
        return {"state": state, "recovery_speed": speed, "recovery_asymmetry": asym, "recovery_curve": curve,
                "liquidity_response": liq, "record": record}


def _speed(now: Optional[float], start: Optional[float], s: float) -> Optional[float]:
    if now is None or start is None or s <= 0:
        return None
    return (now - start) / s
