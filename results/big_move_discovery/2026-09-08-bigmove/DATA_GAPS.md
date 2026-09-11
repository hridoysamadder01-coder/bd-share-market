# Data gaps — big-move discovery, 2026-09-08

Every gap below is a measured number, not an impression. Truth classes are
OBSERVED / INFERRED / NOT_OBSERVABLE; "unknown" is never imputed as zero.

## 1. Point-in-time free float — NOT_OBSERVABLE

The float family (FFV = daily traded shares / free-float shares, and its signed
variant SFF) cannot be built for the historical panel. This is the single biggest
gap in the whole big-move programme, and it is not close.

**Ownership.** DSE publishes at most three shareholding as-on dates per company
and keeps no archive. Today's whole-market sweep captured all of them for 419
symbols — 1,220 rows — and the dates are almost entirely recent:

| as-on year | rows |
|---|---:|
| 2001 | 8 |
| 2016–2019 | 6 |
| 2020–2022 | 41 |
| 2023 | 27 |
| 2024 | 71 |
| 2025 | 317 |
| 2026 | 750 |

Matching those to the discovery panel (587,474 rows after reservations):

| | |
|---|---:|
| panel rows with **any** as-on observation at or before the row's date | **22,377** |
| share of the panel | **3.81 %** |
| symbols with any usable as-on | 106 of 392 |

By year, the share of rows with a point-in-time ownership observation:

| 2012–2015 | 2016 | 2017 | 2018 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| 0.0 % | 0.1 % | 0.5 % | 0.6 % | 5.8 % | 6.8 % | 10.3 % | 44.1 % | 80.5 % |

**Shares outstanding.** `total_shares` exists for 406 symbols, but as a **single
2026-09-08 snapshot**. Applying it to a 2013 row would be flatly wrong, and the
project's own data proves it: the dividend record captured today shows bonus and
stock issues for 371 symbols — ACI 15 % in 2024, BATBC 200 % in 2020 — each of
which changed the share count.

**Verdict.** FFV and SFF are NOT_OBSERVABLE for the historical panel. They are not
KILLED — nothing was tested — and they must not be approximated with today's
snapshot.

**A possible reconstruction, not yet built.** Historical shares outstanding could
be INFERRED by dividing today's count by the cumulative bonus/stock factor from
the dividend history now captured (371 symbols, 2,984 (symbol, year, percent)
rows). That would be INFERRED, not OBSERVED, would only cover symbols with a
complete bonus record, and would still lack the ownership split needed to turn
outstanding shares into *free float*. It is a candidate for future work and is
deliberately not used here.

**The remedy is time.** One ownership snapshot per symbol per month from now on
adds one as-on date per month. The whole-market sweep already does this. Nothing
computed on FFV before that panel exists means anything.

## 2. Micro confirmation — NOT_OBSERVABLE against this panel

The micro order-book captures are dated **2026-09-06 and 2026-09-08**. The
discovery panel ends **2026-01-22**, because the reserved slice begins 2026-01-25.

**Overlap: zero rows.** Both micro sessions sit inside the reserved slice, which
no phase may open. E1, TLPI, causal QT, signed flow and touch geometry therefore
cannot be tested as confirmation layers for any daily buildup event in this run —
not because they failed, but because there is no date on which both exist.

This resolves only when either (a) the reserved slice is deliberately opened under
protocol, or (b) enough new micro sessions accumulate to form their own panel that
overlaps a future daily window. Option (b) is what the whole-market intraday
runner scheduled for 2026-09-09 begins accumulating.

## 3. Execution side — NOT_OBSERVABLE for the daily panel

The signed float variant SFF needs executed buy volume minus sell volume. The
daily EOD panel carries volume, turnover and trade count, with **no execution
side**. Signed flow exists only in the intraday captures, which per §2 do not
overlap the panel. No proxy is manufactured.

## 4. Circuit bands — INFERRED, flagged unverified

`room_pct` uses `bdlib.config.CIRCUIT_BANDS_UNVERIFIED`, a price-dependent ladder
the config itself marks unverified, applied to the row's own close. Two known
weaknesses, both stated rather than papered over:

* The ladder is **today's**, applied to historical dates. DSE has changed the
  bands over the panel's 13 years; using the current ladder for 2013 is an
  approximation, and any circuit-room result inherits that error.
* The floor era (2022-07-28 … 2024-01-31) imposed a hard price floor that the
  ladder does not represent at all.

Circuit room is therefore used only as an **opportunity constraint** — can this
buildup still expand tomorrow — never as directional alpha.

## 5. Sector — NOT AVAILABLE in the historical panel

Sector labels exist in today's capture (BullBD, 472 symbols) but not as a
point-in-time field in the EOD panel, and a symbol's sector classification has
changed over 13 years. Sector-relative leadership is therefore untested in this
run. The `share_z_asof` market-relative measure is computed against the whole
causal cross-section instead, which is a weaker control.

## 6. Coverage break at 2024-02-22

The universe falls from 381 symbols to 88. Cross-sectional statistics are computed
per panel and never across the break. The POSTBREAK panel holds 41,909 of 587,474
rows (7.1 %), so anything claimed for the post-break regime rests on a seventh of
the data and roughly a quarter of the symbols.

## 7. Corporate-action adjustment — NOT APPLIED

`bdlib.config.CORP_ACTIONS_AVAILABLE = False`. The EOD prices are not adjusted for
bonus issues or splits, so a bonus ex-date appears as a large negative return.
This inflates both adverse excursions and apparent "big down moves", and it means
a small number of forward-return observations are mechanical rather than market
moves. The dividend history captured today (371 symbols with dated bonus
percentages) is the raw material for fixing this and has not yet been applied.
