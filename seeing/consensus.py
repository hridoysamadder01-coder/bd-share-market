"""Compare what several sources say about the same instrument, without picking a winner.

DSE, LankaBD, StockNow, CSE and the broker terminal can all publish a book for
BRACBANK. They are independent sensors, not copies, and they disagree in ways
that matter: one is a minute stale, one rounds turnover to millions, one drops
the far side of the book after the close, one stamps its own receipt time and
calls it the exchange time. Averaging them would manufacture a number no source
ever reported; silently preferring one would throw away the disagreement, which
is often the most informative thing on the screen.

So this module reports rather than resolves. Every field keeps every source's
value. ``consensus`` is filled **only** when the sources that reported the field
agree within tolerance; otherwise it stays ``None`` and ``disagreement`` says by
how much. A caller that needs a single number must choose a source explicitly
and say so — the choice is never made here by default.

Three things are measured that a naive field-by-field diff misses:

* **Age vs freshness.** ``age_s`` is how long ago *we* received the payload;
  ``freshness_s`` is how far behind the *source's own* timestamp was when it
  reached us. A source polled a second ago can still be carrying a five-minute-old
  observation, and only the second number shows it.
* **Timestamp offset.** Two sources stamping the same event differently is a
  clock or convention difference, not a data difference, and it is reported
  separately so it is never read as a price disagreement.
* **Missing levels.** A book truncated to 5 levels and a book with 10 do not
  "disagree" on levels 6-10; one is silent. Silence and contradiction are
  counted apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Level = Tuple[float, float]

# Fields whose values are compared as money/prices, where a tick of rounding is
# not a disagreement. Everything else is compared exactly unless a tolerance is
# passed in.
_PRICE_FIELDS = frozenset({"ltp", "open", "high", "low", "close_published", "yclose",
                           "best_bid", "best_ask", "upper_limit", "lower_limit"})
# Turnover is published in units (BDT) by some sources and millions by others;
# comparing them raw produces a 10^6 "disagreement" that means nothing.
_SCALED_FIELDS = frozenset({"day_value", "day_value_mn", "market_value", "market_value_mn"})


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@dataclass
class Observation:
    """One source's view of one instrument at one moment."""

    source: str
    symbol: str
    t_recv_utc: str
    fields: Dict[str, Any] = field(default_factory=dict)
    bid_levels: Optional[Sequence[Level]] = None
    ask_levels: Optional[Sequence[Level]] = None
    t_source_utc: Optional[str] = None
    delayed: bool = False
    delay_note: Optional[str] = None

    def age_s(self, now: datetime) -> Optional[float]:
        t = _parse(self.t_recv_utc)
        return round((now - t).total_seconds(), 3) if t else None

    def freshness_s(self) -> Optional[float]:
        """How far behind the source's own stamp was at the moment we received it."""
        ts, tr = _parse(self.t_source_utc), _parse(self.t_recv_utc)
        return round((tr - ts).total_seconds(), 3) if (ts and tr) else None


@dataclass
class FieldConsensus:
    field_name: str
    values: Dict[str, Any]                       # source -> value, every source kept
    reporting: List[str]
    silent: List[str]                            # sources that did not carry the field at all
    agree: Optional[bool]
    consensus: Any = None                        # filled ONLY when agree is True
    spread: Optional[float] = None               # max - min across numeric values
    rel_spread: Optional[float] = None           # spread / |median|, for scale-free comparison
    disagreement: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"field": self.field_name, "values": self.values, "reporting": self.reporting,
                "silent": self.silent, "agree": self.agree, "consensus": self.consensus,
                "spread": self.spread, "rel_spread": self.rel_spread,
                "disagreement": self.disagreement}


def _tolerance(field_name: str, explicit: Optional[float]) -> float:
    if explicit is not None:
        return explicit
    if field_name in _PRICE_FIELDS:
        return 0.005          # half a tick on a 0.1-tick market
    return 0.0


