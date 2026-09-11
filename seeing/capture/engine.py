"""The public market data engine: many sources, one polite client, raw-first.

`runner.py` already captures DSE + LankaBD for a trading session and is kept as
it is — it is the tuned intraday microstructure capture that the seeing engine
depends on. This module is the wider, slower net: every public Bangladesh
market source we may legally reach, each on its own cadence, each writing raw
bytes into the same append-only hash-chained store.

What it adds over `runner.py`:

* **A declarative registry.** A source is a `SourceSpec` — adapter, cadence,
  whether it is per-symbol, which session phases it is worth polling in, and
  whether it is blocked and why. Adding a source is one entry, not new loop code.
* **Blocked sources stay visible.** A source we may not or cannot reach is
  registered with `blocked_reason` and appears in the status file as BLOCKED
  with that reason. Dropping it from the config would hide the gap; naming it
  keeps the gap in the report.
* **Named failures.** Every non-success goes through `failures.classify_fetch`
  and `classify_parse`, so a 404, an expired token, a maintenance page and a
  silently-changed schema are different rows in the status file rather than one
  "error" count.
* **Phase awareness.** Fundamentals and regulator pages do not change during a
  four-hour trading session; depth does. Each spec names the phases it runs in
  so a closed market does not generate thousands of identical fundamentals polls.

Raw-first is absolute here as everywhere: the engine writes bytes and a receipt
envelope. It calls `adapter.parse` only to classify health — the parsed frames
are deliberately thrown away, because the normalized view is rebuilt on replay
from the raw bytes, and a parser fixed tomorrow must be able to re-read
everything captured today.

    python3 -m seeing.capture.engine --out evidence/public_engine/2026-09-08 --once
    python3 -m seeing.capture.engine --out evidence/public_engine/2026-09-08 --minutes 240
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..clock import now_utc, session_phase, trading_date
from .failures import SourceHealth, classify_fetch, classify_parse
from .http_client import Fetched, PoliteClient
from .raw_store import RawStore

ALL_PHASES = ("CLOSED", "PRE_OPEN", "CONTINUOUS", "POST_CLOSE")
TRADING_PHASES = ("PRE_OPEN", "CONTINUOUS", "POST_CLOSE")

# "this source has never been polled", as a monotonic reading. Not 0.0: on Linux
# `time.monotonic()` counts from boot, so 0.0 means "at boot", and a source with
# an hourly cadence stays not-due for the machine's first hour of uptime.
NEVER_POLLED = float("-inf")


@dataclass
class SourceSpec:
    """One collectable surface: what it is, how often, and when it is worth polling."""

    name: str
    kind: str                                   # book | watch | tape | reference | market | block |
    #                                             fundamentals | ownership | events | regulatory | macro
    adapter: Any = None                         # anything with .fetch(key) -> Fetched (+ optional .parse)
    cadence_s: float = 3600.0
    per_symbol: bool = False
    phases: Sequence[str] = ALL_PHASES
    enabled: bool = True
    blocked_reason: Optional[str] = None
    delayed: bool = False                       # source publishes deliberately delayed data
    delay_note: Optional[str] = None
    expect_frames: bool = True
    access_note: str = ""
    # A closed market still publishes the last session's numbers, and capturing
    # them once after the close is worth doing — but at the trading cadence it
    # would mean thousands of identical overnight requests. Sources that are
    # worth keeping overnight declare the slower cadence they should use there.
    closed_cadence_s: Optional[float] = None
    # Run once before this source's first poll. `lankabd_tape` needs LankaBD's
    # company-id map, which the older runner fetches at bootstrap and the engine
    # did not — so every tape poll failed with "no company id for <symbol>", and
    # a config gap was being reported as a connect_error.
    bootstrap: Optional[Callable[[], Any]] = None
    _bootstrapped: bool = False

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None

    # Multiplies every cadence. Exists for concurrency control (AGENTS.md §37):
    # when another capture is already polling the same hosts, a second engine at
    # full cadence would double the request rate on LankaBD and dsebd.org and
    # could starve the run that matters. Scaling is honest about the trade — the
    # sampling interval is recorded in the status file and in META.
    cadence_scale: float = 1.0

    def cadence_for(self, phase: str) -> float:
        if phase == "CONTINUOUS" or self.closed_cadence_s is None:
            base = self.cadence_s
        elif phase in ("PRE_OPEN", "POST_CLOSE"):
            base = self.cadence_s
        else:
            base = self.closed_cadence_s
        return base * max(self.cadence_scale, 1e-9)

    def due(self, last: float, now: float, phase: str = "CONTINUOUS") -> bool:
        """Is this source due, given the monotonic time of its last poll?

        `last` is NEVER_POLLED for a source that has not run yet, which makes the
        first poll unconditionally due. It used to be 0.0, and 0.0 is a real
        monotonic reading: `time.monotonic()` is time since boot on Linux, so on
        a machine that had been up 23 minutes NOTHING with an hourly cadence was
        due, and the whole-market engine ran 6 minutes making zero requests while
        reporting every source UNTRIED. `--once` hid the defect because it polls
        without asking `due` at all.
        """
        return (now - last) >= self.cadence_for(phase)

    def runs_in(self, phase: str) -> bool:
        return phase in self.phases or (self.closed_cadence_s is not None and phase == "CLOSED")


@dataclass
class PublicMarketEngine:
    out_dir: str
    specs: List[SourceSpec]
    symbols: List[str] = field(default_factory=list)
    client: Optional[PoliteClient] = None
    store: Optional[RawStore] = None
    capturer_id: str = field(default_factory=lambda: f"engine-{socket.gethostname()[:12]}")
    health: Dict[str, SourceHealth] = field(default_factory=dict)
    stop: bool = False
    _last: Dict[str, float] = field(default_factory=dict)
    _sym_last: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        os.makedirs(self.out_dir, exist_ok=True)
        self.client = self.client or PoliteClient(min_gap_s=0.5, timeout_s=40.0)
        self.store = self.store or RawStore(self.out_dir, capturer_id=self.capturer_id,
                                            software_version=_git_commit())
        for s in self.specs:
            self.health[s.name] = SourceHealth(s.name)
            self._last[s.name] = NEVER_POLLED
            self._sym_last[s.name] = {sym: NEVER_POLLED for sym in self.symbols}

    # ------------------------------------------------------------------ one poll
    def poll(self, spec: SourceSpec, key: Optional[str] = None) -> bool:
        """Fetch once, store the raw bytes, classify the outcome. Returns True on success."""
        h = self.health[spec.name]
        t = now_utc().isoformat()
        if spec.blocked or spec.adapter is None:
            return False
        if spec.bootstrap is not None and not spec._bootstrapped:
            spec._bootstrapped = True
            try:
                info = spec.bootstrap()
                self.store.write_meta(spec.name, {"bootstrap": "ok", "detail": info})
            except Exception as exc:                                # noqa: BLE001
                from .failures import Failure, CONNECT_ERROR
                fail = Failure(CONNECT_ERROR,
                               f"bootstrap failed: {type(exc).__name__}: {exc}"[:500])
                h.record_failure(fail, t)
                self.store.write_gap(spec.name, fail.code, detail=fail.detail)
                return False
        try:
            f: Fetched = spec.adapter.fetch(key)
        except Exception as exc:                                    # noqa: BLE001
            from .failures import Failure, CONNECT_ERROR
            fail = Failure(CONNECT_ERROR, f"adapter raised: {type(exc).__name__}: {exc}"[:500])
            h.record_failure(fail, t)
            self.store.write_gap(spec.name, fail.code, detail=fail.detail, key=key)
            return False

        fail = classify_fetch(f)
        if fail is not None:
            h.record_failure(fail, t)
            self.store.write_gap(spec.name, fail.code, detail=fail.detail, key=key,
                                 http=f.http, body=f.body or None)
            return False

        rec = self.store.write_data(spec.name, key=key, body=f.body, http=f.http)

        # Parse only to judge health. The frames are discarded on purpose: the
        # normalized view is rebuilt from raw bytes on replay, never from here.
        if hasattr(spec.adapter, "parse"):
            try:
                p = spec.adapter.parse(f.body, key)
                pfail = classify_parse(p.frames, p.problems, expect_frames=spec.expect_frames)
            except Exception as exc:                                # noqa: BLE001
                from .failures import Failure, PARSE_ERROR
                pfail = Failure(PARSE_ERROR, f"{type(exc).__name__}: {exc}"[:500])
            if pfail is not None:
                h.record_failure(pfail, t)
                self.store.write_gap(spec.name, pfail.code, detail=pfail.detail, key=key)
                return False

        h.record_ok(t, body_sha256=rec.get("body_sha256"))
        return True

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        phase = session_phase(now_utc())
        rows = []
        for s in self.specs:
            h = self.health[s.name]
            rows.append({
                **h.as_dict(),
                "kind": s.kind, "cadence_s": s.cadence_s, "per_symbol": s.per_symbol,
                "phases": list(s.phases), "enabled": s.enabled,
                "status": "BLOCKED" if s.blocked else ("DISABLED" if not s.enabled else h.status()),
                "blocked_reason": s.blocked_reason, "delayed": s.delayed,
                "delay_note": s.delay_note, "access_note": s.access_note,
            })
        by_status: Dict[str, int] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {
            "t_utc": now_utc().isoformat(), "session_phase": phase,
            "trading_date_dhaka": trading_date(now_utc()).isoformat(),
            "capturer_id": self.capturer_id, "out_dir": self.out_dir,
            "cadence_scale": (self.specs[0].cadence_scale if self.specs else 1.0),
            "symbols": self.symbols, "client": dict(self.client.stats),
            "sources": rows, "by_status": by_status,
            "raw_records": sum(h.ok for h in self.health.values()),
        }

    def write_status(self) -> str:
        p = os.path.join(self.out_dir, "SOURCE_STATUS.json")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.status(), fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        return p

    # ------------------------------------------------------------------ loops
    def run_once(self) -> Dict[str, Any]:
        """One pass over every enabled, unblocked source that runs in this phase."""
        phase = session_phase(now_utc())
        for spec in self.specs:
            if self.stop or spec.blocked or not spec.enabled or not spec.runs_in(phase):
                continue
            if spec.per_symbol:
                for sym in self.symbols:
                    if self.stop:
                        break
                    self.poll(spec, sym)
            else:
                self.poll(spec)
        self.write_status()
        return self.status()

    def next_due(self, now: float, phase: str) -> Optional[Tuple[SourceSpec, Optional[str]]]:
        """The single most overdue (source, symbol) pair, measured in cadences.

        Scanning the registry in list order and taking the first due item starves
        every source below a wide per-symbol one. 691 symbols on a 30 s cadence
        cannot be swept in 30 s, so one of them is ALWAYS due, and `dsebd_depth`
        would take every slot for the whole session while `lankabd_depth`, the
        tape, the market feed and the block feed were never polled once. That is
        invisible in a 14-symbol capture and total at market width.

        Overdue-ness is a ratio, `(now - last) / cadence`, so a 20 s source three
        cadences late outranks a 3600 s one that is barely late, and no source can
        crowd another out by sheer symbol count. When capacity runs out the whole
        registry stretches together — depth every ~9 cadences, the market feed
        every ~9 cadences — instead of one source running on time and the rest not
        at all. Ties (everything at the start of a run) fall back to registry
        order, which makes the first pass a deterministic breadth-first sweep.
        """
        best: Optional[Tuple[SourceSpec, Optional[str]]] = None
        best_score = 1.0                                  # below 1.0 is not yet due
        for spec in self.specs:
            if spec.blocked or not spec.enabled or not spec.runs_in(phase):
                continue
            cad = max(spec.cadence_for(phase), 1e-9)
            if spec.per_symbol:
                last_by = self._sym_last[spec.name]
                for sym in self.symbols:
                    score = (now - last_by.get(sym, NEVER_POLLED)) / cad
                    if score > best_score:
                        best_score, best = score, (spec, sym)
            else:
                score = (now - self._last[spec.name]) / cad
                if score > best_score:
                    best_score, best = score, (spec, None)
        return best

    def run_for(self, minutes: float, heartbeat_s: float = 30.0) -> Dict[str, Any]:
        """Cadence-driven loop until the deadline or a signal."""
        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)
        self.store.write_meta("engine", {
            "argv": sys.argv, "host": socket.gethostname(), "python": sys.version.split()[0],
            "symbols": self.symbols,
            "sources": [{"name": s.name, "kind": s.kind, "cadence_s": s.cadence_s,
                         "blocked_reason": s.blocked_reason, "delayed": s.delayed}
                        for s in self.specs],
            "note": "raw-first; parse on replay only",
        })
        deadline = time.monotonic() + minutes * 60.0
        last_hb = NEVER_POLLED           # first heartbeat lands before the first poll
        while not self.stop and time.monotonic() < deadline:
            now = time.monotonic()
            phase = session_phase(now_utc())
            work = None if self.stop else self.next_due(now, phase)
            did = work is not None
            if work is not None:
                spec, sym = work
                self.poll(spec, sym)
                if sym is None:
                    self._last[spec.name] = time.monotonic()
                else:
                    self._sym_last[spec.name][sym] = time.monotonic()
            if now - last_hb >= heartbeat_s:
                self.store.write_heartbeat(self.status())
                self.write_status()
                last_hb = now
            if not did:
                time.sleep(0.5)
        self.store.write_heartbeat(self.status())
        self.write_status()
        self.store.close()
        return self.status()

    def _sig(self, *_: Any) -> None:
        self.stop = True


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:                                              # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------- registry
def build_registry(client: PoliteClient, symbols: Sequence[str]) -> List[SourceSpec]:
    """Every source we know of — working, partial and blocked alike.

    A blocked source keeps its row on purpose. `PUBLIC_SOURCE_MATRIX.md` and the
    status file are both generated from this list, so a source that is dropped
    here disappears from the gap report too, which is exactly the wrong outcome.
    """
    from .adapters import dsebd, lankabd
    from .adapters import bd_public

    lb = lankabd.build_adapters(client)

    def _load_cid_map() -> Dict[str, Any]:
        """LankaBD's symbol -> companyID map, needed before any tape poll."""
        cid, f = lankabd.fetch_cid_map(lb["session"])
        if not cid:
            raise RuntimeError(f"cid map empty (http {f.status})")
        lb["tape"].cid_map = cid
        return {"symbols_in_cid_map": len(cid)}

    specs: List[SourceSpec] = [
        # ---- DSE official -------------------------------------------------
        SourceSpec("dsebd_latest", "watch", dsebd.DSEBDLatestAdapter(client), 60.0,
                   phases=TRADING_PHASES, closed_cadence_s=3600.0,
                   access_note="all-symbol LTP/H/L/YCP/trades/volume/value"),
        SourceSpec("dsebd_depth", "book", dsebd.DSEBDDepthAdapter(client), 30.0, per_symbol=True,
                   phases=TRADING_PHASES, access_note="POST ajax/load-instrument.php"),
        SourceSpec("dsebd_hts", "reference", dsebd.DSEBDSessionsAdapter(client), 21600.0,
                   access_note="sessions + holiday calendar"),
        # ---- LankaBD ------------------------------------------------------
        SourceSpec("lankabd_depth", "book", lb["depth"], 20.0, per_symbol=True, phases=TRADING_PHASES),
        SourceSpec("lankabd_watch", "watch", lb["watch"], 30.0, phases=TRADING_PHASES),
        SourceSpec("lankabd_tape", "tape", lb["tape"], 180.0, per_symbol=True,
                   phases=TRADING_PHASES, bootstrap=_load_cid_map,
                   access_note="needs LankaBD's company-id map, fetched once at bootstrap"),
        SourceSpec("lankabd_market", "market", lb["market"], 60.0, phases=TRADING_PHASES),
        SourceSpec("lankabd_block", "block", lb["block"], 300.0, phases=TRADING_PHASES),
        SourceSpec("lankabd_circuit", "reference", lb["circuit"], 3600.0),
        SourceSpec("lankabd_grid", "l1", lb["grid"], 120.0, phases=TRADING_PHASES),
    ]
    specs += bd_public.build_specs(client, symbols)
    return specs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="raw store directory")
    p.add_argument("--symbols", default="", help="comma list for per-symbol sources")
    p.add_argument("--once", action="store_true", help="one pass over every due source, then exit")
    p.add_argument("--minutes", type=float, default=60.0, help="how long to run (ignored with --once)")
    p.add_argument("--min-gap", type=float, default=0.5, help="minimum seconds between any two requests")
    p.add_argument("--timeout", type=float, default=40.0)
    p.add_argument("--only", default="", help="comma list of source names to run (default: all)")
    p.add_argument("--cadence-scale", type=float, default=1.0,
                   help="multiply every source cadence (>1 = slower). Use when another "
                        "capture is already polling the same hosts.")
    p.add_argument("--list", action="store_true", help="print the registry and exit")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    client = PoliteClient(min_gap_s=a.min_gap, timeout_s=a.timeout)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    specs = build_registry(client, symbols)
    if a.cadence_scale != 1.0:
        for sp in specs:
            sp.cadence_scale = a.cadence_scale
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        specs = [s for s in specs if s.name in want]
    if a.list:
        for s in specs:
            state = "BLOCKED" if s.blocked else ("off" if not s.enabled else "on")
            print(f"{s.name:28s} {s.kind:12s} {s.cadence_s:8.0f}s  {state:8s} "
                  f"{s.blocked_reason or s.access_note}")
        return 0
    os.makedirs(a.out, exist_ok=True)
    eng = PublicMarketEngine(a.out, specs, symbols=symbols, client=client)
    st = eng.run_once() if a.once else eng.run_for(a.minutes)
    if a.once:
        eng.store.close()
    print(json.dumps({"by_status": st["by_status"], "raw_records": st["raw_records"],
                      "status_file": os.path.join(a.out, "SOURCE_STATUS.json")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
