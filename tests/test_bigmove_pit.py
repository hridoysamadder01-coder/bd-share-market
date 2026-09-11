"""Adversarial tests for strict point-in-time cross-sectional state.

These are the gate. `CTX_xs_rank_top` was reported at +21.75 pp using a 60-second
bucket groupby, which let a 10:00:55 observation reach a 10:00:05 decision. No
cross-sectional result may be believed until the four tests named A–D below pass,
because each one fails loudly against the bucket implementation:

  A  a future observation appended later in the same minute changes nothing
  B  rows sharing a timestamp give the same answer in any input order
  C  an entity whose first print is in the future never enters an earlier snapshot
  D  the same raw data replays to a bit-identical hash
"""
import hashlib

import numpy as np
import pandas as pd
import pytest

from research.bigmove.pit import ASOF_COLUMNS, asof_cross_section

T0 = pd.Timestamp("2026-09-08 10:00:00")


def frame(rows):
    return pd.DataFrame(rows, columns=["symbol", "ts", "P"])


def bucket_rank(df, freq="60s"):
    """The implementation being replaced, kept here so the tests can prove it fails."""
    d = df.copy()
    d["_tbin"] = pd.to_datetime(d["ts"]).dt.floor(freq)
    d["xs_rank_bucket"] = d.groupby("_tbin")["P"].rank(pct=True)
    return d


BASE = frame([
    ("AAA", T0 + pd.Timedelta(seconds=5), 1.0),
    ("BBB", T0 + pd.Timedelta(seconds=6), 2.0),
    ("CCC", T0 + pd.Timedelta(seconds=7), 3.0),
    ("DDD", T0 + pd.Timedelta(seconds=8), 4.0),
])


# ------------------------------------------------------------------ TEST A
def test_A_a_future_observation_in_the_same_minute_cannot_change_an_earlier_row():
    """The exact defect: 10:00:55 reaching 10:00:05."""
    before = asof_cross_section(BASE, value="P", min_others=1)
    later = pd.concat([BASE, frame([("EEE", T0 + pd.Timedelta(seconds=55), 1e9)])],
                      ignore_index=True)
    after = asof_cross_section(later, value="P", min_others=1)

    a = before.set_index(["symbol", "ts"])[list(ASOF_COLUMNS)]
    b = after.set_index(["symbol", "ts"]).loc[a.index, list(ASOF_COLUMNS)]
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_A_the_bucket_implementation_fails_this_test():
    """Proof the test bites: the replaced code moves under the same perturbation."""
    before = bucket_rank(BASE).set_index(["symbol", "ts"])["xs_rank_bucket"]
    later = pd.concat([BASE, frame([("EEE", T0 + pd.Timedelta(seconds=55), 1e9)])],
                      ignore_index=True)
    after = bucket_rank(later).set_index(["symbol", "ts"])["xs_rank_bucket"].loc[before.index]
    assert not np.allclose(before.to_numpy(), after.to_numpy()), \
        "the bucket groupby must be shown to leak, or this suite proves nothing"


# ------------------------------------------------------------------ TEST B
def test_B_rows_sharing_a_timestamp_are_order_independent():
    same = frame([("AAA", T0, 1.0), ("BBB", T0, 2.0), ("CCC", T0, 3.0), ("DDD", T0, 4.0)])
    a = asof_cross_section(same, value="P", min_others=1).set_index("symbol")[list(ASOF_COLUMNS)]
    for seed in (0, 1, 2, 7):
        shuffled = same.sample(frac=1.0, random_state=seed).reset_index(drop=True)
        b = asof_cross_section(shuffled, value="P", min_others=1).set_index("symbol")
        pd.testing.assert_frame_equal(a, b.loc[a.index, list(ASOF_COLUMNS)], check_exact=True)


def test_B_a_same_instant_batch_sees_the_whole_batch_not_a_prefix():
    """All four enter the state, then all four read it — never a running prefix."""
    same = frame([("AAA", T0, 1.0), ("BBB", T0, 2.0), ("CCC", T0, 3.0), ("DDD", T0, 4.0)])
    r = asof_cross_section(same, value="P", min_others=1).set_index("symbol")
    assert (r["market_n_asof"] == 3).all(), "leave-one-out of a four-symbol instant"
    assert r.loc["AAA", "xs_rank_asof"] == 0.0, "lowest of the batch"
    assert r.loc["DDD", "xs_rank_asof"] == 1.0, "highest of the batch"


def test_B_strict_before_excludes_the_same_instant_batch_entirely():
    same = frame([("AAA", T0, 1.0), ("BBB", T0, 2.0)])
    r = asof_cross_section(same, value="P", strict_before=True, min_others=1)
    assert r["market_n_asof"].eq(0).all(), "nothing precedes the first instant"
    assert r["xs_rank_asof"].isna().all()