def reconcile_field(obs: Sequence[Observation], field_name: str,
                    tolerance: Optional[float] = None) -> FieldConsensus:
    """Every source's value for one field, plus whether they agree. Never averages."""
    values: Dict[str, Any] = {}
    silent: List[str] = []
    for o in obs:
        if field_name in o.fields and o.fields[field_name] is not None:
            values[o.source] = o.fields[field_name]
        else:
            silent.append(o.source)

    reporting = sorted(values)
    if not reporting:
        return FieldConsensus(field_name, {}, [], silent, agree=None,
                              disagreement="no source reported this field")

    nums = {s: float(v) for s, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if len(nums) != len(values):                      # non-numeric: compare exactly
        distinct = {repr(v) for v in values.values()}
        agree = len(distinct) == 1
        return FieldConsensus(field_name, values, reporting, silent, agree,
                              consensus=next(iter(values.values())) if agree else None,
                              disagreement=None if agree else f"{len(distinct)} distinct values: {sorted(distinct)}")

    lo, hi = min(nums.values()), max(nums.values())
    spread = round(hi - lo, 10)
    mid = sorted(nums.values())[len(nums) // 2]
    rel = round(spread / abs(mid), 6) if mid else None
    tol = _tolerance(field_name, tolerance)
    agree = spread <= tol
    note = None
    if not agree:
        note = f"spread {spread:g} over tolerance {tol:g}: " + \
               ", ".join(f"{s}={nums[s]:g}" for s in sorted(nums))
        if field_name in _SCALED_FIELDS and lo > 0 and hi / lo > 1e5:
            note += " — ratio looks like a unit mismatch (BDT vs millions), not a data conflict"
    return FieldConsensus(field_name, values, reporting, silent, agree,
                          consensus=lo if agree else None, spread=spread, rel_spread=rel,
                          disagreement=note)


def reconcile_book(obs: Sequence[Observation], side: str, max_levels: int = 10,
                   tolerance: float = 0.005) -> Dict[str, Any]:
    """Level-by-level book comparison. Truncation is counted apart from contradiction."""
    books: Dict[str, Sequence[Level]] = {}
    for o in obs:
        b = o.bid_levels if side == "bid" else o.ask_levels
        if b is not None:
            books[o.source] = list(b)
    if not books:
        return {"side": side, "sources": [], "levels": [], "note": "no source carried this side"}

    depths = {s: len(b) for s, b in books.items()}
    out_levels: List[Dict[str, Any]] = []
    price_conflicts = qty_conflicts = 0
    for i in range(min(max_levels, max(depths.values()) if depths else 0)):
        present = {s: b[i] for s, b in books.items() if len(b) > i}
        absent = [s for s in books if len(books[s]) <= i]
        prices = {s: float(p) for s, (p, _) in present.items()}
        qtys = {s: float(q) for s, (_, q) in present.items()}
        p_spread = round(max(prices.values()) - min(prices.values()), 10) if prices else None
        q_spread = round(max(qtys.values()) - min(qtys.values()), 10) if qtys else None
        p_agree = p_spread is not None and p_spread <= tolerance
        q_agree = q_spread is not None and q_spread == 0
        price_conflicts += 0 if (p_agree or p_spread is None) else 1
        qty_conflicts += 0 if (q_agree or q_spread is None) else 1
        out_levels.append({
            "level": i, "prices": prices, "quantities": qtys,
            "truncated_for": absent,                 # silent, NOT disagreeing
            "price_spread": p_spread, "quantity_spread": q_spread,
            "price_agree": p_agree, "quantity_agree": q_agree,
        })
    return {"side": side, "sources": sorted(books), "depths": depths,
            "compared_levels": len(out_levels), "price_conflicts": price_conflicts,
            "quantity_conflicts": qty_conflicts, "levels": out_levels,
            "depth_disagreement": len(set(depths.values())) > 1}


def timestamp_offsets(obs: Sequence[Observation]) -> Dict[str, Any]:
    """Pairwise source-timestamp differences — a clock issue, never a price issue."""
    stamped = {o.source: _parse(o.t_source_utc) for o in obs if _parse(o.t_source_utc)}
    pairs: Dict[str, float] = {}
    names = sorted(stamped)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs[f"{a}|{b}"] = round((stamped[a] - stamped[b]).total_seconds(), 3)
    return {"stamped_sources": names,
            "unstamped_sources": sorted({o.source for o in obs} - set(names)),
            "pairwise_offset_s": pairs,
            "max_abs_offset_s": max((abs(v) for v in pairs.values()), default=None)}


def reconcile(obs: Sequence[Observation], fields: Iterable[str], now: datetime,
              stale_after_s: float = 120.0,
              tolerances: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """The full cross-source record for one instrument at one moment.

    Returns everything and decides nothing: per-field values from every source,
    both book sides level by level, ages, freshness, staleness and clock offsets.
    """
    if not obs:
        return {"symbol": None, "sources": [], "note": "nothing to reconcile"}

    symbols = {o.symbol.upper() for o in obs if o.symbol}
    tol = tolerances or {}
    per_field = [reconcile_field(obs, f, tol.get(f)).as_dict() for f in fields]
    disagreeing = [f["field"] for f in per_field if f["agree"] is False]
    agreeing = [f["field"] for f in per_field if f["agree"] is True]

    source_state = {}
    for o in obs:
        age = o.age_s(now)
        source_state[o.source] = {
            "age_s": age, "freshness_s": o.freshness_s(),
            "t_recv_utc": o.t_recv_utc, "t_source_utc": o.t_source_utc,
            "delayed": o.delayed, "delay_note": o.delay_note,
            "stale": (age is not None and age > stale_after_s),
            "n_bid_levels": len(o.bid_levels) if o.bid_levels is not None else None,
            "n_ask_levels": len(o.ask_levels) if o.ask_levels is not None else None,
        }

    return {
        "symbol": sorted(symbols)[0] if len(symbols) == 1 else None,
        "symbol_conflict": sorted(symbols) if len(symbols) > 1 else None,
        "t_eval_utc": now.isoformat(),
        "sources": sorted(o.source for o in obs),
        "source_state": source_state,
        "stale_sources": sorted(s for s, v in source_state.items() if v["stale"]),
        "delayed_sources": sorted(s for s, v in source_state.items() if v["delayed"]),
        "fields": per_field,
        "fields_agreeing": agreeing,
        "fields_disagreeing": disagreeing,
        "agreement_rate": round(len(agreeing) / (len(agreeing) + len(disagreeing)), 4)
        if (agreeing or disagreeing) else None,
        "bid_book": reconcile_book(obs, "bid"),
        "ask_book": reconcile_book(obs, "ask"),
        "timestamps": timestamp_offsets(obs),
        "resolution_policy": "none — every source value is kept; `consensus` is filled "
                             "only where the reporting sources agree within tolerance",
    }
