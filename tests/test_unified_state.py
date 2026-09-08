"""Stage 2 — the unified market state, its hash, and the look-ahead it must not have.

The test that matters most here is `test_a_circuit_poll_after_a_frame_never_reaches_it`:
`fuse` used to fall back to the day's *first* circuit row when no poll preceded
a frame, flag it `ref_from_future=True`, and carry on. Every limit-derived
quantity on those frames was then computed from a reference the market had not
published yet. The flag made it visible but did not make it point-in-time, and
ROADMAP.md Stage 2 prohibits it outright. That test pins its removal.
"""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from seeing.fusion.fuse import fuse
from seeing.fusion.unified import (CROSS_SOURCE_FIELDS, IdentityIndex, STATE_FIELDS,
                                   assert_no_future_reference, canonical, canonical_json,
                                   chain, cross_source, run_hash, state_hash, summary, unify)

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


def _books(rows):
    """rows: (source, symbol, t_offset_s, bids, asks, day_trades, day_volume, day_value_mn)"""
    out = []
    for i, (src, sym, dt, bids, asks, tr, vol, val) in enumerate(rows):
        out.append({"source": src, "symbol": sym, "t_recv": T0 + timedelta(seconds=dt), "seq": i,
                    "epoch": "e", "body_sha256": f"h{src}{sym}{json.dumps(bids)}{tr}",
                    "http_status": 200, "elapsed_ms": 300, "bid_levels": bids, "ask_levels": asks,
                    "n_bid_levels": len(bids), "n_ask_levels": len(asks),
                    "ltp": (asks[0][0] if asks else None),
                    "open": 10.0, "high": 10.5, "low": 9.9, "close_published": 0.0, "yclose": 10.0,
                    "day_trades": tr, "day_volume": vol, "day_value_mn": val})
    df = pd.DataFrame(out)
    df["t_recv"] = pd.to_datetime(df["t_recv"], utc=True)
    df["dup_payload"] = df.groupby(["source", "symbol"])["body_sha256"].transform(lambda s: s.eq(s.shift()))
    return df


def _empty_tables(books, circuit):
    return {"books": books, "circuit": circuit, "tape": pd.DataFrame(), "watch": pd.DataFrame(),
            "market": pd.DataFrame(), "block": pd.DataFrame()}


def _circuit(offsets):
    c = pd.DataFrame({"symbol": ["X"] * len(offsets),
                      "t_recv": [T0 + timedelta(seconds=o) for o in offsets],
                      "upper_limit": [11.0] * len(offsets), "lower_limit": [9.0] * len(offsets),
                      "tick_size": [0.1] * len(offsets)})
    c["t_recv"] = pd.to_datetime(c["t_recv"], utc=True)
    return c


# --------------------------------------------------------------- the removed look-ahead
def test_a_circuit_poll_after_a_frame_never_reaches_it():
    """Frames before the first circuit poll get NaN limits, not the day's first row."""
    books = _books([("lankabd_depth", "X", t, [(10.0, 100)], [(10.1, 100)], 1, 10, 0.001)
                    for t in (0, 30, 60, 90)])
    # The only circuit observation lands at t+60 — the first two frames precede it.
    f = fuse(_empty_tables(books, _circuit([60])))
    f = f.sort_values("t_frame").reset_index(drop=True)

    assert "ref_from_future" not in f.columns, "the prohibited flag is back"
    assert list(f["ref_status"]) == ["NOT_YET_OBSERVED", "NOT_YET_OBSERVED", "OBSERVED", "OBSERVED"]
    assert f["upper_limit"].isna().tolist() == [True, True, False, False]
    assert f["lower_limit"].isna().tolist() == [True, True, False, False]
    # and nothing derived from the limit was computed for them either
    assert f["shares_to_door"].isna().tolist()[:2] == [True, True]
    assert not f["bid_at_upper_limit"].iloc[:2].any()


def test_the_reference_used_is_the_latest_poll_at_or_before_the_frame():
    books = _books([("lankabd_depth", "X", t, [(10.0, 100)], [(10.1, 100)], 1, 10, 0.001)
                    for t in (0, 50, 100)])
    c = _circuit([10, 60])
    c.loc[1, "upper_limit"] = 12.0                     # the second poll widens the band
    f = fuse(_empty_tables(books, c)).sort_values("t_frame").reset_index(drop=True)
    assert pd.isna(f["upper_limit"].iloc[0])           # frame at t=0 precedes the t=10 poll
    assert f["upper_limit"].iloc[1] == 11.0            # t=50 sees the t=10 poll, not the t=60 one
    assert f["upper_limit"].iloc[2] == 12.0
    assert (f["ref_age_s"].dropna() >= 0).all()


