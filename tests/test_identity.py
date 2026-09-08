"""Stage 0 — the reference and identity spine.

The rules under test are the ones that make this a spine rather than a guess:
a conflict is never merged or voted on, a source-private id space is never
compared against another source's, an unnameable exchange is never assumed, and
the same evidence always produces the same map.
"""
import json
import os

import pytest

from seeing.identity import (ATTRIBUTES, DESCRIPTIVE_ATTRIBUTES, IDENTITY_ATTRIBUTES,
                             SOURCE_SCOPED_ATTRIBUTES, SourceClaim, build_spine,
                             collect_claims, normalise_code, wired_dse_universe)
from seeing.truth import Truth

from .conftest import ROOT


def claim(source, code, exchange="DSE", as_of="2026-09-08", **kw):
    return SourceClaim(source=source, code_raw=code, exchange=exchange, as_of=as_of,
                       evidence=f"test/{source}", **kw)


# ------------------------------------------------------------------ normalisation
@pytest.mark.parametrize("raw,want", [
    ("bracbank", "BRACBANK"),
    ("1 J A N A T A M F", "1JANATAMF"),          # CSE renders one letter per node
    ("  GP\t", "GP"),
    ("", ""),
    (None, ""),
])
def test_normalise_is_case_and_whitespace_only(raw, want):
    assert normalise_code(raw) == want


def test_normalisation_does_not_strip_suffixes_or_fuzzy_match():
    """The one normalisation is documented; anything cleverer would be a guess."""
    assert normalise_code("BRACBANK-EQ") == "BRACBANK-EQ" != normalise_code("BRACBANK")
    assert normalise_code("BRAC BANK") == "BRACBANK"   # whitespace only


# ------------------------------------------------------------------ agreement
def test_two_sources_naming_the_same_code_is_observed_not_inferred():
    s = build_spine([claim("lankabd_watch", "BRACBANK", company_id="88"),
                     claim("stocknow_instruments", "BRACBANK")])
    lst = s.listings["DSE:BRACBANK"]
    assert lst.resolved and lst.truth == Truth.OBSERVED.value
    assert lst.sources == ["lankabd_watch", "stocknow_instruments"]
    assert lst.attributes["company_id"].value == "88"
    assert lst.attributes["company_id"].truth == Truth.OBSERVED.value


def test_a_field_no_source_carries_stays_not_observable():
    s = build_spine([claim("cse_current_price", "BRACBANK", exchange="CSE")])
    r = s.listings["CSE:BRACBANK"].attributes["company_id"]
    assert r.value is None and r.truth == Truth.NOT_OBSERVABLE.value
    assert "no wired source publishes" in r.rule


# ------------------------------------------------------------------ conflicts
def test_an_identity_conflict_leaves_the_listing_unresolved_and_withholds_the_value():
    s = build_spine([claim("lankabd_watch", "X", company_id="1"),
                     claim("lankabd_grid", "X", company_id="2")])
    lst = s.listings["DSE:X"]
    assert lst.resolved is False and lst.truth == Truth.NOT_OBSERVABLE.value
    r = lst.attributes["company_id"]
    assert r.value is None                                   # never merged, never voted on
    assert r.conflicting_values == {"lankabd_grid": "2", "lankabd_watch": "1"}
    assert [u["key"] for u in s.unresolved] == ["DSE:X"]
    assert s.conflicts[0].scope == "identity_attribute"


def test_a_descriptive_conflict_is_recorded_but_identity_survives():
    """'Aamra Networks Limited.' vs 'Aamra Networks Limited' is a full stop, not a
    different instrument. The value is still withheld; the listing still resolves."""
    s = build_spine([claim("lankabd_grid", "AAMRANET", company_name="Aamra Networks Limited."),
                     claim("stocknow_instruments", "AAMRANET", company_name="Aamra Networks Limited")])
    lst = s.listings["DSE:AAMRANET"]
    assert lst.resolved is True and lst.truth == Truth.OBSERVED.value
    assert lst.attributes["company_name"].value is None      # withheld, not picked
    assert lst.conflicts and lst.conflicts[0].scope == "descriptive_attribute"
    assert lst.identity_conflicts == []
    assert s.unresolved == []


