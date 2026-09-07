"""tower.mechanics.accumulation_family + participation_family — mechanisms 6, 7, 11, 12, 13, 28, 29,
32, 38, 39 and 8, 9, 10, 43.

test_machinery_* build deterministic MarketState scenarios (tick 0.1, a displayed 5-level book and
a tape carried the way the tape engine writes it: cumulative volume, interval volume / trades /
direction, a tape clock) and feed them through a StateHistory exactly as the engine does (history
holds the states *before* the current one).  Per mechanism: a scenario that drives the score ≥
active_threshold, a null / mirror scenario that stays < build_threshold, and a check that the
evidence is computed from the inputs (changes when they change); plus the lifecycle building →
active → confirmed → resolved / failed through ``Mechanism.update``.  test_realdata_* run the
committed closed-market capture through the engine.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from tower.mechanics import REGISTRY, all_mechanisms
from tower.mechanics.base import Mechanism, StateHistory
from tower.mechanics import accumulation_family as af
from tower.mechanics import participation_family as pf
from tower.state import MarketState

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)
TICK = 0.1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")

BIDS = [(10.0, 1000.0), (9.9, 800.0), (9.8, 600.0), (9.7, 500.0), (9.6, 400.0)]
ASKS = [(10.1, 1000.0), (10.2, 800.0), (10.3, 600.0), (10.4, 500.0), (10.5, 400.0)]

ACC_NAMES = ["passive_accumulation", "passive_distribution", "block_absorption", "inventory_rebalancing",
             "adverse_retreat", "stealth_accumulation", "stealth_distribution", "absorption", "accumulation_like",
             "distribution_like"]
PART_NAMES = ["pegged_repricing", "participation_footprint", "metaorder_trajectory", "metaorder_impact"]
BASELINE_KEYS = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio", "price_only_response", "volume_only_response")


# ----------------------------------------------------------------------------- builders
def _t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def S(s: float, bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], *,
      tv: Optional[float] = None, iv: Optional[float] = None, itr: Optional[float] = None,
      dirn: Optional[float] = None, intensity: Optional[float] = None, ltp: Optional[float] = None,
      cross: Optional[Dict[str, Any]] = None, tick: Optional[float] = TICK, clock: bool = True,
      impact: Optional[float] = None) -> MarketState:
    """A MarketState carrying what the book / tape engines write.  The tape clock is stamped with
    the state time (as a cumulative feed that re-serves unchanged totals does)."""
    ms = MarketState(symbol="SYN", t=_t(s), tick_size=tick)
    ms.bids = [(float(p), float(q)) for p, q in bids]
    ms.asks = [(float(p), float(q)) for p, q in asks]
    if bids:
        ms.best_bid, ms.bid_qty1 = ms.bids[0]
    if asks:
        ms.best_ask, ms.ask_qty1 = ms.asks[0]
    if bids and asks:
        ms.spread = round(ms.best_ask - ms.best_bid, 6)
        ms.spread_ticks = round(ms.spread / TICK, 6)
        ms.mid = (ms.best_ask + ms.best_bid) / 2.0
    ms.empty_book = not (bids or asks)
    ms.one_sided = bool(bids) != bool(asks)
    ms.trade_volume = tv
    ms.interval_volume = iv
    ms.interval_trades = itr
    ms.trade_flow_direction = dirn
    ms.trade_intensity = intensity
    ms.ltp = ltp
    ms.price_impact = impact
    if cross:
        ms.cross = dict(cross)
    if tv is not None and clock:
        ms.session_state["tape"] = {"tape_clock": _t(s).isoformat()}
    return ms


def run(mech: Mechanism, states: Sequence[MarketState], use_update: bool = False):
    hist = StateHistory()
    out = []
    for ms in states:
        r = mech.update(ms, hist) if use_update else mech.compute(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert not (isinstance(r.score, float) and math.isnan(r.score))
        hist.push(ms)
        out.append(r)
    return out


def shift(levels, ticks: float):
    return [(round(p + ticks * TICK, 6), q) for p, q in levels]


def scale(levels, factor: float):
    return [(p, q * factor) for p, q in levels]


def _check_common(r, expect_direction: Optional[int] = None):
    assert set(BASELINE_KEYS) <= set(r.baseline)
    assert "direction" in r.evidence
    assert r.evidence["direction"] in (-1, 0, 1)
    if expect_direction is not None:
        assert r.evidence["direction"] == expect_direction


# ----------------------------------------------------------------------------- registry / nulls
def test_machinery_registry_has_all_14():
    all_mechanisms()
    for n in ACC_NAMES + PART_NAMES:
        assert n in REGISTRY, n
    assert all(REGISTRY[n].family == "accumulation" for n in ACC_NAMES)
    assert all(REGISTRY[n].family == "participation" for n in PART_NAMES)


@pytest.mark.parametrize("name", ACC_NAMES + PART_NAMES)
def test_machinery_null_static_book_stays_below_build(name):
    """A static displayed book with a tape that never trades builds none of the 14 mechanisms
    and names the tape inputs it cannot see."""
    states = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(60)]
    rs = run(REGISTRY[name](), states)
    assert max(r.score for r in rs) < REGISTRY[name].build_threshold
    _check_common(rs[-1])


@pytest.mark.parametrize("name", ACC_NAMES + PART_NAMES)
def test_machinery_empty_book_is_missing_not_zero(name):
    """An empty book without a tape yields score 0 with the missing inputs named."""
    states = [S(5 * i, [], []) for i in range(12)]
    rs = run(REGISTRY[name](), states)
    assert rs[-1].score == 0.0
    assert rs[-1].evidence.get("missing"), name


def test_machinery_tape_rows_dedupe_repolled_interval():
    """A cumulative feed re-serving unchanged totals advances the tape clock: the same interval
    polled five times is one row, not five."""
    st = [S(0, BIDS, ASKS, tv=1000.0)] + [S(5 * i, BIDS, ASKS, tv=1300.0, iv=300.0, itr=1, dirn=-1.0)
                                         for i in range(1, 6)]
    hist = StateHistory()
    for ms in st[:-1]:
        hist.push(ms)
    fr = af.Frame(st[-1], hist)
    assert len([r for r in fr.tape_rows(60) if r["volume"]]) == 5      # clock-keyed identity
    rows = [r for r in af.tape_rows(fr, 60) if r["volume"]]
    assert len(rows) == 1 and rows[0]["volume"] == 300.0
    fs = af.flow_summary(af.classified_rows(fr, 60))
    assert fs["sell"] == 300.0 and fs["signed"] == -300.0


def test_machinery_tape_rows_interval_leaves_window():
    """A cumulative feed keeps re-serving the last interval with an advancing tape clock: a trade
    at t = 100 must be inside a 120-s window at t = 150 and *gone* at t = 400 — a re-poll inside
    the window is not a new row at the window's first state."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(20)]
    st.append(S(100, BIDS, [(10.1, 500.0)] + ASKS[1:], tv=1500.0, iv=500.0, itr=1, dirn=1.0))
    st += [S(105 + 5 * j, BIDS, ASKS, tv=1500.0, iv=500.0, itr=1, dirn=1.0) for j in range(60)]
    hist = StateHistory()
    seen: Dict[float, List[Tuple[float, float]]] = {}
    for ms in st:
        fr = af.Frame(ms, hist)
        s = (ms.t - T0).total_seconds()
        if s in (150.0, 219.0, 220.0, 225.0, 400.0):
            seen[s] = [((r["t"] - T0).total_seconds(), r["volume"]) for r in af.classified_rows(fr, 120)]
        hist.push(ms)
    assert seen[150.0] == [(100.0, 500.0)]
    assert seen[220.0] == [(100.0, 500.0)]          # the cutoff is inclusive: still inside at exactly 120 s
    assert seen[225.0] == [] and seen[400.0] == []
    # the same through a mechanism: the flow is missing at t = 400, not a 300-s-old print
    rs = run(af.Absorption(), st)
    assert rs[30].evidence["signed_flow"] == 500.0                  # t = 150
    assert rs[-1].score == 0.0 and rs[-1].evidence["missing"]


