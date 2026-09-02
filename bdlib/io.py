"""Loading, schema validation and run manifests. No repair, ever."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

import pandas as pd

from . import config as C

BD = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))   # this repository's root
REPO = BD


def paths() -> dict:
    return {k: os.path.join(BD, k) for k in
            ("data", "qa", "features", "state_engine", "experiments",
             "results", "reports", "manifests")}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bars(path: str) -> pd.DataFrame:
    """Load minute bars. Raises on schema violations — never guesses columns."""
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    missing = [c for c in C.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("missing required columns %s in %s (required: %s)"
                         % (missing, path, list(C.REQUIRED_COLUMNS)))
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "turnover" not in df.columns:
        # Turnover is a distinct observable; when absent it is DERIVED and marked
        # so, never silently treated as if the exchange reported it.
        df["turnover"] = df["close"] * df["volume"]
        df.attrs["turnover_derived"] = True
    else:
        df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce")
        df.attrs["turnover_derived"] = False
    df.attrs["source_path"] = path
    # Stable ordering for reproducibility; duplicates are REPORTED by QA, not dropped.
    return df.sort_values(["symbol", "ts"], kind="mergesort").reset_index(drop=True)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", REPO, "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def write_manifest(name: str, payload: dict) -> str:
    """Every run writes a manifest: inputs (hashed), params, environment, outputs."""
    out = os.path.join(BD, "manifests", name)
    payload = dict(payload)
    payload["written_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["git_commit"] = git_commit()
    payload["python"] = sys.version.split()[0]
    payload["platform"] = platform.platform()
    payload["pandas"] = pd.__version__
    payload["unverified_flags"] = C.unverified_flags()
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return out
