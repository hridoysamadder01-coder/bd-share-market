"""Configuration for the Bangladesh (DSE) market-structure research workspace.

SCOPE LOCK: nothing in bd_research/ imports from, writes to, or depends on the
OYSHE HFT system (/hft, /tests, /tools, /docs). This is a separate problem.

EVERY market-convention constant below is UNVERIFIED until confirmed against an
official DSE source. Research output must never be interpreted as valid while a
constant it depends on is still marked unverified — the QA report prints these
flags on every run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Bar frequency. The audit changes shape with it: intraday session-window checks
# are meaningless on daily bars, and a "gap" means different things at each.
# --------------------------------------------------------------------------
BAR_FREQUENCY = "DAILY"           # "DAILY" | "MINUTE"

# --------------------------------------------------------------------------
# Market conventions — ALL UNVERIFIED (owner action: confirm from dsebd.org)
# --------------------------------------------------------------------------
SESSION_VERIFIED = False          # trading hours confirmed from an official source?
CALENDAR_VERIFIED = False         # holiday calendar loaded from an official source?
CORP_ACTIONS_AVAILABLE = False    # split/bonus/dividend table available?
TICK_RULES_VERIFIED = False       # tick size / circuit-breaker bands confirmed?
BROKERAGE_VERIFIED = False        # commission + regulatory charges confirmed?

# Working assumptions ONLY — used to *flag* anomalies, never to repair data.
ASSUMED_SESSION_START = "10:00"   # local time; MINUTE frequency only
ASSUMED_SESSION_END = "14:30"
ASSUMED_TRADING_WEEKDAYS = (6, 0, 1, 2, 3)  # Sun..Thu as Python weekday() ints
ASSUMED_BAR_MINUTES = 1

# --------------------------------------------------------------------------
# Bangladesh market structure. Carried over from prior_rounds/round2.py — these
# are OWNER-SUPPLIED research constants, not independently confirmed against a
# DSE/BSEC circular, so they are flagged like every other unverified convention.
# They are recorded here because they are not cosmetic: they decide whether a
# measured return was ever capturable.
# --------------------------------------------------------------------------
STRUCTURE_VERIFIED = False

# --- SETTLEMENT vs SALEABILITY: two different mechanics, do not conflate ---
# Settlement is when the trade clears. Saleability is when the BROKER lets you
# sell the position — which depends on account type and broker practice, and can
# be earlier than settlement. Round 2 assumed "earliest legal sale = entry + 2
# sessions" and closed a candidate on that basis alone. That assumption is now
# withdrawn: it is UNKNOWN until confirmed against actual LankaBangla / DSE
# account behaviour, and no candidate may be killed by it while it is unknown.
SALEABILITY_VERIFIED = False
EARLIEST_SALEABILITY_DAYS = None  # UNKNOWN — must not be assumed, in either direction
SETTLEMENT_CYCLE_DAYS = 2         # settlement mechanic only; NOT a saleability claim
SHORT_SELLING_AVAILABLE = False   # long-only market ⇒ a Q1−Q5 spread is not tradeable
FLOOR_ERA = ("2022-07-28", "2024-01-31")   # price-floor regime: separate, never pool
NORMAL_CIRCUIT_PCT = 0.10         # DSE circuit ≈ ±10%; a >15% 1-day move is an ex-date suspect

# --- COSTS: verified and estimated are reported separately, never summed into
# one opaque number. A result must show what it survives on evidence and what it
# survives only on assumption. ---
BROKERAGE_ROUND_TRIP_VERIFIED = 0.008   # 0.8% — owner-verified
BROKERAGE_VERIFIED = True
ESTIMATED_ADDITIONAL_COSTS = (0.000, 0.002, 0.004)  # ESTIMATE band on top of brokerage
CAPITAL_GAINS_TAX = None          # UNKNOWN — modelled as 0, so nets are optimistic
SLIPPAGE_MODEL = None             # UNKNOWN — no impact model exists yet

# --- COVERAGE PANELS: the dataset changes basis on 2024-02-22 (381 → 88
# reporting symbols). Cross-sectional statistics from either side are not
# comparable, so panels are declared explicitly and never pooled. ---
COVERAGE_BREAK_DATE = "2024-02-22"
PANEL_PRIMARY = ("2012-10-01", "2024-02-20")   # full universe — primary panel
PANEL_POSTBREAK = ("2024-02-22", "2030-01-01")  # ~88 symbols — separate panel

# --- PHASE 4.5 SAMPLE DISCIPLINE: discovery on a fixed EARLY window only; the
# HOLDOUT is sealed and dropped on load by experiments/phase45_footprints.py so
# that Phase 5 walk-forward has a period no footprint definition has ever been
# tuned against. (Phase 4 ran full-sample DESCRIPTIVE state statistics across
# this period before the seal existed — recorded, not hidden.) The floor era and
# the post-break panel are separate regimes, reported apart, never pooled. ---
DISCOVERY_WINDOW = ("2012-10-01", "2018-12-31")   # pre-floor, pre-COVID
HOLDOUT_WINDOW = ("2019-01-01", "2022-07-27")     # SEALED until Phase 5

# --- CIRCUIT BREAKER (daily price limit) BANDS — UNVERIFIED against a DSE/BSEC
# circular, but EMPIRICALLY SUPPORTED: on the 2012–2018 discovery window the
# daily-return distribution has a distinct mass point at exactly +band in every
# bucket below (the first schedule tried — 10/7.5/5/3.75/2.5 — was contradicted
# by the data: 26–71% of moves in the upper buckets exceeded it, and was
# replaced by the schedule the mass points actually sit on). phase45 prints the
# per-bucket evidence on every run; the schedule may have changed after 2018,
# which is one more reason the holdout is a separate test. ---
CIRCUIT_BANDS_UNVERIFIED = (          # (previous close ≤ X, ± band)
    (200.0, 0.10), (500.0, 0.0875), (1000.0, 0.075),
    (2000.0, 0.0625), (5000.0, 0.05), (float("inf"), 0.0375))
CIRCUIT_PROXY_FRACTION = 0.95         # close-to-close move ≥ 95% of the band ⇒ limit-proxy hit

REQUIRED_COLUMNS = ("symbol", "ts", "open", "high", "low", "close", "volume")
OPTIONAL_COLUMNS = ("turnover", "trades")


@dataclass(frozen=True)
class QAThresholds:
    """Detection thresholds. These decide what gets FLAGGED, never what gets fixed."""
    max_abs_overnight_gap: float = 0.20      # |log gap| above this ⇒ possible corporate action
    locked_bar_run_flag: int = 5             # consecutive zero-range bars ⇒ locked/circuit proxy
    min_bars_per_day_ratio: float = 0.50     # day with fewer than this share of expected bars
    min_days_coverage_ratio: float = 0.80    # symbol present on fewer days ⇒ survivorship flag
    stale_price_run_flag: int = 30           # consecutive identical closes
    coverage_break_ratio: float = 0.25       # |Δ reporting symbols| / prior level ⇒ break


@dataclass(frozen=True)
class FeatureParams:
    """Windows are in BARS. All baselines are strictly trailing (see features.py)."""
    baseline_window: int = 60        # W — the symbol's own recent baseline
    short_window: int = 10           # k — recent activity window
    vol_window: int = 30             # realized-volatility window
    autocorr_window: int = 60
    abnormal_z: float = 2.0          # z above which a bar counts as "abnormal"
    min_history: int = 90            # bars required before any feature is emitted
    min_active_baseline: int = 20    # trailing bars WITH trading needed for an activity z
    min_meaningful_vol: float = 1e-4 # σ below this = price pinned at grid resolution
    z_clip: float = 20.0             # z-family winsorisation bound (count reported)
    eps: float = 1e-12


@dataclass(frozen=True)
class LabelParams:
    """Forward-looking outcome horizons (BARS). Labels are NEVER features."""
    horizons: tuple = (5, 15, 30, 60)
    move_threshold: float = 0.02     # |log return| counted as a "meaningful move"


@dataclass(frozen=True)
class Config:
    qa: QAThresholds = field(default_factory=QAThresholds)
    features: FeatureParams = field(default_factory=FeatureParams)
    labels: LabelParams = field(default_factory=LabelParams)


DEFAULT = Config()


def unverified_flags() -> dict:
    return {
        "SESSION_VERIFIED": SESSION_VERIFIED,
        "CALENDAR_VERIFIED": CALENDAR_VERIFIED,
        "CORP_ACTIONS_AVAILABLE": CORP_ACTIONS_AVAILABLE,
        "TICK_RULES_VERIFIED": TICK_RULES_VERIFIED,
        "BROKERAGE_VERIFIED": BROKERAGE_VERIFIED,
        "STRUCTURE_VERIFIED": STRUCTURE_VERIFIED,
        "SALEABILITY_VERIFIED": SALEABILITY_VERIFIED,
    }
