"""tower.experiment — machinery tests on a synthetic state store (a planted mechanism that precedes
up-moves, a null mechanism that fires at random) and a real-data test on the closed-market fixture.
"""
import filecmp
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tower.experiment import (BASELINES, EXCLUSION_REASONS, ExperimentConfig, add_exclusions, add_forward_outcomes,
                              assign_splits, bh_adjusted, bh_fdr, denominator, load_store, mechanism_signals,
                              run_experiment, time_shift_signal)
from tower.state import MarketState, MechanismState
from tower.store import StateStore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")

SYMBOLS = {"AAA": 5_000_000.0, "BBB": 3_000_000.0, "CCC": 800_000.0, "DDD": 400_000.0}   # symbol → day_volume
TICK = 0.1
DT = 10          # seconds between states
H = 180          # primary horizon (s) = 18 states
N = 1400         # states per symbol
CFG = ExperimentConfig(horizons=(60, 180, 600), primary_h=H, n_boot=200, n_perm=150, n_min_rows=500,
                       n_min_episodes=30, seed=11)


def _mech(name, family, score, state, direction):
    return MechanismState(name=name, family=family, score=score, state=state,
                          evidence={"direction": direction, "peak_score": score})


def build_synthetic_store(root: str, seed: int = 3) -> dict:
    """Four symbols × N states, 10 s apart, CONTINUOUS. ``planted`` fires (active, direction ±1, 3 states)
    right before an 18-state drift in its direction (65 % up episodes); ``null_mech`` fires at random.
    A handful of rows are deliberately excluded: PRE_OPEN at the start, an empty book, a crossed book,
    a circuit lock, and an exact duplicate of the previous row."""
    rng = np.random.default_rng(seed)
    store = StateStore(root)
    t0 = datetime(2026, 9, 6, 4, 0, tzinfo=timezone.utc)
    planted_truth = {}
    for si, (sym, dvol) in enumerate(SYMBOLS.items()):
        mid_ticks = 1000 + 50 * si
        drift = np.zeros(N, dtype=int)
        planted = np.zeros(N, dtype=int)     # 0 or ±1 while the signal is active
        null = np.zeros(N, dtype=int)
        e = 10
        while e + 22 < N:
            d = 1 if rng.random() < 0.65 else -1
            planted[e:e + 3] = d
            drift[e + 1:e + 1 + 18] = d
            e += 20
        e = 20
        while e + 5 < N:
            null[e:e + 3] = 1 if rng.random() < 0.5 else -1
            e += 20
        planted_truth[sym] = int((planted != 0).sum())
        mids = []
        prev_key = None
        for i in range(N):
            step = int(rng.choice([-1, 0, 1], p=[0.3, 0.4, 0.3]))
            if drift[i] != 0 and rng.random() < 0.7:
                step = drift[i]
            mid_ticks += step
            mids.append(mid_ticks)
            mid = round(mid_ticks * TICK, 2)
            bid, ask = round(mid - TICK / 2, 3), round(mid + TICK / 2, 3)
            bq = [float(rng.integers(100, 1000)) for _ in range(5)]
            aq = [float(rng.integers(100, 1000)) for _ in range(5)]
            bids = [(round(bid - k * TICK, 2), bq[k]) for k in range(5)]
            asks = [(round(ask + k * TICK, 2), aq[k]) for k in range(5)]
            sb, sa = sum(bq), sum(aq)
            wb = sum(q / (1 + k) for k, q in enumerate(bq))
            wa = sum(q / (1 + k) for k, q in enumerate(aq))
            por = float(mid_ticks - mids[max(0, i - 6)])
            ms = MarketState(symbol=sym, t=t0 + timedelta(seconds=DT * i), seq=i + 1, session_phase="CONTINUOUS",
                             best_bid=bid, best_ask=ask, bid_qty1=bq[0], ask_qty1=aq[0], spread=TICK, spread_ticks=1.0,
                             mid=mid, ltp=mid, tick_size=TICK, bids=bids, asks=asks, empty_book=False,
                             imb_l1=(bq[0] - aq[0]) / (bq[0] + aq[0]), imb_topk=(sb - sa) / (sb + sa),
                             imb_weighted=(wb - wa) / (wb + wa), visible_bid_liq=sb, visible_ask_liq=sa,
                             depth_ratio=sb / (sb + sa), price_only_response=por,
                             volume_only_response=float(rng.integers(0, 5000)), trade_count=float(i), trade_volume=float(100 * i),
                             session_state={"quote": {"day_volume": dvol}},
                             circuit={"tick": TICK, "locked_up": False, "locked_down": False})
            # deliberate exclusions
            if i < 3:
                ms.session_phase = "PRE_OPEN"
            if i == 100:
                ms.empty_book, ms.bids, ms.asks, ms.mid, ms.best_bid, ms.best_ask = True, [], [], None, None, None
            if i == 200:
                ms.crossed = True
            if i == 300:
                ms.circuit["locked_up"] = True
            if i == 400:
                ms.trade_count, ms.trade_volume, ms.bids, ms.asks, ms.ltp = prev_key   # identical content to row 399
            prev_key = (ms.trade_count, ms.trade_volume, list(ms.bids), list(ms.asks), ms.ltp)
            p_score = 0.9 if planted[i] else 0.05
            n_score = 0.8 if null[i] else 0.1
            ms.mechanisms = {
                "planted": _mech("planted", "test", p_score, "active" if planted[i] else "inactive", int(planted[i])),
                "null_mech": _mech("null_mech", "test", n_score, "active" if null[i] else "inactive", int(null[i])),
            }
            store.append(ms)
    store.close()
    return planted_truth


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("store"))
    truth = build_synthetic_store(root)
    return root, truth


