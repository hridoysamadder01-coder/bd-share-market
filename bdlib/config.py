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
SETTLEMENT_T_PLUS = 2             # earliest LEGAL sale is entry + 2 sessions
SHORT_SELLING_AVAILABLE = False   # long-only market ⇒ a Q1−Q5 spread is not tradeable
FLOOR_ERA = ("2022-07-28", "2024-01-31")   # price-floor regime: separate, never pool
ROUND_TRIP_COSTS = (0.008, 0.010, 0.012)   # brokerage + charges bracket used in Round 2
CAPITAL_GAINS_TAX = None          # UNKNOWN — Round 2 modelled it as 0
NORMAL_CIRCUIT_PCT = 0.10         # DSE circuit ≈ ±10%; a >15% 1-day move is an ex-date suspect

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
    }
