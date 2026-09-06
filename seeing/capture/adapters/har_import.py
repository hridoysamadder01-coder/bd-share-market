"""Import a browser HAR recording of the account holder's own DSE Investor /
M-Invest (or broker web-terminal) session into the raw store.

The web terminal's market-data connection is visible to the logged-in user in
their own browser; with the platform's permission (owner action D-14) the
session can be saved from DevTools as a HAR file. Every HTTP/WebSocket entry
becomes one DATA record in ``source="har_import"`` with the request URL,
method, status, the browser's ``startedDateTime`` as source time, and the
response body exactly as recorded (text or base64). JSON bodies are additionally
parsed into ``frames`` with their top-level keys listed — the schema of the
terminal's payloads is learned from real recordings, never assumed. Nothing is
dropped: unknown payloads are stored raw and flagged ``schema_unknown``.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from ..raw_store import RawStore
from .base import Parsed, capability_map


def har_entries(har_bytes: bytes) -> List[Dict[str, Any]]:
    d = json.loads(har_bytes.decode("utf-8", "replace"))
    return d.get("log", {}).get("entries", [])


def entry_body(e: Dict[str, Any]) -> bytes:
    content = e.get("response", {}).get("content", {}) or {}
    text = content.get("text")
    if text is None:
        return b""
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text)
        except Exception:  # noqa: BLE001
            return text.encode("utf-8", "replace")
    return text.encode("utf-8", "replace")


def import_har(har_bytes: bytes, store: RawStore, source: str = "har_import") -> Dict[str, Any]:
    n = 0
    ws_frames = 0
    for e in har_entries(har_bytes):
        req = e.get("request", {})
        resp = e.get("response", {})
        http = {"method": req.get("method"), "url": req.get("url"), "status": resp.get("status"),
                "t_send_utc": e.get("startedDateTime"), "elapsed_ms": e.get("time"),
                "response_headers": {h.get("name"): h.get("value") for h in resp.get("headers", [])},
                "request_headers": {h.get("name"): h.get("value") for h in req.get("headers", [])
                                    if h.get("name", "").lower() not in ("cookie", "authorization")},
                "mime": (resp.get("content") or {}).get("mimeType"), "har": True}
        store.write_data(source, key=None, body=entry_body(e), http=http)
        n += 1
        for m in e.get("_webSocketMessages", []) or []:
            store.write_data(source + "_ws", key=req.get("url"),
                             body=str(m.get("data", "")).encode("utf-8", "replace"),
                             http={"url": req.get("url"), "ws_type": m.get("type"), "ws_time": m.get("time"),
                                   "opcode": m.get("opcode"), "har": True})
            ws_frames += 1
    return {"entries": n, "ws_frames": ws_frames}


class HARPayloadAdapter:
    name = "har_import"
    kind = "unknown"
    observes = ("t_recv",)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        text = body.decode("utf-8", "replace").strip()
        if not text:
            out.problems.append("empty body")
            return out
        if text[0] in "[{":
            try:
                d = json.loads(text)
            except Exception as e:  # noqa: BLE001
                out.problems.append(f"json: {e}")
                return out
            keys = sorted(d.keys()) if isinstance(d, dict) else (sorted(d[0].keys()) if d and isinstance(d[0], dict) else [])
            out.frames.append({"schema_unknown": True, "json_type": type(d).__name__, "top_level_keys": keys,
                               "n_items": len(d) if isinstance(d, list) else 1})
            out.problems.append("schema unknown: payload stored raw; a parser is added once a real recording exists")
        else:
            out.frames.append({"schema_unknown": True, "json_type": "text", "n_bytes": len(body)})
        return out
