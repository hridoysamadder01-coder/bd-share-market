"""Every way a public source fails must land in exactly one named bucket.

The cases that matter most are the ones a plain `response.ok` check gets wrong:
a 200 carrying a maintenance page, a 200 that parses to nothing, and a payload
that parses fine but has quietly lost a field.
"""
import pytest

from seeing.capture.failures import (AUTH_EXPIRED, CONNECT_ERROR, EMPTY_RESPONSE, HTTP_ERROR,
                                     NOT_FOUND, PARSE_ERROR, RATE_LIMIT, SCHEMA_CHANGE, STALE,
                                     SOURCE_UNREACHABLE, TIMEOUT, TLS_ERROR, SourceHealth,
                                     classify_fetch, classify_parse, classify_stale)
from seeing.capture.http_client import Fetched


def fetched(status=None, body=b"", error=None, exception=None):
    http = {"method": "GET", "url": "https://example.test/x"}
    if exception:
        http["exception"] = exception
    return Fetched(ok=(status is not None and 200 <= status < 300), status=status,
                   body=body, http=http, error=error)


@pytest.mark.parametrize("status,code", [
    (429, RATE_LIMIT), (401, AUTH_EXPIRED), (403, AUTH_EXPIRED), (404, NOT_FOUND),
    (500, SOURCE_UNREACHABLE), (503, SOURCE_UNREACHABLE), (418, HTTP_ERROR), (302, HTTP_ERROR),
])
def test_http_statuses_map_to_their_own_codes(status, code):
    f = classify_fetch(fetched(status=status, body=b"x"))
    assert f is not None and f.code == code and f.status == status


@pytest.mark.parametrize("exc,code", [
    ("ReadTimeout: timed out", TIMEOUT),
    ("ConnectionError: Failed to resolve 'nope.test'", CONNECT_ERROR),
    ("SSLCertVerificationError: certificate verify failed", TLS_ERROR),
])
def test_transport_exceptions_are_separated(exc, code):
    f = classify_fetch(fetched(exception=exc, error=exc))
    assert f is not None and f.code == code and f.status is None


def test_a_clean_200_is_not_a_failure():
    assert classify_fetch(fetched(status=200, body=b'{"a":1}')) is None


def test_200_carrying_a_maintenance_page_is_still_a_failure():
    """The case a plain response.ok check gets wrong."""
    body = b"<html><body><h1>Service Suspended</h1></body></html>"
    f = classify_fetch(fetched(status=200, body=body))
    assert f is not None and f.code == SOURCE_UNREACHABLE and f.status == 200


def test_200_saying_too_many_requests_is_a_rate_limit_not_an_outage():
    f = classify_fetch(fetched(status=200, body=b"Too Many Requests, slow down"))
    assert f is not None and f.code == RATE_LIMIT


def test_200_with_an_empty_body_is_a_failure_and_can_be_opted_out():
    assert classify_fetch(fetched(status=200, body=b"   ")).code == EMPTY_RESPONSE
    assert classify_fetch(fetched(status=200, body=b""), empty_is_failure=False) is None


def test_parsing_to_zero_frames_is_a_failure():
    assert classify_parse([], []).code == EMPTY_RESPONSE
    assert classify_parse([], ["json: expecting value"]).code == PARSE_ERROR
    assert classify_parse([], [], expect_frames=False) is None


def test_a_dropped_field_is_schema_change_and_extra_fields_are_fine():
    declared = ("ltp", "open", "high")
    assert classify_parse([{"x": 1}], [], declared_keys=declared,
                          seen_keys=("ltp", "open", "high", "brand_new")) is None
    f = classify_parse([{"x": 1}], [], declared_keys=declared, seen_keys=("ltp", "open"))
    assert f is not None and f.code == SCHEMA_CHANGE and "high" in f.detail
    assert f.retryable is False          # retrying will not bring the field back


def test_schema_change_outranks_a_mere_parse_problem():
    f = classify_parse([{"x": 1}], ["row 3 odd"], declared_keys=("a",), seen_keys=())
    assert f.code == SCHEMA_CHANGE


def test_stale_needs_both_identical_bytes_and_the_limit():
    assert classify_stale("aa", "aa", 2, limit=3) is None
    assert classify_stale("aa", "bb", 9, limit=3) is None      # changed → not stale
    assert classify_stale("aa", None, 9, limit=3) is None      # nothing to compare
    assert classify_stale("aa", "aa", 3, limit=3).code == STALE


def test_health_rolls_up_to_a_status_word():
    h = SourceHealth("lankabd_depth")
    assert h.status() == "UNTRIED" and h.success_rate is None

    for i in range(10):
        h.record_ok(f"2026-09-08T00:00:{i:02d}+00:00", body_sha256="same")
    assert h.status() == "WORKING" and h.ok == 10
    assert h.unchanged_polls == 9                     # first poll had nothing to match

    h.record_failure(classify_fetch(fetched(status=503, body=b"x")), "2026-09-08T00:01:00+00:00")
    assert h.status() == "DEGRADED" and h.last_failure_code == SOURCE_UNREACHABLE
    for _ in range(2):
        h.record_failure(classify_fetch(fetched(status=503, body=b"x")), "2026-09-08T00:02:00+00:00")
    assert h.status() == "FAILING" and h.failures[SOURCE_UNREACHABLE] == 3
    assert h.attempts == 13 and h.success_rate == pytest.approx(10 / 13, abs=1e-4)

    h.record_ok("2026-09-08T00:03:00+00:00", body_sha256="different")
    assert h.consecutive_failures == 0 and h.unchanged_polls == 0


def test_every_code_is_reachable_and_serialisable():
    from seeing.capture.failures import CODES
    f = classify_fetch(fetched(status=404))
    d = f.as_gap()
    assert d["reason"] in CODES and d["http_status"] == 404 and d["retryable"] is False
