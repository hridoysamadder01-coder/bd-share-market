"""Polite, serialized HTTP client that returns the raw envelope for the store.

* One request at a time by default (``max_inflight`` is a parameter, not a
  promise); a minimum gap between requests; exponential backoff after errors.
* Every response — including 4xx/5xx and connection errors — is returned as a
  ``Fetched`` object so the caller can write it as DATA or GAP. Nothing is
  discarded because it "looks like an error".
* Timings: ``t_send_utc``, ``t_first_byte_utc`` (approximated as the time the
  response headers arrived), ``t_last_byte_utc``.
* DSE's own site serves a broken TLS chain (see DSE-AI-TRADER
  ``app/collectors/http.py``, reused here in spirit): when — and only when —
  verification fails on a certificate error for a host in ``TLS_FALLBACK_HOSTS``
  the same GET is retried without verification and the record says so.
"""
from __future__ import annotations

import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

from ..clock import now_utc

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36 bd-share-market-seeing/0.1 (personal research; polite)")
TLS_FALLBACK_HOSTS = ("dsebd.org", "www.dsebd.org", "dse.com.bd", "www.dse.com.bd",
                      "cse.com.bd", "www.cse.com.bd")


@dataclass
class Fetched:
    ok: bool
    status: Optional[int]
    body: bytes
    http: Dict[str, Any]
    error: Optional[str] = None


def _is_cert_error(exc: BaseException) -> bool:
    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        t = str(cur).lower()
        if "certificate verify failed" in t or "certificate_verify_failed" in t:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


@dataclass
class PoliteClient:
    min_gap_s: float = 0.35
    timeout_s: float = 40.0
    user_agent: str = DEFAULT_UA
    max_backoff_s: float = 120.0
    session: requests.Session = field(default_factory=requests.Session)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_send: float = 0.0
    _consecutive_errors: int = 0
    stats: Dict[str, int] = field(default_factory=lambda: {"requests": 0, "errors": 0, "tls_fallbacks": 0})

    def backoff_s(self) -> float:
        if self._consecutive_errors == 0:
            return 0.0
        return min(self.max_backoff_s, 2.0 ** min(self._consecutive_errors, 7))

    def request(self, method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
                data: Optional[Dict[str, str]] = None, params: Optional[Dict[str, str]] = None,
                allow_tls_fallback: bool = False) -> Fetched:
        with self._lock:
            wait = max(0.0, self._last_send + self.min_gap_s - time.monotonic())
            wait = max(wait, self.backoff_s() if self._consecutive_errors else 0.0)
            if wait > 0:
                time.sleep(wait)
            hdrs = {"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
            if headers:
                hdrs.update(headers)
            env: Dict[str, Any] = {"method": method, "url": url, "params": params, "form": data,
                                   "request_headers": {k: v for k, v in hdrs.items()
                                                       if k.lower() != "cookie"},
                                   "t_send_utc": now_utc().isoformat(), "tls_verify": True}
            self._last_send = time.monotonic()
            self.stats["requests"] += 1
            try:
                r = self.session.request(method, url, headers=hdrs, data=data, params=params,
                                         timeout=self.timeout_s, allow_redirects=True)
                env["t_first_byte_utc"] = now_utc().isoformat()
                body = r.content
                env["t_last_byte_utc"] = now_utc().isoformat()
                env["status"] = r.status_code
                env["response_headers"] = dict(r.headers)
                env["final_url"] = r.url
                env["elapsed_ms"] = int(r.elapsed.total_seconds() * 1000)
                ok = 200 <= r.status_code < 300
                self._consecutive_errors = 0 if ok else self._consecutive_errors + 1
                if not ok:
                    self.stats["errors"] += 1
                return Fetched(ok, r.status_code, body, env, None if ok else f"http {r.status_code}")
            except Exception as e:  # noqa: BLE001
                if allow_tls_fallback and _is_cert_error(e) and \
                        any(h in url for h in TLS_FALLBACK_HOSTS):
                    self.stats["tls_fallbacks"] += 1
                    try:
                        r = self.session.request(method, url, headers=hdrs, data=data, params=params,
                                                 timeout=self.timeout_s, allow_redirects=True,
                                                 verify=False)
                        env.update({"tls_verify": False, "tls_fallback_reason": str(e)[:300],
                                    "t_first_byte_utc": now_utc().isoformat()})
                        body = r.content
                        env.update({"t_last_byte_utc": now_utc().isoformat(), "status": r.status_code,
                                    "response_headers": dict(r.headers), "final_url": r.url,
                                    "elapsed_ms": int(r.elapsed.total_seconds() * 1000)})
                        ok = 200 <= r.status_code < 300
                        self._consecutive_errors = 0 if ok else self._consecutive_errors + 1
                        return Fetched(ok, r.status_code, body, env, None if ok else f"http {r.status_code}")
                    except Exception as e2:  # noqa: BLE001
                        e = e2
                self._consecutive_errors += 1
                self.stats["errors"] += 1
                env["t_last_byte_utc"] = now_utc().isoformat()
                env["exception"] = f"{type(e).__name__}: {str(e)[:500]}"
                return Fetched(False, None, b"", env, env["exception"])

    def get(self, url: str, **kw) -> Fetched:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Fetched:
        return self.request("POST", url, **kw)
