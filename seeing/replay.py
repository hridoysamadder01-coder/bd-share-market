"""Replay: raw segments → parsed, timestamp-normalized tables.

Replay is the only consumer of the raw store. It never writes back. Every
record's body is verified against its sha256 before parsing; a body that fails
is listed under ``problems`` and skipped, never repaired.

Tables returned by ``replay(root)`` (all pandas DataFrames, UTC-aware):

    books     one row per book snapshot per source (lankabd_depth, dsebd_depth)
    watch     one row per instrument per all-symbol watch poll
    tape      one row per exchange-stamped cumulative-total row per symbol,
              de-duplicated across pulls (first receipt wins; the pull count is kept)
    market    market-wide totals per poll
    block     block-board prints per poll
    circuit   reference limits per symbol per poll
    hts       holidays / sessions per poll (as JSON strings)
    gaps      every GAP record
    heartbeats
    meta      every META record (runner configuration, universe, token page …)

Receipt time ``t_recv`` is the HTTP last-byte time when present, else the
record's own receipt time. Source time ``t_source`` is the exchange stamp
when the source carries one (watch, tape); books carry none — LankaBD and
dsebd depth pages have no timestamp — so for books ``t_source`` is
NOT_OBSERVABLE and ``t_recv`` is the frame clock.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .capture.adapters import dsebd, lankabd
from .capture.http_client import PoliteClient
from .capture.raw_store import decode_body, iter_segment, sha256_bytes
from .truth import Truth


def _adapters() -> Dict[str, Any]:
    client = PoliteClient()
    lb = lankabd.build_adapters(client)
    return {
        "lankabd_depth": lb["depth"], "lankabd_watch": lb["watch"], "lankabd_tape": lb["tape"],
        "lankabd_market": lb["market"], "lankabd_block": lb["block"], "lankabd_circuit": lb["circuit"],
        "lankabd_grid": lb["grid"], "dsebd_latest": dsebd.DSEBDLatestAdapter(client),
        "dsebd_depth": dsebd.DSEBDDepthAdapter(client), "dsebd_hts": dsebd.DSEBDSessionsAdapter(client),
    }


def _segment_paths(root: str) -> List[str]:
    """Segments in manifest order, then any unlisted (unclosed) segments by name."""
    listed: List[str] = []
    mpath = os.path.join(root, "MANIFEST.json")
    if os.path.exists(mpath):
        man = json.load(open(mpath))
        for s in man.get("segments", []):
            p = os.path.join(root, s.get("gz_path") or s["path"])
            if os.path.exists(p):
                listed.append(p)
    on_disk = sorted(glob.glob(os.path.join(root, "segments", "*.jsonl")) +
                     glob.glob(os.path.join(root, "segments", "*.jsonl.gz")))
    unlisted = [p for p in on_disk if p not in set(listed)]
    return listed + unlisted


def _t_recv(rec: Dict[str, Any]) -> str:
    http = rec.get("http") or {}
    return http.get("t_last_byte_utc") or rec["t_recv_utc"]


def replay(root: str, sources: Optional[List[str]] = None) -> Dict[str, Any]:
    ad = _adapters()
    rows: Dict[str, List[Dict[str, Any]]] = {k: [] for k in
                                             ("books", "watch", "tape", "market", "block", "circuit", "hts",
                                              "gaps", "heartbeats", "meta", "latest")}
    problems: List[str] = []
    counts: Dict[str, int] = {}
    for path in _segment_paths(root):
        for rec, ok in iter_segment(path):
            if not ok:
                problems.append(f"unparseable line in {os.path.basename(path)}")
                continue
            src = rec.get("source")
            kind = rec.get("kind")
            if kind == "HEARTBEAT":
                rows["heartbeats"].append({"t_recv": rec["t_recv_utc"], "seq": rec["seq"], **{
                    f"age_{k}": v for k, v in (rec.get("status", {}).get("ages_s") or {}).items()}})
                continue
            if kind == "GAP":
                rows["gaps"].append({"t_recv": rec["t_recv_utc"], "source": src, "key": rec.get("key"),
                                     "reason": rec.get("reason"), "detail": rec.get("detail"),
                                     "status": (rec.get("http") or {}).get("status")})
                continue
            if kind == "META":
                rows["meta"].append({"t_recv": rec["t_recv_utc"], "source": src, "seq": rec["seq"],
                                     "payload": json.dumps({k: v for k, v in rec.items()
                                                            if k not in ("kind", "source", "seq", "capturer_id",
                                                                         "epoch", "t_recv_utc", "t_recv_mono_ns")},
                                                           default=str)})
                continue
            if kind != "DATA" or (sources and src not in sources):
                continue
            counts[src] = counts.get(src, 0) + 1
            try:
                body = decode_body(rec)
            except Exception as e:  # noqa: BLE001
                problems.append(f"{src} seq {rec.get('seq')}: body undecodable: {e}")
                continue
            if rec.get("body_sha256") and sha256_bytes(body) != rec["body_sha256"]:
                problems.append(f"{src} seq {rec.get('seq')}: body sha256 mismatch — skipped")
                continue
            adapter = ad.get(src)
            if adapter is None:
                continue
            parsed = adapter.parse(body, rec.get("key"))
            base = {"source": src, "t_recv": _t_recv(rec), "seq": rec["seq"], "epoch": rec["epoch"],
                    "body_sha256": rec["body_sha256"], "http_status": (rec.get("http") or {}).get("status"),
                    "elapsed_ms": (rec.get("http") or {}).get("elapsed_ms")}
            for pr in parsed.problems:
                problems.append(f"{src} seq {rec['seq']} {rec.get('key') or ''}: {pr}")
            table = {"lankabd_depth": "books", "dsebd_depth": "books", "lankabd_watch": "watch",
                     "lankabd_tape": "tape", "lankabd_market": "market", "lankabd_block": "block",
                     "lankabd_circuit": "circuit", "dsebd_hts": "hts", "dsebd_latest": "latest",
                     "lankabd_grid": "latest"}.get(src)
            if table is None:
                continue
            for fr in parsed.frames:
                if table == "hts":
                    rows[table].append({**base, "holidays": json.dumps(fr.get("holidays")),
                                        "sessions": json.dumps(fr.get("sessions"))})
                else:
                    rows[table].append({**base, **fr})

    out: Dict[str, Any] = {"problems": problems, "counts": counts, "root": root}
    for k, v in rows.items():
        df = pd.DataFrame(v)
        if len(df) and "t_recv" in df.columns:
            df["t_recv"] = pd.to_datetime(df["t_recv"], utc=True)
        out[k] = df

    # ---- books: frame clock is receipt; sort; duplicate-payload flag per (source, symbol)
    b = out["books"]
    if len(b):
        b = b.sort_values(["symbol", "source", "t_recv", "seq"], kind="mergesort").reset_index(drop=True)
        b["dup_payload"] = b.groupby(["source", "symbol"])["body_sha256"].transform(lambda s: s.eq(s.shift()))
        b.attrs["truth"] = {"bid_levels": Truth.OBSERVED, "ask_levels": Truth.OBSERVED, "t_recv": Truth.OBSERVED,
                            "t_source": Truth.NOT_OBSERVABLE, "bid_orders_per_level": Truth.NOT_OBSERVABLE,
                            "ask_orders_per_level": Truth.NOT_OBSERVABLE}
        out["books"] = b

    # ---- tape: de-duplicate exchange-stamped rows across pulls (first receipt wins)
    t = out["tape"]
    if len(t):
        t = t.sort_values(["symbol", "t_source_ms", "t_recv", "seq"], kind="mergesort")
        t["pulls_seen"] = t.groupby(["symbol", "t_source_ms", "cum_trades", "cum_volume"])["seq"].transform("size")
        # a row whose values differ between pulls at the same stamp is a source-side correction: kept, flagged
        t["stamp_versions"] = t.groupby(["symbol", "t_source_ms"])["cum_trades"].transform("nunique")
        t = t.drop_duplicates(subset=["symbol", "t_source_ms", "cum_trades", "cum_volume", "cum_value_mn"],
                              keep="first").reset_index(drop=True)
        t["t_source"] = pd.to_datetime(t["t_source_utc"], utc=True)
        t.attrs["truth"] = {"cum_trades": Truth.OBSERVED, "cum_volume": Truth.OBSERVED, "cum_value_mn": Truth.OBSERVED,
                            "price": Truth.OBSERVED, "t_source": Truth.OBSERVED, "trade_prints": Truth.NOT_OBSERVABLE}
        out["tape"] = t

    w = out["watch"]
    if len(w):
        w["t_source"] = pd.to_datetime(w["t_source_utc"], utc=True)
        out["watch"] = w.sort_values(["t_recv", "symbol"], kind="mergesort").reset_index(drop=True)
    return out


def summary(tables: Dict[str, Any]) -> Dict[str, Any]:
    s: Dict[str, Any] = {"counts": tables["counts"], "n_problems": len(tables["problems"])}
    for k in ("books", "watch", "tape", "market", "block", "circuit", "gaps", "heartbeats"):
        df = tables.get(k)
        s[k] = int(len(df)) if df is not None else 0
    b = tables.get("books")
    if b is not None and len(b):
        s["book_symbols"] = sorted(b["symbol"].dropna().unique().tolist())
        s["book_t_range"] = [str(b["t_recv"].min()), str(b["t_recv"].max())]
        s["book_nonempty_frames"] = int(((b["n_bid_levels"] > 0) | (b["n_ask_levels"] > 0)).sum())
        s["book_changed_frames"] = int((~b["dup_payload"]).sum())
        s["max_levels_seen"] = {"bid": int(b["n_bid_levels"].max()), "ask": int(b["n_ask_levels"].max())}
    return s
