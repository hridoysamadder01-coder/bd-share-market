"""Capture runner: one process, one polite client, several sources, raw-first.

    python3 -m seeing.capture.runner --out evidence/capture/2026-09-06 \
        --start 03:50 --end 08:40 [--symbols A,B,C] [--depth-gap 0.3] ...

Schedule (all intervals are parameters):
  * depth (book) for each universe symbol, round-robin, continuously;
  * all-symbol watch every ``--watch-every`` s (default 30; the portal's own UI uses 15);
  * per-symbol minute tape every ``--tape-every`` s per symbol (default 180) and a
    final full pull for every symbol at the end;
  * market statistics every 60 s; block board every 300 s;
  * circuit reference at start and every 3600 s;
  * dsebd.org depth (POST ajax/load-instrument.php) for the same symbol right after each
    LankaBD depth poll — a second, independent book sensor (``--no-dsebd-depth`` to disable);
  * dsebd.org latest-price page and hts.php once at start and hourly (GAP if unreachable);
  * HEARTBEAT every 5 s with per-source ages; GAP for every failure; META at start.
Times are UTC HH:MM for the trading date of --date (default: today in Dhaka).
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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..clock import DHAKA, UTC, local_hhmm_to_utc, now_utc, session_phase, trading_date
from .adapters import dsebd, lankabd
from .http_client import PoliteClient
from .raw_store import RawStore
from .universe import default_universe, select_universe


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


class Runner:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.client = PoliteClient(min_gap_s=a.depth_gap, timeout_s=a.timeout)
        self.store = RawStore(a.out, capturer_id=a.capturer_id, software_version=_git_commit())
        self.lb = lankabd.build_adapters(self.client)
        self.dse_latest = dsebd.DSEBDLatestAdapter(self.client)
        self.dse_depth = dsebd.DSEBDDepthAdapter(self.client)
        self.dse_hts = dsebd.DSEBDSessionsAdapter(self.client)
        self.symbols: List[str] = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] if a.symbols else []
        self.stop = False
        self.last_ok: Dict[str, float] = {}
        self.counts: Dict[str, Dict[str, int]] = {}
        self.body_hash_last: Dict[str, str] = {}
        self.changed: Dict[str, int] = {}
        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)

    def _sig(self, *_: Any) -> None:
        self.stop = True

    # ------------------------------------------------------------------ helpers
    def _record(self, source: str, key: Optional[str], f, src_seq: Any = None) -> bool:
        c = self.counts.setdefault(source, {"ok": 0, "err": 0, "unchanged": 0})
        if f.ok:
            rec = self.store.write_data(source, key=key, body=f.body, http=f.http, src_seq=src_seq)
            c["ok"] += 1
            self.last_ok[source] = time.monotonic()
            hk = f"{source}:{key}"
            if self.body_hash_last.get(hk) == rec["body_sha256"]:
                c["unchanged"] += 1
            else:
                self.changed[hk] = self.changed.get(hk, 0) + 1
            self.body_hash_last[hk] = rec["body_sha256"]
            return True
        reason = "http" if f.status else "exception"
        self.store.write_gap(source, reason, detail=f.error or "", key=key, http=f.http,
                             body=f.body if f.body else None)
        c["err"] += 1
        return False

    def _status(self) -> Dict[str, Any]:
        now = time.monotonic()
        return {
            "t_utc": now_utc().isoformat(), "phase": session_phase(now_utc()),
            "ages_s": {s: round(now - t, 1) for s, t in self.last_ok.items()},
            "counts": self.counts, "client": dict(self.client.stats),
            "backoff_s": self.client.backoff_s(), "token_refreshes": self.lb["session"].refreshes,
            "symbols": self.symbols, "changed_payloads": self.changed,
        }

    def _write_status_file(self) -> None:
        p = os.path.join(self.a.out, "STATUS.json")
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self._status(), fh, indent=1)
        os.replace(tmp, p)

    # ------------------------------------------------------------------ main
    def run(self) -> int:
        a = self.a
        day = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else trading_date(now_utc())
        t_start = local_hhmm_to_utc(day, a.start) if ":" in a.start and a.local else \
            datetime.combine(day, datetime.strptime(a.start, "%H:%M").time(), tzinfo=UTC)
        t_end = local_hhmm_to_utc(day, a.end) if a.local else \
            datetime.combine(day, datetime.strptime(a.end, "%H:%M").time(), tzinfo=UTC)
        if t_end <= t_start:
            t_end += timedelta(days=1)
        self.store.write_meta("runner", {
            "argv": sys.argv, "trading_date_dhaka": day.isoformat(), "t_start_utc": t_start.isoformat(),
            "t_end_utc": t_end.isoformat(), "host": socket.gethostname(), "python": sys.version.split()[0],
            "user_agent": self.client.user_agent, "min_gap_s": self.client.min_gap_s,
            "intervals": {"watch": a.watch_every, "tape": a.tape_every, "market": a.market_every,
                          "block": a.block_every, "circuit": a.circuit_every, "dsebd": a.dsebd_every,
                          "heartbeat": a.heartbeat}, "note": "raw-first; parse on replay only",
        })
        # wait for start, heartbeating
        while not self.stop and now_utc() < t_start:
            self.store.write_heartbeat({**self._status(), "waiting_for": t_start.isoformat()})
            self._write_status_file()
            time.sleep(min(a.heartbeat * 6, max(1.0, (t_start - now_utc()).total_seconds())))
        if self.stop:
            self.store.close()
            return 0

        # bootstrap: token, cid map, universe, reference
        self.lb["session"].ensure(force=True)
        self.store.write_meta("runner", {"token_page": self.lb["session"].last_fetch.http if self.lb["session"].last_fetch else None,
                                         "token_obtained": bool(self.lb["session"].token)})
        cid_map, f_cid = lankabd.fetch_cid_map(self.lb["session"])
        self._record("lankabd_cidmap", None, f_cid)
        self.lb["tape"].cid_map = cid_map
        f_watch = self.lb["watch"].fetch()
        self._record("lankabd_watch", None, f_watch)
        if not self.symbols:
            parsed = self.lb["watch"].parse(f_watch.body) if f_watch.ok else None
            sel = select_universe(parsed.frames, n_top=a.n_top, n_mid=a.n_mid, seed=a.seed) \
                if (parsed and parsed.frames) else {"symbols": []}
            # At pre-open the watch's day fields are reset to zero for every instrument, so a
            # value-ranked selection is empty; fall back to the last known ranking rather than
            # polling nothing (observed 2026-09-06 03:50 UTC).
            if len(sel["symbols"]) >= max(1, a.n_top // 2):
                self.symbols = sel["symbols"]
                self.store.write_meta("runner", {"universe": sel})
            else:
                self.symbols = default_universe()
                self.store.write_meta("runner", {"universe": {"symbols": self.symbols, "rule": "fallback list",
                                                              "selection_attempt": sel}})
        else:
            self.store.write_meta("runner", {"universe": {"symbols": self.symbols, "rule": "--symbols"}})
        self.store.write_meta("runner", {"cid_map_size": len(cid_map),
                                         "cid_missing": [s for s in self.symbols if s not in cid_map]})
        self._record("lankabd_circuit", None, self.lb["circuit"].fetch())
        self._record("dsebd_latest", None, self.dse_latest.fetch())
        self._record("dsebd_hts", None, self.dse_hts.fetch())

        # schedule
        last = {"watch": 0.0, "market": 0.0, "block": 0.0, "circuit": time.monotonic(),
                "dsebd": time.monotonic(), "hb": 0.0, "status": 0.0}
        tape_last = {s: 0.0 for s in self.symbols}
        i = 0
        while not self.stop and now_utc() < t_end:
            now = time.monotonic()
            if now - last["hb"] >= a.heartbeat:
                self.store.write_heartbeat(self._status())
                last["hb"] = now
            if now - last["status"] >= 30:
                self._write_status_file()
                last["status"] = now
            if now - last["watch"] >= a.watch_every:
                self._record("lankabd_watch", None, self.lb["watch"].fetch())
                last["watch"] = now
                continue
            if now - last["market"] >= a.market_every:
                self._record("lankabd_market", None, self.lb["market"].fetch())
                last["market"] = now
                continue
            if now - last["block"] >= a.block_every:
                self._record("lankabd_block", None, self.lb["block"].fetch())
                last["block"] = now
                continue
            if now - last["circuit"] >= a.circuit_every:
                self._record("lankabd_circuit", None, self.lb["circuit"].fetch())
                last["circuit"] = now
                continue
            if now - last["dsebd"] >= a.dsebd_every:
                self._record("dsebd_latest", None, self.dse_latest.fetch())
                self._record("dsebd_hts", None, self.dse_hts.fetch())
                last["dsebd"] = now
                continue
            due_tape = [s for s in self.symbols if now - tape_last[s] >= a.tape_every]
            if due_tape:
                s = due_tape[0]
                self._record("lankabd_tape", s, self.lb["tape"].fetch(s))
                tape_last[s] = now
                continue
            if self.symbols:
                s = self.symbols[i % len(self.symbols)]
                i += 1
                # two independent book sensors for the same symbol, back to back, so
                # the fusion layer can align them on receipt time
                self._record("lankabd_depth", s, self.lb["depth"].fetch(s))
                if a.dsebd_depth:
                    self._record("dsebd_depth", s, self.dse_depth.fetch(s))
            else:
                time.sleep(1.0)

        # end of session: full tape pull + final watch/market/block/circuit
        for s in self.symbols:
            self._record("lankabd_tape", s, self.lb["tape"].fetch(s))
        self._record("lankabd_watch", None, self.lb["watch"].fetch())
        self._record("lankabd_market", None, self.lb["market"].fetch())
        self._record("lankabd_block", None, self.lb["block"].fetch())
        self.store.write_meta("runner", {"finished": True, "stopped_by_signal": self.stop, **self._status()})
        self.store.write_heartbeat(self._status())
        self._write_status_file()
        self.store.close()
        if a.compress:
            rep = self.store.compress_and_verify()
            with open(os.path.join(a.out, "COMPRESSION_REPORT.json"), "w") as fh:
                json.dump(rep, fh, indent=1)
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--date", default=None, help="trading date (Dhaka) YYYY-MM-DD; default today")
    p.add_argument("--start", default="03:50", help="UTC HH:MM (or Dhaka with --local); 09:50 Dhaka")
    p.add_argument("--end", default="08:20", help="14:20 Dhaka: 10 min after the 14:00–14:10 close session")
    p.add_argument("--local", action="store_true", help="interpret --start/--end as Asia/Dhaka")
    p.add_argument("--symbols", default="", help="comma list; default: selected from the watch at start")
    p.add_argument("--n-top", type=int, default=8)
    p.add_argument("--n-mid", type=int, default=6)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--capturer-id", default=f"ccr-{socket.gethostname()[:12]}")
    p.add_argument("--depth-gap", type=float, default=0.35, help="min seconds between any two requests")
    p.add_argument("--timeout", type=float, default=40.0)
    p.add_argument("--watch-every", type=float, default=30.0)
    p.add_argument("--tape-every", type=float, default=180.0)
    p.add_argument("--market-every", type=float, default=60.0)
    p.add_argument("--block-every", type=float, default=300.0)
    p.add_argument("--circuit-every", type=float, default=3600.0)
    p.add_argument("--dsebd-every", type=float, default=3600.0)
    p.add_argument("--heartbeat", type=float, default=5.0)
    p.add_argument("--no-dsebd-depth", dest="dsebd_depth", action="store_false",
                   help="disable the exchange-site depth sensor (POST ajax/load-instrument.php)")
    p.add_argument("--no-compress", dest="compress", action="store_false")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    return Runner(a).run()


if __name__ == "__main__":
    sys.exit(main())
