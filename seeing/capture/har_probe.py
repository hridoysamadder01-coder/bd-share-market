"""Apply the market-depth capture kit's own filters to an already-recorded HAR.

This is a faithful port of ``rich_sensor_probe.mjs`` (the CDP kit the account holder
runs on their own machine against their own logged-in browser) for the case where
the recording already exists as a HAR file: same market/sensitive key sets, same
URL classes, same ``sanitize()`` and candidate test. It reads RESPONSE BODIES ONLY.
Request headers, cookies, authorization, postData content and response headers are
never read; the only request facts kept are method, redacted URL (scheme+host+path,
query dropped) and whether a POST body existed.

Output: ``rich_sensor_probe.ndjson`` — one record per market-data candidate::

    {t_recv_utc, method, url, status, mime, transport, has_post_body, body_sha256,
     body_bytes, format, market_key_hits, sensitive_keys_removed, payload}

``payload`` is the response body with every sensitive-looking key removed at any
nesting depth. ``sensitive_keys_removed`` counts what was dropped so a non-zero value
is visible in the evidence. Nothing is written for entries whose URL is a
login/auth/account/portfolio class, or whose body carries fewer than two
market-looking keys.

    python3 -m seeing.capture.har_probe <session.har> <rich_sensor_probe.ndjson>
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

MARKET_KEYS = {
    "symbol", "code", "instrument", "instrumentcode", "tradingcode", "scrip", "bid", "bids", "bidprice", "bid_price",
    "bidqty", "bid_qty", "buy", "buyprice", "buyqty", "ask", "asks", "askprice", "ask_price", "askqty", "ask_qty",
    "sell", "sellprice", "sellqty", "depth", "marketdepth", "orderbook", "book", "levels", "orders", "ordercount",
    "numberoforders", "ltp", "lastprice", "last_price", "price", "qty", "quantity", "volume", "value", "turnover",
    "open", "high", "low", "close", "yclose", "prevclose", "uppercircuit", "lowercircuit", "circuit", "timestamp",
    "time", "ts", "exchange_ts", "execution_ts", "sequence", "seq", "messageid", "snapshotid", "tradecount",
    "trades", "tradeprice", "tradeqty", "buy_sell_pressure", "pressure", "marketphase",
}
SENSITIVE_KEYS = {
    "password", "passwd", "pass", "token", "accesstoken", "refresh_token", "authorization", "auth", "cookie",
    "session", "sessionid", "apikey", "api_key", "secret", "clientid", "client_id", "account", "accountid", "bo",
    "boid", "balance", "cash", "portfolio", "orderid", "order_id", "email", "phone", "mobile", "nid", "address",
    "name",
    # beyond the kit: belt-and-braces
    "jwt", "bearer", "csrf", "xsrf", "otp", "pin", "secretkey",
}
MARKET_URL = re.compile(r"(market|depth|quote|price|watch|ticker|feed|stock|instrument|book|trade)", re.I)
SENSITIVE_URL = re.compile(r"(login|auth|token|account|portfolio|balance|fund|withdraw|deposit|profile|client|bo\b)",
                           re.I)
MIN_MARKET_HITS = 2


def redact_url(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


def norm_key(k: Any) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(k).lower())


def is_sensitive_key(k: Any) -> bool:
    lk = norm_key(k)
    return lk in SENSITIVE_KEYS or any(s in lk for s in SENSITIVE_KEYS)


def is_market_key(k: Any) -> bool:
    lk = norm_key(k)
    return lk in MARKET_KEYS or any(m in lk for m in MARKET_KEYS)


def sanitize(v: Any, stats: Dict[str, int]) -> Any:
    """Drop sensitive-looking keys at any depth; count market-looking keys."""
    if isinstance(v, list):
        return [sanitize(x, stats) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if is_sensitive_key(k):
                stats["removed"] += 1
                continue
            if is_market_key(k):
                stats["market"] += 1
            out[k] = sanitize(x, stats)
        return out
    return v


def parse_candidate(body: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(body)
    except Exception:  # noqa: BLE001 — non-JSON: keep only if it looks like market text
        t = body or ""
        hints = len(re.findall(r"bid|ask|depth|price|qty|volume|ltp|trade", t, re.I))
        if hints < 3:
            return None
        return {"clean": {"raw_text": t[:200000]}, "stats": {"market": hints, "removed": 0}, "format": "text"}
    stats = {"market": 0, "removed": 0}
    clean = sanitize(parsed, stats)
    if stats["market"] < MIN_MARKET_HITS:
        return None
    return {"clean": clean, "stats": stats, "format": "json"}


def _response_text(entry: Dict[str, Any]) -> Optional[str]:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if text is None:
        return None
    if content.get("encoding") == "base64":
        import base64
        try:
            return base64.b64decode(text).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None
    return text


def probe_har(har: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """HAR dict → (records, summary). Pure; writes nothing."""
    entries = (har.get("log") or {}).get("entries") or []
    records: List[Dict[str, Any]] = []
    skipped: Counter = Counter()
    per_ep: Dict[str, List[str]] = defaultdict(list)
    ws_frames = 0
    hosts: Counter = Counter()
    for e in entries:
        req, resp = e.get("request") or {}, e.get("response") or {}
        url = redact_url(str(req.get("url") or ""))
        hosts[urlparse(url).netloc] += 1
        ws_frames += len(e.get("_webSocketMessages") or [])
        if SENSITIVE_URL.search(url) and not MARKET_URL.search(url):
            skipped["sensitive_url"] += 1
            continue
        body = _response_text(e)
        if not body:
            skipped["empty_body"] += 1
            continue
        c = parse_candidate(body)
        if not c:
            skipped["not_market"] += 1
            continue
        records.append({
            "t_recv_utc": e.get("startedDateTime"), "method": req.get("method"), "url": url,
            "status": resp.get("status"), "mime": ((resp.get("content") or {}).get("mimeType") or ""),
            "transport": "xhr_or_fetch", "has_post_body": bool(req.get("postData")),
            "body_sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
            "body_bytes": len(body), "format": c["format"], "market_key_hits": c["stats"]["market"],
            "sensitive_keys_removed": c["stats"]["removed"], "payload": c["clean"],
        })
        per_ep[url].append(str(e.get("startedDateTime") or ""))
    endpoints = []
    for u, ts in sorted(per_ep.items(), key=lambda x: -len(x[1])):
        tt = sorted(_iso(t) for t in ts if _iso(t) is not None)
        gaps = [(tt[i + 1] - tt[i]).total_seconds() for i in range(len(tt) - 1)]
        med = sorted(gaps)[len(gaps) // 2] if gaps else None
        endpoints.append({"url": u, "n": len(ts), "median_gap_s": None if med is None else round(med, 1),
                          "first": ts[0] if ts else None, "last": ts[-1] if ts else None})
    summary = {"entries": len(entries), "kept": len(records), "skipped": dict(skipped),
               "websocket_frames_in_har": ws_frames, "hosts": dict(hosts), "endpoints": endpoints,
               "sensitive_keys_removed_total": sum(r["sensitive_keys_removed"] for r in records)}
    return records, summary


def _iso(t: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def write_ndjson(records: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def leaked_keys(records: List[Dict[str, Any]]) -> List[str]:
    """Self-check: any sensitive-looking key left anywhere in the written payloads."""
    found: List[str] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if is_sensitive_key(k):
                    found.append(f"{path}.{k}")
                walk(x, f"{path}.{k}")
        elif isinstance(v, list):
            for x in v:
                walk(x, path + "[]")

    for i, r in enumerate(records):
        walk(r.get("payload"), f"rec{i}")
    return found


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        har = json.load(fh)
    records, summary = probe_har(har)
    leaks = leaked_keys(records)
    if leaks:
        print(f"REFUSING TO WRITE: {len(leaks)} sensitive-looking keys survived sanitize: {leaks[:10]}")
        return 1
    write_ndjson(records, argv[2])
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
