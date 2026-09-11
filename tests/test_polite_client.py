"""What the polite client backs off from, and what it does not.

Backoff is a promise to the operator of a source, not a general error handler.
The distinction it has to make is between a server that is struggling or telling
us to slow down (429, 403, 5xx) and a server that answered promptly and
definitively about one resource (404, 410). Sweeping a 691-symbol universe
against a source that only lists equities produces hundreds of 404s in a row;
counting those toward an exponential backoff took the whole-market run to 120 s
per request and would have turned a nine-minute sweep into six hours.

The waits are recorded rather than served — a test that actually slept the
backoff it is asserting would take eight minutes to prove one branch.
"""
from datetime import timedelta

import pytest

from seeing.capture import http_client as hc
from seeing.capture.http_client import NO_BACKOFF_STATUSES, PoliteClient


class FakeResponse:
    def __init__(self, status, body=b"{}"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": "application/json"}
        self.url = "https://source.test/x"
        self.elapsed = timedelta(milliseconds=12)


class FakeSession:
    """Answers with a scripted list of statuses; the last one repeats."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def request(self, method, url, **kw):
        s = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return FakeResponse(s)


@pytest.fixture
def waits(monkeypatch):
    """Every sleep the client asks for, in order, without serving any of them."""
    recorded = []
    monkeypatch.setattr(hc.time, "sleep", lambda s: recorded.append(s))
    return recorded


def client(statuses, **kw):
    return PoliteClient(min_gap_s=0.0, session=FakeSession(statuses), **kw)


def test_a_success_leaves_no_backoff(waits):
    c = client([200])
    c.request("GET", "https://source.test/x")
    assert c.backoff_s() == 0.0 and waits == []


def test_a_missing_instrument_is_an_answer_not_a_rate_problem(waits):
    """400 consecutive 404s must not slow the sweep down by one second."""
    c = client([404])
    for _ in range(400):
        f = c.request("GET", "https://source.test/TB10Y0127")
        assert f.ok is False and f.status == 404
    assert c.backoff_s() == 0.0, "a 404 sweep must stay at the minimum gap"
    assert sum(waits) == 0.0, "and must not have waited at all"
    assert c.stats["errors"] == 400, "while every miss is still counted as a gap"


def test_gone_is_treated_the_same_as_not_found(waits):
    c = client([410])
    for _ in range(10):
        c.request("GET", "https://source.test/x")
    assert c.backoff_s() == 0.0 and sum(waits) == 0.0
    assert NO_BACKOFF_STATUSES == {404, 410}


@pytest.mark.parametrize("status", [429, 403, 500, 502, 503])
def test_rate_limits_blocks_and_server_faults_still_back_off(status, waits):
    """These are about us or about the server, so the promise still applies."""
    c = client([status])
    c.request("GET", "https://source.test/x")
    first = c.backoff_s()
    c.request("GET", "https://source.test/x")
    assert first >= 2.0, f"http {status} must earn a backoff"
    assert c.backoff_s() > first, f"http {status} must escalate"
    assert waits == [pytest.approx(first)], "and the earned wait is actually asked for"


def test_the_backoff_is_capped(waits):
    c = client([503], max_backoff_s=30.0)
    for _ in range(20):
        c.request("GET", "https://source.test/x")
    assert c.backoff_s() == 30.0
    assert max(waits) == 30.0


def test_a_success_after_failures_clears_the_backoff(waits):
    c = client([503, 503, 503, 200])
    for _ in range(4):
        c.request("GET", "https://source.test/x")
    assert c.backoff_s() == 0.0


def test_a_404_does_not_clear_a_real_backoff_it_simply_does_not_add_to_it(waits):
    """A miss between two rate limits must not be read as the server recovering.

    Neutrality is the whole point: a 404 neither escalates the backoff nor
    cancels one a 429 earned, because it says nothing about the server's load
    in either direction.
    """
    c = client([503, 503, 404])
    c.request("GET", "https://source.test/x")
    c.request("GET", "https://source.test/x")
    earned = c.backoff_s()
    c.request("GET", "https://source.test/missing")
    assert earned >= 4.0
    assert c.backoff_s() == earned, "the 404 changed nothing in either direction"
