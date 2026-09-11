"""Every source the engine can capture must have somewhere to land on replay.

Capture breadth is worth nothing until replay can read it back. The whole-market
pull of 2026-09-08 stored 691 fundamentals pages and 691 ownership pages that no
table could return: `replay()` carried its own hand-written routing map covering
only the two intraday hosts, so seven registered sources parsed into nothing and
the omission was silent — no error, no empty table, just absence.

This test walks the shipped registry, so a source added to the engine tomorrow
fails here until replay knows where to put it.
"""
import pytest

from seeing.capture.engine import build_registry
from seeing.capture.http_client import PoliteClient
from seeing.replay import SOURCE_TABLE, TABLES, _JSON_ENCODED_TABLES, _adapters


def registry():
    return build_registry(PoliteClient(), ["BRACBANK"])


def test_every_fetchable_source_is_routed_to_a_table():
    missing = [s.name for s in registry()
               if s.adapter is not None and not s.blocked and s.name not in SOURCE_TABLE]
    assert missing == [], f"captured but unreadable on replay: {missing}"


def test_every_fetchable_source_has_a_replay_adapter():
    ad = _adapters()
    missing = [s.name for s in registry()
               if s.adapter is not None and not s.blocked and s.name not in ad]
    assert missing == [], f"no adapter to parse these back: {missing}"


def test_a_blocked_source_needs_no_table():
    """Blocked sources stay in the registry as a gap record; they never fetch."""
    blocked = [s.name for s in registry() if s.blocked]
    assert blocked, "the registry must keep naming its blocked sources"
    assert all(s.adapter is None for s in registry() if s.blocked)


def test_every_routed_table_is_one_replay_actually_builds():
    unknown = sorted(set(SOURCE_TABLE.values()) - set(TABLES))
    assert unknown == [], f"routed to tables replay never creates: {unknown}"


def test_books_are_never_json_encoded():
    """bid_levels/ask_levels must stay real lists — fusion reads them directly."""
    assert "books" not in _JSON_ENCODED_TABLES
    assert _JSON_ENCODED_TABLES <= set(TABLES)


@pytest.mark.parametrize("source,table", sorted(SOURCE_TABLE.items()))
def test_the_routing_is_stable_per_source(source, table):
    """Pins the map itself: a source silently re-routed is a data-loss bug."""
    assert isinstance(table, str) and table in TABLES
