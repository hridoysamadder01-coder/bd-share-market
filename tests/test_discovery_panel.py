"""Discovery must not be contaminated, however aggressive it is allowed to be.

Two rules carry the whole thing: the sealed holdout and the reserved 2026 slice
never enter DEV, and no candidate reads a column that encodes the future.
"""
import numpy as np
import pandas as pd
import pytest

from research.discovery import panel
from research.discovery.candidates import Candidate, evaluate, vol_quintile


def test_sealed_holdout_and_reserved_slice_are_asserted_not_just_filtered():
    d = panel.load_dev()
    assert not d["ts"].between(*panel.SEALED_HOLDOUT).any()
    assert (d["ts"] < panel.VIRGIN_SLICE_START).all()
    assert d.attrs["rows_removed_as_reserved"] > 0


def test_lookahead_columns_are_refused_as_candidate_inputs():
    """bdlib/qa.py:40 stamps a run's whole length onto every day of it."""
    for col in panel.LOOKAHEAD_COLUMNS:
        with pytest.raises(ValueError, match="look-ahead"):
            panel.assert_pit([col])


def test_forward_labels_are_refused_as_candidate_inputs():
    with pytest.raises(ValueError, match="forward labels"):
        panel.assert_pit(["fwd_ret_5"])
    with pytest.raises(ValueError, match="forward labels"):
        panel.assert_pit(["rel_volume_z", "fwd_mae_30"])


def test_ordinary_features_pass_the_pit_check():
    panel.assert_pit(["rel_volume_z", "range_compression", "approach_up"])


def test_input_columns_exclude_labels_and_lookahead():
    d = pd.DataFrame({"symbol": ["A"], "ts": [pd.Timestamp("2020-01-01")],
                      "rel_volume_z": [1.0], "flag_locked_run": [False],
                      "flag_stale_run": [False], "fwd_ret_5": [0.1]})
    cols = panel.input_columns(d)
    assert cols == ["rel_volume_z"]


def test_band_ladder_matches_the_published_equity_table():
    p = pd.Series([50.0, 300.0, 800.0, 5000.0])
    assert list(panel.band_pct(p)) == [10.00, 8.75, 7.50, 6.25]


def test_a_candidate_reading_a_label_raises_rather_than_scoring():
    bad = Candidate("X", "cheats", ("fwd_ret_5",), lambda d: d["fwd_ret_5"] > 0, "test")
    d = pd.DataFrame({"symbol": ["A"] * 10, "ts": pd.date_range("2020-01-01", periods=10),
                      "realized_vol": np.linspace(0.01, 0.1, 10),
                      "fwd_ret_5": np.linspace(-0.1, 0.1, 10),
                      "fwd_mfe_5": 0.1, "fwd_mae_5": -0.1})
    with pytest.raises(ValueError, match="look-ahead"):
        bad.mask(d)


def test_vol_quintile_is_defined_even_when_a_date_has_few_symbols():
    """qcut raises on a date with fewer than five distinct values; ranking does not."""
    d = pd.DataFrame({"ts": [pd.Timestamp("2020-01-01")] * 2 + [pd.Timestamp("2020-01-02")] * 6,
                      "realized_vol": [0.01, 0.01] + list(np.linspace(0.01, 0.06, 6))})
    q = vol_quintile(d)
    assert q.notna().all() and q.between(0, 4).all() and len(q) == len(d)


def test_a_state_that_never_occurs_is_killed_not_crashed():
    d = pd.DataFrame({"symbol": ["A"] * 20, "ts": pd.date_range("2020-01-01", periods=20),
                      "realized_vol": np.linspace(0.01, 0.1, 20), "x": 0.0,
                      "fwd_ret_5": 0.01, "fwd_mfe_5": 0.02, "fwd_mae_5": -0.01})
    never = Candidate("N", "never", ("x",), lambda f: f["x"] > 1e9, "test")
    r = evaluate(d, never, 5)
    assert r["status"] == "KILLED" and r["n"] == 0


def test_events_are_counted_distinctly_not_as_occurrences():
    """I-012: 102 'hits' were 28 distinct events."""
    d = pd.DataFrame({"symbol": ["A"] * 40 + ["B"] * 40,
                      "ts": list(pd.date_range("2020-01-01", periods=40)) * 2,
                      "realized_vol": list(np.linspace(0.01, 0.2, 40)) * 2,
                      "x": [1.0] * 80,
                      "fwd_ret_5": 0.02, "fwd_mfe_5": 0.03, "fwd_mae_5": -0.01})
    always = Candidate("A", "always", ("x",), lambda f: f["x"] > 0, "test")
    r = evaluate(d, always, 5, min_events=1)
    assert r["n_events"] == 80 and r["symbols"] == 2


def test_dev_slices_never_pool_panels_or_eras():
    d = panel.add_regime_and_room(panel.load_dev())
    for s in panel.dev_slices(d):
        assert s.frame["panel"].nunique() == 1
        assert s.frame["floor_era"].nunique() == 1
