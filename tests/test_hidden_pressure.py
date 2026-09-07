"""Tests for the hidden-queue-pressure inferrer.

The two rules the model is built on are asserted directly: a cliff must earn little
credibility, smooth accumulation must earn a lot, and no book shape may raise.
"""
import json
import os

import numpy as np
import pandas as pd
import pytest

from seeing.features.hidden_pressure import HiddenPressureInferrer

from .conftest import ROOT

SMOOTH = {"bids": [(100.0, 800), (99.9, 1400), (99.8, 2000), (99.7, 2600), (99.6, 3200)],
          "asks": [(100.1, 500), (100.2, 500), (100.3, 500), (100.4, 500), (100.5, 500)]}
CLIFF = {"bids": [(100.0, 200), (99.9, 200), (99.8, 9200), (99.7, 200), (99.6, 200)],
         "asks": [(100.1, 500), (100.2, 500), (100.3, 500), (100.4, 500), (100.5, 500)]}


def test_cliff_is_penalised_and_smooth_accumulation_is_rewarded():
    """Same visible volume, same spread — only the shape differs."""
    h = HiddenPressureInferrer()
    cliff, smooth = h.calculate(CLIFF), h.calculate(SMOOTH)
    assert cliff["book"]["visible_bid_volume"] == smooth["book"]["visible_bid_volume"] == 10000.0
    cb, sb = cliff["bid_side"], smooth["bid_side"]
    assert cb["cliff_score"] > 0.8 and sb["cliff_score"] < 0.2
    assert sb["smoothness"] > cb["smoothness"]
    assert sb["slope_per_tick"] > 0                       # volume grows away from the touch
    assert sb["credibility"] > 0.5 and cb["credibility"] < 0.05
    assert sb["hidden_volume"] > 10 * max(cb["hidden_volume"], 1.0)
    assert smooth["inferred_buy_queue"] > cliff["inferred_buy_queue"]


def test_hidden_volume_is_capped_and_never_negative():
    h = HiddenPressureInferrer(max_hidden_multiple=2.0)
    steep = {"bids": [(100.0 - 0.1 * i, 100 * 4 ** i) for i in range(5)],
             "asks": [(100.1, 500), (100.2, 500)]}
    r = h.calculate(steep)
    b = r["bid_side"]
    assert b["hidden_capped"] is True
    assert b["hidden_volume"] <= 2.0 * b["visible_volume"] + 1e-6
    assert r["inferred_buy_queue"] >= 0 and r["inferred_sell_queue"] >= 0


def test_falling_ladder_infers_little_hidden_depth():
    """Volume shrinking away from the touch is not accumulation."""
    h = HiddenPressureInferrer()
    falling = {"bids": [(100.0, 4000), (99.9, 2000), (99.8, 1000), (99.7, 500), (99.6, 250)],
               "asks": [(100.1, 500), (100.2, 500), (100.3, 500)]}
    b = h.calculate(falling)["bid_side"]
    assert b["slope_per_tick"] < 0
    assert b["accumulation"] < 0.5
    assert b["hidden_volume"] < b["visible_volume"]


@pytest.mark.parametrize("book", [
    {"bids": [], "asks": []},
    {"bids": [(10.0, 500)], "asks": []},
    {"bids": [(10.0, 500)], "asks": [(10.1, 400)]},
    {"bids": [(10.0, 500), (9.9, 900)], "asks": [(10.1, 400), (10.2, 300)]},
    {"bids": [(10.0, 0), (9.9, 0)], "asks": [(10.1, 0)]},
    {"bids": [(10.5, 100), (10.4, 200)], "asks": [(10.2, 100), (10.3, 200)]},   # crossed
    {"bids": [(10.0, 500), (9.9, None)], "asks": [(10.1, 400)]},
    {"bids": [(10.0, 500)] * 4, "asks": [(10.1, 500)] * 3},                     # duplicate prices
])
def test_every_book_shape_returns_finite_bounded_values(book):
    r = HiddenPressureInferrer().calculate(book)
    assert 0.0 <= r["breakout_probability"] <= 1.0
    assert r["inferred_buy_queue"] >= 0.0 and r["inferred_sell_queue"] >= 0.0
    assert np.isfinite([r["inferred_buy_queue"], r["inferred_sell_queue"],
                        r["breakout_probability"]]).all()
    assert r["breakout_direction"] in ("UP", "DOWN", "NEUTRAL")


def test_empty_side_is_not_treated_as_a_fragile_wall():
    """An absent side is missing evidence, not a flimsy barrier."""
    h = HiddenPressureInferrer()
    empty = h.calculate({"bids": [], "asks": []})
    assert empty["drivers"]["wall_fragility"] == 0.0
    assert empty["insufficient_book"] is True
    assert empty["breakout_probability"] < 0.2
    assert any("fewer than 2 levels" in p for p in empty["problems"])


def test_crossed_book_is_reported():
    r = HiddenPressureInferrer().calculate({"bids": [(10.5, 100), (10.4, 200)],
                                            "asks": [(10.2, 100), (10.3, 200)]})
    assert any("crossed or locked" in p for p in r["problems"])


def test_velocity_raises_the_score_and_needs_history():
    h = HiddenPressureInferrer()
    past = [{"bids": [(99.0, 800)], "asks": [(99.1, 500)]},
            {"bids": [(99.5, 800)], "asks": [(99.6, 500)]}]
    still = h.calculate(SMOOTH)
    moving = h.calculate(SMOOTH, history=past, minutes_elapsed=5.0)
    assert still["velocity_ticks_per_min"] == 0.0
    assert moving["velocity_ticks_per_min"] > 0
    assert moving["breakout_probability"] > still["breakout_probability"]