def test_market_category_granularity_is_a_descriptive_conflict():
    """LankaBD says A-MF, everyone else says A — the same category, two granularities."""
    s = build_spine([claim("lankabd_watch", "1JANATAMF", market_category="A-MF"),
                     claim("dse_fundamentals", "1JANATAMF", market_category="A")])
    lst = s.listings["DSE:1JANATAMF"]
    assert lst.resolved is True
    assert lst.attributes["market_category"].value is None
    assert lst.conflicts[0].attribute == "market_category"


def test_one_company_id_under_two_codes_is_reported():
    s = build_spine([claim("lankabd_watch", "TB5Y0428", company_id="10814"),
                     claim("lankabd_watch", "TB5Y0628", company_id="10814")])
    reuse = [c for c in s.conflicts if c.scope == "company_id_reuse"]
    assert len(reuse) == 1
    assert set(reuse[0].values) == {"TB5Y0428", "TB5Y0628"}
    assert "rename or a source error" in reuse[0].note


# ------------------------------------------------------------------ source-scoped ids
def test_source_private_sector_ids_are_kept_apart_not_compared():
    """LankaBD sector 10 and StockNow sector 25 are the same sector in unrelated
    numbering schemes. Calling that a conflict would be a category error."""
    s = build_spine([claim("lankabd_watch", "CLICL", sector_id="10"),
                     claim("stocknow_instruments", "CLICL", sector_id="25")])
    lst = s.listings["DSE:CLICL"]
    assert lst.resolved is True
    assert lst.source_scoped["sector_id"] == {"lankabd_watch": "10", "stocknow_instruments": "25"}
    assert all(c.attribute != "sector_id" for c in s.conflicts)
    assert "sector_id" not in lst.attributes                 # never reconciled as one value


def test_the_three_attribute_classes_do_not_overlap():
    assert not set(IDENTITY_ATTRIBUTES) & set(DESCRIPTIVE_ATTRIBUTES)
    assert not set(SOURCE_SCOPED_ATTRIBUTES) & set(ATTRIBUTES)
    assert set(ATTRIBUTES) == set(IDENTITY_ATTRIBUTES) | set(DESCRIPTIVE_ATTRIBUTES)


# ------------------------------------------------------------------ exchanges
def test_the_same_code_on_two_exchanges_is_two_listings_and_one_inferred_company():
    """DSE BRACBANK and CSE BRACBANK have separate books; merging them would
    manufacture a price series no exchange publishes."""
    s = build_spine([claim("lankabd_watch", "BRACBANK", exchange="DSE"),
                     claim("cse_current_price", "BRACBANK", exchange="CSE")])
    assert sorted(s.listings) == ["CSE:BRACBANK", "DSE:BRACBANK"]
    co = s.companies["BRACBANK"]
    assert co.listings == ["CSE:BRACBANK", "DSE:BRACBANK"]
    assert co.truth == Truth.INFERRED.value                  # never OBSERVED
    assert "NOT the same instrument" in co.rule
    assert "uncorroborated" in co.corroboration


def test_a_single_exchange_company_is_observed():
    s = build_spine([claim("lankabd_watch", "GP", exchange="DSE")])
    assert s.companies["GP"].truth == Truth.OBSERVED.value


def test_an_opaque_exchange_enum_is_placed_only_when_one_exchange_is_possible():
    """EcoSoft sends StockExchange=1. That is not an exchange name."""
    placed = build_spine([claim("lankabd_watch", "GP", exchange="DSE"),
                          claim("ecosoft_ost", "GP", exchange=None, source_exchange_code="1")])
    lst = placed.listings["DSE:GP"]
    assert "ecosoft_ost" in lst.sources
    assert lst.exchange_truth == Truth.INFERRED.value        # inferred, not assumed
    assert "opaque exchange enum" in lst.exchange_rule
    assert placed.unresolved == []


