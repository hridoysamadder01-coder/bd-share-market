# REJECTED_CANDIDATES

> Permanent ledger. A rejected idea is evidence and is never deleted — deleting
> rejections is how a research programme fools itself into rediscovering the same
> dead end. Append only.

## Round 2 — preserved 2026-09-01 from owner-supplied material

Source, committed verbatim: `prior_rounds/round2.py` (the exact script) and
`prior_rounds/round2_full_output.txt` (its full output). Definitions below are
quoted from that run, not reconstructed from memory. Universe: 392 equity-like
DSE instruments after filtering index/bond/MF — the same filter this workspace
now uses in `data/ingest_dse_eod.py`, and the same count it reproduces.

Period definitions used there: **P1** 2013-01-01…2017-12-31 · **P2**
2018-01-01…2022-07-27 · **FLOOR ERA** 2022-07-28…2024-01-31 (separated, never
pooled) · **P3** 2024-02-01…end. Cost bracket 0.8% / 1.0% / 1.2% round trip.

### R2-1 — A-family: volume spike + N-day breakout ❌ NEVER WORKED

| Variant | Definition | P1 full | P2 full | P3 full |
|---|---|---|---|---|
| A1 | spike > 3.0× ADV20, close > 20-day high, TP 8% / SL 4% / 15d | n=3918, net@1% **−0.66%**, t −5.35 | n=3806, **−0.92%**, t −6.93 | n=395, **−2.11%**, t −5.75 |
| A2 | spike > 2.5× ADV20, close > 15-day high | n=5014, **−0.59%**, t −5.46 | n=4831, **−0.83%**, t −7.19 | n=526, **−1.84%**, t −5.67 |
| A3 | spike > 4.0× ADV20, close > 30-day high | n=2382, **−0.78%**, t −4.76 | n=2404, **−1.06%**, t −6.27 | n=222, **−1.79%**, t −3.43 |

Verdict recorded: *"NEVER WORKED — P3 'failure' is not special; family negative
in early periods too."* Same-symbol P2-vs-P3 matching (59 symbols, S2) showed the
raw deterioration survives universe matching, so it is **not** a coverage
artifact. The 3-period matched universe is only **6 symbols** — declared
underpowered rather than reported as a result.

**Consequence for this workspace:** "volume spike + breakout" is a closed
question on DSE daily data, measured over ~11,000 trades. It is not to be
re-proposed as a candidate without a materially different definition and a
stated reason the old measurement does not apply.

### R2-2 — Panic-day rebound ❌ NOT CAPTURABLE

Event definition: equal-weight market return ≤ −2%, ≥ 70% decliners, ≥ 50 names;
entry at the **next open**; ex-date suspects (1-day drop > 15%) excluded; 58
independent events (≥ 5 days apart) — 40 pre-floor, 17 post-floor, 1 in-floor.

Every horizon, every cost, both regimes: **negative**. Pre-floor 3d/5d/10d net@1%
= −1.56% / −1.92% / −1.22%; post-floor = −1.78% / −2.87% / −2.49%. Liquid-100
restriction did not rescue it.

Decisive finding (S3): the "rebound" **lives entirely in the overnight gap** —
panic-day close → next open averaged +0.51% pre-floor and +0.83% post-floor. A
decision taken at the panic-day close cannot be filled before the next open, so
that gap is not capturable. *"Any backtest entering at panic-day close books this
gap as fictional profit."*

### R2-3 — Independent report's panic-regime cross-sectional mean reversion ❌ CLOSED BY T+2

Re-implemented independently and **replicated** on event count (32 pre-floor vs
their 29); **0 post-floor events**, so no persistence evidence was even possible.

| Exit | Gross | Net @1% | t | Legal under T+2? |
|---|---|---|---|---|
| close e+1 | +2.05% | **+1.05%** | 1.69 | ❌ **illegal** — their headline zone |
| close e+2 | +1.32% | +0.32% | 0.43 | ✅ earliest legal |
| close e+3 | +1.19% | +0.19% | 0.18 | ✅ |
| close e+5 | +0.96% | −0.04% | −0.03 | ✅ |

*"Their +1.98% net was measured at an exit the T+2 rule forbids. Move the exit
one day to the first legal session and the candidate decays to noise. The rebound
is front-loaded precisely inside the settlement-blocked window."*

The Q1−Q5 cross-sectional spread at the legal e+2 exit is real (+1.49%, 75%
positive, t = 3.15) — but capturing it **requires shorting Q5, unavailable in
Bangladesh**. The long-only capturable piece is the +0.32% / t = 0.43 row: noise.

**Status: CLOSED as a trade.** Archived as a frozen rule with an explicit
re-open trigger — **intraday netting OR short-selling going live**, either of
which legalises the profitable window or leg.

### What Round 2 established as structure, not opinion

These now live in `bdlib/config.py` and constrain every future candidate:

- **T+2 settlement** — a return measured at an exit earlier than entry + 2
  sessions was never capturable. Any horizon < 2 days is measurement only.
- **No short selling** — a long/short spread is not a strategy here.
- **Floor era 2022-07-28…2024-01-31** — a distinct regime; pooling it with free
  periods mixes two different markets.
- **Round-trip cost ≈ 1%** — at DSE daily-horizon effect sizes this is not a
  detail, it is usually the entire result.
- Capital-gains tax **unknown**, modelled as 0 — so every net figure above is
  optimistic by an unknown amount.

## Rejected implementation choices (kept so they are not retried)

| # | Date | Choice | Why rejected | Evidence |
|---|---|---|---|---|
| I-001 | 2026-09-01 | Robust z with denominator `1.4826·MAD + eps` | Locked / zero-volume stretches give MAD = 0, so z reached 4·10¹³ and would have poisoned every downstream mean, threshold and model | Observed on the synthetic fixture; replaced by a relative scale floor plus NaN when the baseline is degenerate |
| I-002 | 2026-09-01 | Amihud impact as `\|ret\| / (turnover + eps)` | Zero-turnover bars produced 1.7·10¹⁷ | Caught by the numeric-sanity gate; now NaN when turnover = 0 |
| I-003 | 2026-09-01 | Fixture that set a stale close without re-bracketing high/low | Planted a second, unintended defect and corrupted the planted-vs-detected accounting (39 detected vs 5 planted) | `qa/verify_detectors.py` now reconciles counts exactly |
| I-004 | 2026-09-01 | **Z-scoring raw volume / turnover levels** | On REAL DSE data an illiquid symbol with trailing median volume ≈ 1 share produced `rel_volume_z` ≈ 5·10⁶ the first day it traded — the number described the denominator floor, not the market | Now z-scored in **log space** (`_robust_z_log`), plus a validity guard: fewer than 20 traded days in the trailing window ⇒ NaN |
| I-005 | 2026-09-01 | `vol_regime_ratio = σ_short / (σ_long + eps)` | Floor-era pinning makes σ_long exactly 0 ⇒ ratio 4363 on real data | NaN when σ_long is below price-grid resolution (`min_meaningful_vol`) |
| I-006 | 2026-09-01 | Unbounded z-family features | A pinned-range symbol that suddenly ranges 16% gives a truthful but unusable `range_z` = 1600; a handful of rows would dominate any downstream statistic | Winsorised at ±20 with per-feature clip counts reported (2,827 cells = 0.011% on the real dataset) — the bound is documented, not hidden |
