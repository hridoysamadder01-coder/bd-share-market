"""Observation Tower API server.

    python3 -m tower.ui.server --store DIR --port 8765 [--host 127.0.0.1]

Reads the state store format from ``tower/CONTRACTS.md``::

    <store>/states/<SYMBOL>.jsonl   one MarketState.to_dict() per line, event order
    <store>/timeline.jsonl          one Transition per line (+ "symbol")
    <store>/metrics.json            engine metrics
    <store>/latest.json             {symbol: last state dict}
    <store>/RUN.json                inputs + final state_hash per symbol

Tailing rule: every request first re-reads whatever bytes were appended to the
files since the previous request (a growing live store and a finished replay
store are served identically). Per-symbol state files are indexed by
``(t, byte offset, length)`` only — a state is parsed on demand, so the memory
footprint is independent of the size of each state.

Time rule (causal): ``?at=<iso>`` answers with the last state, in event order,
up to which every frame time ``t`` is <= ``at`` (bisect over the running
maximum of ``t``, so a live store whose tailer released an event late — a
state with a ``t`` below its predecessor's — never lets a state produced after
one with ``t > at`` leak into the answer); nothing after ``at`` is ever read
to answer a request at ``at``. Times are compared at full microsecond
precision: ``at`` equal to a state's own ``t`` string returns that state.

Downsampling rule: ``/api/history`` picks at most ``max_points`` states, evenly
spaced by index over the requested range, always keeping the first and the
last state of the range.

Nothing here computes market quantities; a field the store does not carry is
served as ``null`` (NOT_OBSERVABLE), never as 0.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..events import utc

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_POINTS = 2000
_T_RE = re.compile(rb'"t"\s*:\s*"([^"]+)"')

# mechanism lifecycle states that open / close an episode (for chart shading)
EPISODE_OPEN = ("building", "active", "confirmed")
EPISODE_CLOSE = ("inactive", "resolved", "failed")


def _parse_at(at: Optional[str]) -> Optional[datetime]:
    """'latest' / None → None; otherwise an aware UTC datetime or HTTP 400."""
    if at is None or at == "" or at == "latest":
        return None
    at = at.strip()
    try:
        return utc(at)
    except (ValueError, TypeError):
        pass
    # an unescaped '+' of a UTC offset arrives URL-decoded as a space ("...T04:00:00 00:00"): restore it
    fixed = re.sub(r" (\d{2}:?\d{2})$", r"+\1", at)
    try:
        if fixed != at:
            return utc(fixed)
    except (ValueError, TypeError):
        pass
    raise HTTPException(status_code=400, detail=f"bad time {at!r}: not an ISO-8601 timestamp")


def _extract_t(line: bytes) -> Optional[datetime]:
    """Frame time of a state line: the first top-level ``"t"`` key (``MarketState``
    serialises ``symbol`` then ``t``, so the first match is the frame time);
    falls back to a full parse when the fast path fails. ``None`` when the line
    carries no usable time (such a line is skipped by the index, never invented)."""
    m = _T_RE.search(line[:400])
    if m:
        try:
            return utc(m.group(1).decode())
        except (ValueError, TypeError):
            pass
    t = json.loads(line).get("t")
    return utc(t) if isinstance(t, str) else None


def _get_path(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


class _StateFile:
    """Tail-indexed ``states/<SYMBOL>.jsonl``: list of frame times and byte spans.

    ``times`` are the frame times in event (file) order; ``keys`` is their running
    maximum, which is what the time queries bisect over: it is monotone even when
    a live tailer released an event late, so "at or before ``at``" always means
    the longest event-order prefix whose frame times are all <= ``at``."""

    def __init__(self, path: str, symbol: str) -> None:
        self.path = path
        self.symbol = symbol
        self.offset = 0                       # bytes indexed so far (complete lines only)
        self.times: List[datetime] = []
        self.keys: List[datetime] = []        # running max of times (monotone bisect key)
        self.spans: List[Tuple[int, int]] = []
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._cache_order: List[int] = []

    def refresh(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.offset:                # truncated / rewritten: re-index from scratch
            self.offset, self.times, self.keys, self.spans = 0, [], [], []
            self._cache.clear()
            self._cache_order.clear()
        if size == self.offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:                            # only a partial line so far
            return
        base, pos = self.offset, 0
        while True:
            nl = data.find(b"\n", pos)
            if nl < 0 or nl > end:
                break
            line = data[pos:nl]
            if line.strip():
                try:
                    t = _extract_t(line)
                except (ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError):
                    t = None
                if t is None:                  # unreadable line / no frame time: skipped, never invented
                    pos = nl + 1
                    continue
                self.times.append(t)
                self.keys.append(t if not self.keys or t > self.keys[-1] else self.keys[-1])
                self.spans.append((base + pos, nl - pos))
            pos = nl + 1
        self.offset = base + end + 1

    def __len__(self) -> int:
        return len(self.times)

    def read(self, i: int) -> Dict[str, Any]:
        d = self._cache.get(i)
        if d is not None:
            return d
        off, ln = self.spans[i]
        with open(self.path, "rb") as fh:
            fh.seek(off)
            d = json.loads(fh.read(ln))
        self._cache[i] = d
        self._cache_order.append(i)
        if len(self._cache_order) > 64:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
        return d

    def index_at_or_before(self, at: Optional[datetime]) -> Optional[int]:
        """Index of the last state (event order) up to which every t <= at
        (None → last state; None if there is none)."""
        if not self.times:
            return None
        if at is None:
            return len(self.times) - 1
        i = bisect.bisect_right(self.keys, at) - 1
        return i if i >= 0 else None

    def range_indices(self, t_from: Optional[datetime], t_to: Optional[datetime]) -> Tuple[int, int]:
        """[lo, hi) event-order slice: from the first state with t >= t_from up to the
        last state at or before t_to (same rule as ``index_at_or_before``)."""
        lo = bisect.bisect_left(self.keys, t_from) if t_from is not None else 0
        hi = bisect.bisect_right(self.keys, t_to) if t_to is not None else len(self.times)
        return lo, hi


class _TimelineFile:
    """Tail-parsed ``timeline.jsonl`` (transitions are small: fully parsed and kept)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.offset = 0
        self.rows: List[Dict[str, Any]] = []

    def refresh(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.offset:
            self.offset, self.rows = 0, []
        if size == self.offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            return
        for line in data[:end].split(b"\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                rec["_t"] = utc(rec["t"])
            except (KeyError, ValueError, TypeError):
                continue
            self.rows.append(rec)
        self.offset += end + 1

    def select(self, symbol: Optional[str], t_from: Optional[datetime], t_to: Optional[datetime]) -> List[Dict[str, Any]]:
        out = []
        for r in self.rows:
            if symbol is not None and r.get("symbol") != symbol:
                continue
            if t_from is not None and r["_t"] < t_from:
                continue
            if t_to is not None and r["_t"] > t_to:
                continue
            out.append({k: v for k, v in r.items() if k != "_t"})
        return out


class StoreReader:
    """Tailing reader over one state store directory (thread-safe)."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.states_dir = os.path.join(root, "states")
        self.files: Dict[str, _StateFile] = {}
        self.timeline = _TimelineFile(os.path.join(root, "timeline.jsonl"))
        self.cursor: Dict[str, Dict[str, Any]] = {}          # replay cursor per symbol (server-side position)
        # one lock for tailing and querying: uvicorn runs sync endpoints on a thread pool, and a
        # re-index after truncation or a cache eviction must not interleave with another request's read
        self.lock = threading.RLock()
        self._lock = self.lock

    # ------------------------------------------------------------ tailing
    def refresh(self) -> None:
        with self._lock:
            try:
                names = sorted(os.listdir(self.states_dir))
            except OSError:
                names = []
            for n in names:
                if not n.endswith(".jsonl"):
                    continue
                sym = n[:-6]
                f = self.files.get(sym)
                if f is None:
                    f = _StateFile(os.path.join(self.states_dir, n), sym)
                    self.files[sym] = f
            for f in self.files.values():
                f.refresh()
            self.timeline.refresh()

    def symbols(self) -> List[str]:
        return sorted(s for s, f in self.files.items() if len(f))

    def file(self, symbol: str) -> _StateFile:
        f = self.files.get(symbol)
        if f is None or not len(f):
            raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
        return f

    def _json_file(self, name: str) -> Optional[Dict[str, Any]]:
        p = os.path.join(self.root, name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None                        # mid-rewrite: report as unavailable, not as empty

    # ------------------------------------------------------------ queries
    def state_at(self, symbol: str, at: Optional[datetime], index: Optional[int] = None) -> Dict[str, Any]:
        """The state at ``at`` (time rule) or, when ``index`` is given, the state at that
        exact event-order position. Consecutive states frequently share one frame time
        (several events of one poll), so a time can only ever address the last state of
        such a group; stepping state by state needs the index."""
        f = self.file(symbol)
        if index is not None:
            if index < 0 or index >= len(f):
                raise HTTPException(status_code=404, detail=f"no state of {symbol} at index {index} (count {len(f)})")
            i = index
        else:
            i = f.index_at_or_before(at)
        if i is None:
            raise HTTPException(status_code=404, detail=f"no state of {symbol} at or before {at.isoformat() if at else 'latest'}")
        d = f.read(i)
        return {
            "symbol": symbol, "index": i, "count": len(f),
            "at": f"#{index}" if index is not None else at.isoformat() if at else "latest",
            "t": f.times[i].isoformat(),
            "first_t": f.times[0].isoformat(), "last_t": f.times[-1].isoformat(),
            "prev_t": f.times[i - 1].isoformat() if i > 0 else None,
            "next_t": f.times[i + 1].isoformat() if i + 1 < len(f) else None,
            "is_last": i == len(f) - 1,
            "state": d,
        }

    def symbol_rows(self) -> List[Dict[str, Any]]:
        out = []
        for sym in self.symbols():
            f = self.files[sym]
            last = f.read(len(f) - 1)
            out.append({
                "symbol": sym, "count": len(f),
                "first_t": f.times[0].isoformat(), "last_t": f.times[-1].isoformat(),
                "session_phase": last.get("session_phase"),
                "active_mechanisms": last.get("active_mechanisms") or [],
                "n_mechanisms": len(last.get("mechanisms") or {}),
                "layer_states": last.get("layer_states") or {},
                "mid": last.get("mid"), "ltp": last.get("ltp"),
                "best_bid": last.get("best_bid"), "best_ask": last.get("best_ask"),
                "empty_book": last.get("empty_book"), "book_source": last.get("book_source"),
                "sector": (last.get("sector") or {}).get("sector"),
                "locked": bool((last.get("circuit") or {}).get("locked_up") or (last.get("circuit") or {}).get("locked_down")),
            })
        return out

    def history(self, symbol: str, fields: List[str], t_from: Optional[datetime], t_to: Optional[datetime],
                max_points: int = MAX_POINTS) -> Dict[str, Any]:
        f = self.file(symbol)
        lo, hi = f.range_indices(t_from, t_to)
        n = max(0, hi - lo)
        if n <= max_points:
            idx = list(range(lo, hi))
        else:
            # exactly max_points indices, evenly spaced from the first to the last state of the range
            idx = [lo + int(round(k * (n - 1) / float(max_points - 1))) for k in range(max_points)]
        points = []
        for i in idx:
            d = f.read(i) if i in f._cache else self._read_nocache(f, i)
            row: Dict[str, Any] = {"t": f.times[i].isoformat()}
            for fld in fields:
                v = _get_path(d, fld)
                row[fld] = v if isinstance(v, (int, float, str, bool)) or v is None else None
            points.append(row)
        return {
            "symbol": symbol, "fields": fields, "from": t_from.isoformat() if t_from else None,
            "to": t_to.isoformat() if t_to else None, "n_total": n, "n": len(points),
            "downsampled": n > len(points), "points": points,
            "episodes": self.episodes(symbol, t_from, t_to),
        }

    @staticmethod
    def _read_nocache(f: _StateFile, i: int) -> Dict[str, Any]:
        off, ln = f.spans[i]
        with open(f.path, "rb") as fh:
            fh.seek(off)
            return json.loads(fh.read(ln))

    def episodes(self, symbol: str, t_from: Optional[datetime], t_to: Optional[datetime]) -> List[Dict[str, Any]]:
        """Mechanism episodes from the timeline: an episode opens on a transition into
        building/active/confirmed from a closed state and closes on a transition into
        inactive/resolved/failed; the closing state is the episode's outcome. Episodes
        open at the end of the store carry ``end: null``."""
        open_: Dict[str, Dict[str, Any]] = {}
        done: List[Dict[str, Any]] = []
        for r in self.timeline.rows:
            if r.get("symbol") != symbol or not str(r.get("layer", "")).startswith("mechanism:"):
                continue
            if t_to is not None and r["_t"] > t_to:
                continue                        # not `break`: a live tailer may release a late event after a later one
            name = r["layer"][len("mechanism:"):]
            to = r.get("to_state")
            ep = open_.get(name)
            if ep is None and to in EPISODE_OPEN:
                open_[name] = {"name": name, "start": r["t"], "end": None, "peak_state": to, "outcome": None, "_start": r["_t"]}
            elif ep is not None:
                if to in EPISODE_OPEN:
                    order = {"building": 0, "active": 1, "confirmed": 2}
                    if order.get(to, 0) > order.get(ep["peak_state"], 0):
                        ep["peak_state"] = to
                elif to in EPISODE_CLOSE:
                    ep["end"], ep["outcome"] = r["t"], to
                    done.append(open_.pop(name))
        done.extend(open_.values())
        if t_from is not None:
            done = [e for e in done if e["end"] is None or utc(e["end"]) >= t_from]
        done.sort(key=lambda e: e["_start"])       # by parsed time, not by string form
        for e in done:
            del e["_start"]
        return done

    def cross(self, symbol: str, at: Optional[datetime], index: Optional[int] = None) -> Dict[str, Any]:
        """Cross-stock view as of ``at`` (or the state at ``index``): the symbol's own
        cross/sector context plus the state (at or before that state's own ``t``) of
        every related symbol — leaders, laggers and same-sector members in the store."""
        s = self.state_at(symbol, at, index)
        st = s["state"]
        cross = st.get("cross") or {}
        sector = st.get("sector") or {}
        related: Dict[str, Dict[str, Any]] = {}

        def add(sym: str, role: str, extra: Optional[Dict[str, Any]] = None) -> None:
            if sym == symbol:
                return
            row = related.get(sym)
            if row is None:
                row = {"symbol": sym, "roles": [], "present": sym in self.files and len(self.files[sym]) > 0}
                if row["present"]:
                    f = self.files[sym]
                    j = f.index_at_or_before(utc(s["t"]))
                    if j is not None:
                        o = f.read(j)
                        row.update({"t": f.times[j].isoformat(), "mid": o.get("mid"), "ltp": o.get("ltp"),
                                    "pressure_direction": o.get("pressure_direction"),
                                    "pressure_strength": o.get("pressure_strength"),
                                    "layer_states": o.get("layer_states") or {},
                                    "locked_up": (o.get("circuit") or {}).get("locked_up"),
                                    "locked_down": (o.get("circuit") or {}).get("locked_down"),
                                    "sector": (o.get("sector") or {}).get("sector"),
                                    "symbol_return_60s": (o.get("cross") or {}).get("symbol_return_60s")})
                related[sym] = row
            row["roles"].append(role)
            if extra:
                row.update(extra)

        for entry in cross.get("leaders") or []:
            sym, lag, corr = (list(entry) + [None, None])[:3]
            add(str(sym), "leader", {"lag_s": lag, "corr": corr})
        for entry in cross.get("laggers") or []:
            sym, lag, corr = (list(entry) + [None, None])[:3]
            add(str(sym), "lagger", {"lag_s": lag, "corr": corr})
        my_sector = sector.get("sector")
        if my_sector is not None:
            for sym in self.symbols():
                if sym == symbol:
                    continue
                f = self.files[sym]
                j = f.index_at_or_before(utc(s["t"]))
                if j is None:
                    continue
                o = f.read(j)
                if (o.get("sector") or {}).get("sector") == my_sector:
                    add(sym, "sector_peer")
        return {"symbol": symbol, "t": s["t"], "cross": cross, "sector": sector,
                "related": sorted(related.values(), key=lambda r: r["symbol"])}

    def replay_info(self) -> Dict[str, Any]:
        syms: Dict[str, Any] = {}
        first: Optional[datetime] = None
        last: Optional[datetime] = None
        total = 0
        for sym in self.symbols():
            f = self.files[sym]
            syms[sym] = {"first_t": f.times[0].isoformat(), "last_t": f.times[-1].isoformat(), "count": len(f)}
            total += len(f)
            first = f.times[0] if first is None or f.times[0] < first else first
            last = f.times[-1] if last is None or f.times[-1] > last else last
        return {"store": self.root, "first_t": first.isoformat() if first else None,
                "last_t": last.isoformat() if last else None, "count": total, "symbols": syms,
                "cursor": dict(self.cursor)}

    def metrics(self) -> Dict[str, Any]:
        m = self._json_file("metrics.json")
        run = self._json_file("RUN.json")
        files = {}
        for name in ("metrics.json", "latest.json", "RUN.json", "timeline.jsonl"):
            p = os.path.join(self.root, name)
            files[name] = {"exists": os.path.exists(p), "bytes": os.path.getsize(p) if os.path.exists(p) else None}
        return {
            "store": self.root, "metrics": m, "metrics_available": m is not None,
            "run": {k: run.get(k) for k in ("capture", "symbols", "t_from", "t_to", "events", "processed",
                                              "states_written", "final_state_hash")} if run else None,
            "files": files,
            "states": {s: {"count": len(f), "bytes": f.offset, "last_t": f.times[-1].isoformat()}
                       for s, f in sorted(self.files.items()) if len(f)},
            "transitions": len(self.timeline.rows),
        }


def create_app(store: str) -> FastAPI:
    reader = StoreReader(store)
    app = FastAPI(title="DSE Observation Tower", version="1.0")
    app.state.reader = reader

    @app.get("/api/symbols")
    def api_symbols() -> Any:
        with reader.lock:
            reader.refresh()
            return {"symbols": reader.symbol_rows()}

    @app.get("/api/state/{symbol}")
    def api_state(symbol: str, at: Optional[str] = Query(default="latest"),
                  index: Optional[int] = Query(default=None, ge=0, description="exact event-order position; overrides at")) -> Any:
        t = _parse_at(at)
        with reader.lock:
            reader.refresh()
            return reader.state_at(symbol, t, index)

    @app.get("/api/history/{symbol}")
    def api_history(symbol: str, fields: str = Query(default="mid,ltp"), from_: Optional[str] = Query(default=None, alias="from"),
                    to: Optional[str] = Query(default=None), max_points: int = Query(default=MAX_POINTS, ge=2, le=MAX_POINTS)) -> Any:
        flds = [x.strip() for x in fields.split(",") if x.strip()]
        if not flds:
            raise HTTPException(status_code=400, detail="fields required")
        t_from, t_to = _parse_at(from_), _parse_at(to)
        with reader.lock:
            reader.refresh()
            return reader.history(symbol, flds, t_from, t_to, max_points)

    @app.get("/api/timeline/{symbol}")
    def api_timeline_symbol(symbol: str, from_: Optional[str] = Query(default=None, alias="from"),
                            to: Optional[str] = Query(default=None)) -> Any:
        t_from, t_to = _parse_at(from_), _parse_at(to)
        with reader.lock:
            reader.refresh()
            reader.file(symbol)
            rows = reader.timeline.select(symbol, t_from, t_to)
        return {"symbol": symbol, "n": len(rows), "transitions": rows}

    @app.get("/api/timeline")
    def api_timeline(from_: Optional[str] = Query(default=None, alias="from"), to: Optional[str] = Query(default=None)) -> Any:
        t_from, t_to = _parse_at(from_), _parse_at(to)
        with reader.lock:
            reader.refresh()
            rows = reader.timeline.select(None, t_from, t_to)
        return {"n": len(rows), "transitions": rows}

    @app.get("/api/metrics")
    def api_metrics() -> Any:
        with reader.lock:
            reader.refresh()
            return reader.metrics()

    @app.get("/api/cross/{symbol}")
    def api_cross(symbol: str, at: Optional[str] = Query(default="latest"),
                  index: Optional[int] = Query(default=None, ge=0)) -> Any:
        t = _parse_at(at)
        with reader.lock:
            reader.refresh()
            return reader.cross(symbol, t, index)

    @app.get("/api/replay")
    def api_replay() -> Any:
        with reader.lock:
            reader.refresh()
            return reader.replay_info()

    @app.post("/api/replay/seek")
    def api_replay_seek(symbol: str, at: Optional[str] = Query(default="latest"),
                        index: Optional[int] = Query(default=None, ge=0)) -> Any:
        t = _parse_at(at)
        with reader.lock:
            reader.refresh()
            s = reader.state_at(symbol, t, index)
            reader.cursor[symbol] = {"at": s["at"], "t": s["t"], "index": s["index"], "count": s["count"]}
            return s

    @app.get("/api/latest")
    def api_latest() -> Any:
        d = reader._json_file("latest.json")
        if d is None:
            raise HTTPException(status_code=404, detail="latest.json not available")
        return d

    @app.get("/")
    def index() -> Any:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"), media_type="text/html")

    @app.get("/favicon.ico")
    def favicon() -> Any:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" fill="#0b1017"/>'
               '<rect x="6" y="2" width="4" height="12" fill="#4fc3f7"/><rect x="3" y="12" width="10" height="2" fill="#4fc3f7"/></svg>')
        return Response(content=svg, media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(HTTPException)
    async def _http_err(_req: Any, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    return app


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="DSE Observation Tower UI server")
    ap.add_argument("--store", required=True, help="state store directory written by tower.replay / tower.live")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.store), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