def test_no_circuit_source_at_all_is_its_own_recorded_state():
    books = _books([("lankabd_depth", "X", 0, [(10.0, 100)], [(10.1, 100)], 1, 10, 0.001)])
    f = fuse(_empty_tables(books, pd.DataFrame()))
    assert list(f["ref_status"]) == ["NO_CIRCUIT_SOURCE"]
    assert f["upper_limit"].isna().all()


def test_the_tick_used_to_scale_a_book_is_not_one_observed_later_in_the_day():
    """`.last()` handed 08:00's tick to a 04:00 book; the first observation is used."""
    books = _books([("lankabd_depth", "X", t, [(10.0, 100)], [(10.1, 100)], 1, 10, 0.001)
                    for t in (0, 3600)])
    c = _circuit([0, 3600])
    c.loc[1, "tick_size"] = 1.0                        # a later, different tick
    f = fuse(_empty_tables(books, c)).sort_values("t_frame").reset_index(drop=True)
    # spread 0.1 over the FIRST observed tick 0.1 == 1 tick, on both frames
    assert f["spread_ticks"].iloc[0] == pytest.approx(1.0)
    assert f["spread_ticks"].iloc[1] == pytest.approx(1.0)


# ------------------------------------------------------------------------ canonical form
def test_every_flavour_of_missing_hashes_the_same():
    a = state_hash({"upper_limit": None})
    b = state_hash({"upper_limit": float("nan")})
    c = state_hash({"upper_limit": np.nan})
    d = state_hash({"upper_limit": pd.NaT})
    assert a == b == c == d
    assert state_hash({"upper_limit": 0.0}) != a


def test_negative_zero_is_zero_and_floats_are_quantized():
    assert canonical(-0.0) == 0.0
    assert state_hash({"x": -0.0}) == state_hash({"x": 0.0})
    # last-bit noise below the 12-significant-digit rule does not move the hash
    assert state_hash({"x": 1.0 / 3.0}) == state_hash({"x": 1.0 / 3.0 + 1e-17})
    # a real difference does
    assert state_hash({"x": 1.0}) != state_hash({"x": 1.0000001})


def test_timestamps_serialize_as_utc_microseconds():
    t = pd.Timestamp("2026-09-06T04:00:00.123456+00:00")
    assert canonical(t) == "2026-09-06T04:00:00.123456+00:00"
    naive = pd.Timestamp("2026-09-06T04:00:00.123456")
    assert canonical(naive) == canonical(t), "a naive stamp is read as UTC, not local"


def test_canonical_json_is_key_sorted_and_compact():
    s = canonical_json({"b": 1, "a": 2})
    assert s == '{"a":2,"b":1}'


def test_book_levels_keep_source_order():
    lv = [(10.2, 5), (10.0, 9)]
    assert canonical(lv) == [[10.2, 5], [10.0, 9]], "levels must not be re-sorted"


def test_the_chain_depends_on_order():
    a, b = state_hash({"x": 1}), state_hash({"x": 2})
    assert chain(chain(None, a), b) != chain(chain(None, b), a)


def test_only_declared_fields_enter_the_hash():
    base = {f: None for f in STATE_FIELDS}
    h = state_hash(base)
    assert state_hash({**base, "not_a_state_field": 99}) != h, \
        "state_hash hashes what it is given; unify is what restricts it to STATE_FIELDS"


# ---------------------------------------------------------------------------- identity
def _index():
    return IdentityIndex({
        "listings": [
            {"key": "DSE:BRACBANK", "code": "BRACBANK", "exchange": "DSE",
             "resolved": True, "truth": "OBSERVED"},
            {"key": "CSE:BRACBANK", "code": "BRACBANK", "exchange": "CSE",
             "resolved": True, "truth": "OBSERVED"},
            {"key": "DSE:MUDDLE", "code": "MUDDLE", "exchange": "DSE",
             "resolved": False, "truth": "NOT_OBSERVABLE"},
        ],
        "companies": [{"key": "C1", "listings": ["DSE:BRACBANK", "CSE:BRACBANK"],
                       "truth": "INFERRED"}],
    }, spine_sha256="deadbeef")


def test_a_symbol_resolves_to_a_listing_on_the_named_exchange_only():
    ix = _index()
    assert ix.resolve("BRACBANK", "DSE")["listing_key"] == "DSE:BRACBANK"
    assert ix.resolve("BRACBANK", "CSE")["listing_key"] == "CSE:BRACBANK"
    # the same issuer, two books — the company links them, the listing key does not
    assert ix.resolve("BRACBANK", "DSE")["company_key"] == ix.resolve("BRACBANK", "CSE")["company_key"]


def test_an_unknown_symbol_is_not_observable_rather_than_guessed():
    r = _index().resolve("NOSUCHTHING", "DSE")
    assert r["listing_key"] is None and r["identity_truth"] == "NOT_OBSERVABLE"
    assert r["exchange"] is None, "an unknown symbol must not be placed on an exchange"
    assert "not in the spine" in r["identity_note"]


