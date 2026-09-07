#!/usr/bin/env python3
"""Watch the DSE book while the market is CLOSED, to test one specific claim.

The claim (Hridoy, 2026-09-07): the exchange chart cannot move while the market is
closed, but advance orders keep accumulating behind it, and that accumulation is what
moves the chart the next morning. If the accumulation is visible in the book before
the open, it is foreknowledge of the opening pressure. If it is not visible, the claim
may still be true about the broker's internal queue while being unobservable from here.

What was already observed, and is the reason this watcher exists — the closed book is
NOT frozen (evidence/overnight/README.md carries the full note):

    CITYGENINS  20:22-20:31 Dhaka  10 bid levels + 7 ask levels
    CITYGENINS  22:08      Dhaka   0 bid levels +  0 ask levels   <- emptied while closed
    AAMRANET    21:41 -> 22:08     1 bid  (16.80 x 3000) unchanged
    1STPRIMFMF  21:59 -> 22:08     1 ask  (21.90 x 650)  unchanged

So orders DISAPPEAR while closed. The open question this watcher answers is the
opposite direction: do levels APPEAR before the 10:00 Dhaka open?

    levels only disappear, book empty at open  -> accumulation is NOT observable here
    levels appear / grow before the open       -> accumulation IS observable = a signal

This script only READS a public sensor at a polite interval and appends what it saw.
It decides nothing, and it is NOT part of the frozen micro experiment: MICRO_PREREG,
universe, thresholds, splits and holdout are untouched. Any edge claim built on this
would need its own pre-registration first.

    python3 -m micro.engine.overnight_book_watch [--symbols A,B] [--out evidence/overnight]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seeing.capture.adapters import lankabd            # noqa: E402
from seeing.capture.http_client import PoliteClient    # noqa: E402
from seeing.clock import session_phase, to_dhaka       # noqa: E402

# A mix on purpose: symbols seen holding residual orders while closed (AAMRANET,
# 1STPRIMFMF, BEXIMCO, ROBI, BRACBANK) and symbols seen empty (CITYGENINS, GP,
# SQURPHARMA, WALTONHIL). Both behaviours have to be tracked, not just one.
DEFAULT_SYMBOLS = ("CITYGENINS", "AAMRANET", "1STPRIMFMF", "BRACBANK", "GP",
                   "SQURPHARMA", "BEXIMCO", "ROBI", "WALTONHIL")


def snapshot(symbols: List[str], min_gap_s: float = 1.2) -> Dict[str, Any]:
    """One pass over the symbol list. Never raises on a per-symbol failure: a failed
    symbol is recorded as a gap so a missing row is never mistaken for an empty book."""
    client = PoliteClient(min_gap_s=min_gap_s, timeout_s=40)
    ad = lankabd.build_adapters(client)
    now = dt.datetime.now(dt.timezone.utc)
    rows: List[Dict[str, Any]] = []
    for sym in symbols:
        f = ad["depth"].fetch(sym)
        if not f.ok:
            rows.append({"symbol": sym, "gap": True, "status": f.status, "error": f.error})
            continue
        parsed = ad["depth"].parse(f.body, sym)
        if not parsed.frames:
            rows.append({"symbol": sym, "gap": True, "status": f.status,
                         "error": "; ".join(parsed.problems) or "no frame"})
            continue
        fr = parsed.frames[0]
        bids, asks = fr["bid_levels"], fr["ask_levels"]
        rows.append({
            "symbol": sym, "gap": False,
            "bids": bids, "asks": asks,
            "n_bid_levels": len(bids), "n_ask_levels": len(asks),
            "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
            "total_bid_qty": sum(q for _, q in bids if q), "total_ask_qty": sum(q for _, q in asks if q),
            "ltp": fr["ltp"], "day_trades": fr["day_trades"], "day_volume": fr["day_volume"],
            "book_sha256": hashlib.sha256(json.dumps([bids, asks], sort_keys=True).encode()).hexdigest(),
            "problems": parsed.problems,
        })
    return {"t_utc": now.isoformat(), "t_dhaka": to_dhaka(now).isoformat(),
            "session_phase": session_phase(now), "source": "lankabd_depth",
            "sensor_note": "public portal; the terminal republishes the same exchange book",
            "rows": rows}


def append(snap: Dict[str, Any], out_dir: str) -> str:
    """Append one snapshot as a JSON line, named by the Dhaka trading date."""
    day = snap["t_dhaka"][:10]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{day}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return path


def changes_since(path: str, snap: Dict[str, Any]) -> Dict[str, Any]:
    """Compare this snapshot with the previous line in the same file. Reports, per
    symbol, whether the book changed and in WHICH direction: levels appearing is the
    accumulation the claim predicts; levels disappearing is the purge already seen."""
    prev = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        if lines:
            try:
                prev = json.loads(lines[-1])
            except json.JSONDecodeError:
                prev = None
    if prev is None:
        return {"first_snapshot": True}
    old = {r["symbol"]: r for r in prev["rows"] if not r.get("gap")}
    out: Dict[str, Any] = {"since_utc": prev["t_utc"], "appeared": {}, "disappeared": {}, "unchanged": []}
    for r in snap["rows"]:
        if r.get("gap") or r["symbol"] not in old:
            continue
        o = old[r["symbol"]]
        if o["book_sha256"] == r["book_sha256"]:
            out["unchanged"].append(r["symbol"])
            continue
        ob = {tuple(x) for x in o["bids"]} | {tuple(x) for x in o["asks"]}
        nb = {tuple(x) for x in r["bids"]} | {tuple(x) for x in r["asks"]}
        gained, lost = sorted(nb - ob), sorted(ob - nb)
        if gained:
            out["appeared"][r["symbol"]] = gained
        if lost:
            out["disappeared"][r["symbol"]] = lost
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--out", default="evidence/overnight")
    ap.add_argument("--min-gap", type=float, default=1.2)
    a = ap.parse_args(argv[1:])
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    snap = snapshot(syms, min_gap_s=a.min_gap)
    path = os.path.join(a.out, f"{snap['t_dhaka'][:10]}.jsonl")
    delta = changes_since(path, snap)
    append(snap, a.out)
    ok = sum(1 for r in snap["rows"] if not r.get("gap"))
    print(json.dumps({"t_dhaka": snap["t_dhaka"], "phase": snap["session_phase"],
                      "symbols_ok": ok, "gaps": len(snap["rows"]) - ok,
                      "file": path, "delta": delta}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
