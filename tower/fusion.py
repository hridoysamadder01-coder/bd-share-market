"""Multi-source reconciliation: per-source status, field-level fusion with
provenance, and explicit agreement / disagreement between sources.

The tower observes the same exchange through several sensors at once — two
depth pages (``lankabd_depth``, ``dsebd_depth``), a tape of cumulative totals
(``lankabd_tape``), quote pollers (``lankabd_watch``, ``dsebd_latest``,
``lankabd_grid``) — each with its own cadence, lag and failure modes. The
:class:`Fuser` never blends values: every fused field is **one** source's
observation (the *primary*), the ``provenance`` map names that source, and
whenever another source that observed the same thing at about the same time
says something different, the difference is exposed in ``disagreement`` with
both values and both sources. Nothing is chosen silently.

Rules (all causal — ``now`` is the frame time handed in by the engine; the
module never reads a clock):

* **Per-source status** is tracked per (source, symbol) — market-wide sources
  under symbol ``None``. ``last_update`` is the ``t_recv`` of the last accepted
  event (GAP events are not updates), ``t_exch`` the last exchange stamp
  carried, ``updates`` counts accepted events, ``duplicates`` the ones the
  normalizer flagged ``duplicate``, ``gaps`` the GAP events naming that key,
  ``field_coverage`` is the union of ``observed_fields`` seen.
* **Cadence** of a (source, symbol) is learned causally as the median of the
  inter-update receipt gaps seen so far (bounded ring of the last 64 gaps),
  exposed as ``SourceStatus.cadence_s``. A source is **stale** at ``now`` when
  ``now − last_update`` exceeds ``max(stale_min_s, stale_factor × cadence)``
  (defaults 30 s, 3×); before the first gap the floor alone applies.
* **Book primary** (``fuse_book``): among book sources holding an image for
  the symbol, the *concurrent set* is the sources whose last **book
  observation** (snapshot or incremental update receipt — a ``STATUS`` such as
  an unparsed page keeps a source alive but does not refresh its image) lies
  within ``coalesce_s`` of the newest one and that are not stale; the
  previous primary is kept while it is in that set and does not *lag*: it
  lags when another concurrent source saw a content change after the
  primary's last change, that change is older than ``coalesce_s`` at ``now``
  (the primary had a full window to catch up) and the two images differ
  (stability: two sensors that are equally current carry the same book, and
  switching would only break per-sensor dynamics; identical images never
  cause a switch). Otherwise the primary is the concurrent source with the most
  recent non-duplicate (content-changing) snapshot, ties broken by freshest
  receipt, then ``SOURCE_PRIORITY``, then name. If every source is stale the
  freshest one is used (and reported stale in ``sources``).
* **Book comparison**: when at least two sources are concurrent, the
  primary's displayed levels are compared level-by-level (index-aligned, best
  first; price and quantity) with each other concurrent source. Identical on
  both sides → ``agreement["book"] = True``; otherwise ``disagreement["book"]``
  carries the number of differing levels, both sources, both best bid / ask,
  both receipt times and the first differing levels.
* **Quote fields** (``fuse_quote``: ``ltp``, ``day_trades``, ``day_volume``,
  ``day_value``): every source that observes the field (depth snapshots, QUOTE
  pollers, tape CUM_TOTALS, prints) is a candidate; the primary per field is
  the source with the freshest receipt of that field (ties: exchange-stamped
  first, then ``SOURCE_PRIORITY``, then name). Another source is compared when
  its observation lies within ``max(coalesce_s, cadence_this, cadence_other)``
  of the primary's — a slow poller is judged within one of its own cycles,
  not within a window it can never meet. ``ltp`` must match exactly; day
  totals agree when the other value equals the primary's or lags it by at
  most ``total_tol`` (5 %) — a lagging poller is expected, a *leading* one or
  a larger gap is a disagreement. Fields a page marks as unpopulated zeros
  (``zero_fields``) and fields absent from ``observed_fields`` are not
  observations. Tape rows (``CUM_TOTALS`` / ``TRADE``) whose exchange stamp
  falls on another trading date than their receipt are the previous session's
  tape (a pull made before the day's first trade returns it): they count as a
  receipt for the source's status but are neither quote observations nor a
  tape delivery for the symbol. Several rows of one pull share a receipt time;
  the row with the newest exchange stamp is the observation.
* **fill_state**: copies per-source status with ``freshness_s`` / ``stale``
  at ``now`` into ``ms.sources`` (each with its own ``agreement`` /
  ``disagreement`` view), sets ``ms.source_agreement`` /
  ``ms.source_disagreement``, ``ms.provenance`` (field → source) for the fused
  fields (plus ``book`` and ``tape``), ``ms.book_source`` / ``ms.book_age_s``
  (age of the primary's last book observation) from the book primary and
  ``ms.tape_source`` / ``ms.tape_age_s`` from the freshest tape source (age of
  its last current-session row) when the tape engine has not named a feed. Fused quote
  values fill ``ms.ltp`` / ``ms.trade_count`` / ``ms.trade_volume`` /
  ``ms.trade_value`` and the displayed book only where upstream engines left
  them empty — the primary's value, never a blend.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Tuple

from seeing.clock import trading_date

from .events import SOURCE_PRIORITY, Event, EventType
from .state import MarketState, SourceStatus

BOOK_SOURCES: Tuple[str, ...] = ("lankabd_depth", "dsebd_depth", "fix_md", "itch", "broker_l2_export")
TAPE_SOURCES: Tuple[str, ...] = ("lankabd_tape", "broker_tns_export", "minute_dataset")
QUOTE_FIELDS: Tuple[str, ...] = ("ltp", "day_trades", "day_volume", "day_value")

# payload key → canonical fused field (several sources spell the same quantity differently)
_PAYLOAD_TO_FIELD: Dict[str, str] = {
    "ltp": "ltp", "price": "ltp",
    "day_trades": "day_trades", "cum_trades": "day_trades",
    "day_volume": "day_volume", "cum_volume": "day_volume",
    "day_value_mn": "day_value", "cum_value_mn": "day_value", "day_value": "day_value",
}
_EPS = 1e-9


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _levels(levels: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for lv in levels or []:
        if lv is None or len(lv) < 2:
            continue
        p, q = _num(lv[0]), _num(lv[1])
        if p is None or q is None:
            continue
        out.append((p, q))
    return out


def _priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 60)


# ---------------------------------------------------------------------------- per-source tracking
@dataclass
class _Track:
    """Status and cadence memory of one (source, symbol)."""

    status: SourceStatus
    gaps: Deque[float] = field(default_factory=lambda: deque(maxlen=64))
    cadence_s: Optional[float] = None

    def on_receipt(self, t: datetime, unchanged: bool) -> None:
        prev = self.status.last_update
        if prev is not None:
            gap = (t - prev).total_seconds()
            if gap > 0:
                # several rows of one pull share a t_recv: one receipt, not a 0 s cadence
                self.gaps.append(gap)
                self.cadence_s = float(statistics.median(self.gaps))
        self.status.last_update = t if prev is None or t >= prev else prev
        self.status.updates += 1
        self.status.duplicate = unchanged

    def stale_threshold(self, stale_min_s: float, stale_factor: float) -> float:
        if self.cadence_s is None or self.cadence_s <= 0:
            return stale_min_s
        return max(stale_min_s, stale_factor * self.cadence_s)


@dataclass
class _Obs:
    """One observed value of one fused field by one source."""

    value: float
    t: datetime
    t_exch: Optional[datetime]
    unchanged: bool


@dataclass
class _Snap:
    """Latest full book image held for one (source, symbol)."""

    t: datetime
    t_exch: Optional[datetime]
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]
    unchanged: bool


@dataclass
class _Cand:
    """A book source's standing for one symbol at ``now``."""

    source: str
    t_last: datetime                             # last book observation receipt (snapshot or update)
    t_change: Optional[datetime]                 # last content-changing book receipt
    snap: Optional[_Snap]                        # None for incremental sources (image lives in their book)


