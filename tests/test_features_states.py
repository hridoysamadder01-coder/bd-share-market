"""Features, labels, state machine, and the no-lookahead proof for the feature layer."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from seeing.experiment.design import DESIGN
from seeing.features.micro import features, labels, largest_wall_removed_imbalances
from seeing.state_machine.machine import run_state_machine

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


def synthetic_frames(n=60, symbol="X", seed=1, plant_from=30):
    """A calm book, then (from plant_from) a planted bid-pressure build with ask thinning
    and constant spread — the composite must fire inside the planted window only."""
    rng = np.random.default_rng(seed)
    rows = []
    bid_depth = 1000.0
    ask_depth = 1000.0
    mid = 50.05
    for i in range(n):
        if i >= plant_from:
            bid_depth += 120 + rng.integers(0, 20)
            ask_depth = max(200.0, ask_depth - 90)
        else:
            bid_depth = 1000 + rng.integers(-30, 30)
            ask_depth = 1000 + rng.integers(-30, 30)
        bids = [(50.0, bid_depth * 0.4), (49.9, bid_depth * 0.3), (49.8, bid_depth * 0.3)]
        asks = [(50.1, ask_depth * 0.4), (50.2, ask_depth * 0.3), (50.3, ask_depth * 0.3)]
        rows.append({"symbol": symbol, "t_frame": T0 + timedelta(seconds=15 * i), "seq": i, "frame_no": i,
                     "bid_levels": bids, "ask_levels": asks, "best_bid": 50.0, "best_ask": 50.1, "mid": mid,
                     "spread_ticks": 1.0, "tick_size": 0.1, "ltp": 50.1,
                     "bid_qty1": bids[0][1], "ask_qty1": asks[0][1],
                     "bid_depth_top3": sum(q for _, q in bids), "ask_depth_top3": sum(q for _, q in asks),
                     "bid_depth_top5": sum(q for _, q in bids), "ask_depth_top5": sum(q for _, q in asks),
                     "bid_depth_all": sum(q for _, q in bids), "ask_depth_all": sum(q for _, q in asks),
                     "bid_weighted_depth": bid_depth, "ask_weighted_depth": ask_depth,
                     "largest_wall_side": "bid" if bid_depth > ask_depth else "ask",
                     "crossed": False, "locked": False, "empty": False, "one_sided": False,
                     "unchanged_run": 0, "dup_payload": False, "watch_age_s": 10.0,
                     "ev_bid_replenish": 1 if i >= plant_from else 0, "ev_bid_add_qty": 150.0 if i >= plant_from else 0.0,
                     "ev_bid_reduce_qty": 0.0, "ev_ask_replenish": 0, "ev_ask_add_qty": 0.0, "ev_ask_reduce_qty": 90.0 if i >= plant_from else 0.0,
                     "bid_at_upper_limit": False, "ask_at_lower_limit": False, "day_value_mn": 10.0,
                     "frame_dt_s": 15.0, "tape_rows": 0, "book_agree": np.nan})
    f = pd.DataFrame(rows)
    f["t_frame"] = pd.to_datetime(f["t_frame"], utc=True)
    tot = f["bid_depth_top5"] + f["ask_depth_top5"]
    f["imb_l1"] = (f["bid_qty1"] - f["ask_qty1"]) / (f["bid_qty1"] + f["ask_qty1"])
    f["imb_top3"] = (f["bid_depth_top3"] - f["ask_depth_top3"]) / tot
    f["imb_top5"] = f["imb_top3"]
    f["imb_all"] = f["imb_top3"]
    f["imb_weighted"] = (f["bid_weighted_depth"] - f["ask_weighted_depth"]) / (f["bid_weighted_depth"] + f["ask_weighted_depth"])
    return f


def test_composite_fires_only_in_planted_window():
    f = features(synthetic_frames())
    fired = f.index[f["composite"]].tolist()
    assert fired, "composite never fired on a planted pressure build"
    assert min(fired) >= 30 + DESIGN.W - 1          # needs a full window of pressure first
    assert not f.loc[:29, "composite"].any()
    assert f["composite_episode_start"].sum() >= 1
    assert not f["mirror_composite"].any()
    assert f["b_imb_top5"].iloc[40] and not f["b_imb_top5"].iloc[5]


def test_labels_are_forward_and_separate():
    f = labels(features(synthetic_frames()))
    fwd = [c for c in f.columns if c.startswith("fwd_")]
    assert fwd and all(c.startswith("fwd_") for c in fwd)
    assert f["fwd_valid_h4"].iloc[-4:].sum() == 0 and f["fwd_valid_h4"].iloc[0]


def test_no_lookahead_in_features():
    """Corrupt every frame after a cut; every feature at frames before the cut must be bit-identical."""
    base = synthetic_frames()
    fa = features(base)
    cut = 40
    corrupted = base.copy()
    for c in ("bid_depth_top5", "ask_depth_top5", "imb_top5", "imb_top3", "imb_l1", "imb_weighted", "mid",
              "ev_bid_replenish", "ev_bid_add_qty", "spread_ticks"):
        corrupted[c] = corrupted[c].astype(float)
        corrupted.loc[cut:, c] = corrupted.loc[cut:, c] * 3.7 + 1.0
    fb = features(corrupted)
    feat_cols = [c for c in fa.columns if c not in ("bid_levels", "ask_levels") and not c.startswith("fwd_")]
    for c in feat_cols:
        a, b = fa.loc[:cut - 1, c], fb.loc[:cut - 1, c]
        if a.dtype == object:
            assert a.astype(str).equals(b.astype(str)), f"lookahead in {c}"
        else:
            assert np.array_equal(a.fillna(-999).values, b.fillna(-999).values), f"lookahead in {c}"
    # positive control: a deliberately leaky feature must be caught by the same check
    leaky = fa["mid"].shift(-1)
    leaky_b = fb["mid"].shift(-1)
    assert not np.array_equal(leaky.loc[:cut - 1].fillna(-999).values, leaky_b.loc[:cut - 1].fillna(-999).values)


def test_state_machine_transitions():
    f = run_state_machine(features(synthetic_frames()))
    assert f["state"].iloc[0] == "BALANCED"
    assert (f["state"] == "BID_PRESSURE_CONFIRMED").any()
    tr = f.attrs["transitions"]
    assert len(tr) and set(tr["to"]).issubset(set(f.attrs["states"]))
    assert (f["state_age"] >= 0).all()


def test_largest_wall_removed_imbalance():
    f = synthetic_frames(n=3)
    f.at[0, "bid_levels"] = [(50.0, 10000.0), (49.9, 10.0)]
    f.at[0, "ask_levels"] = [(50.1, 10.0), (50.2, 10.0)]
    s = largest_wall_removed_imbalances(f)
    assert abs(s.iloc[0] - (10 - 20) / 30) < 1e-9
