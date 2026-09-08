"""One trading session, the whole market — the intraday counterpart to the EOD sweep.

The 2026-09-08 pull established the market's *picture*: 691 listings, 395 of them
traded, with circuit limits, fundamentals, ownership and dividends. What it could
not establish is the market's *film*. Depth and tape are `TRADING_PHASES` sources,
and everything the research runs on — E1, the TLPI family, quote+tape fusion — has
so far come from **14 symbols on one session**. That is 14 of 395: 3.5 %.

This runner closes that gap on the public sources only, in its own store, on its
own branch, writing nowhere near `micro/`. It is the deliberate alternative to
widening the pre-registered capture, which is frozen and must not be touched.

**The symbol rule.** Poll every symbol that actually traded on the most recent
session we have an EOD snapshot for, falling back to the identity spine's DSE
listings. Not a top-N by liquidity: capturing more is never a selection bias, but
capturing only the liquid names and calling the result whole-market is. The ~230
treasury bills and bonds in the 691 are dropped only because they did not trade —
if one trades, it is in the list the next day by the same rule.

**What width costs, stated up front.** One depth sweep of N symbols takes at least
`N * min_gap` seconds. At 395 symbols and a 0.4 s gap that is 158 s, against a
public refresh of roughly 43 s. So a market-wide frame is ~3.7 refreshes apart:
coarser than the 43.7 s frames the 14-symbol capture produced, and comparable to
the h2 horizon rather than to a tick. `SESSION.json` records the achieved sweep
interval per source so nobody later reads a 158 s frame as a 43 s one.

**Politeness.** `--cadence-scale` multiplies every cadence. Use it when another
capture is live against the same hosts, so two runs cannot double the request rate
on dsebd.org and LankaBD between them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from ..clock import CONTINUOUS_END, PRE_OPEN_START, now_utc, to_dhaka, trading_date
from .engine import PublicMarketEngine, build_registry
from .http_client import PoliteClient

# Nothing this runner writes may land inside the pre-registered experiment. The
# prohibition is a hard check rather than a comment, because "do not touch micro/"
# is exactly the sort of rule that a later convenience edit erodes.
FORBIDDEN_DIR = "micro"

DEFAULT_OUT_ROOT = "evidence/market_day"

# The intraday set. Every per-symbol source shares one request budget, so what is
# left out decides the resolution of what is kept:
#
#   all registered sources          book frame 4.57 min
#   this set                        book frame 2.85 min
#   lankabd_depth alone (+ cheap)   book frame 2.57 min
#
# `dsebd_depth` is excluded on purpose. It is a genuine second view of the book
# and it cost 37 % of the budget for TWO levels where LankaBD gives five; the two
# were already cross-validated against each other on the 14-symbol capture, so at
# market width the levels are worth more than the redundancy. `--only` puts it
# back for a session where cross-checking matters more than resolution.
#
# The per-symbol fundamentals and ownership sources are excluded because they do
# not change intraday — they belong to the closed-market sweep, and their cadences
# would give them almost no budget here anyway.
DEFAULT_INTRADAY_SOURCES = (
    "lankabd_depth",      # 5-level book — the reason this run exists
    "lankabd_tape",       # cumulative trade totals, for the quote+tape fusion
    "lankabd_watch",      # all-symbol L1, cheap, one request
    "lankabd_market",     # market-wide totals
    "lankabd_block",      # block board
    "lankabd_grid",       # L1 grid, a second cheap cross-check
    "dsebd_latest",       # the exchange's own all-symbol snapshot
    "lankabd_circuit",    # limits can move intraday on a halt
)


def assert_safe_out(path: str) -> str:
    """Refuse any output path inside the pre-registered experiment.

    Compares path COMPONENTS rather than string prefixes, so `micrometer/` and
    `evidence/micro_notes` pass while `micro/`, `./micro/x` and any absolute path
    containing a `micro` directory do not. The first version resolved against
    `os.getcwd()`, which made the answer depend on where the process happened to
    be started — the wrong property for a guard whose whole job is to be
    unconditional.
    """
    parts = [p for p in os.path.normpath(path).replace("\\", "/").split("/") if p not in ("", ".")]
    if FORBIDDEN_DIR in parts:
        raise ValueError(f"refusing to write inside the pre-registered experiment: {path}")
    return path


def traded_universe(extract_csv: str) -> List[str]:
    """Symbols that actually traded, from an EOD snapshot already captured.

    `day_volume > 0` is the whole rule. A symbol listed but untraded carries no
    book worth sweeping, and a symbol that trades once is back in the list the
    next day — no threshold, no ranking, nothing to tune.
    """
    import pandas as pd
    df = pd.read_csv(extract_csv)
    if "symbol" not in df.columns:
        raise ValueError(f"{extract_csv} has no symbol column")
    vol = pd.to_numeric(df.get("day_volume"), errors="coerce") if "day_volume" in df else None
    if vol is not None:
        df = df[vol.fillna(0) > 0]
    return sorted({str(s).upper() for s in df["symbol"].dropna()})


def spine_universe(identity_map: str, exchange: str = "DSE") -> List[str]:
    with open(identity_map, encoding="utf-8") as fh:
        d = json.load(fh)
    return sorted({l["code"] for l in d["core"]["listings"] if l.get("exchange") == exchange})


def resolve_universe(traded_from: Optional[str], spine_from: Optional[str]) -> Dict[str, Any]:
    """The symbol list plus the provenance of how it was chosen."""
    if traded_from and os.path.exists(traded_from):
        syms = traded_universe(traded_from)
        if syms:
            return {"symbols": syms, "rule": "traded on the last captured session "
                                             "(day_volume > 0)", "source": traded_from}
    if spine_from and os.path.exists(spine_from):
        syms = spine_universe(spine_from)
        return {"symbols": syms, "rule": "every DSE listing on the identity spine "
                                         "(no EOD snapshot available to narrow it)",
                "source": spine_from}
    raise FileNotFoundError("no traded snapshot and no identity map — cannot choose a universe")


def session_window(day: Optional[str] = None) -> Dict[str, Any]:
    """Pre-open through post-close for a trading date, as UTC instants."""
    now = now_utc()
    d = datetime.strptime(day, "%Y-%m-%d").date() if day else trading_date(now)
    dhaka_open = to_dhaka(now).replace(year=d.year, month=d.month, day=d.day,
                                       hour=PRE_OPEN_START.hour, minute=PRE_OPEN_START.minute,
                                       second=0, microsecond=0)
    dhaka_end = dhaka_open.replace(hour=CONTINUOUS_END.hour, minute=CONTINUOUS_END.minute) \
        + timedelta(minutes=15)          # through the close session, plus a margin
    return {"trading_date": d.isoformat(),
            "start_utc": dhaka_open.astimezone(now.tzinfo),
            "end_utc": dhaka_end.astimezone(now.tzinfo)}


def plan(symbols: Sequence[str], min_gap: float, cadence_scale: float = 1.0,
         only: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """What a sweep of this width actually costs, before running it.

    Reported rather than assumed, because width buys breadth by spending
    resolution and the exchange rate is not obvious. A per-symbol source cannot
    go round faster than `n * min_gap` whatever cadence it declares — but the
    number that decides whether the data is usable is `round_s`: every per-symbol
    source shares ONE request budget, so three of them over 384 symbols is 1,152
    requests, and at 0.4 s that is a 7.7-minute frame, not a 43-second one.

    Reading an 8-minute frame as if it were a tick is the mistake this function
    exists to prevent, so the figure goes in `SESSION.json` and in the store META.
    """
    client = PoliteClient(min_gap_s=min_gap)
    n = len(symbols)
    specs = []
    for s in build_registry(client, list(symbols)):
        if s.blocked or not s.enabled or not s.runs_in("CONTINUOUS"):
            continue
        if only and s.name not in only:
            continue
        specs.append(s)

    # Demand, in requests per second, is what each source WANTS: its symbol count
    # over its cadence. Capacity is one request per `min_gap`. Summing the first
    # and comparing it with the second is the whole arithmetic, and it is not
    # optional — a 384-symbol depth feed on a 20 s cadence wants 19.2 req/s
    # against a capacity of 2.5.
    demand = {}
    for s in specs:
        cadence = max(s.cadence_for("CONTINUOUS") * cadence_scale, 1e-9)
        demand[s.name] = (n if s.per_symbol else 1) / cadence
    total_demand = sum(demand.values())
    capacity = 1.0 / max(min_gap, 1e-9)
    oversubscribed = total_demand > capacity

    rows = []
    for s in specs:
        cadence = s.cadence_for("CONTINUOUS") * cadence_scale
        polls = n if s.per_symbol else 1
        if oversubscribed:
            # `next_due` allocates by overdue-ness in cadence units, which settles
            # at a share proportional to each source's own demand. A slow source
            # therefore does NOT cost a fast one a whole pass — the mistake in the
            # first version of this function, which counted every per-symbol source
            # as if it cycled every round and reported 12.8 min where the depth
            # pair actually achieves about 5.
            rate = capacity * demand[s.name] / total_demand
            pass_s = polls / max(rate, 1e-12)
        else:
            pass_s = max(cadence, polls * min_gap)
        rows.append({"source": s.name, "kind": s.kind, "per_symbol": s.per_symbol,
                     "declared_cadence_s": round(cadence, 1),
                     "wants_req_per_s": round(demand[s.name], 3),
                     "budget_share": round(demand[s.name] / total_demand, 4),
                     "pass_s": round(pass_s, 1), "pass_min": round(pass_s / 60.0, 2)})

    books = [r for r in rows if r["kind"] == "book"]
    return {"n_symbols": n, "min_gap_s": min_gap, "cadence_scale": cadence_scale,
            "capacity_req_per_s": round(capacity, 3),
            "demand_req_per_s": round(total_demand, 3),
            "oversubscribed_x": round(total_demand / capacity, 1),
            # The number that decides whether the data is microstructure or bars:
            # how long a full sweep of the ORDER BOOK takes once the budget is split.
            "book_frame_s": round(min((r["pass_s"] for r in books), default=float("nan")), 1),
            "book_frame_min": round(min((r["pass_s"] for r in books),
                                        default=float("nan")) / 60.0, 2),
            "sources": sorted(rows, key=lambda r: -r["budget_share"])}


def run(out_dir: str, symbols: Sequence[str], minutes: float, min_gap: float,
        cadence_scale: float = 1.0, universe_note: Optional[Dict[str, Any]] = None,
        only: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    assert_safe_out(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    client = PoliteClient(min_gap_s=min_gap, timeout_s=40.0)
    specs = build_registry(client, list(symbols))
    if only:
        want = set(only)
        specs = [s for s in specs if s.name in want]
    if cadence_scale != 1.0:
        for sp in specs:
            sp.cadence_scale = cadence_scale
    eng = PublicMarketEngine(out_dir, specs, symbols=list(symbols), client=client)
    eng.store.write_meta("market_day", {
        "universe": universe_note or {}, "n_symbols": len(symbols),
        "plan": plan(symbols, min_gap, cadence_scale, only),
        "note": "whole-market intraday sweep; public sources only; writes nothing under micro/",
    })
    return eng.run_for(minutes)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    p.add_argument("--date", default=None, help="trading date (default: today in Dhaka)")
    p.add_argument("--traded-from", default="evidence/public_engine/2026-09-08-full/extract/latest.csv",
                   help="EOD snapshot that decides which symbols traded")
    p.add_argument("--spine", default="evidence/identity/2026-09-08/IDENTITY_MAP.json",
                   help="fallback universe when no EOD snapshot is available")
    p.add_argument("--min-gap", type=float, default=0.4)
    p.add_argument("--cadence-scale", type=float, default=1.0,
                   help="multiply every cadence; use when another capture is live "
                        "against the same hosts")
    p.add_argument("--minutes", type=float, default=None,
                   help="override the run length (default: to the end of the close session)")
    p.add_argument("--only", default=",".join(DEFAULT_INTRADAY_SOURCES),
                   help="comma list of sources to run (default: the intraday set). Every "
                        "per-symbol source shares one request budget, so naming fewer buys "
                        "resolution back. Pass 'all' to run everything the phase allows")
    p.add_argument("--plan-only", action="store_true",
                   help="print what the sweep would cost and exit without fetching")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    uni = resolve_universe(a.traded_from, a.spine)
    win = session_window(a.date)
    syms = uni["symbols"]
    only = None if a.only.strip().lower() == "all" else \
        ([s.strip() for s in a.only.split(",") if s.strip()] or None)

    if a.plan_only:
        print(json.dumps({"trading_date": win["trading_date"], "universe": uni["rule"],
                          **plan(syms, a.min_gap, a.cadence_scale, only)},
                         indent=1, default=str))
        return 0

    if a.minutes is not None:
        minutes = a.minutes
    else:
        minutes = max(0.0, (win["end_utc"] - now_utc()).total_seconds() / 60.0)
        if minutes <= 0:
            print(json.dumps({"skipped": "session window has already closed",
                              "trading_date": win["trading_date"],
                              "end_utc": win["end_utc"].isoformat()}, indent=1))
            return 0

    out_dir = os.path.join(a.out_root, win["trading_date"])
    pl = plan(syms, a.min_gap, a.cadence_scale, only)
    st = run(out_dir, syms, minutes, a.min_gap, a.cadence_scale, universe_note=uni, only=only)
    print(json.dumps({"trading_date": win["trading_date"], "out": out_dir,
                      "n_symbols": len(syms), "book_frame_s": pl["book_frame_s"],
                      "by_status": st["by_status"],
                      "raw_records": st["raw_records"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