# ------------------------------------------------------------------ TEST C
def test_C_an_entity_whose_first_print_is_later_never_enters_an_earlier_snapshot():
    df = frame([
        ("AAA", T0, 1.0), ("BBB", T0, 2.0), ("CCC", T0, 3.0),
        ("ZZZ", T0 + pd.Timedelta(minutes=5), 1e9),
        ("AAA", T0 + pd.Timedelta(minutes=5), 1.0),
    ])
    r = asof_cross_section(df, value="P", min_others=1)
    early = r[r.ts == T0]
    assert (early["market_n_asof"] == 2).all(), "ZZZ must not be counted before it exists"
    assert early["market_median_asof"].max() <= 3.0, "and cannot move the median"

    late = r[(r.ts > T0) & (r.symbol == "AAA")]
    assert late["market_n_asof"].iloc[0] == 3, "but it is in the state once it has printed"


def test_C_an_entity_that_stops_printing_persists_with_a_visible_age():
    df = frame([
        ("AAA", T0, 1.0), ("BBB", T0, 2.0),
        ("AAA", T0 + pd.Timedelta(days=1), 1.5),
    ])
    r = asof_cross_section(df, value="P", min_others=1)
    late = r.iloc[-1]
    assert late["market_n_asof"] == 1, "BBB is stale, not gone"
    assert late["age_max_s"] == pytest.approx(86400.0), "and its age is on the record"


def test_C_a_freshness_cutoff_drops_the_stale_entity_and_counts_it():
    df = frame([
        ("AAA", T0, 1.0), ("BBB", T0, 2.0),
        ("AAA", T0 + pd.Timedelta(days=1), 1.5),
    ])
    r = asof_cross_section(df, value="P", min_others=1, max_age_s=3600.0)
    late = r.iloc[-1]
    assert late["market_n_asof"] == 0
    assert late["n_stale_dropped"] == 1, "dropping is counted, never silent"


# ------------------------------------------------------------------ TEST D
def _hash(df):
    cols = ["symbol", "ts"] + list(ASOF_COLUMNS)
    return hashlib.sha256(
        df[cols].round(12).to_csv(index=False).encode()).hexdigest()


def test_D_the_same_raw_data_replays_to_an_identical_hash():
    rng = np.random.default_rng(7)
    rows = []
    for k in range(300):
        for s in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            rows.append((s, T0 + pd.Timedelta(seconds=43 * k), float(rng.normal())))
    df = frame(rows)
    h1 = _hash(asof_cross_section(df, value="P", min_others=1))
    h2 = _hash(asof_cross_section(df.copy(), value="P", min_others=1))
    h3 = _hash(asof_cross_section(df.sample(frac=1.0, random_state=3).reset_index(drop=True),
                                  value="P", min_others=1)
               .sort_values(["ts", "symbol"]).reset_index(drop=True))
    ref = _hash(asof_cross_section(df, value="P", min_others=1)
                .sort_values(["ts", "symbol"]).reset_index(drop=True))
    assert h1 == h2, "two runs of identical input must agree bit for bit"
    assert h3 == ref, "and shuffling the input must not change the answer"


# ------------------------------------------------------------------ semantics
def test_the_market_is_always_leave_one_out():
    """A share must not dilute the market it is being measured against."""
    df = frame([("AAA", T0, 100.0), ("BBB", T0, 1.0), ("CCC", T0, 1.0)])
    r = asof_cross_section(df, value="P", min_others=1).set_index("symbol")
    assert r.loc["AAA", "market_median_asof"] == 1.0, "AAA excluded from its own market"
    assert r.loc["AAA", "share_resid_asof"] == 99.0


def test_min_others_refuses_a_market_too_thin_to_be_one():
    df = frame([("AAA", T0, 1.0), ("BBB", T0, 2.0)])
    r = asof_cross_section(df, value="P", min_others=3)
    assert r["xs_rank_asof"].isna().all(), "one other share is not a cross-section"
    assert (r["market_n_asof"] == 1).all(), "but the count is still reported"


def test_a_missing_value_neither_enters_the_state_nor_gets_a_rank():
    df = frame([("AAA", T0, np.nan), ("BBB", T0, 2.0), ("CCC", T0, 3.0), ("DDD", T0, 4.0)])
    r = asof_cross_section(df, value="P", min_others=1).set_index("symbol")
    assert np.isnan(r.loc["AAA", "xs_rank_asof"]), "no value, no rank"
    assert r.loc["BBB", "market_n_asof"] == 2, "and AAA is not in anyone's market"


def test_the_rank_is_a_fraction_of_the_others_not_of_the_whole():
    df = frame([("A", T0, 1.0), ("B", T0, 2.0), ("C", T0, 3.0), ("D", T0, 4.0), ("E", T0, 5.0)])
    r = asof_cross_section(df, value="P", min_others=1).set_index("symbol")
    assert r.loc["C", "xs_rank_asof"] == pytest.approx(0.5), "middle of four others"
    assert r["xs_rank_asof"].between(0, 1).all()
