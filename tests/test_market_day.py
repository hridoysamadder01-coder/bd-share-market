"""The whole-market intraday runner: what it polls, what it costs, where it refuses to write.

This is the deliberate alternative to widening the pre-registered 14-symbol
capture, which is frozen. Two things therefore matter more than anything else
here: that it can never write inside `micro/`, and that it reports honestly what
width costs in resolution — a market-wide book frame is minutes, not seconds, and
a reader who mistakes one for the other draws conclusions the data cannot carry.
"""
import json
import os

import pytest

from seeing.capture.market_day import (DEFAULT_INTRADAY_SOURCES, assert_safe_out,
                                       plan, resolve_universe, session_window,
                                       spine_universe, traded_universe)

SYMS = [f"SYM{i:03d}" for i in range(384)]


# ------------------------------------------------------------------ the guard
@pytest.mark.parametrize("bad", ["micro", "micro/sessions", "micro/raw/2026-09-09",
                                 "./micro/anything", "/anywhere/at/all/micro/x",
                                 "evidence/../micro/sneaky"])
def test_it_refuses_to_write_inside_the_pre_registered_experiment(bad):
    with pytest.raises(ValueError, match="pre-registered"):
        assert_safe_out(bad)


def test_the_guard_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    """A guard whose answer changes with where the process started is not a guard."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        assert_safe_out("/somewhere/micro/sessions")
    assert assert_safe_out("evidence/market_day/2026-09-09")


@pytest.mark.parametrize("ok", ["evidence/market_day/2026-09-09", "evidence/x",
                                "micrometer/data", "evidence/micro_notes"])
def test_it_allows_paths_that_merely_look_similar(ok):
    """`micrometer/` and `evidence/micro_notes` are not the experiment."""
    assert assert_safe_out(ok) == ok


# ------------------------------------------------------------------ the universe
def test_the_universe_is_every_symbol_that_traded_not_the_liquid_ones(tmp_path):
    """Capturing more is never a selection bias; capturing only the liquid names
    and calling it whole-market is."""
    p = tmp_path / "latest.csv"
    p.write_text("symbol,day_volume\nAAA,1000000\nBBB,1\nCCC,0\nDDD,\n")
    assert traded_universe(str(p)) == ["AAA", "BBB"], "one share traded is traded"


def test_a_symbol_that_did_not_trade_is_left_out_but_can_return_tomorrow(tmp_path):
    p = tmp_path / "latest.csv"
    p.write_text("symbol,day_volume\nTB10Y0127,0\nACI,5\n")
    assert traded_universe(str(p)) == ["ACI"]
    p.write_text("symbol,day_volume\nTB10Y0127,50\nACI,5\n")
    assert traded_universe(str(p)) == ["ACI", "TB10Y0127"], "no threshold, no ranking"


def test_the_spine_is_the_fallback_when_no_snapshot_exists(tmp_path):
    m = tmp_path / "map.json"
    m.write_text(json.dumps({"core": {"listings": [
        {"code": "ACI", "exchange": "DSE"}, {"code": "GP", "exchange": "DSE"},
        {"code": "ACI", "exchange": "CSE"}]}}))
    assert spine_universe(str(m)) == ["ACI", "GP"]
    r = resolve_universe(str(tmp_path / "missing.csv"), str(m))
    assert r["symbols"] == ["ACI", "GP"]
    assert "identity spine" in r["rule"], "and the fallback says so on the record"


def test_the_universe_records_which_rule_chose_it(tmp_path):
    p = tmp_path / "latest.csv"
    p.write_text("symbol,day_volume\nACI,5\n")
    r = resolve_universe(str(p), None)
    assert r["symbols"] == ["ACI"] and "traded" in r["rule"] and r["source"] == str(p)


def test_no_universe_at_all_is_an_error_not_an_empty_sweep(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_universe(str(tmp_path / "a.csv"), str(tmp_path / "b.json"))


# ------------------------------------------------------------------ the cost
def test_width_is_priced_in_book_frame_seconds():
    p = plan(SYMS, min_gap=0.4, only=DEFAULT_INTRADAY_SOURCES)
    assert p["oversubscribed_x"] > 5, "384 symbols on a 20 s cadence cannot be met"
    assert 150 < p["book_frame_s"] < 200, "a market-wide book frame is minutes"
    assert p["book_frame_min"] == pytest.approx(p["book_frame_s"] / 60, abs=0.01)


def test_the_budget_goes_where_the_demand_is():
    p = plan(SYMS, min_gap=0.4, only=DEFAULT_INTRADAY_SOURCES)
    by = {r["source"]: r for r in p["sources"]}
    assert by["lankabd_depth"]["budget_share"] > 0.8, "the book is what this run is for"
    assert by["lankabd_market"]["budget_share"] < 0.01, "a one-request feed costs nothing"
    assert sum(r["budget_share"] for r in p["sources"]) == pytest.approx(1.0, abs=1e-3)


def test_a_slow_source_does_not_cost_a_fast_one_a_whole_pass():
    """The bug in the first version of `plan`: it counted every per-symbol source
    as cycling each round and reported 12.8 min where the book achieves ~4.6."""
    everything = plan(SYMS, min_gap=0.4)
    assert everything["book_frame_s"] < 500, \
        "the 30-day ownership cadence must not be charged as a full pass"


def test_dropping_a_source_buys_resolution_back():
    wide = plan(SYMS, min_gap=0.4, only=DEFAULT_INTRADAY_SOURCES)
    lean = plan(SYMS, min_gap=0.4, only=("lankabd_depth", "lankabd_watch"))
    assert lean["book_frame_s"] < wide["book_frame_s"], \
        "fewer per-symbol sources means a faster book sweep — that is the trade"


def test_fewer_symbols_buy_resolution_back_too():
    wide = plan(SYMS, min_gap=0.4, only=DEFAULT_INTRADAY_SOURCES)
    narrow = plan(SYMS[:100], min_gap=0.4, only=DEFAULT_INTRADAY_SOURCES)
    assert narrow["book_frame_s"] < wide["book_frame_s"] / 3


def test_an_undersubscribed_plan_runs_at_its_declared_cadence():
    p = plan(["ACI"], min_gap=0.4, only=("lankabd_depth",))
    assert p["oversubscribed_x"] < 1
    assert p["book_frame_s"] == pytest.approx(20.0), "one symbol, the 20 s cadence holds"


def test_the_second_depth_source_is_excluded_on_purpose():
    """Two levels for 37 % of the budget, where LankaBD gives five."""
    assert "dsebd_depth" not in DEFAULT_INTRADAY_SOURCES
    assert "lankabd_depth" in DEFAULT_INTRADAY_SOURCES


def test_the_intraday_set_excludes_what_does_not_move_intraday():
    for slow in ("bullbd_detail", "dse_ownership", "bsec_publications", "cdbl_stats"):
        assert slow not in DEFAULT_INTRADAY_SOURCES


def test_every_default_source_is_real_and_runs_while_the_market_is_open():
    from seeing.capture.engine import build_registry
    from seeing.capture.http_client import PoliteClient
    by = {s.name: s for s in build_registry(PoliteClient(), ["ACI"])}
    for name in DEFAULT_INTRADAY_SOURCES:
        assert name in by, name
        assert not by[name].blocked and by[name].enabled, name
        assert by[name].runs_in("CONTINUOUS"), name


# ------------------------------------------------------------------ the window
def test_the_window_covers_the_session_and_a_margin_past_the_close():
    w = session_window("2026-09-09")
    assert w["trading_date"] == "2026-09-09"
    span_min = (w["end_utc"] - w["start_utc"]).total_seconds() / 60
    assert 260 < span_min < 300, "09:45 through 14:15 Dhaka, about 4.5 hours"
    assert w["start_utc"] < w["end_utc"]
