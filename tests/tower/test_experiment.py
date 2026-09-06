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


# ---------------------------------------------------------------------------- adversarial: causality / contracts / edges
def _tiny_table(n=6, vols=(1., 2., 3., 4., 5., 6.)):
    return pd.DataFrame({"symbol": ["A"] * n, "volume_only_response": list(vols), "eligible": [True] * n})


def test_machinery_volume_baseline_threshold_is_causal():
    from tower.experiment import baseline_signal
    df = _tiny_table()
    a = baseline_signal(df, "volume_only_response", "move", CFG, scope=df["eligible"])
    df2 = df.copy()
    df2.loc[5, "volume_only_response"] = 0.0          # a FUTURE row changes: the past must not
    b = baseline_signal(df2, "volume_only_response", "move", CFG, scope=df2["eligible"])
    assert np.array_equal(a.values[:5], b.values[:5])
    # running median: 1 ≥ 1, 2 ≥ 1.5, 3 ≥ 2, 4 ≥ 2.5, 5 ≥ 3, 6 ≥ 3.5 → all True; a drop below the running median → False
    assert a.all()
    df3 = _tiny_table(vols=(5., 5., 5., 5., 1., 5.))
    c = baseline_signal(df3, "volume_only_response", "move", CFG, scope=df3["eligible"])
    assert list(c.values) == [True, True, True, True, False, True]
    # out-of-scope rows do not feed the threshold but still receive one; NaN never fires
    df4 = _tiny_table(vols=(1., 100., 1., np.nan, 1., 1.))
    df4.loc[1, "eligible"] = False
    d = baseline_signal(df4, "volume_only_response", "move", CFG, scope=df4["eligible"])
    assert list(d.values) == [True, True, True, False, True, True]


def test_machinery_source_duplicate_flag_applies_only_to_the_producing_receipt(tmp_path):
    from tower.state import SourceStatus
    root = str(tmp_path / "s")
    store = StateStore(root)
    t0 = datetime(2026, 9, 6, 5, 0, tzinfo=timezone.utc)
    bids, asks = [(10.0, 100.0)], [(10.1, 100.0)]
    for i in range(4):
        t = t0 + timedelta(seconds=10 * i)
        # the depth source's last receipt (t0 + 10 s) was a repeat; rows 2 and 3 are produced by other sources
        st = SourceStatus(source="depth", last_update=t0 + timedelta(seconds=10), duplicate=(i >= 1))
        ms = MarketState(symbol="ZZZ", t=t, seq=i + 1, session_phase="CONTINUOUS", best_bid=10.0, best_ask=10.1,
                         mid=10.05, ltp=10.05, tick_size=0.1, bids=bids, asks=asks, empty_book=False,
                         book_source="depth", trade_count=float(i), trade_volume=float(10 * i),
                         sources={"depth": st})
        store.append(ms)
    store.close()
    df = add_exclusions(add_forward_outcomes(load_store(root), (10,)), CFG, 10)
    assert list(df["book_src_duplicate"]) == [False, True, False, False]
    assert list(df["excl_duplicate"]) == [False, True, False, False]
    # content identical to the previous row is still a duplicate without any source flag
    assert df["session_phase"].eq("CONTINUOUS").all()


def test_machinery_missing_session_phase_is_not_reported_as_closed():
    from tower.experiment import _flatten_state
    row = _flatten_state({"symbol": "Q", "t": "2026-09-06T05:00:00+00:00", "session_phase": None}, {})
    assert row["session_phase"] == "UNKNOWN"
    assert np.isnan(row["mid"]) and row["bids"] == [] and row["book_src_duplicate"] is None


def test_machinery_fdr_family_excludes_blocked_mechanisms():
    from tower.experiment import finalize_verdicts
    def prov(name, p, blocked):
        return {"mechanism": name, "permutation": {"p_value": p, "n_perm": 10}, "blocked": blocked,
                "blocked_reasons": ["too small"] if blocked else [],
                "checks": {"a_beats_best_baseline_ci": True, "b_permutation": p < 0.05 or True, "c_side_flip": True,
                           "d_placebo": True, "e_wall_removal": True, "f_loso": True, "g_liquidity": True},
                "side_flip_not_applicable": False}
    cfg = ExperimentConfig(fdr_q=0.10)
    v = finalize_verdicts({"m1": prov("m1", 0.06, False), "m2": prov("m2", 0.09, False), "mb": prov("mb", 0.9, True)}, cfg)
    assert v["fdr"]["n_tested"] == 2 and v["fdr"]["tested"] == ["m1", "m2"] and v["fdr"]["n_pass"] == 2
    assert v["mechanisms"]["m1"]["fdr_pass"] is True and v["mechanisms"]["m2"]["fdr_pass"] is True
    assert v["mechanisms"]["mb"]["verdict"] == "BLOCKED" and v["mechanisms"]["mb"]["fdr_pass"] is None
    assert v["mechanisms"]["mb"]["fdr_q_value"] is None and v["fdr"]["p_values"]["mb"] == 0.9
    # with the blocked one inside the family (m = 3) BH would have rejected nothing: 0.06 > 0.1·1/3, 0.09 > 0.1·2/3
    keep, _ = bh_fdr(np.array([0.06, 0.09, 0.9]), 0.10)
    assert not keep.any()


