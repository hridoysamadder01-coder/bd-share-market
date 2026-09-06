"""The experiment harness must (1) KEEP a planted, genuine effect, (2) KILL a
null, and (3) produce every pre-registered falsification row either way."""
import numpy as np
import pandas as pd

from seeing.experiment.design import DESIGN, Design
from seeing.experiment.falsify import run_falsifications
from seeing.experiment.run_experiment import assign_splits, denominator, evaluate, incremental
from seeing.features.micro import features, labels
from tests.test_features_states import synthetic_frames

SMALL = Design(n_boot=60, n_perm=60, n_min_episodes=3, n_min_frames=200, block_len=10)


def _universe(n_frames: int, seed: int, effect: bool):
    """Several symbols; repeated planted pressure builds; when ``effect`` the mid
    rises after each build (a real response), otherwise the mid path is noise."""
    rng = np.random.default_rng(seed)
    parts = []
    for k, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        f = synthetic_frames(n=n_frames, symbol=sym, seed=seed + k, plant_from=10_000)   # calm baseline
        mid = np.full(n_frames, 50.05)
        bid = np.full(n_frames, 1000.0)
        ask = np.full(n_frames, 1000.0)
        rep = np.zeros(n_frames)
        i = 20
        while i + 24 < n_frames:
            # 10 frames of build, then 6 frames of response
            for j in range(10):
                bid[i + j] = 1000 + 140 * (j + 1)
                ask[i + j] = max(200, 1000 - 90 * (j + 1))
                rep[i + j] = 1
            if effect:
                mid[i + 10:i + 16] += 0.1 * np.arange(1, 7)
                mid[i + 16:] = mid[i + 15]
            i += 40 + int(rng.integers(0, 10))
        if not effect:
            mid = 50.05 + 0.1 * np.cumsum(rng.choice([-1, 0, 1], size=n_frames, p=[0.2, 0.6, 0.2]))
        f["mid"] = mid
        f["bid_depth_top5"] = bid; f["bid_depth_top3"] = bid; f["bid_depth_all"] = bid; f["bid_qty1"] = bid * 0.4
        f["ask_depth_top5"] = ask; f["ask_depth_top3"] = ask; f["ask_depth_all"] = ask; f["ask_qty1"] = ask * 0.4
        f["bid_weighted_depth"] = bid; f["ask_weighted_depth"] = ask
        tot = bid + ask
        f["imb_top5"] = (bid - ask) / tot; f["imb_top3"] = f["imb_top5"]; f["imb_all"] = f["imb_top5"]
        f["imb_l1"] = f["imb_top5"]; f["imb_weighted"] = f["imb_top5"]
        f["largest_wall_side"] = np.where(bid > ask, "bid", "ask")
        f["ev_bid_replenish"] = rep; f["ev_bid_add_qty"] = rep * 150; f["ev_ask_reduce_qty"] = rep * 90
        f["bid_levels"] = [[(50.0, b * 0.4), (49.9, b * 0.3), (49.8, b * 0.3)] for b in bid]
        f["ask_levels"] = [[(50.1, a * 0.4), (50.2, a * 0.3), (50.3, a * 0.3)] for a in ask]
        parts.append(f)
    raw = pd.concat(parts, ignore_index=True)
    return raw


def _run(raw, d):
    f = assign_splits(labels(features(raw, d), d), d)
    return f, run_falsifications(f, raw, d)


def test_harness_keeps_planted_effect_and_reports_all_tests():
    raw = _universe(600, seed=3, effect=True)
    f, r = _run(raw, SMALL)
    tests = set(r["table"]["test"])
    for t in ("real", "baseline_comparison", "baseline", "graded_score", "timestamp_permutation", "side_flip",
              "anchor_shift", "removal", "leave_one_symbol_out", "liquidity_split"):
        assert t in tests, t
    assert set(r["table"][r["table"].test == "removal"]["variant"]) == {"stale_removed", "duplicate_removed",
                                                                          "crossed_locked_removed", "largest_wall_removed"}
    den = denominator(f, SMALL)
    assert den["composite_episodes_holdout"] >= SMALL.n_min_episodes
    v = r["verdict"]
    assert v["verdict"] in ("KEEP", "KILL")
    real = r["table"][r["table"].test == "real"].iloc[0]
    assert real["lift_vs_base"] > 0.2, real
    assert v["checks"]["a_beats_best_baseline_ci"] is True
    assert v["checks"]["b_permutation"] is True
    ev = evaluate(f, SMALL)
    assert (ev["n_frames"] > 0).all() and "composite" in set(ev["signal"])


def test_harness_kills_or_blocks_null():
    raw = _universe(600, seed=11, effect=False)
    f, r = _run(raw, SMALL)
    assert r["verdict"]["verdict"] in ("KILL", "BLOCKED")


def test_blocked_when_denominator_too_small():
    raw = _universe(120, seed=5, effect=True)
    f, r = _run(raw, Design(n_boot=20, n_perm=20, n_min_episodes=50, n_min_frames=200))
    assert r["verdict"]["verdict"] == "BLOCKED"
    assert "episodes" in r["verdict"]["reasons"][0] or "frames" in r["verdict"]["reasons"][0]


def test_incremental_table_shape():
    raw = _universe(300, seed=2, effect=True)
    f = assign_splits(labels(features(raw, SMALL), SMALL), SMALL)
    inc = incremental(f, SMALL, "holdout", 4)
    assert set(inc["baseline"]) == {"b_imb_l1", "b_imb_top5", "b_imb_weighted", "b_largest_wall_bid", "b_one_frame_pressure"}
    assert {"incremental_lift", "within_baseline_gain", "n_both", "n_only_baseline"} <= set(inc.columns)
