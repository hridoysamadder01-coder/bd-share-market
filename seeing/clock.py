"""Two clocks, one time zone.

* ``mono_ns()``  — monotonic clock for ordering and gap detection inside one
  process epoch (never compared across machines or restarts).
* ``now_utc()``  — wall clock, UTC. Receipt times are always UTC.
* Source time strings are stored verbatim and interpreted as Asia/Dhaka
  (UTC+06:00, no daylight saving) on the trading date.

Session windows: the exchange's own "Holidays and Trading Sessions" page
(https://www.dsebd.org/hts.php, fetched first-hand 2026-09-06, fixture
``tests/fixtures/dsebd_hts_2026-09-06.html``) lists for the Public / Spot /
Debt markets: pre-open "Not Applicable", continuous 10:00 AM – 2:00 PM,
closing & post-closing (trade at close price) 2:00 – 2:10 PM. That supersedes
the 2022 press schedule (10:00–14:20 / 14:20–14:30) the design document
carried. A pre-open label is kept for the minutes before 10:00 so that any
pre-10:00 book activity is labelled, not dropped. Windows label frames — they
never drop them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional

DHAKA = timezone(timedelta(hours=6), name="Asia/Dhaka")
UTC = timezone.utc

SESSION_VERIFIED = True
SESSION_SOURCE = "dsebd.org/hts.php fetched 2026-09-06 (Public/Spot/Debt: continuous 10:00–14:00, close session 14:00–14:10)"

# Windows in Dhaka local time.
PRE_OPEN_START = dtime(9, 45)     # label only: hts.php says pre-open is "Not Applicable"
CONTINUOUS_START = dtime(10, 0)
CONTINUOUS_END = dtime(14, 0)
POST_CLOSE_END = dtime(14, 10)
TRADING_WEEKDAYS = (6, 0, 1, 2, 3)  # Sun..Thu as Python weekday()

PHASES = ("CLOSED", "PRE_OPEN", "CONTINUOUS", "POST_CLOSE")


def mono_ns() -> int:
    return time.monotonic_ns()


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_dhaka(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(DHAKA)


def parse_source_local(s: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """Interpret a naive source string as Asia/Dhaka wall time → aware UTC.

    Returns None when the string does not parse. The verbatim string is kept
    by the caller; this function never mutates or repairs it.
    """
    try:
        naive = datetime.strptime(s.strip(), fmt)
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=DHAKA).astimezone(UTC)


def epoch_ms_to_utc(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def session_phase(ts_utc: datetime) -> str:
    """Label a UTC instant with the assumed session phase."""
    local = to_dhaka(ts_utc)
    if local.weekday() not in TRADING_WEEKDAYS:
        return "CLOSED"
    t = local.time()
    if PRE_OPEN_START <= t < CONTINUOUS_START:
        return "PRE_OPEN"
    if CONTINUOUS_START <= t < CONTINUOUS_END:
        return "CONTINUOUS"
    if CONTINUOUS_END <= t < POST_CLOSE_END:
        return "POST_CLOSE"
    return "CLOSED"


def trading_date(ts_utc: datetime) -> date:
    return to_dhaka(ts_utc).date()


def local_hhmm_to_utc(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=DHAKA).astimezone(UTC)


@dataclass(frozen=True)
class ClockSample:
    t_utc: str
    t_mono_ns: int

    @staticmethod
    def now() -> "ClockSample":
        return ClockSample(now_utc().isoformat(), mono_ns())
