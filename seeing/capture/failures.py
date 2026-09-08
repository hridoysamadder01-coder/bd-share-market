"""One named failure code for every way a public source can let us down.

The runner already writes a GAP record for anything that is not a clean 2xx, but
it wrote only two reasons — ``http`` and ``exception`` — which collapses a DNS
failure, a 429, an expired anti-forgery token and a silently-changed schema into
the same bucket. On replay that makes the difference between "the source was
down" and "the source changed shape" unrecoverable, and those two demand
opposite responses: wait, versus fix the parser.

So every failure is classified into exactly one code here, and the code is
written into the GAP record. Nothing is swallowed: a payload that arrives with
HTTP 200 and parses to zero frames is still a failure (``EMPTY_RESPONSE``), and
a payload whose keys no longer match what the adapter declared is still a
failure (``SCHEMA_CHANGE``) even though the bytes were fine.

`classify_fetch` looks only at the transport; `classify_parse` looks at what the
adapter made of the bytes. A capture step normally calls both, because a source
can fail at either layer independently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .http_client import Fetched

# The full taxonomy. Every GAP record carries one of these in `reason`.
CONNECT_ERROR = "connect_error"           # DNS, refused, reset — never reached the server
TIMEOUT = "timeout"                       # the request exceeded the client timeout
TLS_ERROR = "tls_error"                   # chain verification failed and no fallback applied
HTTP_ERROR = "http_error"                 # a 4xx/5xx that is not one of the specific codes below
RATE_LIMIT = "rate_limit"                 # 429, or a body that says so
AUTH_EXPIRED = "auth_expired"             # 401/403, or the anti-forgery token went stale
SOURCE_UNREACHABLE = "source_unreachable"  # 5xx / maintenance page / service suspended
NOT_FOUND = "not_found"                   # 404 — the route moved or never existed
EMPTY_RESPONSE = "empty_response"         # 2xx with a zero-length or content-free body
PARSE_ERROR = "parse_error"               # the adapter raised or could not read the body
SCHEMA_CHANGE = "schema_change"           # parsed, but the shape no longer matches the declaration
STALE = "stale"                           # the payload is byte-identical and older than the limit
GAP = "gap"                               # a known hole: the poll did not happen at all

CODES = (CONNECT_ERROR, TIMEOUT, TLS_ERROR, HTTP_ERROR, RATE_LIMIT, AUTH_EXPIRED,
         SOURCE_UNREACHABLE, NOT_FOUND, EMPTY_RESPONSE, PARSE_ERROR, SCHEMA_CHANGE,
         STALE, GAP)

# A 200 that is really an error. Bangladeshi exchange and regulator hosts return
# these routinely instead of a status code, so the body has to be read.
_SOFT_ERROR_RE = re.compile(
    r"service\s+suspended|site\s+is\s+under\s+maintenance|temporarily\s+unavailable|"
    r"too\s+many\s+requests|rate\s+limit|access\s+denied|request\s+blocked",
    re.I)
_TIMEOUT_RE = re.compile(r"timeout|timed out|ReadTimeout|ConnectTimeout", re.I)
_CONNECT_RE = re.compile(
    r"NameResolution|Failed to resolve|getaddrinfo|Connection refused|ConnectionError|"
    r"Connection reset|NewConnectionError|ProxyError", re.I)
_TLS_RE = re.compile(r"SSL|certificate", re.I)


@dataclass
class Failure:
    """One classified failure, ready to become a GAP record."""

    code: str
    detail: str = ""
    status: Optional[int] = None
    retryable: bool = True

    def as_gap(self) -> Dict[str, Any]:
        return {"reason": self.code, "detail": self.detail[:4000], "http_status": self.status,
                "retryable": self.retryable}


def classify_fetch(f: Fetched, *, empty_is_failure: bool = True) -> Optional[Failure]:
    """Transport-layer verdict. ``None`` means the response is worth parsing.

    A 2xx whose body is empty or is a maintenance page is a failure even though
    the transport succeeded — that is the case a plain ``f.ok`` check misses.
    """
    if f.status is None:
        err = f.error or f.http.get("exception") or ""
        if _TIMEOUT_RE.search(err):
            return Failure(TIMEOUT, err, None)
        if _TLS_RE.search(err) and not _CONNECT_RE.search(err):
            return Failure(TLS_ERROR, err, None, retryable=False)
        if _CONNECT_RE.search(err):
            return Failure(CONNECT_ERROR, err, None)
        return Failure(CONNECT_ERROR, err or "no status and no exception recorded", None)

    s = int(f.status)
    if s == 429:
        return Failure(RATE_LIMIT, f.error or "", s)
    if s in (401, 403):
        return Failure(AUTH_EXPIRED, f.error or "", s, retryable=True)
    if s == 404:
        return Failure(NOT_FOUND, f.error or "", s, retryable=False)
    if 500 <= s < 600:
        return Failure(SOURCE_UNREACHABLE, f.error or "", s)
    if not (200 <= s < 300):
        return Failure(HTTP_ERROR, f.error or "", s)

    head = f.body[:2000].decode("utf-8", "replace")
    if _SOFT_ERROR_RE.search(head):
        m = _SOFT_ERROR_RE.search(head)
        text = m.group(0).lower() if m else ""
        code = RATE_LIMIT if ("rate" in text or "many requests" in text) else SOURCE_UNREACHABLE
        return Failure(code, f"HTTP 200 but the body says: {m.group(0) if m else ''!r}", s)
    if empty_is_failure and len(f.body.strip()) == 0:
        return Failure(EMPTY_RESPONSE, "2xx with a zero-length body", s)
    return None


def classify_parse(frames: Sequence[Any], problems: Iterable[str], *,
                   expect_frames: bool = True,
                   declared_keys: Optional[Iterable[str]] = None,
                   seen_keys: Optional[Iterable[str]] = None,
                   missing_tolerated: int = 0) -> Optional[Failure]:
    """Parse-layer verdict, applied to what the adapter produced.

    ``declared_keys`` vs ``seen_keys`` is the schema-drift check: a source that
    quietly drops a field still returns 200 and still parses, and only this
    comparison catches it. Extra keys are fine (sources add columns); missing
    ones beyond ``missing_tolerated`` are a SCHEMA_CHANGE.
    """
    probs = [str(p) for p in problems]
    if expect_frames and not frames:
        return Failure(EMPTY_RESPONSE if not probs else PARSE_ERROR,
                       "; ".join(probs) or "parsed cleanly but produced no frames", None)
    if declared_keys is not None and seen_keys is not None:
        missing = sorted(set(declared_keys) - set(seen_keys))
        if len(missing) > missing_tolerated:
            return Failure(SCHEMA_CHANGE, f"declared keys absent from the payload: {missing}",
                           None, retryable=False)
    if probs:
        return Failure(PARSE_ERROR, "; ".join(probs), None)
    return None


def classify_stale(body_sha256: str, previous_sha256: Optional[str],
                   unchanged_polls: int, *, limit: int) -> Optional[Failure]:
    """A source that keeps returning the same bytes is reported, not hidden.

    This is deliberately not an error during a closed market, where an identical
    book is the correct answer — the caller decides the ``limit`` per source and
    per session phase. Here we only name the condition.
    """
    if previous_sha256 is not None and body_sha256 == previous_sha256 and unchanged_polls >= limit:
        return Failure(STALE, f"{unchanged_polls} consecutive identical payloads "
                              f"(sha256 {body_sha256[:12]}…)", None)
    return None


@dataclass
class SourceHealth:
    """Rolling per-source health, the input to the status JSON."""

    source: str
    ok: int = 0
    failures: Dict[str, int] = field(default_factory=dict)
    consecutive_failures: int = 0
    last_ok_utc: Optional[str] = None
    last_failure_utc: Optional[str] = None
    last_failure_code: Optional[str] = None
    last_failure_detail: Optional[str] = None
    unchanged_polls: int = 0
    last_body_sha256: Optional[str] = None
    problems: List[str] = field(default_factory=list)

    def record_ok(self, t_utc: str, body_sha256: Optional[str] = None) -> None:
        self.ok += 1
        self.consecutive_failures = 0
        self.last_ok_utc = t_utc
        if body_sha256 is not None:
            self.unchanged_polls = self.unchanged_polls + 1 if body_sha256 == self.last_body_sha256 else 0
            self.last_body_sha256 = body_sha256

    def record_failure(self, f: Failure, t_utc: str) -> None:
        self.failures[f.code] = self.failures.get(f.code, 0) + 1
        self.consecutive_failures += 1
        self.last_failure_utc = t_utc
        self.last_failure_code = f.code
        self.last_failure_detail = f.detail[:500]

    @property
    def attempts(self) -> int:
        return self.ok + sum(self.failures.values())

    @property
    def success_rate(self) -> Optional[float]:
        return round(self.ok / self.attempts, 4) if self.attempts else None

    def status(self) -> str:
        """WORKING / DEGRADED / FAILING / UNTRIED — what the dashboard shows."""
        if not self.attempts:
            return "UNTRIED"
        if self.consecutive_failures >= 3:
            return "FAILING"
        if self.consecutive_failures or (self.success_rate or 1.0) < 0.9:
            return "DEGRADED"
        return "WORKING"

    def as_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "status": self.status(), "attempts": self.attempts,
                "ok": self.ok, "success_rate": self.success_rate, "failures": dict(self.failures),
                "consecutive_failures": self.consecutive_failures, "last_ok_utc": self.last_ok_utc,
                "last_failure_utc": self.last_failure_utc, "last_failure_code": self.last_failure_code,
                "last_failure_detail": self.last_failure_detail,
                "unchanged_polls": self.unchanged_polls}