@pytest.fixture(scope="module")
def experiment(synthetic, tmp_path_factory):
    root, _ = synthetic
    out = str(tmp_path_factory.mktemp("out"))
    res = run_experiment(root, out, CFG)
    return root, out, res


# ---------------------------------------------------------------------------- loading / outcomes
def test_machinery_load_flattens_mechanisms_and_circuit(synthetic):
    root, truth = synthetic
    df = load_store(root)
    assert len(df) == N * len(SYMBOLS)
    for c in ("mech_planted_score", "mech_planted_state", "mech_planted_direction", "mech_null_mech_state",
              "circuit_locked_up", "circuit_tick", "imb_l1", "imb_topk", "imb_weighted", "depth_ratio",
              "price_only_response", "volume_only_response", "day_volume"):
        assert c in df.columns, c
    assert df.attrs["mech_families"] == {"planted": "test", "null_mech": "test"}
    active = df[df["mech_planted_state"] == "active"].groupby("symbol").size().to_dict()
    assert active == truth
    assert df["t"].dt.tz is not None


def test_machinery_forward_outcomes_use_last_state_at_or_before_t_plus_h(synthetic):
    root, _ = synthetic
    df = add_forward_outcomes(load_store(root), (60, 180, 600))
    g = df[df["symbol"] == "AAA"].reset_index(drop=True)
    # exact horizon in states: 18 rows for 180 s
    i = 50
    expect = (g.loc[i + 18, "mid"] - g.loc[i, "mid"]) / TICK
    assert g.loc[i, "fwd_valid_h180"]
    assert g.loc[i, "fwd_mid_ticks_h180"] == pytest.approx(expect, abs=1e-6)
    # the last 18 rows have no complete 180 s window → None, never 0
    tail = g.iloc[-18:]
    assert not tail["fwd_valid_h180"].any()
    assert tail["fwd_mid_ticks_h180"].isna().all()
    # the empty-book row (100) has no outcome; the row before it skips over it to the next observed mid
    assert not g.loc[100, "fwd_valid_h180"]
    assert g.loc[82, "fwd_valid_h180"]
    assert g["fwd_valid_h60"].sum() > g["fwd_valid_h600"].sum()


# ---------------------------------------------------------------------------- denominator
def test_machinery_denominator_sums_and_reasons(experiment):
    _, out, res = experiment
    den = json.load(open(os.path.join(out, "DENOMINATOR.json")))
    total = den["total_rows"]
    assert total == N * len(SYMBOLS)
    assert set(den["per_reason_primary"]) == set(EXCLUSION_REASONS)
    assert set(den["per_reason_any"]) == set(EXCLUSION_REASONS)
    assert den["eligible"] + sum(den["per_reason_primary"].values()) == total
    assert den["sum_check"] is True
    for r in EXCLUSION_REASONS:
        assert den["per_reason_any"][r] >= den["per_reason_primary"][r]
    ns = len(SYMBOLS)
    assert den["per_reason_primary"]["outside_continuous_session"] == 3 * ns
    assert den["per_reason_primary"]["no_book"] == ns
    assert den["per_reason_primary"]["crossed_locked"] == ns
    assert den["per_reason_primary"]["circuit_locked"] == ns
    assert den["per_reason_primary"]["duplicate"] == ns
    assert den["per_reason_primary"]["no_forward_outcome"] == 18 * ns
    assert den["eligible"] == total - (3 + 1 + 1 + 1 + 1 + 18) * ns
    df = res["table"]
    row = df[(df["symbol"] == "AAA") & (df["seq"] == 401)].iloc[0]
    assert row["primary_exclusion"] == "duplicate" and "duplicate" in row["exclusion_reasons"]