def test_an_ambiguous_opaque_exchange_refuses_to_place_the_claim():
    """The code exists on both exchanges, so the enum settles nothing."""
    s = build_spine([claim("lankabd_watch", "AAMRANET", exchange="DSE"),
                     claim("cse_current_price", "AAMRANET", exchange="CSE"),
                     claim("ecosoft_ost", "AAMRANET", exchange=None, source_exchange_code="1")])
    assert [u["key"] for u in s.unresolved] == ["UNKNOWN:AAMRANET"]
    assert s.unresolved[0]["attributes"] == ["exchange"]
    assert "'1'" in s.unresolved[0]["detail"]
    for k in ("DSE:AAMRANET", "CSE:AAMRANET"):
        assert "ecosoft_ost" not in s.listings[k].sources     # not silently attached


def test_an_unplaceable_code_with_no_corroboration_is_unresolved():
    s = build_spine([claim("ecosoft_ost", "MYSTERY", exchange=None, source_exchange_code="1")])
    assert [u["key"] for u in s.unresolved] == ["UNKNOWN:MYSTERY"]
    assert s.listings == {}


# ------------------------------------------------------------------ listing history
def test_a_source_that_stops_naming_a_listing_is_recorded():
    s = build_spine([claim("lankabd_watch", "OLDCO", as_of="2026-09-06"),
                     claim("lankabd_watch", "NEWCO", as_of="2026-09-06"),
                     claim("lankabd_watch", "NEWCO", as_of="2026-09-08")])
    old, new = s.listings["DSE:OLDCO"], s.listings["DSE:NEWCO"]
    assert old.first_seen == old.last_seen == "2026-09-06"
    assert old.absent_from_latest == ["lankabd_watch"]
    assert new.first_seen == "2026-09-06" and new.last_seen == "2026-09-08"
    assert new.absent_from_latest == []


def test_absence_is_not_reported_for_a_source_that_did_not_run_in_the_latest_evidence():
    """A source with no claims at all on the latest date has not delisted anything."""
    s = build_spine([claim("ecosoft_ost", "X", as_of="2026-09-06"),
                     claim("lankabd_watch", "X", as_of="2026-09-06"),
                     claim("lankabd_watch", "X", as_of="2026-09-08")])
    assert s.listings["DSE:X"].absent_from_latest == []


# ------------------------------------------------------------------ determinism
def test_the_same_evidence_gives_the_same_hash_and_claim_order_does_not_matter():
    cs = [claim("lankabd_watch", "B", company_id="2"), claim("stocknow_instruments", "A"),
          claim("cse_current_price", "A", exchange="CSE"), claim("lankabd_grid", "B", company_id="2")]
    a = build_spine(cs).sha256()
    b = build_spine(list(reversed(cs))).sha256()
    assert a == b and len(a) == 64


def test_the_hashed_core_excludes_the_wall_clock():
    s = build_spine([claim("lankabd_watch", "GP")])
    assert s.to_json("2026-01-01T00:00:00+00:00")["spine_sha256"] == \
           s.to_json("2099-12-31T23:59:59+00:00")["spine_sha256"]
    assert "generated_utc" not in json.dumps(s.core())


# ------------------------------------------------------------------ coverage
def test_coverage_separates_absent_from_unresolved():
    s = build_spine([claim("lankabd_watch", "OK"),
                     claim("lankabd_watch", "BAD", company_id="1"),
                     claim("lankabd_grid", "BAD", company_id="2")])
    cov = s.coverage(["OK", "BAD", "NOWHERE"], "DSE")
    assert cov == {"universe_size": 3, "exchange": "DSE", "resolved": 1, "unresolved": 1,
                   "absent": 1, "resolved_pct": 33.33,
                   "unresolved_symbols": ["BAD"], "absent_symbols": ["NOWHERE"]}


def test_empty_input_does_not_crash():
    s = build_spine([])
    assert s.listings == {} and s.companies == {} and s.conflicts == [] and s.unresolved == []
    assert len(s.sha256()) == 64
    assert s.coverage([], "DSE")["resolved_pct"] is None


