"""M1 / M2 exactly as frozen in MICRO_PREREG.json v2. No other model, no search."""
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from micro_features import FEATURES

CLASSES = [0.0, 1.0, 2.0]          # DOWN, FLAT, UP

def make_M1():
    return Pipeline([("imp", SimpleImputer(strategy="median")),
                     ("sc", StandardScaler()),
                     ("clf", LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000,
                                                class_weight="balanced"))])

def make_M2():
    return Pipeline([("imp", SimpleImputer(strategy="median")),
                     ("clf", HistGradientBoostingClassifier(
                         max_depth=3, learning_rate=0.05, max_iter=300,
                         min_samples_leaf=50, l2_regularization=1.0,
                         early_stopping=False, random_state=7))])

def fit(model, dev, H):
    m = dev[dev["eligible"] & dev[f"y_{H}"].notna()]
    X, y = m[FEATURES], m[f"y_{H}"].astype(float)
    model.fit(X, y)
    return model

def predict(model, df):
    P = model.predict_proba(df[FEATURES])
    cls = list(model.named_steps["clf"].classes_)
    out = {}
    for name, c in (("p_down", 0.0), ("p_flat", 1.0), ("p_up", 2.0)):
        out[name] = P[:, cls.index(c)] if c in cls else np.zeros(len(df))
    return pd.DataFrame(out, index=df.index)