def test_machinery_bh_fdr_matches_phase45():
    import importlib.util
    spec = importlib.util.spec_from_file_location("phase45", os.path.join(ROOT, "experiments", "phase45_footprints.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rng = np.random.default_rng(0)
    for _ in range(50):
        p = rng.random(12) ** 3
        p[rng.random(12) < 0.2] = np.nan
        for q in (0.05, 0.10, 0.25):
            k1, c1 = bh_fdr(p, q)
            k2, c2 = mod.bh_fdr(p, q)
            assert np.array_equal(k1, k2)
            assert (np.isnan(c1) and np.isnan(c2)) or c1 == pytest.approx(c2)


def test_machinery_empty_store_runs_and_writes_zero_denominator(tmp_path):
    root = str(tmp_path / "empty")
    os.makedirs(os.path.join(root, "states"))
    out = str(tmp_path / "out")
    res = run_experiment(root, out, CFG)
    den = res["denominator"]
    assert den["total_rows"] == 0 and den["eligible"] == 0 and den["sum_check"] is True
    assert res["verdicts"]["counts"] == {"KEEP": 0, "KILL": 0, "BLOCKED": 0} and res["verdicts"]["fdr"]["n_tested"] == 0
    for name in ("MECHANISM_RESULTS.csv", "FALSIFICATION.csv", "DENOMINATOR.json", "VERDICTS.json", "MANIFEST.json"):
        assert os.path.exists(os.path.join(out, name)), name
    assert len(pd.read_csv(os.path.join(out, "FALSIFICATION.csv"))) == 0


def test_machinery_wall_free_geometry_edges():
    from tower.experiment import wall_free_geometry
    df = pd.DataFrame({
        "bids": [[(10.0, 500.0), (9.9, 100.0)], [(10.0, 100.0)], [], [(10.0, 100.0), (9.9, 100.0)], [(10.0, 300.0), (9.9, 100.0)]],
        "asks": [[(10.1, 100.0), (10.2, 100.0)], [], [(10.1, 100.0)], [(10.1, 100.0)], [(10.1, 300.0), (10.2, 100.0)]],
        "tick_eff": [0.1, 0.1, 0.1, 0.1, np.nan]})
    g = wall_free_geometry(df, 5)
    # row 0: the 500 wall at the bid touch goes; l1 = (100−100)/200 = 0, topk = (100−200)/300
    assert g.loc[0, "imb_l1_wf"] == pytest.approx(0.0)
    assert g.loc[0, "imb_topk_wf"] == pytest.approx(-100.0 / 300.0)
    assert g.loc[0, "depth_ratio_wf"] == pytest.approx(100.0 / 300.0)
    # weighted: bid level 9.9 is 1 tick from the wall-free touch 9.9 → weight 1; asks 10.1 (w 1), 10.2 (w 0.5)
    assert g.loc[0, "imb_weighted_wf"] == pytest.approx((100.0 - 150.0) / 250.0)
    # one-sided books and an empty side after the removal → NaN, never 0
    assert g.loc[1].isna().all() and g.loc[2].isna().all()
    # tie between the two 100-lots on the bid side and the ask 100-lot: the first (bid touch) is removed
    assert g.loc[3, "imb_l1_wf"] == pytest.approx(0.0) and g.loc[3, "depth_ratio_wf"] == pytest.approx(0.5)
    # 300-lot tie between bid touch and ask touch → the bid one (first) goes; no tick known → index weights 1/(1+i):
    # bids [100] → 100; asks [300, 100] → 300 + 50
    assert g.loc[4, "imb_weighted_wf"] == pytest.approx((100.0 - 350.0) / 450.0)
    assert g.loc[4, "imb_l1_wf"] == pytest.approx((100.0 - 300.0) / 400.0)


def test_machinery_forward_outcomes_duplicate_timestamps_and_gaps():
    t0 = pd.Timestamp("2026-09-06T05:00:00Z")
    df = pd.DataFrame({"symbol": ["A"] * 5, "seq": [1, 2, 3, 4, 5],
                       "t": [t0, t0, t0 + pd.Timedelta(seconds=100), t0 + pd.Timedelta(seconds=200), t0 + pd.Timedelta(seconds=380)],
                       "mid": [10.0, 10.0, 10.2, np.nan, 10.5], "tick_size": [0.1] * 5})
    for c in ("crossed", "locked", "one_sided", "empty_book"):
        df[c] = False
    out = add_forward_outcomes(df, (180,))
    # rows 0 and 1 share a timestamp: t+180 → last state ≤ t+180 is row 2 (mid 10.2) → +2 ticks, window complete
    assert out.loc[0, "fwd_valid_h180"] and out.loc[1, "fwd_valid_h180"]
    assert out.loc[0, "fwd_mid_ticks_h180"] == pytest.approx(2.0) and out.loc[1, "fwd_mid_ticks_h180"] == pytest.approx(2.0)
    # row 2: t+180 = 280 → last state ≤ 280 is row 3, whose mid is None → falls back to row 2 itself → no outcome
    assert not out.loc[2, "fwd_valid_h180"] and np.isnan(out.loc[2, "fwd_mid_ticks_h180"])
    # row 3 has no mid; row 4's window is incomplete
    assert not out.loc[3, "fwd_valid_h180"] and not out.loc[4, "fwd_valid_h180"]
    assert add_forward_outcomes(df.iloc[0:0], (180,)).empty


def test_machinery_bootstrap_and_permutation_detect_a_planted_signal():
    from tower.experiment import block_bootstrap_ci, permutation_p
    rng = np.random.default_rng(1)
    n = 400
    rows = []
    for sym in ("A", "B"):
        t0 = pd.Timestamp("2026-09-06T05:00:00Z")
        sig = np.zeros(n, dtype=bool)
        for e in range(5, n - 5, 25):
            sig[e:e + 3] = True
        up = np.where(sig, rng.random(n) < 0.9, rng.random(n) < 0.3)
        for i in range(n):
            rows.append({"symbol": sym, "t": t0 + pd.Timedelta(seconds=10 * i), "sig": sig[i], "noise": rng.random() < 0.1,
                         "fwd_up_h180": bool(up[i]), "fwd_valid_h180": True})
    fs = pd.DataFrame(rows)
    cfg = ExperimentConfig(n_boot=200, n_perm=200, seed=3)
    ci = block_bootstrap_ci(fs, fs["sig"], None, "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(1))
    assert ci["n_boot_valid"] == 200 and ci["ci_lo"] > 0.3 and ci["ci_lo"] < ci["point"] < ci["ci_hi"]
    ci2 = block_bootstrap_ci(fs, fs["sig"], fs["noise"], "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(1))
    assert ci2["ci_lo"] > 0.2                                 # incremental over an uninformative baseline
    pp = permutation_p(fs, fs["sig"], "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(2))
    assert pp["p_value"] == pytest.approx(1.0 / 201.0) and pp["observed"] > 0.4 and pp["null_p95"] < pp["observed"]
    pn = permutation_p(fs, fs["noise"], "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(2))
    assert pn["p_value"] > 0.05
    # determinism: same generator seed → identical numbers
    again = block_bootstrap_ci(fs, fs["sig"], None, "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(1))
    assert again == ci
    # an all-False signal → NaN, never 0
    empty = permutation_p(fs, pd.Series(False, index=fs.index), "fwd_up_h180", "fwd_valid_h180", cfg, np.random.default_rng(2))
    assert np.isnan(empty["p_value"]) and empty["n_perm"] == 0


def test_machinery_graded_variant_label_follows_config(synthetic, tmp_path):
    from tower.experiment import run_mechanism, prepare
    root, _ = synthetic
    cfg = ExperimentConfig(horizons=(180,), primary_h=H, n_boot=10, n_perm=10, score_threshold=0.5, seed=1)
    df = prepare(load_store(root, symbols=["AAA"]), cfg)
    r = run_mechanism(df, "planted", cfg, "test")
    assert {x["variant"] for x in r["results"]} == {"state", "score_ge_0.5", "mirror"}
    assert [x["variant"] for x in r["falsification"] if x["test"] == "graded_score"] == ["score_ge_0.5"]