# ---------------------------------------------------------------------------- the fuser
class Fuser:
    """See the module docstring for every rule."""

    def __init__(self, coalesce_s: float = 6.0, *, stale_min_s: float = 30.0, stale_factor: float = 3.0,
                 total_tol: float = 0.05, max_diff_examples: int = 5) -> None:
        self.coalesce_s = float(coalesce_s)
        self.stale_min_s = float(stale_min_s)
        self.stale_factor = float(stale_factor)
        self.total_tol = float(total_tol)
        self.max_diff_examples = int(max_diff_examples)
        self._tracks: Dict[Tuple[str, Optional[str]], _Track] = {}
        self._books: Dict[Tuple[str, str], _Snap] = {}                 # (source, symbol) → latest image
        self._book_last: Dict[Tuple[str, str], datetime] = {}          # (source, symbol) → last book receipt
        self._book_change: Dict[Tuple[str, str], datetime] = {}        # (source, symbol) → last content change
        self._quotes: Dict[Tuple[str, str], Dict[str, _Obs]] = {}       # (source, symbol) → field → obs
        self._tape_last: Dict[Tuple[str, str], datetime] = {}          # (source, symbol) → last current-session tape row
        self._primary: Dict[str, str] = {}                              # symbol → sticky book primary
        self.events_seen = 0
        self.previous_session_rows = 0                                  # tape rows stamped on another trading date

    # ------------------------------------------------------------------ status helpers
    def _track(self, source: str, symbol: Optional[str]) -> _Track:
        k = (source, symbol)
        tr = self._tracks.get(k)
        if tr is None:
            tr = _Track(status=SourceStatus(source=source))
            self._tracks[k] = tr
        return tr

    def cadence_s(self, source: str, symbol: Optional[str] = None) -> Optional[float]:
        """Learned median inter-update gap of (source, symbol); None before the first gap."""
        tr = self._tracks.get((source, symbol))
        return tr.cadence_s if tr else None

    def stale_threshold_s(self, source: str, symbol: Optional[str] = None) -> Optional[float]:
        tr = self._tracks.get((source, symbol))
        return tr.stale_threshold(self.stale_min_s, self.stale_factor) if tr else None

    def is_stale(self, source: str, symbol: Optional[str], now: datetime) -> Optional[bool]:
        """``None`` when the source never delivered for this key (nothing to judge)."""
        tr = self._tracks.get((source, symbol))
        if tr is None or tr.status.last_update is None:
            return None
        age = (now - tr.status.last_update).total_seconds()
        return age > tr.stale_threshold(self.stale_min_s, self.stale_factor)

    def source_status(self, source: str, symbol: Optional[str], now: datetime) -> Optional[SourceStatus]:
        """A copy of the (source, symbol) status with freshness / stale evaluated at ``now``."""
        tr = self._tracks.get((source, symbol))
        if tr is None:
            return None
        st = replace(tr.status, agreement={}, disagreement={}, field_coverage=tuple(tr.status.field_coverage))
        if st.last_update is not None:
            st.freshness_s = (now - st.last_update).total_seconds()
            st.stale = st.freshness_s > tr.stale_threshold(self.stale_min_s, self.stale_factor)
        else:
            st.freshness_s = None
            st.stale = False
        st.cadence_s = tr.cadence_s
        return st

    # ------------------------------------------------------------------ ingestion
    def on_event(self, ev: Event) -> None:
        """Track status for ``ev.source`` and keep the latest book image / quote
        observations of its symbol. Events must arrive in ``sort_key`` order."""
        self.events_seen += 1
        et = ev.event_type
        symbol = ev.symbol.upper() if ev.symbol else None
        tr = self._track(ev.source, symbol)
        if et == EventType.GAP:
            tr.status.gaps += 1          # counted on the key the gap names; a GAP is not an update
            return
        dup_flag = bool(ev.flags.get("duplicate"))
        unchanged = dup_flag or bool(ev.flags.get("unchanged"))
        tr.on_receipt(ev.t_recv, unchanged)
        if dup_flag:
            tr.status.duplicates += 1
        if ev.t_exch is not None:
            tr.status.t_exch = ev.t_exch
        if ev.observed_fields:
            cov = list(tr.status.field_coverage)
            for f in ev.observed_fields:
                if f not in cov:
                    cov.append(f)
            tr.status.field_coverage = tuple(cov)
        if symbol is None:
            return
        k = (ev.source, symbol)
        if et == EventType.BOOK_SNAPSHOT:
            p = ev.payload or {}
            self._books[k] = _Snap(t=ev.t_recv, t_exch=ev.t_exch, bids=_levels(p.get("bids")),
                                   asks=_levels(p.get("asks")), unchanged=unchanged)
            self._note_book_receipt(k, ev.t_recv, unchanged)
            self._observe_quote(ev, symbol, unchanged)
        elif et == EventType.BOOK_UPDATE:
            self._note_book_receipt(k, ev.t_recv, unchanged)
        elif et in (EventType.CUM_TOTALS, EventType.TRADE):
            if ev.t_exch is not None and trading_date(ev.t_exch) != trading_date(ev.t_recv):
                # a pull made before the day's first trade returns the previous session's rows:
                # a receipt (the feed is alive), not an observation of today's tape
                self.previous_session_rows += 1
                return
            self._tape_last[k] = ev.t_recv
            self._observe_quote(ev, symbol, unchanged)
        elif et == EventType.QUOTE:
            self._observe_quote(ev, symbol, unchanged)

    def _note_book_receipt(self, k: Tuple[str, str], t: datetime, unchanged: bool) -> None:
        prev = self._book_last.get(k)
        if prev is None or t >= prev:
            self._book_last[k] = t
        if not unchanged:
            prev_c = self._book_change.get(k)
            if prev_c is None or t >= prev_c:
                self._book_change[k] = t

    def _observe_quote(self, ev: Event, symbol: str, unchanged: bool) -> None:
        p = ev.payload or {}
        zero = set(p.get("zero_fields") or [])
        obs = self._quotes.setdefault((ev.source, symbol), {})
        observed = set(ev.observed_fields or ())
        seen: set = set()

        def put(fld: str, value: float) -> None:
            cur = obs.get(fld)
            if cur is not None and cur.t == ev.t_recv and cur.t_exch is not None and (
                    ev.t_exch is None or cur.t_exch > ev.t_exch):
                return          # same pull, an older exchange stamp: the newer row stays the observation
            obs[fld] = _Obs(value=value, t=ev.t_recv, t_exch=ev.t_exch, unchanged=unchanged)

        for key, fld in _PAYLOAD_TO_FIELD.items():
            if key not in p or fld in seen or fld not in observed or key in zero:
                continue
            v = _num(p.get(key))
            if v is None:
                continue
            seen.add(fld)
            put(fld, v)
        if ev.event_type == EventType.TRADE and "ltp" not in seen and ev.price is not None:
            # a print IS the last traded price: the trade itself is the observation
            put("ltp", float(ev.price))

    # ------------------------------------------------------------------ book fusion
    def _book_candidates(self, symbol: str, now: datetime) -> Dict[str, _Cand]:
        """Book sources with an observation for ``symbol`` at or before ``now``."""
        out: Dict[str, _Cand] = {}
        for (src, sym), t_last in self._book_last.items():
            if sym != symbol or t_last > now:
                continue
            snap = self._books.get((src, sym))
            if snap is not None and snap.t > now:
                continue            # only the latest image is held; nothing observable for this source yet
            t_change = self._book_change.get((src, sym))
            if t_change is not None and t_change > now:
                t_change = None
            out[src] = _Cand(src, t_last, t_change, snap)
        return out

    def _concurrent(self, symbol: str, now: datetime, cands: Dict[str, _Cand]) -> List[str]:
        """Sources whose last book observation lies within ``coalesce_s`` of the newest one and that
        are not stale at ``now``; when none qualifies (the newest is itself stale), the single
        freshest one — shown, and reported stale in ``sources``."""
        if not cands:
            return []
        newest = max(c.t_last for c in cands.values())
        live = [s for s, c in cands.items()
                if (newest - c.t_last).total_seconds() <= self.coalesce_s and not self.is_stale(s, symbol, now)]
        if live:
            return sorted(live, key=lambda s: (-cands[s].t_last.timestamp(), _priority(s), s))
        best = sorted(cands.values(), key=lambda c: (-c.t_last.timestamp(), _priority(c.source), c.source))[0]
        return [best.source]

    def primary_book_source(self, symbol: str, now: datetime) -> Optional[str]:
        """The book source the frame at ``now`` is built from (rule in the module docstring)."""
        symbol = symbol.upper()
        cands = self._book_candidates(symbol, now)
        if not cands:
            return None
        conc = self._concurrent(symbol, now, cands)
        cur = self._primary.get(symbol)
        if cur in conc and not self._primary_lags(cands, cur, conc, now):
            return cur

        def rank(src: str) -> Tuple[float, float, int, str]:
            c = cands[src]
            tc = c.t_change.timestamp() if c.t_change else float("-inf")
            return (-tc, -c.t_last.timestamp(), _priority(src), src)

        chosen = sorted(conc, key=rank)[0]
        self._primary[symbol] = chosen
        return chosen

    def _primary_lags(self, cands: Dict[str, _Cand], cur: str, conc: List[str], now: datetime) -> bool:
        """The sticky primary lags when another concurrent source observed a content
        change more recently than the primary's last change, that change is older
        than ``coalesce_s`` at ``now`` (the primary had a full window to catch up),
        and the two images actually differ (an incremental source that cannot be
        compared counts as differing). Identical images never trigger a switch —
        two sensors' first snapshots are both "changes" without disagreeing."""
        me = cands[cur]
        my_tc = me.t_change.timestamp() if me.t_change is not None else float("-inf")
        for s in conc:
            if s == cur:
                continue
            o = cands[s]
            if o.t_change is None or o.t_change.timestamp() <= my_tc:
                continue
            if (now - o.t_change).total_seconds() <= self.coalesce_s:
                continue
            if me.snap is None or o.snap is None:
                return True
            if _compare_books(me.snap, o.snap, cur, s, 0)["n_diff_levels"] > 0:
                return True
        return False

    def _book_detail(self, symbol: str, now: datetime) -> Tuple[Optional[str], Optional[_Snap], List[Dict[str, Any]]]:
        """(primary, primary image, per-other comparison dicts — n_diff_levels == 0 means identical)."""
        cands = self._book_candidates(symbol, now)
        primary = self.primary_book_source(symbol, now)
        if primary is None:
            return None, None, []
        snap = cands[primary].snap
        compared: List[Dict[str, Any]] = []
        if snap is not None:
            for other in self._concurrent(symbol, now, cands):
                osnap = cands[other].snap
                if other == primary or osnap is None:
                    continue
                compared.append(_compare_books(snap, osnap, primary, other, self.max_diff_examples))
        return primary, snap, compared

    def fuse_book(self, symbol: str, now: datetime) -> Tuple[Optional[List[Tuple[float, float]]],
                                                              Optional[List[Tuple[float, float]]],
                                                              Optional[str], Dict[str, bool], Dict[str, Any]]:
        """``(bids, asks, book_source, agreement, disagreement)`` at ``now``.

        ``bids``/``asks`` are the primary's latest displayed levels (``None`` when
        the primary is an incremental source whose image lives in its own
        EvolvingBook, or when no book source has delivered). ``agreement``
        carries ``"book"`` only when a second concurrent source exists;
        ``disagreement["book"]`` is the comparison with the most differing
        other source, ``others_compared`` naming every source compared."""
        symbol = symbol.upper()
        agreement: Dict[str, bool] = {}
        disagreement: Dict[str, Any] = {}
        primary, snap, compared = self._book_detail(symbol, now)
        if primary is None:
            return None, None, None, agreement, disagreement
        bids = list(snap.bids) if snap is not None else None
        asks = list(snap.asks) if snap is not None else None
        if compared:
            bad = [d for d in compared if d["n_diff_levels"] > 0]
            agreement["book"] = not bad
            if bad:
                worst = dict(max(bad, key=lambda d: d["n_diff_levels"]))
                worst["others_compared"] = [d["other_source"] for d in compared]
                disagreement["book"] = worst
        return bids, asks, primary, agreement, disagreement

    # ------------------------------------------------------------------ quote fusion
    def _quote_detail(self, symbol: str, now: datetime) -> Dict[str, Dict[str, Any]]:
        """field → {value, source, t, compared: [{other, other_source, dt_s, window_s, rule, agree}]}."""
        out: Dict[str, Dict[str, Any]] = {}
        for fld in QUOTE_FIELDS:
            obs: List[Tuple[str, _Obs]] = []
            for (src, sym), fields in self._quotes.items():
                if sym != symbol:
                    continue
                o = fields.get(fld)
                if o is not None and o.t <= now:
                    obs.append((src, o))
            if not obs:
                continue
            obs.sort(key=lambda so: (-so[1].t.timestamp(), 0 if so[1].t_exch is not None else 1,
                                     _priority(so[0]), so[0]))
            psrc, pobs = obs[0]
            compared: List[Dict[str, Any]] = []
            for osrc, oobs in obs[1:]:
                window = max(self.coalesce_s, self.cadence_s(psrc, symbol) or 0.0, self.cadence_s(osrc, symbol) or 0.0)
                dt = (pobs.t - oobs.t).total_seconds()
                if abs(dt) > window:
                    continue
                ok, rule = _quote_agree(fld, pobs.value, oobs.value, self.total_tol)
                compared.append({"other": oobs.value, "other_source": osrc, "other_t": oobs.t.isoformat(),
                                 "dt_s": dt, "window_s": window, "rule": rule, "agree": ok})
            out[fld] = {"value": pobs.value, "source": psrc, "t": pobs.t, "compared": compared}
        return out

    def fuse_quote(self, symbol: str, now: datetime) -> Tuple[Dict[str, Optional[float]], Dict[str, str],
                                                               Dict[str, bool], Dict[str, Any]]:
        """``(values, provenance, agreement, disagreement)`` for :data:`QUOTE_FIELDS` at ``now``
        (rule in the module docstring). ``values[f]`` is None when no source observed ``f``;
        ``agreement[f]`` exists only when another source was comparable."""
        symbol = symbol.upper()
        values: Dict[str, Optional[float]] = {f: None for f in QUOTE_FIELDS}
        provenance: Dict[str, str] = {}
        agreement: Dict[str, bool] = {}
        disagreement: Dict[str, Any] = {}
        for fld, det in self._quote_detail(symbol, now).items():
            values[fld] = det["value"]
            provenance[fld] = det["source"]
            comp = det["compared"]
            if not comp:
                continue
            bad = [c for c in comp if not c["agree"]]
            agreement[fld] = not bad
            if bad:
                first = bad[0]
                disagreement[fld] = {"this": det["value"], "this_source": det["source"], "this_t": det["t"].isoformat(),
                                     "other": first["other"], "other_source": first["other_source"],
                                     "other_t": first["other_t"], "dt_s": first["dt_s"], "window_s": first["window_s"],
                                     "rule": first["rule"], "others": [dict(c) for c in comp]}
        return values, provenance, agreement, disagreement

    # ------------------------------------------------------------------ tape
    def tape_source(self, symbol: str, now: datetime) -> Tuple[Optional[str], Optional[float]]:
        """Freshest tape source (CUM_TOTALS / TRADE deliverer) for the symbol and its age at ``now``."""
        symbol = symbol.upper()
        best: Optional[Tuple[datetime, str]] = None
        for (src, sym), t in self._tape_last.items():
            if sym != symbol or t > now:
                continue
            if best is None or t > best[0] or (t == best[0] and _priority(src) < _priority(best[1])):
                best = (t, src)
        if best is None:
            return None, None
        return best[1], (now - best[0]).total_seconds()

    # ------------------------------------------------------------------ state
    def fill_state(self, ms: MarketState, now: datetime) -> None:
        """Write sources, agreement / disagreement, provenance and the book / tape
        source and ages into ``ms`` for the frame at ``now`` (rules above)."""
        symbol = ms.symbol.upper()
        primary, snap, book_compared = self._book_detail(symbol, now)
        qdet = self._quote_detail(symbol, now)

        provenance: Dict[str, str] = {}
        agreement: Dict[str, bool] = {}
        disagreement: Dict[str, Any] = {}
        # per-source agreement views: (source → field → agreed) and (source → field → {this, other, other_source})
        src_agree: Dict[str, Dict[str, bool]] = {}
        src_disagree: Dict[str, Dict[str, Any]] = {}

        def note(src: str, fld: str, ok: bool, this: Any, other: Any, other_src: str, extra: Optional[Dict[str, Any]] = None) -> None:
            src_agree.setdefault(src, {})
            src_agree[src][fld] = src_agree[src].get(fld, True) and ok
            if not ok and fld not in src_disagree.setdefault(src, {}):
                d = {"this": this, "other": other, "other_source": other_src}
                if extra:
                    d.update(extra)
                src_disagree[src][fld] = d

        # ---- book
        if primary is not None:
            ms.book_source = primary
            t_last = self._book_last.get((primary, symbol))
            ms.book_age_s = (now - t_last).total_seconds() if t_last is not None else None
            provenance["book"] = primary
            if snap is not None and not ms.bids and not ms.asks and ms.best_bid is None and ms.best_ask is None:
                # nothing upstream displayed a book: show the primary's image (its own values, unblended)
                ms.bids, ms.asks = list(snap.bids), list(snap.asks)
                ms.best_bid = snap.bids[0][0] if snap.bids else None
                ms.best_ask = snap.asks[0][0] if snap.asks else None
                ms.bid_qty1 = snap.bids[0][1] if snap.bids else None
                ms.ask_qty1 = snap.asks[0][1] if snap.asks else None
                ms.empty_book = not (snap.bids or snap.asks)
            if ms.best_bid is not None:
                provenance["best_bid"] = primary
            if ms.best_ask is not None:
                provenance["best_ask"] = primary
            if book_compared:
                bad = [d for d in book_compared if d["n_diff_levels"] > 0]
                agreement["book"] = not bad
                if bad:
                    worst = dict(max(bad, key=lambda d: d["n_diff_levels"]))
                    worst["others_compared"] = [d["other_source"] for d in book_compared]
                    disagreement["book"] = worst
                for d in book_compared:
                    ok = d["n_diff_levels"] == 0
                    this_l1 = {"best_bid": d["this_best_bid"], "best_ask": d["this_best_ask"], "n_levels": d["this_n_levels"]}
                    other_l1 = {"best_bid": d["other_best_bid"], "best_ask": d["other_best_ask"], "n_levels": d["other_n_levels"]}
                    ex = {"n_diff_levels": d["n_diff_levels"], "dt_s": d["dt_s"]}
                    note(primary, "book", ok, this_l1, other_l1, d["other_source"], ex)
                    note(d["other_source"], "book", ok, other_l1, this_l1, primary, dict(ex, dt_s=-d["dt_s"]))

        # ---- quote fields
        for fld, det in qdet.items():
            provenance[fld] = det["source"]
            comp = det["compared"]
            if comp:
                bad = [c for c in comp if not c["agree"]]
                agreement[fld] = not bad
                if bad:
                    first = bad[0]
                    disagreement[fld] = {"this": det["value"], "this_source": det["source"],
                                         "this_t": det["t"].isoformat(), "other": first["other"],
                                         "other_source": first["other_source"], "other_t": first["other_t"],
                                         "dt_s": first["dt_s"], "window_s": first["window_s"], "rule": first["rule"],
                                         "others": [dict(c) for c in comp]}
                for c in comp:
                    ex = {"rule": c["rule"], "dt_s": c["dt_s"]}
                    note(det["source"], fld, c["agree"], det["value"], c["other"], c["other_source"], ex)
                    note(c["other_source"], fld, c["agree"], c["other"], det["value"], det["source"], dict(ex, dt_s=-c["dt_s"]))
        if ms.ltp is None and "ltp" in qdet:
            ms.ltp = qdet["ltp"]["value"]
        if ms.trade_count is None and "day_trades" in qdet:
            ms.trade_count = qdet["day_trades"]["value"]
        if ms.trade_volume is None and "day_volume" in qdet:
            ms.trade_volume = qdet["day_volume"]["value"]
        if ms.trade_value is None and "day_value" in qdet:
            ms.trade_value = qdet["day_value"]["value"]

        # ---- tape
        tsrc, tage = self.tape_source(symbol, now)
        if tsrc is not None:
            provenance["tape"] = tsrc
            if ms.tape_source is None or ms.tape_source == tsrc:
                ms.tape_source, ms.tape_age_s = tsrc, tage

        ms.source_agreement = agreement
        ms.source_disagreement = disagreement
        ms.provenance = provenance

        # ---- per-source status for this symbol (+ market-wide entries of sources not keyed by it)
        sources: Dict[str, SourceStatus] = {}
        for (src, sym) in list(self._tracks.keys()):
            if sym is not None and sym != symbol:
                continue
            if sym is None and (src, symbol) in self._tracks:
                continue
            st = self.source_status(src, sym, now)
            if st is not None:
                sources[src] = st
        for src, st in sources.items():
            st.agreement = dict(src_agree.get(src, {}))
            st.disagreement = dict(src_disagree.get(src, {}))
        ms.sources = sources