def test_machinery_tape_rows_flagged_rows_dropped():
    """The day's first row and a monotone break carry no interval: dropped, and their re-polls too."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(4)]
    first = S(20, BIDS, ASKS, tv=5000.0, iv=5000.0, itr=40, dirn=1.0)
    first.session_state["tape"]["last_first_row"] = True
    repoll = S(25, BIDS, ASKS, tv=5000.0, iv=5000.0, itr=40, dirn=1.0)
    repoll.session_state["tape"]["last_first_row"] = True
    real = S(30, BIDS, ASKS, tv=5300.0, iv=300.0, itr=1, dirn=1.0)
    brk = S(35, BIDS, ASKS, tv=4000.0, iv=-1300.0, itr=-30, dirn=None)
    brk.session_state["tape"]["last_monotone_break"] = True
    st += [first, repoll, real, brk]
    hist = StateHistory()
    for ms in st[:-1]:
        hist.push(ms)
    rows = [r for r in af.tape_rows(af.Frame(st[-1], hist), 60) if r["volume"] is not None]
    assert [(r["volume"], (r["t"] - T0).total_seconds()) for r in rows] == [(300.0, 30.0)]
    assert rows[0]["state"] is real


def test_machinery_tape_rows_negative_interval_dropped():
    """An unflagged negative interval (a source reset the tape engine did not mark) is not a
    trade: it is dropped from the rows, so no flow sum can go negative or lose its sign."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(4)]
    st.append(S(20, BIDS, ASKS, tv=1300.0, iv=300.0, itr=1, dirn=1.0))
    st.append(S(25, BIDS, ASKS, tv=900.0, iv=-400.0, itr=-1, dirn=-1.0))
    st.append(S(30, BIDS, ASKS, tv=1100.0, iv=200.0, itr=1, dirn=1.0))
    hist = StateHistory()
    for m in st[:-1]:
        hist.push(m)
    fr = af.Frame(st[-1], hist)
    rows = af.classified_rows(fr, 60)
    assert [(r["volume"], r["direction"]) for r in rows] == [(300.0, 1.0), (200.0, 1.0)]
    fs = af.flow_summary(rows)
    assert fs["total"] == 500.0 and fs["signed"] == 500.0 and fs["one_sided"] == 1.0
    # a cumulative delta across the reset is a phantom: None, the caller falls back to the rows
    assert af.cum_delta(st) is None and af.cum_delta(st[:5]) == 300.0
    assert af.volume_since(fr, st[3]) == 500.0


def test_machinery_block_absorption_reset_inside_baseline_is_not_a_phantom_rate():
    """A cumulative reset inside the baseline (1500 → 100 at t = 300, then counting on from
    there) must not read as a negative or phantom baseline delta: the rows carry the 13 real
    100-share intervals, the reset row itself is dropped."""
    st = _block_scenario()
    for m in st:
        s = (m.t - T0).total_seconds()
        if s >= 300:
            m.trade_volume = m.trade_volume - 1400.0
            if 300 <= s < 360:                       # the reset row and its re-polls
                m.interval_volume, m.interval_trades = -1400.0, -14
    ref = run(af.BlockAbsorption(), _block_scenario())[-1]
    r = run(af.BlockAbsorption(), st)[-1]
    assert r.evidence["size_source"] == "baseline_rate" and r.evidence["burst_volume"] == pytest.approx(6100.0)
    assert r.evidence["baseline_volume"] == pytest.approx(ref.evidence["baseline_volume"] - 100.0)
    assert r.evidence["baseline_rate_per_s"] > 0 and r.score >= 0.6


