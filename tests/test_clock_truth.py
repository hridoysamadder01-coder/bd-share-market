from datetime import datetime, timezone

from seeing import clock
from seeing.truth import Truth, merge_truth, not_observable, observed, truth_summary, CANONICAL_FIELDS


def test_source_local_is_dhaka_plus_six():
    t = clock.parse_source_local("2026-09-03 14:09:55")
    assert t == datetime(2026, 9, 3, 8, 9, 55, tzinfo=timezone.utc)
    assert clock.parse_source_local("garbage") is None


def test_epoch_ms_matches_lm_date_time():
    # tape row 1788422995000 (BRACBANK last row) == watch LM_DATE_TIME 2026-09-03 14:09:55 Dhaka
    assert clock.epoch_ms_to_utc(1788422995000.0) == clock.parse_source_local("2026-09-03 14:09:55")


def test_session_phases():
    d = datetime(2026, 9, 6, 3, 57, tzinfo=timezone.utc)   # Sunday 09:57 Dhaka
    assert clock.session_phase(d) == "PRE_OPEN"
    assert clock.session_phase(d.replace(hour=4, minute=0)) == "CONTINUOUS"
    assert clock.session_phase(d.replace(hour=7, minute=59)) == "CONTINUOUS"
    assert clock.session_phase(d.replace(hour=8, minute=5)) == "POST_CLOSE"
    assert clock.session_phase(d.replace(hour=8, minute=15)) == "CLOSED"
    fri = datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc)
    assert clock.session_phase(fri) == "CLOSED"


def test_truth_merge_prefers_observed():
    a = not_observable(CANONICAL_FIELDS)
    b = observed(["ltp", "bid_levels"])
    c = {"ltp": Truth.INFERRED, "trade_side": Truth.INFERRED}
    m = merge_truth(a, c, b)
    assert m["ltp"] is Truth.OBSERVED
    assert m["trade_side"] is Truth.INFERRED
    assert m["queue_position"] is Truth.NOT_OBSERVABLE
    s = truth_summary(m)
    assert "queue_position" in s["NOT_OBSERVABLE"] and "ltp" in s["OBSERVED"]