# ---------------------------------------------------------------------------- comparison rules
def _quote_agree(fld: str, this: float, other: float, tol: float) -> Tuple[bool, str]:
    """ltp: exact. Day totals: the other source may LAG (other ≤ this) by at
    most ``tol`` of this value; equal always agrees; a leading other never does."""
    if fld == "ltp":
        return abs(this - other) <= _EPS, "ltp exact"
    if abs(this - other) <= _EPS:
        return True, "total equal"
    if other > this:
        return False, "other leads this (this source lags)"
    if this > 0 and (this - other) / this <= tol:
        return True, f"other lags within {tol:.0%}"
    return False, f"other lags beyond {tol:.0%}"


def _compare_books(this: _Snap, other: _Snap, this_src: str, other_src: str, max_examples: int = 5) -> Dict[str, Any]:
    """Level-by-level (index-aligned, best first) comparison of price and quantity."""
    n_diff = 0
    examples: List[Dict[str, Any]] = []
    for side, a, b in (("bid", this.bids, other.bids), ("ask", this.asks, other.asks)):
        for i in range(max(len(a), len(b))):
            la = a[i] if i < len(a) else None
            lb = b[i] if i < len(b) else None
            same = (la is not None and lb is not None and abs(la[0] - lb[0]) <= _EPS and abs(la[1] - lb[1]) <= _EPS)
            if not same:
                n_diff += 1
                if len(examples) < max_examples:
                    examples.append({"side": side, "level": i, "this": la, "other": lb})
    return {
        "n_diff_levels": n_diff,
        "this_source": this_src, "other_source": other_src,
        "this_best_bid": this.bids[0][0] if this.bids else None,
        "other_best_bid": other.bids[0][0] if other.bids else None,
        "this_best_ask": this.asks[0][0] if this.asks else None,
        "other_best_ask": other.asks[0][0] if other.asks else None,
        "this_n_levels": (len(this.bids), len(this.asks)),
        "other_n_levels": (len(other.bids), len(other.asks)),
        "this_t": this.t.isoformat(), "other_t": other.t.isoformat(),
        "dt_s": (this.t - other.t).total_seconds(),
        "this_unchanged": this.unchanged, "other_unchanged": other.unchanged,
        "examples": examples,
    }