def test_an_unreadable_code_is_dropped_not_turned_into_a_listing():
    s = build_spine([claim("cse_current_price", "   ", exchange="CSE"), claim("lankabd_watch", "GP")])
    assert sorted(s.listings) == ["DSE:GP"]


# ------------------------------------------------------------------ real evidence
@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "evidence", "public_engine",
                                                    "2026-09-08", "MANIFEST.json")),
                    reason="engine evidence not present")
def test_builds_from_the_committed_evidence_and_covers_the_wired_universe():
    """The Stage 0 gate, exercised on the real committed evidence."""
    spine = build_spine(collect_claims())
    assert spine.claims > 2000
    assert set(spine.sources) >= {"lankabd_watch", "lankabd_grid", "dse_fundamentals",
                                  "stocknow_instruments", "cse_current_price",
                                  "bullbd_detail", "ecosoft_ost"}
    exchanges = {l.exchange for l in spine.listings.values()}
    assert exchanges == {"DSE", "CSE"}                       # no enum leaked in as an exchange

    cov = spine.coverage(wired_dse_universe(), "DSE")
    # The gate: every wired symbol either resolves or is explicitly listed.
    assert cov["unresolved"] == 0
    assert cov["resolved"] + cov["unresolved"] + cov["absent"] == cov["universe_size"]
    assert cov["resolved_pct"] > 85

    # every unresolved entry names why, and every conflict names its sources
    for u in spine.unresolved:
        assert u["reason"] and u["sources"]
    for c in spine.conflicts:
        assert c.values and c.note


# ------------------------------------------------------------------ the gate
def test_the_gate_is_an_accounting_check_not_a_coverage_threshold():
    """A low resolved percentage must not fail the gate — 68 bonds no source
    lists are a fact about the market, and hiding them would be the real defect."""
    from seeing.identity import stage0_gate
    s = build_spine([claim("lankabd_watch", "OK")])
    g = stage0_gate(s, ["OK"] + [f"BOND{i}" for i in range(99)], s.sha256())
    assert g["result"] == "PASS"
    assert g["coverage_wired_dse"]["resolved_pct"] == 1.0        # 1 of 100
    assert g["coverage_wired_dse"]["absent"] == 99
    assert len(g["coverage_wired_dse"]["absent_symbols"]) == 99  # all named


def test_the_gate_blocks_when_a_rebuild_disagrees():
    from seeing.identity import stage0_gate
    s = build_spine([claim("lankabd_watch", "OK")])
    g = stage0_gate(s, ["OK"], "a-different-hash")
    assert g["result"] == "BLOCKED"
    assert [c["check"] for c in g["checks"] if not c["pass"]] == \
           ["deterministic rebuild reproduces the same spine hash"]


def test_the_gate_blocks_when_an_enum_leaks_in_as_an_exchange():
    from seeing.identity import stage0_gate
    s = build_spine([claim("ecosoft_ost", "X", exchange="1")])
    g = stage0_gate(s, ["X"], s.sha256())
    assert g["result"] == "BLOCKED"
    assert any("opaque source enum" in c["check"] for c in g["checks"] if not c["pass"])


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "evidence", "identity",
                                                    "2026-09-08", "STAGE0_GATE.json")),
                    reason="gate output not present")
def test_the_committed_gate_output_says_pass():
    g = json.load(open(os.path.join(ROOT, "evidence", "identity", "2026-09-08",
                                    "STAGE0_GATE.json")))
    assert g["result"] == "PASS" and g["stage"] == 0
    assert all(c["pass"] for c in g["checks"])


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "evidence", "identity",
                                                    "2026-09-08", "IDENTITY_MAP.json")),
                    reason="identity map not present")
def test_the_committed_map_hash_matches_a_fresh_rebuild():
    """Deterministic replay, checked against what is on disk."""
    m = json.load(open(os.path.join(ROOT, "evidence", "identity", "2026-09-08",
                                    "IDENTITY_MAP.json")))
    assert build_spine(collect_claims()).sha256() == m["spine_sha256"]