def test_a_spine_listing_that_is_unresolved_stays_unresolved_here():
    r = _index().resolve("MUDDLE", "DSE")
    assert r["listing_key"] is None and r["identity_truth"] == "NOT_OBSERVABLE"
    assert "unresolved" in r["identity_note"]


def test_the_symbol_is_normalised_the_way_stage_0_normalises_it():
    assert _index().resolve(" brac bank ", "DSE")["code"] == "BRACBANK"


# --------------------------------------------------------------------------- consensus
def test_two_sources_agreeing_produce_a_consensus_value():
    x = cross_source({"t_frame": T0, "symbol": "X", "ltp": 10.0, "w_ltp": 10.0},
                     "lankabd_depth")
    assert x["cons_ltp"] == 10.0
    assert "ltp" in x["xsrc_agreeing"] and x["xsrc_disagreeing"] == ""


def test_two_sources_disagreeing_leave_the_consensus_empty_and_are_counted():
    x = cross_source({"t_frame": T0, "symbol": "X", "day_volume": 0.0, "w_day_volume": 135167.0},
                     "lankabd_depth")
    assert x["cons_day_volume"] is None, "a disagreement must never be averaged away"
    assert "day_volume" in x["xsrc_disagreeing"]


def test_one_reporting_source_is_not_a_consensus():
    x = cross_source({"t_frame": T0, "symbol": "X", "ltp": 10.0}, "lankabd_depth")
    assert x["cons_ltp"] is None
    assert "ltp" not in x["xsrc_agreeing"] and "ltp" not in x["xsrc_disagreeing"]


def test_every_cross_source_field_gets_a_consensus_column():
    x = cross_source({"t_frame": T0, "symbol": "X"}, "lankabd_depth")
    for f in CROSS_SOURCE_FIELDS:
        assert f"cons_{f}" in x


# ------------------------------------------------------------------------ unify + gate
def _unified():
    books = _books([("lankabd_depth", "BRACBANK", t, [(10.0, 100 + t)], [(10.1, 100)], 1, 10 + t, 0.001)
                    for t in (0, 30, 60)])
    c = _circuit([0]).assign(symbol="BRACBANK")
    return unify(fuse(_empty_tables(books, c)), identity=_index())


def test_unify_keys_states_by_listing_and_hashes_them():
    u = _unified()
    assert len(u) == 3
    assert set(u["listing_key"]) == {"DSE:BRACBANK"}
    assert u["state_sha256"].nunique() == 3, "three different books must hash differently"
    assert u["state_seq"].tolist() == [0, 1, 2]
    assert run_hash(u) == u["state_chain_sha256"].iloc[-1]


def test_unify_is_order_independent():
    """The chain must be a function of the data, not of pandas' concatenation order."""
    books = _books([("lankabd_depth", "BRACBANK", t, [(10.0, 100 + t)], [(10.1, 100)], 1, 10 + t, 0.001)
                    for t in (0, 30, 60)])
    c = _circuit([0]).assign(symbol="BRACBANK")
    f = fuse(_empty_tables(books, c))
    a = unify(f, identity=_index())
    b = unify(f.iloc[::-1].reset_index(drop=True), identity=_index())
    assert run_hash(a) == run_hash(b)


def test_an_unresolved_symbol_still_produces_a_state_and_says_so():
    books = _books([("lankabd_depth", "NOSUCHTHING", 0, [(10.0, 100)], [(10.1, 100)], 1, 10, 0.001)])
    u = unify(fuse(_empty_tables(books, pd.DataFrame())), identity=_index())
    assert len(u) == 1, "a state that cannot be identified is reported, never dropped"
    assert u["listing_key"].isna().all() and u["identity_truth"].iloc[0] == "NOT_OBSERVABLE"


def test_unify_of_nothing_is_nothing():
    assert unify(pd.DataFrame()).empty


def test_the_gate_assertion_rejects_a_reinstated_future_reference():
    u = _unified()
    with pytest.raises(AssertionError, match="ref_from_future"):
        assert_no_future_reference(u.assign(ref_from_future=False))

    smuggled = u.copy()
    smuggled.loc[smuggled.index[0], "ref_status"] = "NOT_YET_OBSERVED"   # limit left in place
    with pytest.raises(AssertionError, match="without a preceding observation"):
        assert_no_future_reference(smuggled)

    backwards = u.copy()
    backwards.loc[backwards.index[0], "ref_age_s"] = -5.0
    with pytest.raises(AssertionError, match="negative ref_age_s"):
        assert_no_future_reference(backwards)


def test_a_clean_run_passes_the_assertion_and_summarises():
    u = _unified()
    assert_no_future_reference(u)
    s = summary(u)
    assert s["states"] == 3 and s["listings"] == 1
    assert s["run_sha256"] == run_hash(u)
    assert s["ref_status"] == {"OBSERVED": 3}
    assert "ref_from_future_present" not in s