def test_deeper_smooth_accumulation_never_lowers_the_inferred_queue():
    h = HiddenPressureInferrer()
    seen = -1.0
    for k in range(2, 9):
        book = {"bids": [(100.0 - 0.1 * i, 500 * (i + 1)) for i in range(k)],
                "asks": [(100.1, 500), (100.2, 500)]}
        q = h.calculate(book)["inferred_buy_queue"]
        assert q >= seen - 1e-6
        seen = q


def test_accepts_the_three_book_shapes_this_repo_produces():
    h = HiddenPressureInferrer()
    pairs = h.calculate(SMOOTH)
    frame = h.calculate(pd.DataFrame({                      # bdshare / dsebd, NaN-padded
        "buy_price": [100.0, 99.9, 99.8, 99.7, 99.6],
        "buy_volume": [800, 1400, 2000, 2600, 3200],
        "sell_price": [100.1, 100.2, 100.3, 100.4, 100.5],
        "sell_volume": [500, 500, 500, 500, 500]}))
    terminal = h.calculate({"Depth": {"BuySellDetails": [                 # broker terminal
        {"BuyPrice": 100.0, "BuyVolume": 800, "SellPrice": 100.1, "SellVolume": 500},
        {"BuyPrice": 99.9, "BuyVolume": 1400, "SellPrice": 100.2, "SellVolume": 500},
        {"BuyPrice": 99.8, "BuyVolume": 2000, "SellPrice": 100.3, "SellVolume": 500},
        {"BuyPrice": 99.7, "BuyVolume": 2600, "SellPrice": 100.4, "SellVolume": 500},
        {"BuyPrice": 99.6, "BuyVolume": 3200, "SellPrice": 100.5, "SellVolume": 500}]}})
    for other in (frame, terminal):
        assert other["inferred_buy_queue"] == pytest.approx(pairs["inferred_buy_queue"])
        assert other["breakout_probability"] == pytest.approx(pairs["breakout_probability"])


def test_unrecognised_shape_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        HiddenPressureInferrer().calculate({"foo": [1, 2, 3]})
    with pytest.raises(ValueError):
        HiddenPressureInferrer().calculate(None)


def test_bad_constructor_arguments_are_rejected():
    for kw in ({"extrapolation_ticks": -1}, {"max_hidden_multiple": -0.5},
               {"spike_cap": 1.0}, {"slope_scale": 0.0}, {"velocity_scale": -1.0},
               {"default_tick": 0.0}, {"weights": {"nonsense": 1.0}}):
        with pytest.raises(ValueError):
            HiddenPressureInferrer(**kw)


def test_json_output_is_valid_and_keeps_flags_as_booleans():
    h = HiddenPressureInferrer()
    d = json.loads(h.to_json(SMOOTH))
    assert isinstance(d["fitted"], bool) and d["fitted"] is False
    assert isinstance(d["book"]["two_sided"], bool)
    assert isinstance(d["bid_side"]["hidden_capped"], bool)
    assert d["truth"]["orders_per_level"] == "NOT_OBSERVABLE"
    assert "unfitted" in d["truth"]["breakout_probability"]
    # a one-sided book puts NaN in the mid; JSON must carry null, never bare NaN
    one = h.to_json({"bids": [(10.0, 500), (9.9, 900)], "asks": []})
    assert "NaN" not in one and json.loads(one)["book"]["mid"] is None


def test_fitted_weights_flip_the_flag_and_change_the_score():
    base = HiddenPressureInferrer().calculate(SMOOTH)
    tuned = HiddenPressureInferrer(weights={"queue_imbalance": 4.0}).calculate(SMOOTH)
    assert base["fitted"] is False and tuned["fitted"] is True
    assert "fitted weights" in tuned["truth"]["breakout_probability"]
    assert tuned["breakout_probability"] != base["breakout_probability"]


def test_runs_on_the_committed_real_books():
    """The real ladders in evidence/ must parse and score without a problem."""
    p = os.path.join(ROOT, "evidence", "bdshare", "order_book_2026-09-08T0247Z.json")
    if not os.path.exists(p):
        pytest.skip("bdshare evidence not present")
    h = HiddenPressureInferrer()
    d = json.load(open(p))
    scored = 0
    for sym, v in d["symbols"].items():
        rows = v.get("data") or []
        if not rows:
            continue
        r = h.calculate(pd.DataFrame(rows))
        assert 0.0 <= r["breakout_probability"] <= 1.0, sym
        scored += 1
    assert scored >= 5

    q = os.path.join(ROOT, "evidence", "ecosoft_ost",
                     "rich_sensor_probe_2026-09-07T1616Z_AAMRATECH.ndjson")
    if os.path.exists(q):
        with open(q) as fh:
            payload = json.loads(fh.readline())["payload"]
        r = h.calculate(payload)
        assert r["book"]["n_bid_levels"] == 10 and r["book"]["n_ask_levels"] == 8
        assert r["book"]["tick_size"] == pytest.approx(0.1)
        # 51,400 sitting at one price among levels of 100–6,200 is a wall, not a ladder
        assert r["bid_side"]["spike_ratio"] > 50
        assert r["bid_side"]["cliff_score"] > 0.7
        assert r["bid_side"]["credibility"] < 0.05
