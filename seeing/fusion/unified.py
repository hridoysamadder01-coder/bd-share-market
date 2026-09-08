"""Stage 2 — one unified market state per (listing, instant), hashed and replayable.

`fuse.py` already builds one timestamp-aligned row per (symbol, t_frame). Three
things it does not do are what Stage 2 is:

**It is keyed by a symbol string, not by an instrument.** `"BRACBANK"` is a label
several sources happen to share; Stage 0 (`seeing/identity.py`) resolved which
*listing* that label denotes, and DSE:BRACBANK and CSE:BRACBANK are two order
books that must never be merged. The state is keyed by the listing.

**It picks a value where sources disagree.** The watch poll and the depth page
both publish an LTP and a day volume for the same instrument. `fuse` keeps them
in separate columns (`ltp`, `w_ltp`), which is honest but leaves the reader to
notice a contradiction. `seeing/consensus.py` already holds the rule — a
consensus value exists **only** where the reporting sources agree — so it is
applied here and its verdict travels on the state.

**It is not addressable.** Two replays of the same raw store must produce the
same state, and "the same" has to mean something checkable. Each state carries a
SHA256 over a canonical serialization of a declared field set, and the states of
a run are chained so one hash covers the whole replay.

Canonical form
--------------
Hashing floating-point state needs a written rule or the hash is a coin flip:

* ``None``, NaN and NaT all serialize to ``null``. They are the same fact — no
  value — and a state where the limit is unknown must not hash differently from
  one where it is missing.
* Floats are emitted through :data:`FLOAT_FORMAT` (12 significant digits) with
  ``-0.0`` normalized to ``0.0``. Twelve digits is well inside the precision of
  every quantity here (prices to 0.1, quantities integral) and outside the reach
  of the last-bit noise that different BLAS builds produce on the same sum.
* Timestamps are ISO-8601 UTC to microseconds. The store's own resolution.
* Book levels are ``[price, quantity]`` pairs in source order, which is the
  order the exchange displayed them; sorting them would discard information.
* The JSON is ``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=False``,
  UTF-8 — the same rule `seeing/identity.py` hashes the spine with.

What the hash covers is :data:`STATE_FIELDS`, declared explicitly. Adding a
field changes every hash in the repository, so it is a deliberate act with a
schema bump, not a side effect of fusion growing a column.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..consensus import Observation, reconcile_field
from ..identity import normalise_code
from .state import FRAME_TRUTH

STATE_SCHEMA = "unified_market_state/2"
FLOAT_FORMAT = "%.12g"

DEFAULT_IDENTITY_MAP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "evidence", "identity", "2026-09-08", "IDENTITY_MAP.json")

# Fields both the depth page and the all-symbol watch publish for the same
# instrument at the same moment. These are the only ones where a consensus
# question can even be asked from the sources this repository reaches.
CROSS_SOURCE_FIELDS = ("ltp", "day_trades", "day_volume", "day_value_mn",
                       "high", "low", "open", "yclose")

# ---------------------------------------------------------------- hashed field set
IDENTITY_FIELDS = ("listing_key", "exchange", "code", "company_key", "identity_truth")
CLOCK_FIELDS = ("t_frame", "t_prev_frame", "frame_no", "frame_dt_s")
BOOK_FIELDS = ("book_primary", "bid_levels", "ask_levels", "best_bid", "best_ask",
               "bid_qty1", "ask_qty1", "n_bid", "n_ask", "spread", "spread_ticks",
               "mid", "microprice", "crossed", "locked", "one_sided", "empty",
               "bid_depth_top3", "ask_depth_top3", "bid_depth_top5", "ask_depth_top5",
               "bid_depth_all", "ask_depth_all", "imb_l1", "imb_top3", "imb_top5",
               "imb_all", "imb_weighted", "unchanged_run", "n_level_events")
XCHECK_FIELDS = ("book_agree", "book_diff", "xcheck_dt_s")
TAPE_FIELDS = ("int_d_volume", "int_d_trades", "int_vwap", "int_source",
               "tape_rows", "tape_monotone_break", "snap_monotone_break",
               "side_score", "side_truth", "side_conf", "signed_int_volume")
EVENT_FIELDS = ("ev_n", "ev_bid_add_qty", "ev_bid_reduce_qty", "ev_ask_add_qty",
                "ev_ask_reduce_qty", "ev_bid_replenish", "ev_ask_replenish",
                "ev_ask_touch_consumed", "ev_bid_touch_consumed",
                "ev_ask_cancel_away", "ev_bid_cancel_away", "ev_sweeps")
REFERENCE_FIELDS = ("upper_limit", "lower_limit", "tick_size", "ref_status", "ref_age_s",
                    "shares_to_door", "door_visible", "bid_at_upper_limit",
                    "ask_at_lower_limit")
CONTEXT_FIELDS = ("market_trades", "market_volume", "market_value_mn", "market_age_s",
                  "mkt_up", "mkt_down", "mkt_n", "watch_age_s", "watch_src_age_s",
                  "block_trades_today", "block_quantity_today")
CONSENSUS_FIELDS = tuple(f"cons_{f}" for f in CROSS_SOURCE_FIELDS) + (
    "xsrc_agreeing", "xsrc_disagreeing", "xsrc_agreement_rate")

STATE_FIELDS = (IDENTITY_FIELDS + CLOCK_FIELDS + BOOK_FIELDS + XCHECK_FIELDS +
                TAPE_FIELDS + EVENT_FIELDS + REFERENCE_FIELDS + CONTEXT_FIELDS +
                CONSENSUS_FIELDS)


# --------------------------------------------------------------------- canonical form
def _is_missing(v: Any) -> bool:
    if v is None or v is pd.NaT:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):        # arrays, lists: not scalar-missing
        return False


def canonical(v: Any) -> Any:
    """Every value reduced to the JSON form the hash is taken over.

    Missing is one thing, not three: ``None``, NaN and NaT all become ``null``,
    so a state whose limit was never observed hashes identically however the
    absence arrived.
    """
    if _is_missing(v):
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        t = v.tz_localize("UTC") if getattr(v, "tzinfo", None) is None else v.astimezone(timezone.utc)
        return t.isoformat(timespec="microseconds")
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isinf(f):
            return "Infinity" if f > 0 else "-Infinity"
        f = 0.0 if f == 0.0 else f          # -0.0 and 0.0 are one value
        return float(FLOAT_FORMAT % f)
    if isinstance(v, (np.ndarray, list, tuple)):
        return [canonical(x) for x in v]
    if isinstance(v, dict):
        return {str(k): canonical(v[k]) for k in sorted(v, key=str)}
    return str(v)


def canonical_json(record: Dict[str, Any]) -> str:
    return json.dumps(canonical(record), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def state_hash(record: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def chain(previous: Optional[str], state_sha: str) -> str:
    """Same discipline as the raw store: each state commits to its predecessor."""
    return hashlib.sha256(f"{previous or ''}|{state_sha}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------- identity
class IdentityIndex:
    """The Stage 0 spine, read for lookup only. Never rebuilt or amended here."""

    def __init__(self, core: Dict[str, Any], spine_sha256: Optional[str] = None):
        self.spine_sha256 = spine_sha256
        self._listings = {l["key"]: l for l in core.get("listings", [])}
        self._company_of: Dict[str, str] = {}
        for c in core.get("companies", []):
            for k in c.get("listings", []):
                self._company_of[k] = c["key"]

    @classmethod
    def load(cls, path: Optional[str] = None) -> "IdentityIndex":
        p = path or DEFAULT_IDENTITY_MAP
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        return cls(doc.get("core", doc), doc.get("spine_sha256"))

    def resolve(self, symbol: Any, exchange: str = "DSE") -> Dict[str, Any]:
        """Which listing a captured symbol string denotes — or that it is unknown.

        A symbol the spine has never seen is **not** quietly promoted to a
        listing: it gets NOT_OBSERVABLE identity and says so on every state it
        appears in. Stage 0's whole point is that guessing here is forbidden.
        """
        code = normalise_code(symbol)
        key = f"{exchange}:{code}"
        row = self._listings.get(key)
        if row is None:
            return {"listing_key": None, "exchange": None, "code": code,
                    "company_key": None, "identity_truth": "NOT_OBSERVABLE",
                    "identity_note": f"{key} is not in the spine"}
        if not row.get("resolved"):
            return {"listing_key": None, "exchange": None, "code": code,
                    "company_key": None, "identity_truth": "NOT_OBSERVABLE",
                    "identity_note": f"{key} is in the spine but unresolved"}
        return {"listing_key": key, "exchange": row["exchange"], "code": row["code"],
                "company_key": self._company_of.get(key),
                "identity_truth": row.get("truth", "OBSERVED"), "identity_note": None}


# ------------------------------------------------------------------ cross-source view
def cross_source(frame: Dict[str, Any], primary_source: str,
                 fields: Sequence[str] = CROSS_SOURCE_FIELDS) -> Dict[str, Any]:
    """What the depth page and the watch poll each say, and where they agree.

    Returns a consensus value per field **only** where both reporting sources
    agree within `seeing/consensus.py`'s tolerance; a disagreement leaves the
    consensus null and is counted. Nothing is averaged and no source is preferred.
    """
    depth_fields = {f: frame.get(f) for f in fields}
    watch_fields = {f: frame.get(f"w_{f}") for f in fields}
    t = frame.get("t_frame")
    t_iso = canonical(t) or ""
    obs = [Observation(source=primary_source, symbol=str(frame.get("symbol") or ""),
                       t_recv_utc=t_iso,
                       fields={k: v for k, v in depth_fields.items() if not _is_missing(v)})]
    if any(not _is_missing(v) for v in watch_fields.values()):
        obs.append(Observation(source="lankabd_watch", symbol=str(frame.get("symbol") or ""),
                               t_recv_utc=t_iso,
                               t_source_utc=canonical(frame.get("w_t_source")),
                               fields={k: v for k, v in watch_fields.items()
                                       if not _is_missing(v)}))

    out: Dict[str, Any] = {}
    agreeing: List[str] = []
    disagreeing: List[str] = []
    for f in fields:
        fc = reconcile_field(obs, f)
        # One reporting source is not agreement — it is a single opinion. The
        # consensus column is for corroborated values only; the raw per-source
        # columns are still on the frame for anyone who wants the single view.
        if fc.agree and len(fc.reporting) > 1:
            out[f"cons_{f}"] = fc.consensus
            agreeing.append(f)
        else:
            out[f"cons_{f}"] = None
            if fc.agree is False:
                disagreeing.append(f)
    n = len(agreeing) + len(disagreeing)
    out["xsrc_agreeing"] = ",".join(agreeing)
    out["xsrc_disagreeing"] = ",".join(disagreeing)
    out["xsrc_agreement_rate"] = round(len(agreeing) / n, 4) if n else None
    return out


# ------------------------------------------------------------------------- the state
def unify(frames: pd.DataFrame, identity: Optional[IdentityIndex] = None,
          exchange: str = "DSE") -> pd.DataFrame:
    """Fused frames → unified, identity-keyed, consensus-carrying, hashed states.

    One row per (listing, t_frame), ordered by (listing_key, t_frame, frame_no)
    so the chain is a function of the data and not of the order pandas happened
    to concatenate groups in.
    """
    if frames is None or not len(frames):
        return pd.DataFrame()
    idx = identity if identity is not None else IdentityIndex.load()
    d = frames.copy()

    ident = [idx.resolve(s, exchange) for s in d["symbol"]]
    for k in ("listing_key", "exchange", "code", "company_key", "identity_truth", "identity_note"):
        d[k] = [r[k] for r in ident]

    xs = [cross_source(r, str(r.get("book_primary") or "")) for r in d.to_dict("records")]
    for k in xs[0]:
        d[k] = [r[k] for r in xs]

    # An unresolved symbol still sorts and hashes; it sorts under its code so a
    # state that could not be identified is never silently dropped from the run.
    d["_sort_key"] = d["listing_key"].fillna("UNRESOLVED:" + d["code"].astype(str))
    d = d.sort_values(["_sort_key", "t_frame", "frame_no"], kind="mergesort").reset_index(drop=True)

    records = d.to_dict("records")
    shas: List[str] = []
    for r in records:
        shas.append(state_hash({f: r.get(f) for f in STATE_FIELDS}))
    d["state_sha256"] = shas

    prev: Optional[str] = None
    ch: List[str] = []
    for s in shas:
        prev = chain(prev, s)
        ch.append(prev)
    d["state_chain_sha256"] = ch
    d["state_seq"] = range(len(d))
    d = d.drop(columns=["_sort_key"])

    truth = dict(FRAME_TRUTH)
    truth["listing identity (listing_key, exchange, company_key)"] = (
        "OBSERVED via the Stage 0 spine; NOT_OBSERVABLE for a symbol the spine "
        "does not resolve — never guessed onto an exchange")
    truth["cons_* (cross-source consensus)"] = (
        "OBSERVED where two independent sources report the field and agree within "
        "tolerance; null where they disagree or only one reported it — never averaged")
    d.attrs["truth"] = truth
    d.attrs["schema"] = STATE_SCHEMA
    d.attrs["spine_sha256"] = idx.spine_sha256
    return d


def run_hash(unified: pd.DataFrame) -> Optional[str]:
    """The single value that stands for a whole replay: the last chain link."""
    if unified is None or not len(unified):
        return None
    return str(unified["state_chain_sha256"].iloc[-1])


def summary(unified: pd.DataFrame) -> Dict[str, Any]:
    """What the gate reports. Every count is measured, none is asserted."""
    if unified is None or not len(unified):
        return {"states": 0, "run_sha256": None}
    ref = unified["ref_status"].value_counts().to_dict() if "ref_status" in unified else {}
    out: Dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "states": int(len(unified)),
        "listings": int(unified["listing_key"].nunique(dropna=True)),
        "unresolved_states": int(unified["listing_key"].isna().sum()),
        "companies": int(unified["company_key"].nunique(dropna=True)),
        "t_first": canonical(unified["t_frame"].min()),
        "t_last": canonical(unified["t_frame"].max()),
        "distinct_state_sha256": int(unified["state_sha256"].nunique()),
        "run_sha256": run_hash(unified),
        "spine_sha256": unified.attrs.get("spine_sha256"),
        "ref_status": {str(k): int(v) for k, v in sorted(ref.items())},
        "hashed_fields": len(STATE_FIELDS),
    }
    if "xsrc_agreement_rate" in unified:
        r = unified["xsrc_agreement_rate"].dropna()
        out["xsrc_agreement_rate_mean"] = round(float(r.mean()), 6) if len(r) else None
        out["states_with_a_disagreement"] = int((unified["xsrc_disagreeing"] != "").sum())
    if "ref_from_future" in unified.columns:                   # must never come back
        out["ref_from_future_present"] = True
    return out


def assert_no_future_reference(unified: pd.DataFrame) -> None:
    """The Stage 2 prohibition, enforced rather than documented.

    A reference value on a state whose `ref_status` is not OBSERVED came from a
    circuit row the market had not yet published. That is the defect this stage
    removed, and it is checked rather than trusted.
    """
    if "ref_from_future" in unified.columns:
        raise AssertionError(
            "ref_from_future is present: a future circuit row is supplying the "
            "reference. Prohibited by ROADMAP.md Stage 2.")
    if "ref_status" not in unified.columns:
        raise AssertionError("ref_status missing: reference provenance is not recorded")
    bad = unified[(unified["ref_status"] != "OBSERVED") & unified["upper_limit"].notna()]
    if len(bad):
        raise AssertionError(
            f"{len(bad)} states carry an upper_limit with ref_status != OBSERVED — "
            f"a reference was supplied without a preceding observation")
    stale = unified["ref_age_s"].dropna() if "ref_age_s" in unified else pd.Series(dtype=float)
    if len(stale) and float(stale.min()) < 0:
        raise AssertionError("negative ref_age_s: the reference is stamped after its frame")