def test_machinery_adverse_retreat_vacated_side_is_the_strongest_retreat():
    """After a buy the whole ask side disappears while bids remain: no spread exists any more,
    which is the strongest widening there is — a full pull scores fully, it is not "missing"."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(10)]
    st.append(S(50, BIDS, [(10.1, 800.0)] + ASKS[1:], tv=1200.0, iv=200.0, itr=1, dirn=1.0))
    st += [S(55 + 5 * j, BIDS, [], tv=1200.0, iv=200.0, itr=1, dirn=1.0) for j in range(3)]
    r = run(af.AdverseRetreat(), st)[-1]
    assert not r.evidence.get("missing") and r.evidence["hit_side_vacated"] is True
    assert r.evidence["pulled_share"] == pytest.approx((3300.0 - 200.0) / 3300.0)
    assert r.evidence["components"]["spread"] == 1.0 and r.score >= 0.9
    assert r.evidence["direction"] == 1
    # the book itself unobservable (both sides empty): nothing can be called pulled
    st2 = st[:11] + [S(55 + 5 * j, [], [], tv=1200.0, iv=200.0, itr=1, dirn=1.0) for j in range(3)]
    r2 = run(af.AdverseRetreat(), st2)[-1]
    assert r2.score == 0.0 and r2.evidence["missing"]


def test_machinery_adverse_retreat_counts_later_prints_when_trade_state_is_oldest():
    """The classified trade is the oldest state held; a later unclassified 300-share print is
    still subtracted from the depth that vanished."""
    st = [S(50, BIDS, [(10.1, 800.0)] + ASKS[1:], tv=1200.0, iv=200.0, itr=1, dirn=1.0),
          S(55, BIDS, [(10.1, 800.0)] + ASKS[1:], tv=1500.0, iv=300.0, itr=1, dirn=None),
          S(60, BIDS, [(10.3, 100.0)], tv=1500.0, iv=300.0, itr=1, dirn=None)]
    r = run(af.AdverseRetreat(), st)[-1]
    assert r.evidence["traded_since_pre"] == pytest.approx(300.0)
    assert r.evidence["pulled_share"] == pytest.approx((3100.0 - 100.0 - 300.0) / 3100.0)


def test_machinery_passive_one_sided_book_names_missing_not_half():
    """Sells absorbed at a bid while no ask is displayed: the mid flatness and the two-sided depth
    share are unobservable — named as missing, score 0, not a 0.5 stand-in for each."""
    st = [S((m.t - T0).total_seconds(), m.bids, [], tv=m.trade_volume, iv=m.interval_volume,
            itr=m.interval_trades, dirn=m.trade_flow_direction) for m in _passive_scenario("bid")]
    r = run(af.PassiveAccumulation(), st)[-1]
    assert r.evidence["absorbed_share"] == pytest.approx(1.0) and r.evidence["refill_ratio"] > 0.9
    assert r.evidence["flat"] is None and r.evidence["depth_share_change"] is None
    assert r.score == 0.0 and r.evidence["direction"] == 0
    assert any("mid" in m for m in r.evidence["missing"]) and any("depth" in m for m in r.evidence["missing"])


def test_machinery_participation_bucket_cum_reset_is_not_a_phantom():
    """A cumulative reset inside a bucket (1800 → 100, then counting on: 300, 500, …) leaves
    that bucket unobservable (its only interval was destroyed by the reset) and the following
    buckets at their true 200 — never a phantom or negative volume."""
    st = _participation_scenario()
    for m in st[5:]:
        m.trade_volume -= 1900.0
    st[5].interval_volume, st[5].interval_trades = -1900.0, -19
    r = run(pf.ParticipationFootprint(), st)[-1]
    assert r.evidence["bucket_volumes"] == [200.0] * 9 and r.evidence["buckets"] == 9
    assert r.evidence["mode"] == "market" and r.evidence["coverage"] == 1.0 and r.score >= 0.6
    # the same window without the reset: 10 buckets, otherwise identical ratios
    ref = run(pf.ParticipationFootprint(), _participation_scenario())[-1]
    assert ref.evidence["buckets"] == 10 and ref.evidence["mean_ratio"] == pytest.approx(r.evidence["mean_ratio"])
    # a bucket no state landed in (a 120-s poll gap) is unobserved, not a silent zero of coverage
    gap = [m for m in _participation_scenario() if (m.t - T0).total_seconds() != 300.0]
    rg = run(pf.ParticipationFootprint(), gap)[-1]
    vols = rg.evidence["bucket_volumes"]
    assert rg.evidence["buckets"] == 9 and rg.evidence["coverage"] == 1.0 and 0.0 not in vols
    assert vols.count(400.0) == 1 and vols.count(200.0) == 8       # the delta after the gap spans two buckets
    assert 0.35 <= rg.score < ref.score


def test_machinery_duplicate_timestamp_pre_state_by_identity():
    """Two states share a timestamp (the trade's state and a book update right after it): the
    book *before* the trade is found by identity, so the traded volume is subtracted from the
    depth that vanished instead of being counted as pulled."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(10)]
    trade = S(50, BIDS, [(10.1, 800.0)] + ASKS[1:], tv=1200.0, iv=200.0, itr=1, dirn=1.0)
    after = S(50, BIDS, [(10.3, 200.0), (10.4, 200.0)], tv=1200.0, iv=200.0, itr=1, dirn=1.0)
    st += [trade, after]
    st += [S(55 + 5 * j, BIDS, [(10.3, 200.0), (10.4, 200.0)], tv=1200.0, iv=200.0, itr=1, dirn=1.0)
           for j in range(3)]
    r = run(af.AdverseRetreat(), st)[-1]
    assert r.evidence["depth_topk_pre"] == pytest.approx(3300.0)
    assert r.evidence["traded_since_pre"] == pytest.approx(200.0)
    assert r.evidence["pulled_share"] == pytest.approx((3300.0 - 400.0 - 200.0) / 3300.0)
    # the impact path's origin mid is the pre-trade mid as well
    hist = StateHistory()
    for ms in st[:-1]:
        hist.push(ms)
    path = pf.flow_path(af.Frame(st[-1], hist), 600, TICK)
    assert path["mid0"] == pytest.approx(10.05)
    assert af.state_before(af.Frame(st[-1], hist), trade) is st[9]
    assert af.state_before(af.Frame(st[-1], hist), st[0]) is None


def test_machinery_missing_inputs_score_zero_not_a_default():
    """When an input the rule needs is unobservable the score is 0 with the input named — never a
    substituted constant (a 0.5 "neutral" factor) times the rest."""
    # stealth: no trade_intensity and no interval trades anywhere → low-intensity unobservable
    st = [S((m.t - T0).total_seconds(), m.bids, m.asks, tv=m.trade_volume, iv=m.interval_volume, itr=None,
            dirn=m.trade_flow_direction, intensity=None) for m in _stealth_scenario()]
    r = run(af.StealthAccumulation(), st)[-1]
    assert r.evidence["net_share"] == pytest.approx(1.0) and r.evidence["low_intensity"] is None
    assert "trade_intensity/interval_trades" in r.evidence["missing"]
    assert r.score == 0.0 and r.evidence["direction"] == 0
    # absorption: a one-level book without a tick size → the best-price hold cannot be judged
    st = []
    for m in _absorption_scenario():
        st.append(S((m.t - T0).total_seconds(), m.bids[:1], m.asks[:1], tv=m.trade_volume, iv=m.interval_volume,
                    itr=m.interval_trades, dirn=m.trade_flow_direction, tick=None))
    r = run(af.Absorption(), st)[-1]
    assert r.evidence["flow_vs_touch"] == pytest.approx(3.0) and r.evidence["retreat_ticks"] is None
    assert "tick_size" in r.evidence["missing"] and r.score == 0.0
    # block absorption: nothing in the burst carries a direction → one-sidedness unobservable
    st = _block_scenario()
    for m in st:
        if (m.t - T0).total_seconds() >= 845:
            m.trade_flow_direction = None
    r = run(af.BlockAbsorption(), st)[-1]
    assert r.evidence["volume_ratio"] > 10 and r.evidence["one_sided"] is None
    assert "trade_flow_direction" in r.evidence["missing"] and r.score == 0.0
    # passive accumulation without a tick: the hold / flatness cannot be judged
    st = [S((m.t - T0).total_seconds(), m.bids[:1], m.asks[:1], tv=m.trade_volume, iv=m.interval_volume,
            itr=m.interval_trades, dirn=m.trade_flow_direction, tick=None) for m in _passive_scenario()]
    r = run(af.PassiveAccumulation(), st)[-1]
    assert r.evidence["absorbed_share"] == pytest.approx(1.0) and r.evidence["missing"] == ["tick_size"]
    assert r.score == 0.0


def test_machinery_composite_ignores_uninformative_instants():
    """Instants at which every component lacks its inputs (a book without a tape) add no point to
    the composite window: they are neither zeros dragging the mean down nor span."""
    quiet = [S(5 * i, BIDS, ASKS) for i in range(20)]                     # 0..95 s, no tape at all
    scen = [S((m.t - T0).total_seconds() + 100.0, m.bids, m.asks, tv=m.trade_volume, iv=m.interval_volume,
              itr=m.interval_trades, dirn=m.trade_flow_direction) for m in _passive_scenario("bid", n=60)]
    rs = run(af.AccumulationLike(), quiet + scen)
    assert all(r.score == 0.0 and r.evidence.get("missing") for r in rs[:20])
    # 59 points: the scenario's own first state carries no interval yet, so it adds none either
    assert rs[-1].evidence["points"] == 59 and rs[-1].evidence["span_s"] == pytest.approx(580.0)
    ref = run(af.AccumulationLike(), _passive_scenario("bid", n=60))[-1]
    assert ref.evidence["points"] == 59
    # the composite mean is the mean over the informative instants only (rs[20] is the scenario's
    # first, interval-less state): 20 silent zeros would have scaled it by 59 / 79
    informative = [r.evidence["strongest_now"] for r in rs[21:]]
    assert len(informative) == 59
    assert rs[-1].evidence["mean_strongest"] == pytest.approx(sum(informative) / len(informative))
    assert rs[-1].score >= 0.6


def test_machinery_episode_direction_not_inherited_by_next_episode():
    """A directed mechanism judges an episode's outcome by the direction *that* episode carried; a
    later episode without a direction resolves (never fails) even after a directed one."""
    class Scripted(af.DirectedMechanism):
        name, family = "scripted", "test"

        def __init__(self, script):
            super().__init__()
            self.script = list(script)

        def compute(self, ms, hist):
            score, d = self.script.pop(0)
            from tower.mechanics.base import MechanismReading
            return MechanismReading(self.name, self.family, score, "inactive", {"direction": d}, {})

    # episode 1 (direction +1) resolves on a rising mid; episode 2 carries direction 0 and the mid falls
    script = [(0.8, 1), (0.8, 1), (0.0, 0), (0.8, 0), (0.8, 0), (0.0, 0)]
    mids = [10.0, 10.0, 10.5, 10.5, 10.5, 9.0]
    mech = Scripted(script)
    hist = StateHistory()
    out = []
    for i, m in enumerate(mids):
        ms = S(5 * i, shift(BIDS, (m - 10.05) / TICK), shift(ASKS, (m - 10.05) / TICK))
        out.append(mech.update(ms, hist))
        hist.push(ms)
    assert [o.state for o in out] == ["active", "active", "resolved", "active", "active", "resolved"]
    assert out[1].evidence["episode_direction"] == 1 and out[4].evidence["episode_direction"] == 0


@pytest.mark.parametrize("name", ACC_NAMES + PART_NAMES)
def test_machinery_deterministic_same_inputs_same_reading(name):
    """Two fresh instances over the same states produce byte-identical readings."""
    import json
    from tower.state import _jsonable
    st = _passive_scenario("bid", n=40) if name != "participation_footprint" else _participation_scenario()
    a = run(REGISTRY[name](), st, use_update=True)
    b = run(REGISTRY[name](), st, use_update=True)
    for x, y in zip(a, b):
        assert json.dumps(_jsonable(x.evidence), sort_keys=True) == json.dumps(_jsonable(y.evidence), sort_keys=True)
        assert x.score == y.score and x.state == y.state


# ============================================================================= #6 / #7 passive
def _passive_scenario(side: str = "bid", n: int = 30, refill: bool = True, sell: float = 300.0,
                      price_move: int = 0) -> List[MarketState]:
    """Every 20 s a print of ``sell`` hits the ``side`` touch (qty 1000 → 700), the next poll
    shows it refilled to 1000 — or, without ``refill``, the touch drains steadily from 1000 to
    100 and is never rebuilt; the other side thins out over the window so the visible share
    of ``side`` rises.  ``price_move`` shifts the whole book at the last state."""
    st = []
    tv = 1000.0
    for i in range(n):
        s = 10 * i
        f = 1.0 - 0.3 * i / n
        hit = i % 2 == 1
        if hit:
            tv += sell
        d = -1.0 if side == "bid" else 1.0
        tape = dict(tv=tv, iv=(sell if i else None), itr=(1 if i else None), dirn=(d if i else None))
        touch_q = (700.0 if hit else 1000.0) if refill else (1000.0 - 900.0 * i / (n - 1))
        if side == "bid":
            b, a = [(10.0, touch_q)] + BIDS[1:], scale(ASKS, f)
        else:
            b, a = scale(BIDS, f), [(10.1, touch_q)] + ASKS[1:]
        mv = price_move if i == n - 1 else 0
        st.append(S(s, shift(b, mv), shift(a, mv), **tape))
    return st


def test_machinery_passive_accumulation_activates():
    r = run(af.PassiveAccumulation(), _passive_scenario("bid"))[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "bid"
    assert r.evidence["absorbed_share"] == pytest.approx(1.0)
    assert r.evidence["absorbed_volume"] == pytest.approx(15 * 300.0)
    assert r.evidence["refills"] >= 10 and r.evidence["refill_ratio"] > 0.9
    assert r.evidence["hold"] == 1.0 and r.evidence["flat"] == 1.0
    assert r.evidence["depth_share_change"] > 0
    _check_common(r, 1)


def test_machinery_passive_distribution_activates_and_mirror_is_null():
    rd = run(af.PassiveDistribution(), _passive_scenario("ask"))[-1]
    assert rd.score >= 0.6 and rd.evidence["side"] == "ask"
    _check_common(rd, -1)
    # buys into the ask are not passive accumulation and vice versa
    assert run(af.PassiveAccumulation(), _passive_scenario("ask"))[-1].score < 0.35
    assert run(af.PassiveDistribution(), _passive_scenario("bid"))[-1].score < 0.35


def test_machinery_passive_accumulation_evidence_changes():
    full = run(af.PassiveAccumulation(), _passive_scenario())[-1]
    no_refill = run(af.PassiveAccumulation(), _passive_scenario(refill=False))[-1]
    assert no_refill.evidence["refills"] == 0 and no_refill.evidence["refill_ratio"] == 0.0
    assert no_refill.evidence["consumed_qty"] == pytest.approx(900.0)
    assert no_refill.score == 0.0 < full.score
    small = run(af.PassiveAccumulation(), _passive_scenario(sell=50.0))[-1]
    assert small.evidence["absorbed_vs_touch"] < full.evidence["absorbed_vs_touch"] and small.score < full.score
    moved = run(af.PassiveAccumulation(), _passive_scenario(price_move=-4))[-1]
    assert moved.evidence["flat"] == 0.0 and moved.score == 0.0
    # no tape at all → missing, never a silent zero
    st = [S((m.t - T0).total_seconds(), m.bids, m.asks) for m in _passive_scenario()]
    rn = run(af.PassiveAccumulation(), st)[-1]
    assert rn.score == 0.0 and rn.evidence["missing"]


# ============================================================================= #11 block_absorption
def _block_scenario(size: float = 6000.0, move: int = 0, baseline: bool = True) -> List[MarketState]:
    """900 s of a trickle (100 shares per minute) then one print of ``size``; the book shifts by
    ``move`` ticks at the print."""
    st = []
    tv = 1000.0
    for i in range(0, 181):
        s = 5 * i
        if i % 12 == 0 and i:
            tv += 100.0
        started = tv > 1000.0
        st.append(S(s, BIDS, ASKS, tv=tv if baseline else 1000.0, iv=(100.0 if started and baseline else None),
                    itr=(1 if started and baseline else None), dirn=(1.0 if started and baseline else None)))
    tv += size
    st.append(S(905, shift(BIDS, move), shift(ASKS, move), tv=tv, iv=size, itr=1, dirn=1.0))
    return st


def test_machinery_block_absorption_activates():
    r = run(af.BlockAbsorption(), _block_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["size_source"] == "baseline_rate"
    assert r.evidence["burst_volume"] == pytest.approx(6100.0)
    assert r.evidence["volume_ratio"] > 10
    assert r.evidence["mid_move_ticks"] == 0.0 and r.evidence["price_factor"] == 1.0
    assert r.evidence["hit_side"] == "ask" and r.evidence["largest_vs_touch"] == pytest.approx(6.0)
    _check_common(r, -1)


def test_machinery_block_absorption_price_move_and_size_change_evidence():
    moved = run(af.BlockAbsorption(), _block_scenario(move=3))[-1]
    assert moved.evidence["mid_move_ticks"] == pytest.approx(3.0) and moved.score == 0.0
    small = run(af.BlockAbsorption(), _block_scenario(size=150.0))[-1]
    assert small.evidence["volume_ratio"] < 3 and small.score < 0.35
    mid = run(af.BlockAbsorption(), _block_scenario(size=400.0))[-1]
    assert 3 < mid.evidence["volume_ratio"] < 10
    assert small.score < mid.score < run(af.BlockAbsorption(), _block_scenario())[-1].score
    # without a traded baseline the size is judged against the touch it hit
    nb = run(af.BlockAbsorption(), _block_scenario(baseline=False))[-1]
    assert nb.evidence["size_source"] == "touch_qty" and nb.evidence["volume_ratio"] is None
    assert nb.score >= 0.6


# ============================================================================= #12 inventory_rebalancing
def _rebalance_scenario(n: int = 12, alternate: bool = True, drift: bool = False,
                        sizes: Optional[Sequence[float]] = None) -> List[MarketState]:
    st = [S(0, BIDS, ASKS, tv=1000.0)]
    tv = 1000.0
    for i in range(1, n + 1):
        d = (1.0 if i % 2 else -1.0) if alternate else 1.0
        q = sizes[(i - 1) % len(sizes)] if sizes else 200.0
        tv += q
        k = i if drift else (1 if i % 2 else 0)
        st.append(S(20 * i, shift(BIDS, k), shift(ASKS, k), tv=tv, iv=q, itr=1, dirn=d))
    return st


def test_machinery_inventory_rebalancing_activates():
    r = run(af.InventoryRebalancing(), _rebalance_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["rows"] == 12 and r.evidence["flips"] == 11 and r.evidence["flip_rate"] == 1.0
    assert r.evidence["symmetry"] == pytest.approx(1.0)
    assert r.evidence["reversion"] > 0.85
    _check_common(r, 0)


def test_machinery_inventory_rebalancing_null_and_evidence_changes():
    one_way = run(af.InventoryRebalancing(), _rebalance_scenario(alternate=False))[-1]
    assert one_way.evidence["flips"] == 0 and one_way.score == 0.0
    drift = run(af.InventoryRebalancing(), _rebalance_scenario(drift=True))[-1]
    assert drift.evidence["reversion"] == pytest.approx(0.0) and drift.score < run(
        af.InventoryRebalancing(), _rebalance_scenario())[-1].score
    asym = run(af.InventoryRebalancing(), _rebalance_scenario(sizes=[900.0, 100.0]))[-1]
    assert asym.evidence["symmetry"] == pytest.approx(0.2) and asym.score < 0.35


# ============================================================================= #13 adverse_retreat
def _adverse_scenario(pull: float = 1.0, widen: int = 2, wait_s: float = 20.0) -> List[MarketState]:
    """A 200-share buy lifts the ask at t = 50 s; afterwards the ask side keeps only (1 − pull) of
    its depth, re-posted ``widen`` ticks higher."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(10)]
    st.append(S(50, BIDS, [(10.1, 800.0)] + ASKS[1:], tv=1200.0, iv=200.0, itr=1, dirn=1.0))
    kept = [(round(p + widen * TICK, 6), q * (1.0 - pull)) for p, q in ASKS] if pull < 1.0 \
        else [(round(10.1 + widen * TICK, 6), 200.0), (round(10.2 + widen * TICK, 6), 200.0)]
    s = 55.0
    while s <= 50.0 + wait_s:
        st.append(S(s, BIDS, kept, tv=1200.0, iv=200.0, itr=1, dirn=1.0))
        s += 5.0
    return st


def test_machinery_adverse_retreat_activates():
    r = run(af.AdverseRetreat(), _adverse_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["hit_side"] == "ask"
    assert r.evidence["traded_since_pre"] == pytest.approx(200.0)
    assert r.evidence["pulled_share"] == pytest.approx((3300.0 - 400.0 - 200.0) / 3300.0)
    assert r.evidence["spread_widening_ticks"] == pytest.approx(2.0)
    assert r.evidence["recency"] == 1.0
    _check_common(r, 1)


def test_machinery_adverse_retreat_null_and_evidence_changes():
    none = run(af.AdverseRetreat(), _adverse_scenario(pull=0.0, widen=0))[-1]
    assert none.evidence["pulled_share"] == pytest.approx(0.0) and none.score == 0.0
    half = run(af.AdverseRetreat(), _adverse_scenario(pull=0.5, widen=0))[-1]
    full = run(af.AdverseRetreat(), _adverse_scenario())[-1]
    assert 0 < half.evidence["pulled_share"] < full.evidence["pulled_share"] and half.score < full.score
    assert half.evidence["spread_widening_ticks"] == 0.0
    late = run(af.AdverseRetreat(), _adverse_scenario(wait_s=170.0))[-1]
    assert late.evidence["recency"] < 0.1 and late.score < full.score
    # sells hitting the bid, bid pulled → direction −1
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(10)]
    st.append(S(50, [(10.0, 800.0)] + BIDS[1:], ASKS, tv=1200.0, iv=200.0, itr=1, dirn=-1.0))
    st += [S(55 + 5 * j, [(9.8, 200.0)], ASKS, tv=1200.0, iv=200.0, itr=1, dirn=-1.0) for j in range(4)]
    rb = run(af.AdverseRetreat(), st)[-1]
    assert rb.evidence["hit_side"] == "bid" and rb.evidence["direction"] == -1 and rb.score >= 0.6


# ============================================================================= #28 / #29 stealth
def _stealth_scenario(d: float = 1.0, n: int = 11, intensity: Optional[float] = 1.0, gap_s: float = 60.0,
                      mixed: bool = False, move: int = 0, history_intensity: Optional[float] = None
                      ) -> List[MarketState]:
    """One 100-share print every ``gap_s`` over 600 s at low intensity, flat book; ``mixed`` flips
    every third print; ``history_intensity`` prepends 1200 s of history carrying that intensity."""
    st = []
    if history_intensity is not None:
        st = [S(-1200 + 20 * i, BIDS, ASKS, tv=1000.0, intensity=history_intensity) for i in range(60)]
    st.append(S(0, BIDS, ASKS, tv=1000.0, intensity=intensity))
    tv = 1000.0
    for i in range(1, n):
        tv += 100.0
        dd = -d if (mixed and i % 3 == 0) else d
        k = move if i == n - 1 else 0
        st.append(S(gap_s * i, shift(BIDS, k), shift(ASKS, k), tv=tv, iv=100.0, itr=1, dirn=dd, intensity=intensity))
    return st


def test_machinery_stealth_accumulation_activates():
    r = run(af.StealthAccumulation(), _stealth_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["net_share"] == pytest.approx(1.0) and r.evidence["persistence"] == 1.0
    assert r.evidence["rows"] == 10 and r.evidence["low_intensity"] == 1.0
    assert r.evidence["low_intensity_source"] == "absolute" and r.evidence["flat"] == 1.0
    _check_common(r, 1)


def test_machinery_stealth_distribution_activates_and_mirror_null():
    rd = run(af.StealthDistribution(), _stealth_scenario(-1.0))[-1]
    assert rd.score >= 0.6
    _check_common(rd, -1)
    assert run(af.StealthAccumulation(), _stealth_scenario(-1.0))[-1].score == 0.0
    assert run(af.StealthDistribution(), _stealth_scenario(1.0))[-1].score == 0.0


def test_machinery_stealth_evidence_changes():
    full = run(af.StealthAccumulation(), _stealth_scenario())[-1]
    loud = run(af.StealthAccumulation(), _stealth_scenario(intensity=30.0))[-1]
    assert loud.evidence["low_intensity"] == 0.0 and loud.score == 0.0
    mixed = run(af.StealthAccumulation(), _stealth_scenario(mixed=True))[-1]
    assert mixed.evidence["net_share"] < 1.0 and mixed.evidence["persistence"] < 1.0 and mixed.score < full.score
    moved = run(af.StealthAccumulation(), _stealth_scenario(move=4))[-1]
    assert moved.evidence["flat"] == 0.0 and moved.score == 0.0
    # with the symbol's own longer history the intensity is judged relative to it
    rel_low = run(af.StealthAccumulation(), _stealth_scenario(intensity=2.0, history_intensity=10.0))[-1]
    assert rel_low.evidence["low_intensity_source"] == "relative"
    assert rel_low.evidence["intensity_relative"] == pytest.approx(0.2) and rel_low.evidence["low_intensity"] == 1.0
    rel_high = run(af.StealthAccumulation(), _stealth_scenario(intensity=2.0, history_intensity=0.5))[-1]
    assert rel_high.evidence["intensity_relative"] == pytest.approx(4.0) and rel_high.score == 0.0
    # no intensity anywhere: rows per minute from interval trades
    st = _stealth_scenario(intensity=None)
    r = run(af.StealthAccumulation(), st)[-1]
    assert r.evidence["intensity_source"] == "interval_trades" and r.evidence["intensity"] == pytest.approx(1.0)


# ============================================================================= #32 absorption
def _absorption_scenario(refill: bool = True, one_sided: bool = True, size: float = 500.0, retreat: int = 0,
                         n: int = 6) -> List[MarketState]:
    """Buys of ``size`` hit the ask touch (1000) every 15 s; the touch shows 500 after each print
    and is back to 1000 at the next poll when ``refill``."""
    st = [S(0, BIDS, ASKS, tv=1000.0)]
    tv = 1000.0
    for i in range(1, n + 1):
        tv += size
        aq = 500.0 if i % 2 else (1000.0 if refill else 500.0)
        d = 1.0 if (one_sided or i % 2) else -1.0
        st.append(S(15 * i, BIDS, [(10.1, aq)] + ASKS[1:], tv=tv, iv=size, itr=1, dirn=d))
    final = [(10.1, 1000.0 if refill else 300.0)] + ASKS[1:]
    st.append(S(15 * (n + 1), BIDS, shift(final, retreat), tv=tv, iv=size, itr=1, dirn=1.0))
    return st


def test_machinery_absorption_activates():
    r = run(af.Absorption(), _absorption_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["hit_side"] == "ask" and r.evidence["one_sided"] == 1.0
    assert r.evidence["flow_vs_touch"] == pytest.approx(3.0) and r.evidence["touch_ref"] == 1000.0
    assert r.evidence["refill_ratio"] == 1.0 and r.evidence["refills"] == 3
    assert r.evidence["refill_window"] == pytest.approx(1.0) and r.evidence["refill_now"] == pytest.approx(1.0)
    # the score does not flicker with the poll phase: at the just-consumed poll it stays active
    assert run(af.Absorption(), _absorption_scenario()[:-1])[-1].score >= 0.6
    assert r.evidence["retreat_ticks"] == 0.0 and r.evidence["price_factor"] == 1.0
    _check_common(r, -1)


def test_machinery_absorption_null_and_evidence_changes():
    full = run(af.Absorption(), _absorption_scenario())[-1]
    nr = run(af.Absorption(), _absorption_scenario(refill=False))[-1]
    assert nr.evidence["refill_ratio"] == pytest.approx(0.3) and nr.score == 0.0
    bal = run(af.Absorption(), _absorption_scenario(one_sided=False))[-1]
    assert bal.evidence["one_sided"] < 0.5 and bal.score < 0.35
    small = run(af.Absorption(), _absorption_scenario(size=100.0))[-1]
    assert small.evidence["flow_vs_touch"] == pytest.approx(0.6) and small.score < full.score
    gave = run(af.Absorption(), _absorption_scenario(retreat=1))[-1]
    assert gave.evidence["retreat_ticks"] == pytest.approx(1.0) and gave.score < 1e-9
    # sells absorbed by the bid → direction +1
    st = []
    for m in _absorption_scenario():
        b = [(10.0, m.asks[0][1])] + BIDS[1:]
        st.append(S((m.t - T0).total_seconds(), b, ASKS, tv=m.trade_volume, iv=m.interval_volume,
                    itr=m.interval_trades, dirn=(-m.trade_flow_direction if m.trade_flow_direction else None)))
    rb = run(af.Absorption(), st)[-1]
    assert rb.evidence["hit_side"] == "bid" and rb.evidence["direction"] == 1 and rb.score >= 0.6


# ============================================================================= #38 / #39 composites
def test_machinery_accumulation_like_activates_on_persistent_passive_accumulation():
    rs = run(af.AccumulationLike(), _passive_scenario("bid", n=60))
    r = rs[-1]
    assert r.score >= 0.6
    assert r.evidence["persistence"] > 0.8 and r.evidence["mean_strongest"] > 0.6
    assert r.evidence["span_s"] >= 300 and r.evidence["span_factor"] == 1.0
    assert r.evidence["net_buy_share"] == pytest.approx(-1.0)          # sells being absorbed
    assert set(r.evidence["components"]) == {"passive_accumulation", "stealth_accumulation", "absorption"}
    _check_common(r, 1)
    # the composite needs span: the same strength over a short window is damped
    early = rs[8]
    assert early.evidence["span_factor"] < 1.0 and early.score < r.score


def test_machinery_distribution_like_activates_and_mirrors_are_null():
    rd = run(af.DistributionLike(), _passive_scenario("ask", n=60))[-1]
    assert rd.score >= 0.6 and rd.evidence["net_buy_share"] == pytest.approx(1.0)
    _check_common(rd, -1)
    # mirrors: the passive side is wrong for the other composite; the flow is made loud (high
    # trade intensity) so the stealth component — which the quiet one-way prints do satisfy —
    # is gated off and nothing is left for the mirror
    def loud(states):
        return [S((m.t - T0).total_seconds(), m.bids, m.asks, tv=m.trade_volume, iv=m.interval_volume,
                  itr=m.interval_trades, dirn=m.trade_flow_direction, intensity=40.0) for m in states]
    ra = run(af.AccumulationLike(), loud(_passive_scenario("ask", n=60)))[-1]
    assert ra.score < 0.35 and ra.evidence["components"]["passive_accumulation"]["score"] == 0.0
    rd2 = run(af.DistributionLike(), loud(_passive_scenario("bid", n=60)))[-1]
    assert rd2.score < 0.35 and rd2.evidence["components"]["passive_distribution"]["score"] == 0.0
    # loud flow does not disturb the right composite
    assert run(af.DistributionLike(), loud(_passive_scenario("ask", n=60)))[-1].score >= 0.6


def test_machinery_composite_absorption_counts_only_in_its_direction():
    """Buys absorbed by the ask (absorption direction −1) feed distribution_like; accumulation_like
    zeroes that component (what it keeps from this scenario is the stealth buying — steady
    low-intensity buys with a flat mid — which is its own, correctly attributed, reading)."""
    st = _absorption_scenario(n=20)
    ra = run(af.AccumulationLike(), st)[-1]
    rd = run(af.DistributionLike(), st)[-1]
    ca, cd = ra.evidence["components"], rd.evidence["components"]
    assert ca["absorption"]["score"] >= 0.6 and ca["absorption"]["direction"] == -1
    assert ca["absorption"]["effective"] == 0.0
    assert cd["absorption"]["effective"] == cd["absorption"]["score"] >= 0.6
    assert ca["passive_accumulation"]["score"] == 0.0
    assert ca["stealth_accumulation"]["effective"] == ra.evidence["strongest_now"]
    # with the buys loud (high intensity) nothing is left for accumulation_like
    loud = [S((m.t - T0).total_seconds(), m.bids, m.asks, tv=m.trade_volume, iv=m.interval_volume,
              itr=m.interval_trades, dirn=m.trade_flow_direction, intensity=40.0) for m in st]
    ra2 = run(af.AccumulationLike(), loud)[-1]
    rd2 = run(af.DistributionLike(), loud)[-1]
    assert ra2.score < 0.35 and rd2.score >= 0.6


# ============================================================================= #8 pegged_repricing
def _pegged_scenario(n: int = 8, same_size: bool = True, follow: bool = True, use_ltp: bool = True,
                     lag: bool = False) -> List[MarketState]:
    """The last traded price steps up one tick every 20 s; the bid is re-posted one tick behind
    it with the same size (500) — or with varying sizes — or does not follow at all.  With
    ``lag`` the ask leads instead (one tick at odd polls) and the bid re-posts two ticks behind
    it at the *next* poll."""
    st = []
    for i in range(n):
        b_steps = (i // 2) if lag else (i if follow else 0)
        a_steps = ((i + 1) // 2) if lag else i
        p = round(10.0 + b_steps * TICK, 6)
        q = 500.0 if same_size else 500.0 * (1 + 0.8 * (i % 3))
        b = [(p, q)] + [(round(p - k * TICK, 6), 800.0) for k in range(1, 4)]
        ap = round(10.0 + a_steps * TICK + 0.2, 6)
        a = [(ap, 900.0 if same_size else 900.0 * (1 + 0.6 * ((i + 1) % 3)))] + \
            [(round(ap + k * TICK, 6), 700.0) for k in range(1, 4)]
        ltp = round(10.0 + i * TICK + 0.1, 6) if use_ltp else None
        st.append(S(20 * i, b, a, tv=1000.0 + 100 * i, iv=100.0, itr=1, dirn=1.0, ltp=ltp))
    return st


def test_machinery_pegged_repricing_activates():
    r = run(pf.PeggedRepricing(), _pegged_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["side"] == "bid" and r.evidence["follows"] == 7 and r.evidence["moves"] == 7
    assert r.evidence["consistency"] == 1.0 and r.evidence["size_similarity"] == pytest.approx(1.0)
    assert r.evidence["reference"] == ["ltp"]
    assert r.evidence["drift_ticks"] == pytest.approx(7.0)
    _check_common(r, 1)


def test_machinery_pegged_repricing_null_and_evidence_changes():
    still = run(pf.PeggedRepricing(), _pegged_scenario(follow=False))[-1]
    assert still.evidence["sides"]["bid"]["moves"] == 0 and still.evidence["sides"]["bid"]["score"] == 0.0
    varied = run(pf.PeggedRepricing(), _pegged_scenario(same_size=False))[-1]
    full = run(pf.PeggedRepricing(), _pegged_scenario())[-1]
    assert varied.evidence["size_similarity"] < 0.5 and varied.score < full.score
    few = run(pf.PeggedRepricing(), _pegged_scenario(n=3))[-1]
    assert few.evidence["follows"] == 2 and few.score < full.score
    # without a last traded price the opposite best is the reference: the ask leads at odd polls,
    # the bid re-posts two ticks behind it at the next poll (a one-poll lag is a follow)
    nol = run(pf.PeggedRepricing(), _pegged_scenario(n=12, use_ltp=False, lag=True))[-1]
    assert nol.evidence["reference"] == ["opposite_best"] and nol.score >= 0.6
    assert nol.evidence["side"] == "bid" and nol.evidence["follows"] == 5 and nol.evidence["moves"] == 5
    assert nol.evidence["sides"]["ask"]["score"] < nol.score           # the leader is not the pegged side


def test_machinery_pegged_repricing_parallel_shift_is_not_pegging():
    """Without a last traded price, a whole-book shift (both bests move one tick in the same poll)
    makes each side the other's "leader": ambiguous, counted as moves but never as follows."""
    st = [S(20 * i, shift(BIDS, i), shift(ASKS, i), tv=1000.0 + 100 * i, iv=100.0, itr=1, dirn=1.0)
          for i in range(8)]
    r = run(pf.PeggedRepricing(), st)[-1]
    for sd in ("bid", "ask"):
        rec = r.evidence["sides"][sd]
        assert rec["moves"] == 7 and rec["parallel_moves"] == 7 and rec["follows"] == 0
    assert r.score == 0.0 and r.evidence["direction"] == 0
    # a static last traded price while both bests move: the reference did not lead either
    for m in st:
        m.ltp = 10.05
    r2 = run(pf.PeggedRepricing(), st)[-1]
    assert r2.evidence["follows"] == 0 and r2.score == 0.0
    # the same shift with the trade price stepping ahead of it *is* repricing around the tape
    assert run(pf.PeggedRepricing(), _pegged_scenario())[-1].score >= 0.6


# ============================================================================= #9 participation_footprint
def _participation_scenario(steady: bool = True, market: bool = True, n: int = 11, cross_share: float = 0.02
                            ) -> List[MarketState]:
    bursty = [50.0, 900.0, 0.0, 400.0, 10.0, 700.0, 0.0, 300.0, 1200.0, 20.0, 5.0]
    st = []
    tv = 1000.0
    for i in range(n):
        v = 200.0 if steady else bursty[i % len(bursty)]
        if i:
            tv += v
        cross = {"market_volume_60s": 200.0 / cross_share} if market else None
        st.append(S(60 * i, BIDS, ASKS, tv=tv, iv=(v if i else None), itr=(1 if i else None),
                    dirn=(1.0 if i else None), cross=cross))
    return st


def test_machinery_participation_footprint_market_mode_activates():
    r = run(pf.ParticipationFootprint(), _participation_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["mode"] == "market" and r.evidence["buckets"] == 10
    assert r.evidence["mean_ratio"] == pytest.approx(0.02)
    assert r.evidence["ratio_cv"] == pytest.approx(0.0) and r.evidence["coverage"] == 1.0
    assert r.evidence["volume_source"] == "cum_volume"
    _check_common(r, 1)


def test_machinery_participation_footprint_self_mode_and_null():
    own = run(pf.ParticipationFootprint(), _participation_scenario(market=False))[-1]
    assert own.evidence["mode"] == "self" and own.evidence["mean_ratio"] == pytest.approx(1.0) and own.score >= 0.6
    bursty = run(pf.ParticipationFootprint(), _participation_scenario(steady=False))[-1]
    assert bursty.evidence["ratio_cv"] > 1.0 and bursty.evidence["coverage"] < 1.0 and bursty.score < 0.35
    # the ratio follows the market volume, not the symbol's own level
    r5 = run(pf.ParticipationFootprint(), _participation_scenario(cross_share=0.05))[-1]
    assert r5.evidence["mean_ratio"] == pytest.approx(0.05)
    short = run(pf.ParticipationFootprint(), _participation_scenario(n=3))[-1]
    assert short.evidence["buckets"] == 2 and short.score < 0.35


# ============================================================================= #10 / #43 metaorder
def _metaorder_scenario(shape: str = "sqrt", n: int = 10, d: float = 1.0, size: float = 300.0,
                        mixed: bool = False) -> List[MarketState]:
    """``n`` prints of ``size`` in direction ``d`` every 30 s; the book (mid) moves 6 ticks along
    a sqrt / linear / square path of the cumulative flow."""
    st = [S(0, BIDS, ASKS, tv=1000.0)]
    tv = 1000.0
    for i in range(1, n + 1):
        x = i / n
        y = {"sqrt": math.sqrt(x), "lin": x, "sq": x * x}[shape] * 6.0
        k = round(y)
        tv += size
        dd = -d if (mixed and i % 3 == 0) else d
        st.append(S(30 * i, shift(BIDS, d * k), shift(ASKS, d * k), tv=tv, iv=size, itr=1, dirn=dd))
    return st


def test_machinery_metaorder_trajectory_activates():
    r = run(pf.MetaorderTrajectory(), _metaorder_scenario())[-1]
    assert r.score >= 0.6
    assert r.evidence["one_sided"] == 1.0 and r.evidence["consistency"] == 1.0
    assert r.evidence["move_along_ticks"] == pytest.approx(6.0)
    assert r.evidence["path_slope"] > 0 and r.evidence["concavity"] > 1.2
    _check_common(r, 1)
    rd = run(pf.MetaorderTrajectory(), _metaorder_scenario(d=-1.0))[-1]
    assert rd.evidence["direction"] == -1 and rd.score >= 0.6


def test_machinery_metaorder_trajectory_null_and_evidence_changes():
    lin = run(pf.MetaorderTrajectory(), _metaorder_scenario("lin"))[-1]
    assert abs(lin.evidence["concavity"]) < 0.05 and lin.score == 0.0
    convex = run(pf.MetaorderTrajectory(), _metaorder_scenario("sq"))[-1]
    assert convex.evidence["concavity"] < 0 and convex.score == 0.0
    mixed = run(pf.MetaorderTrajectory(), _metaorder_scenario(mixed=True))[-1]
    full = run(pf.MetaorderTrajectory(), _metaorder_scenario())[-1]
    assert mixed.evidence["one_sided"] < 1.0 and mixed.evidence["consistency"] < 1.0 and mixed.score < full.score
    # flow without any price move has no impact path
    flat = [S(30 * i, BIDS, ASKS, tv=1000.0 + 300.0 * i, iv=300.0 if i else None, itr=1 if i else None,
              dirn=1.0 if i else None) for i in range(11)]
    rf = run(pf.MetaorderTrajectory(), flat)[-1]
    assert rf.evidence["move_along_ticks"] == 0.0 and rf.score == 0.0


def test_machinery_metaorder_impact_activates():
    r = run(pf.MetaorderImpact(), _metaorder_scenario())[-1]
    assert r.score >= 0.6
    assert 0.4 <= r.evidence["loglog_exponent"] <= 0.6
    assert r.evidence["loglog_r2"] > 0.9 and r.evidence["sqrt_like"] > 0.75
    assert r.evidence["concavity"] > 1.2 and r.evidence["slope_linear_ticks_per_unit"] > 0
    assert r.evidence["engine_price_impact"] is None
    _check_common(r, 1)


def test_machinery_metaorder_impact_null_and_evidence_changes():
    lin = run(pf.MetaorderImpact(), _metaorder_scenario("lin"))[-1]
    assert lin.evidence["loglog_exponent"] > 0.8 and lin.score == 0.0
    convex = run(pf.MetaorderImpact(), _metaorder_scenario("sq"))[-1]
    assert convex.evidence["loglog_exponent"] > 1.4 and convex.evidence["sqrt_like"] == 0.0 and convex.score == 0.0
    few = run(pf.MetaorderImpact(), _metaorder_scenario(n=3))[-1]
    assert few.evidence["points"] == 4 and few.score < 0.35
    # the tape engine's linear impact is carried through as evidence
    st = _metaorder_scenario()
    st[-1].price_impact = 0.0017
    assert run(pf.MetaorderImpact(), st)[-1].evidence["engine_price_impact"] == 0.0017


# ============================================================================= lifecycle
def _lifecycle_states(mid_move_ticks: int) -> List[MarketState]:
    """Static → buys absorbed by the ask (t 65..200, one 300-share print every 15 s, the ask touch
    refilled every other poll; the flow-vs-touch ramp climbs through the building band) → quiet
    (no new trades) so the 120-s window empties and the score releases; the whole book shifts
    by ``mid_move_ticks`` at t = 260 to set the outcome."""
    st = [S(5 * i, BIDS, ASKS, tv=1000.0) for i in range(11)]
    tv = 1000.0
    for i in range(1, 11):
        tv += 300.0
        aq = 700.0 if i % 2 else 1000.0
        st.append(S(50 + 15 * i, BIDS, [(10.1, aq)] + ASKS[1:], tv=tv, iv=300.0, itr=1, dirn=1.0))
    for s in range(205, 400, 5):
        k = mid_move_ticks if s >= 260 else 0
        st.append(S(s, shift(BIDS, k), shift(ASKS, k), tv=tv, iv=300.0, itr=1, dirn=1.0))
    return st


@pytest.mark.parametrize("mid_move,terminal", [(-2, "resolved"), (2, "failed")])
def test_machinery_lifecycle_building_active_confirmed_release(mid_move, terminal):
    mech = af.Absorption()
    rs = run(mech, _lifecycle_states(mid_move), use_update=True)
    seq = [r.state for r in rs]
    assert seq[0] == "inactive"
    assert "building" in seq and "active" in seq and "confirmed" in seq
    i_b, i_a, i_c = seq.index("building"), seq.index("active"), seq.index("confirmed")
    assert i_b < i_a < i_c
    assert rs[i_c].start_time is not None and rs[i_c].duration_s >= mech.confirm_s
    assert terminal in seq
    i_t = seq.index(terminal)
    assert i_t > i_c and rs[i_t].score < mech.release_threshold
    assert rs[i_c].evidence["direction"] == -1
    assert rs[i_c].evidence["peak_score"] >= 0.6
    assert "mid_change_since_start" in rs[i_c].evidence


def test_machinery_lifecycle_direction_zero_resolves():
    """inventory_rebalancing implies no direction: its episodes resolve, never fail."""
    mech = af.InventoryRebalancing()
    st = _rebalance_scenario(n=16)                                  # 320 s of flipping flow
    tv = st[-1].trade_volume
    st += [S(340 + 5 * i, BIDS, ASKS, tv=tv, iv=200.0, itr=1, dirn=-1.0) for i in range(80)]
    rs = run(mech, st, use_update=True)
    seq = [r.state for r in rs]
    assert "confirmed" in seq and "resolved" in seq and "failed" not in seq


@pytest.mark.parametrize("name", ACC_NAMES + PART_NAMES)
def test_machinery_update_never_raises_on_sparse_states(name):
    """Partial states (one side, no tick, no tape, no history, no mid) never raise inside update()."""
    mech = REGISTRY[name]()
    hist = StateHistory()
    states = [S(0, [], ASKS, tick=None), S(5, BIDS, [], tick=None), S(10, BIDS[:1], ASKS[:1]),
              S(15, BIDS, ASKS, tv=500.0, iv=100.0, itr=1, dirn=0.3, intensity=2.0),
              S(16, BIDS, ASKS, tv=600.0, iv=100.0, itr=1, dirn=-1.0, tick=None),
              S(20, [], [], tv=700.0, iv=100.0, itr=1, dirn=1.0, tick=None),
              S(25, BIDS, ASKS, tv=700.0, iv=100.0, itr=1, dirn=1.0, cross={"market_volume_60s": 0.0}),
              S(30, [], [], tick=None)]
    for ms in states:
        r = mech.update(ms, hist)
        assert 0.0 <= r.score <= 1.0
        assert isinstance(r.evidence, dict) and isinstance(r.baseline, dict)
        hist.push(ms)


# ============================================================================= real data
def test_realdata_fixture_capture_through_engine():
    """The closed-market fixture: every mechanism computes on every state without error; the
    closed (empty / static) books with no traded interval never build any of the 14 mechanisms,
    the readings name their missing inputs, and baselines are present."""
    from tower.engine import Engine, EngineConfig
    from tower.normalize import normalize_store
    events, _ = normalize_store(FIXTURE)
    assert events
    eng = Engine(EngineConfig(strict=True))
    n = 0
    seen = set()
    with_missing = 0
    for ev in events:
        ms = eng.process(ev)
        if ms is None:
            continue
        n += 1
        for name in ACC_NAMES + PART_NAMES:
            m = ms.mechanisms.get(name)
            if m is None:
                continue
            seen.add(name)
            assert 0.0 <= m.score <= 1.0
            assert m.state in ("inactive", "building", "active", "confirmed", "failed", "resolved")
            assert m.score < REGISTRY[name].build_threshold, (name, ms.symbol, ms.t, m.evidence)
            if m.evidence.get("missing"):
                with_missing += 1
            assert set(BASELINE_KEYS) <= set(m.baseline) or m.evidence.get("missing")
    assert n > 0 and seen == set(ACC_NAMES + PART_NAMES)
    assert with_missing > 0
    assert not eng.metrics["errors"]
