"""Whole-market sweep → committable tables.

The raw store is the record of truth and it is large: one whole-market sweep of
691 DSE listings is roughly half a gigabyte of server-rendered HTML, which
belongs on disk, not in git. What belongs in git is what the sweep *found* — a
few hundred kilobytes of CSV — plus the manifest that ties every row back to the
sha256 of the bytes it was parsed from.

So this module is the bridge between the two: it replays a store, writes one CSV
per non-empty table, and writes a COVERAGE.json that answers the only question
worth asking about a breadth pull — for each source, how many of the universe's
symbols came back, how many were missing, and why the rest are not there.

A missing symbol is never silently dropped. `not_found` (the source does not
list this instrument) and `parse_error` (the page exists but carries no such
block) are counted separately, because they mean different things: the first is
a coverage boundary of that source, the second is either an instrument type the
parser does not handle or a layout change worth looking at.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from .capture.raw_store import verify_store
from .replay import replay


def coverage(tables: Dict[str, Any], universe: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Per source: symbols returned, symbols missing, and the reason for each gap."""
    uni = sorted({s.upper() for s in (universe or [])})
    gaps = tables.get("gaps")
    out: Dict[str, Any] = {"universe_size": len(uni), "sources": {}}

    gap_by_source: Dict[str, Dict[str, List[str]]] = {}
    if gaps is not None and len(gaps):
        for _, g in gaps.iterrows():
            src, reason = g.get("source"), g.get("reason")
            key = (g.get("key") or "")
            gap_by_source.setdefault(src, {}).setdefault(reason, []).append(key)

    for name, df in tables.items():
        if not isinstance(df, pd.DataFrame) or name in ("gaps", "heartbeats", "meta") or not len(df):
            continue
        for src, part in df.groupby("source"):
            syms = sorted({str(s).upper() for s in part["symbol"].dropna()}) \
                if "symbol" in part.columns else []
            reasons = {r: len(v) for r, v in gap_by_source.get(src, {}).items()}
            row: Dict[str, Any] = {
                "table": name, "rows": int(len(part)), "symbols_returned": len(syms),
                "polls": int(part["seq"].nunique()) if "seq" in part.columns else None,
                "gap_reasons": reasons,
            }
            if uni and syms:
                missing = sorted(set(uni) - set(syms))
                row["universe_covered"] = len(set(uni) & set(syms))
                row["universe_missing"] = len(missing)
                row["missing_sample"] = missing[:25]
            out["sources"][src] = row

    # Sources that produced no rows at all still deserve a line: a source that
    # failed every attempt must not vanish from the coverage report.
    for src, reasons in gap_by_source.items():
        out["sources"].setdefault(src, {"table": None, "rows": 0, "symbols_returned": 0,
                                        "polls": 0, "gap_reasons": {r: len(v) for r, v in reasons.items()}})
    return out


def extract(root: str, out_dir: str, universe: Optional[Sequence[str]] = None,
            verify: bool = True) -> Dict[str, Any]:
    """Replay `root`, write CSVs + COVERAGE.json into `out_dir`, return the summary."""
    os.makedirs(out_dir, exist_ok=True)
    tables = replay(root)
    written: Dict[str, Dict[str, Any]] = {}
    for name, df in tables.items():
        if not isinstance(df, pd.DataFrame) or not len(df):
            continue
        path = os.path.join(out_dir, f"{name}.csv")
        df.to_csv(path, index=False)
        written[name] = {"path": os.path.basename(path), "rows": int(len(df)),
                         "columns": list(df.columns), "bytes": os.path.getsize(path)}

    report: Dict[str, Any] = {
        "store": root,
        "tables": written,
        "coverage": coverage(tables, universe),
        "problems": tables.get("problems", [])[:200],
        "n_problems": len(tables.get("problems", [])),
        "raw_records_by_source": tables.get("counts", {}),
    }
    if verify:
        v = verify_store(root)
        report["store_verified"] = {"all_ok": v["all_ok"], "chain_ok": v["chain_ok"],
                                    "segments": v["n"]}
    with open(os.path.join(out_dir, "COVERAGE.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", required=True, help="raw store directory to replay")
    p.add_argument("--out", required=True, help="directory for the CSVs and COVERAGE.json")
    p.add_argument("--universe", default="", help="comma list of symbols the sweep aimed at")
    p.add_argument("--no-verify", action="store_true")
    a = p.parse_args(argv)
    uni = [s.strip().upper() for s in a.universe.split(",") if s.strip()]
    rep = extract(a.store, a.out, universe=uni, verify=not a.no_verify)
    print(json.dumps({"tables": {k: v["rows"] for k, v in rep["tables"].items()},
                      "n_problems": rep["n_problems"],
                      "store_verified": rep.get("store_verified")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
