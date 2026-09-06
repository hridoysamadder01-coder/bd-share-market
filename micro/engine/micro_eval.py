"""Frozen evaluation: AUC(UP vs DOWN), incremental vs strongest baseline, session-block bootstrap."""
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from micro_features import BASELINES

def auc_updown(score, y):
    """y in {0 DOWN,1 FLAT,2 UP}; FLAT excluded. Returns AUC or nan."""
    m = np.isfinite(score) & np.isin(y, (0.0, 2.0))
    if m.sum() < 30: return np.nan, int(m.sum())
    yy = (y[m] == 2.0).astype(int)
    if yy.min() == yy.max(): return np.nan, int(m.sum())
    return float(roc_auc_score(yy, score[m])), int(m.sum())

def score_all(df, H, model_score):
    y = df[f"y_{H}"].to_numpy(dtype=float)
    rows = [{"name": "FUSED", "auc": auc_updown(np.asarray(model_score, dtype=float), y)[0],
             "n": auc_updown(np.asarray(model_score, dtype=float), y)[1]}]
    for b in BASELINES:
        a, n = auc_updown(df[b].to_numpy(dtype=float), y)
        rows.append({"name": b, "auc": a, "n": n})
    return pd.DataFrame(rows)

def block_bootstrap_incremental(df, H, model_score, best_baseline, reps=2000, seed=7):
    """Block = one whole SESSION. CI on AUC(fused) - AUC(best baseline)."""
    y = df[f"y_{H}"].to_numpy(dtype=float)
    ms = np.asarray(model_score, dtype=float)
    bs = df[best_baseline].to_numpy(dtype=float)
    sess = df["session"].to_numpy()
    us = np.unique(sess)
    rng = np.random.default_rng(seed)
    out = np.full(reps, np.nan)
    for i in range(reps):
        pick = rng.choice(us, len(us), replace=True)
        idx = np.concatenate([np.flatnonzero(sess == s) for s in pick])
        a1, _ = auc_updown(ms[idx], y[idx]); a2, _ = auc_updown(bs[idx], y[idx])
        if np.isfinite(a1) and np.isfinite(a2): out[i] = a1 - a2
    lo, hi = np.nanpercentile(out, [2.5, 97.5])
    return float(lo), float(hi), float(np.nanmean(out))

def episodes(df, direction, cooldown_s=300):
    """Collapse repeated same-direction pressure per symbol into independent episodes."""
    d = df.assign(sig_dir=direction).sort_values(["symbol", "t_frame"])
    keep = []
    last = {}
    for r in d.itertuples():
        if r.sig_dir == 0: continue
        k = (r.symbol, r.sig_dir)
        t = r.t_frame.timestamp()
        if k not in last or (t - last[k]) > cooldown_s:
            keep.append(r.Index)
        last[k] = t
    return pd.Index(keep)
