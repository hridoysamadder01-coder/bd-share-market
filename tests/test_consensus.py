"""Cross-source reconciliation must report disagreement, never resolve it.

The rule under test throughout: `consensus` is filled only when the reporting
sources agree. A disagreement leaves it None and says by how much — no average,
no preferred source, no silent pick.
"""
from datetime import datetime, timezone

import pytest

from seeing.consensus import (Observation, reconcile, reconcile_book, reconcile_field,
                              timestamp_offsets)

NOW = datetime(2026, 9, 8, 8, 0, 0, tzinfo=timezone.utc)


def obs(source, **kw):
    kw.setdefault("symbol", "BRACBANK")
    kw.setdefault("t_recv_utc", "2026-09-08T07:59:58+00:00")
    fields = kw.pop("fields", {})
    return Observation(source=source, fields=fields, **kw)


def test_agreeing_sources_produce_a_consensus():
    r = reconcile_field([obs("lankabd", fields={"ltp": 62.3}),
                         obs("dsebd", fields={"ltp": 62.3})], "ltp")
    assert r.agree is True and r.consensus == 62.3 and r.spread == 0
    assert r.reporting == ["dsebd", "lankabd"] and r.silent == []


def test_disagreement_leaves_consensus_empty_and_keeps_every_value():
    r = reconcile_field([obs("lankabd", fields={"ltp": 62.3}),
                         obs("dsebd", fields={"ltp": 61.0}),
                         obs("stocknow", fields={"ltp": 62.9})], "ltp")
    assert r.agree is False
    assert r.consensus is None                       # the whole point: nothing is chosen
    assert r.values == {"lankabd": 62.3, "dsebd": 61.0, "stocknow": 62.9}
    assert r.spread == pytest.approx(1.9)
    assert "lankabd=62.3" in r.disagreement and "dsebd=61" in r.disagreement


def test_a_half_tick_of_rounding_is_not_a_disagreement():
    r = reconcile_field([obs("a", fields={"ltp": 62.30}), obs("b", fields={"ltp": 62.3049})], "ltp")
    assert r.agree is True


def test_a_source_that_is_silent_is_not_a_source_that_disagrees():
    r = reconcile_field([obs("a", fields={"ltp": 62.3}),
                         obs("b", fields={"open": 61.0}),
                         obs("c", fields={"ltp": None})], "ltp")
    assert r.reporting == ["a"] and sorted(r.silent) == ["b", "c"]
    assert r.agree is True and r.consensus == 62.3


def test_no_source_reporting_a_field_is_reported_as_such():
    r = reconcile_field([obs("a", fields={"open": 1.0})], "ltp")
    assert r.agree is None and r.consensus is None and "no source reported" in r.disagreement


def test_a_unit_mismatch_is_named_rather_than_read_as_a_price_conflict():
    r = reconcile_field([obs("a", fields={"day_value": 5_000_000.0}),
                         obs("b", fields={"day_value": 5.0})], "day_value")
    assert r.agree is False and "unit mismatch" in r.disagreement


def test_non_numeric_fields_compare_exactly():
    same = reconcile_field([obs("a", fields={"sector": "Bank"}), obs("b", fields={"sector": "Bank"})], "sector")
    diff = reconcile_field([obs("a", fields={"sector": "Bank"}), obs("b", fields={"sector": "NBFI"})], "sector")
    assert same.agree is True and same.consensus == "Bank"
    assert diff.agree is False and diff.consensus is None


def test_truncated_book_counts_as_silence_not_contradiction():
    deep = obs("lankabd", bid_levels=[(62.3, 100), (62.2, 200), (62.1, 300)])
    shallow = obs("dsebd", bid_levels=[(62.3, 100)])
    b = reconcile_book([deep, shallow], "bid")
    assert b["depths"] == {"lankabd": 3, "dsebd": 1} and b["depth_disagreement"] is True
    assert b["price_conflicts"] == 0 and b["quantity_conflicts"] == 0
    assert b["levels"][0]["truncated_for"] == []
    assert b["levels"][1]["truncated_for"] == ["dsebd"]      # silent, not wrong


def test_a_real_book_conflict_is_counted():
    a = obs("a", bid_levels=[(62.3, 100), (62.2, 200)])
    b = obs("b", bid_levels=[(62.3, 100), (62.2, 999)])
    r = reconcile_book([a, b], "bid")
    assert r["price_conflicts"] == 0 and r["quantity_conflicts"] == 1
    assert r["levels"][1]["quantity_agree"] is False


def test_missing_side_is_reported_rather_than_treated_as_empty():
    r = reconcile_book([obs("a", bid_levels=[(1.0, 1)])], "ask")
    assert r["sources"] == [] and "no source carried this side" in r["note"]


def test_age_and_freshness_are_different_numbers():
    """A payload fetched a second ago can still carry a five-minute-old observation."""
    o = obs("slow", t_recv_utc="2026-09-08T07:59:59+00:00",
            t_source_utc="2026-09-08T07:55:00+00:00")
    assert o.age_s(NOW) == pytest.approx(1.0)
    assert o.freshness_s() == pytest.approx(299.0)


def test_timestamp_offsets_are_kept_apart_from_value_disagreement():
    r = timestamp_offsets([obs("a", t_source_utc="2026-09-08T07:59:00+00:00"),
                           obs("b", t_source_utc="2026-09-08T07:58:30+00:00"),
                           obs("c")])
    assert r["pairwise_offset_s"]["a|b"] == pytest.approx(30.0)
    assert r["unstamped_sources"] == ["c"] and r["max_abs_offset_s"] == pytest.approx(30.0)


def test_full_reconcile_reports_state_without_choosing():
    o1 = obs("lankabd", fields={"ltp": 62.3, "day_volume": 1000},
             bid_levels=[(62.3, 100)], ask_levels=[(62.4, 50)],
             t_source_utc="2026-09-08T07:59:50+00:00")
    o2 = obs("dsebd", fields={"ltp": 61.0, "day_volume": 1000},
             bid_levels=[(62.3, 100)], t_recv_utc="2026-09-08T07:50:00+00:00")
    o3 = obs("amarstock_like", fields={"ltp": 62.3}, delayed=True, delay_note="15-minute delay")

    r = reconcile([o1, o2, o3], ["ltp", "day_volume"], NOW, stale_after_s=120.0)
    assert r["symbol"] == "BRACBANK" and r["symbol_conflict"] is None
    assert r["fields_disagreeing"] == ["ltp"] and r["fields_agreeing"] == ["day_volume"]
    assert r["agreement_rate"] == pytest.approx(0.5)
    assert r["stale_sources"] == ["dsebd"]                  # received 10 minutes ago
    assert r["delayed_sources"] == ["amarstock_like"]
    assert r["ask_book"]["sources"] == ["lankabd"]
    assert "none" in r["resolution_policy"]
    ltp = next(f for f in r["fields"] if f["field"] == "ltp")
    assert ltp["consensus"] is None and set(ltp["values"]) == {"lankabd", "dsebd", "amarstock_like"}


def test_conflicting_symbols_are_flagged_not_merged():
    r = reconcile([obs("a", symbol="BRACBANK"), obs("b", symbol="GP")], ["ltp"], NOW)
    assert r["symbol"] is None and r["symbol_conflict"] == ["BRACBANK", "GP"]


def test_empty_input_is_handled():
    assert reconcile([], ["ltp"], NOW)["note"] == "nothing to reconcile"