# ---------------------------------------------------------------------------- signals / verdicts
def test_machinery_signal_orientation(synthetic):
    root, _ = synthetic
    df = load_store(root)
    s = mechanism_signals(df, "planted", CFG)
    assert s["primary_direction"] == 1 and s["outcome"] == "up" and s["mirror_outcome"] == "down"
    assert s["state"].sum() == s["n_dir_pos"] and s["mirror"].sum() == s["n_dir_neg"]
    assert s["n_dir_pos"] > s["n_dir_neg"] > 0
    assert (s["score"] == s["state"]).all()          # score 0.9 ≥ 0.6 exactly on the active rows


def test_machinery_planted_mechanism_kept_null_killed(experiment):
    _, out, res = experiment
    v = res["verdicts"]["mechanisms"]
    assert set(v) == {"planted", "null_mech"}
    p, n = v["planted"], v["null_mech"]
    assert p["holdout_episodes"] >= 30 and n["holdout_episodes"] >= 30
    assert p["verdict"] == "KEEP", p["reasons"]
    assert all(p["checks"][k] is True for k in p["checks"]), p["checks"]
    assert p["lift_vs_base"] > 0.3 and p["bootstrap"]["ci_lo"] > 0
    assert p["permutation"]["p_value"] < 0.05 and p["fdr_pass"] is True
    assert n["verdict"] in ("KILL", "BLOCKED"), n
    assert n["checks"]["a_beats_best_baseline_ci"] is False or n["checks"]["b_permutation"] is False
    assert res["verdicts"]["counts"] == {"KEEP": 1, "KILL": 1, "BLOCKED": 0}
    # results table: three variants on three splits at three horizons for the planted (directional) mechanism
    r = pd.read_csv(os.path.join(out, "MECHANISM_RESULTS.csv"))
    pr = r[r["mechanism"] == "planted"]
    assert set(pr["variant"]) == {"state", "score_ge_0.6", "mirror"}
    assert set(pr["split"]) == {"dev", "val", "holdout"} and set(pr["h"]) == {60, 180, 600}
    hold = pr[(pr["split"] == "holdout") & (pr["h"] == H) & (pr["variant"] == "state")].iloc[0]
    assert hold["outcome"] == "up" and hold["lift_vs_matched"] > 0.3 and hold["n_matched"] > 0
    for b in BASELINES:
        assert hold[f"inc_vs_{b}"] > 0
    mirror = pr[(pr["split"] == "holdout") & (pr["h"] == H) & (pr["variant"] == "mirror")].iloc[0]
    assert mirror["outcome"] == "down" and mirror["lift_vs_base"] > 0.3


def test_machinery_every_falsification_row_exists(experiment):
    _, out, _ = experiment
    f = pd.read_csv(os.path.join(out, "FALSIFICATION.csv"))
    verdicts = json.load(open(os.path.join(out, "VERDICTS.json")))["mechanisms"]
    for m in ("planted", "null_mech"):
        fm = f[f["mechanism"] == m]
        tests = fm.groupby("test")["variant"].apply(set).to_dict()
        assert "holdout" in tests["real"]
        assert len(tests["baseline_comparison"]) == 1
        assert tests["baseline"] == set(BASELINES)
        assert tests["graded_score"] == {"score_ge_0.6"}
        assert tests["timestamp_permutation"] == {"circular shift within symbol"}
        opposite = {"up": "down", "down": "up"}[verdicts[m]["outcome"]]
        assert tests["side_flip"] == {f"mirror → P({opposite})"}
        assert tests["anchor_shift"] == {"placebo_shift_-h", "leak_control_shift_+h"}
        assert tests["removal"] == {"largest_wall_removed"}
        assert tests["leave_one_symbol_out"] == {f"without {s}" for s in SYMBOLS}
        assert tests["liquidity_split"] == {"top", "mid"}
        assert (fm["h"] == H).all() and (fm["split"] == "holdout").all()
    fp = f[f["mechanism"] == "planted"].set_index(["test", "variant"])
    real = fp.loc[("real", "holdout"), "lift_vs_base"]
    placebo = fp.loc[("anchor_shift", "placebo_shift_-h"), "lift_vs_base"]
    assert abs(placebo) < 0.5 * abs(real)
    assert fp.loc[("side_flip", "mirror → P(down)"), "lift_vs_base"] > 0
    assert fp.loc[("liquidity_split", "top"), "note"].startswith("symbols: AAA, BBB")
    assert fp.loc[("liquidity_split", "mid"), "note"].startswith("symbols: CCC, DDD")
    assert "BH-FDR" in fp.loc[("timestamp_permutation", "circular shift within symbol"), "note"]


def test_machinery_time_shift_signal_is_time_based(synthetic):
    root, _ = synthetic
    df = load_store(root)
    sig = df["mech_planted_state"].isin(("active", "confirmed"))
    back = time_shift_signal(df, sig, -H)
    fwd = time_shift_signal(df, sig, H)
    g = df["symbol"] == "AAA"
    s, b, fw = sig[g].values, back[g].values, fwd[g].values
    assert np.array_equal(b[18:], s[:-18]) and not b[:18].any()
    assert np.array_equal(fw[:-18], s[18:])


# ---------------------------------------------------------------------------- FDR
def test_machinery_fdr_monotone_and_prefix():
    p = np.array([0.001, 0.2, 0.01, np.nan, 0.03, 0.5, 0.0005])
    keep, cutoff = bh_fdr(p, 0.05)
    q = bh_adjusted(p)
    finite = np.isfinite(p)
    order = np.argsort(p[finite])
    qs = q[finite][order]
    assert np.all(np.diff(qs) >= -1e-12)                       # adjusted q non-decreasing in p
    assert np.isnan(q[3]) and not keep[3]
    ks = keep[finite][order]
    assert np.array_equal(ks, np.r_[np.ones(ks.sum(), bool), np.zeros(len(ks) - ks.sum(), bool)])   # a prefix
    assert keep[6] and keep[0] and keep[2] and not keep[5]
    assert np.isfinite(cutoff) and cutoff >= p[finite][order][ks.sum() - 1]
    assert np.all(q[finite][keep[finite]] <= 0.05 + 1e-12)
    # every NaN → nothing kept, NaN cutoff
    k2, c2 = bh_fdr(np.array([np.nan, np.nan]), 0.1)
    assert not k2.any() and np.isnan(c2)


# ---------------------------------------------------------------------------- determinism
def test_machinery_determinism_same_store_identical_bytes(synthetic, tmp_path):
    root, _ = synthetic
    cfg = ExperimentConfig(horizons=(60, 180), primary_h=H, n_boot=60, n_perm=40, n_min_rows=500, seed=5)
    o1, o2 = str(tmp_path / "a"), str(tmp_path / "b")
    run_experiment(root, o1, cfg)
    run_experiment(root, o2, cfg)
    for name in ("MECHANISM_RESULTS.csv", "FALSIFICATION.csv", "DENOMINATOR.json", "VERDICTS.json"):
        assert filecmp.cmp(os.path.join(o1, name), os.path.join(o2, name), shallow=False), name
    m1, m2 = json.load(open(os.path.join(o1, "MANIFEST.json"))), json.load(open(os.path.join(o2, "MANIFEST.json")))
    assert m1["outputs"] == m2["outputs"] and m1["inputs"] == m2["inputs"]


def test_machinery_cli(synthetic, tmp_path):
    import subprocess, sys
    root, _ = synthetic
    out = str(tmp_path / "cli")
    r = subprocess.run([sys.executable, "-m", "tower.experiment", "--store", root, "--out", out, "--horizon", "60",
                        "--n-boot", "30", "--n-perm", "20"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    assert summary["rows"] == N * len(SYMBOLS)
    assert os.path.exists(os.path.join(out, "MANIFEST.json"))
    den = json.load(open(os.path.join(out, "DENOMINATOR.json")))
    assert den["horizon_s"] == 60 and den["per_reason_primary"]["no_forward_outcome"] == 6 * len(SYMBOLS)


# ---------------------------------------------------------------------------- real data
def test_realdata_closed_fixture_all_rows_excluded_and_blocked(tmp_path):
    from tower.replay import Replayer
    store = str(tmp_path / "store")
    rp = Replayer(FIXTURE, store)
    rp.load()
    rp.run()
    out = str(tmp_path / "exp")
    res = run_experiment(store, out, ExperimentConfig(n_boot=20, n_perm=20))
    den = res["denominator"]
    assert den["total_rows"] > 0
    assert den["per_reason_any"]["no_book"] == den["total_rows"]      # closed market: every book is empty
    assert den["eligible"] == 0 and den["sum_check"] is True
    v = res["verdicts"]["mechanisms"]
    assert len(v) == 49
    assert all(x["verdict"] == "BLOCKED" for x in v.values())
    assert res["verdicts"]["fdr"]["n_tested"] == 0
    f = pd.read_csv(os.path.join(out, "FALSIFICATION.csv"))
    assert set(f["mechanism"]) == set(v) and (f["n_rows"] == 0).all()
    man = json.load(open(os.path.join(out, "MANIFEST.json")))
    assert set(man["outputs"]) == {"MECHANISM_RESULTS.csv", "FALSIFICATION.csv", "DENOMINATOR.json", "VERDICTS.json"}
    assert man["inputs"]
