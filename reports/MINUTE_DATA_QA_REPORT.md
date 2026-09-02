# MINUTE DATA QA — DSE trade-minute dataset (Phase 1, detection only)

> Run 2026-09-02 (v2) · `qa/run_minute_qa.py` · input: clone of
> [Muntasib-creator/DSE_dataset](https://github.com/Muntasib-creator/DSE_dataset)
> at commit `62403f3` (MIT) · machine-readable summary: `qa/MINUTE_QA_ISSUES.json`
> (strict JSON; every number below is in it or in the CSVs it names).
> **Nothing was interpolated, forward-filled, deduplicated, repaired or dropped.**
> No signal, footprint, alpha, precursor or trading hypothesis was tested.
>
> v1 of this report was checked by three independent adversarial lenses
> (recompute every count from the raw files · hunt misreadings · audit for
> silent repair). Every v1 count reproduced; several v1 *readings* did not
> survive and are corrected here — §17 lists them.

## 1. What the data is

**Irregular trade-minute rows, not a 1-minute grid.** A row exists for a minute
in which the symbol printed. Every check below is defined for that structure;
§14 says what our regular-grid assumptions would get wrong.

| | |
|---|---|
| Files in `minute_price_unadjusted/` | 419 = **417 symbol files** + 2 non-symbol files (`summary.csv`, `__summary__.csv`), excluded |
| Symbol files with data | 412 · 5 header-only: BXSYNTH, CBLPBOND, KAY&QUE, NLI1STMF, SAVAREFR · 0 corrupt |
| **Distinct series** | **411** — `ONEBANKLTD` is `ONEBANKPLC` under its old name: 143,600 of 143,601 timestamps in common, 143,599 rows identical in every field, ending 2023-02-19 while ONEBANKPLC continues to 2024-01-31 |
| Rows | **43,119,617** parsed = physical lines − headers (0 lines skipped by the parser) · **42,976,016 net of the duplicate** |
| Span | 2015-10-15 10:34 → 2024-01-31 14:29 · 305 symbols start in 2015 (302 in October), 410 end in Jan 2024 (APSCLBOND and the ONEBANKLTD alias end earlier) |
| Symbol-days | 674,763 (673,004 net) · minute trading calendar (≥ 30 symbols printing) **2,002 dates** |
| Schema | `timestamp,closing,opening,high,low,volume` on every file; seconds always `:00`; all closes on the Tk 0.10 tick grid (0 off-grid); timestamps naive — Asia/Dhaka local assumed, UNVERIFIED |
| Companion daily files | `day_price_unadjusted/`: 421 files, `Date,Open,High,Low,Close,Volume`, from 2012-10-01; 0 unparseable or duplicate dates |
| Overlap with our 392-symbol EOD universe | **383 in both** · 34 minute-only (mostly mutual funds, bonds and SME-board names) · 9 EOD-only |
| Floor era 2022-07-28 → 2024-01-31 (inclusive, dates UNVERIFIED) | 8,019,066 rows · 129,466 symbol-days · all 412 symbols |

Three of the five empty files are **coverage holes, not defunct names**: their
daily files have 1,145 (BXSYNTH), 1,952 (KAY&QUE) and 1,500 (SAVAREFR) rows
inside the minute era. CBLPBOND has 1 daily row; NLI1STMF has no daily file.

## 2. Structural integrity — clean

| Check | Result |
|---|---|
| Header mismatch · unparseable timestamps · non-numeric cells | 0 · 0 · 0 |
| Lines skipped by the CSV parser (physical lines − headers − parsed rows) | **0** |
| Duplicate timestamps within a symbol | 0 |
| Non-monotonic rows (file order) | 0 |
| OHLC violations (high < max(o,c), low > min(o,c), high < low) | 0 |
| Negative volume | 0 · prices off the Tk 0.10 grid: 0 |

## 3. Provenance — same source as our EOD data, and a gap in that source

This dataset's **daily** files agree with our own EOD parquet on **131,512 of
131,512** overlapping symbol-days (60 symbols, close within 0.5%): one lineage.
Two consequences:

- **19 SME-board symbols** (WONDERTOYS, ORYZAAGRO, NIALCO, ACHIASF, MASTERAGRO,
  APEXWEAV, …) have minute rows from **2022-08-30** but daily files that start
  only on **2024-01-28** (18 rows). That is **4,528 symbol-days / 108,569 rows of
  real trading on normal calendar days** that the daily files — and therefore
  our EOD data — do not contain.
- APEXWEAV, CROWNCEMNT, RAHIMAFOOD and UCB have daily history that predates
  their minute file by more than the common 2012→2015 offset.

Where the minute prints themselves come from, and how they were captured, the
README does not say (D-1).

## 4. Timestamps, sessions and snapshot rows — the corrected reading

v1 read the 2015–16 weekend and overnight rows as "corrupt timestamps". They
are not. They are **board snapshots**: one row per symbol, stamped at scrape
time, carrying the previous session's last price and — critically — the
previous session's **whole-day volume**.

| Evidence | Count |
|---|---|
| Minute dates absent from the dataset's own daily calendar | **47** = 37 Fri/Sat (2015-12-04 → 2016-05-07) + 10 weekday **holidays** (2016-03-17, 04-14, 05-23, 08-15, 08-25, 09-11…15) |
| Rows on those symbol-dates | 2015: 3,730 · 2016: 36,154 |
| A weekend symbol-day | median **2 rows**, last row 11:22 — vs 50 rows ending 14:29 on a weekday |
| Rows whose volume equals the *previous* session's daily Volume **and** whose price equals its Close | **15,846** (2016: 12,909 · 2023: 1,328 · 2015: 534) |
| Rows whose volume equals the *same* day's daily Volume on a day with > 1 rows (an end-of-day snapshot appended to real prints) | **30,742** (2016: 26,723 · **2019: 1,954 · 2020: 1,412**) — e.g. 2016-07-26 (2,771 rows), 2020-01-30 (485), 2019-12-30 (349), 2019-09-09, 2019-12-24, 2019-12-15, 2020-06-08 |
| Two Saturdays **are** real sessions (in the daily calendar): 2016-07-16, 2016-09-24 | so "weekend ⇒ not a session" is contradicted by the data itself |
| 2016 minute calendar | 285 dates = 241 daily-calendar dates (incl. the 2 Saturdays) + 44 snapshot dates |

So the rule is not "drop weekends"; it is **"a row whose volume is a whole
session's volume is a snapshot, on any date"** — and snapshots exist inside the
session on specific 2019–2020 dates, not only in 2015–16. Snapshot rows must be
excluded from every volume aggregation and are the reason the minute-vs-daily
volume check over-counts (§9). They are flagged in `MINUTE_QA_SYMBOL_DAYS.csv.gz`
(`snapshot_prev_session`, `snapshot_same_day_multirow`) — flagged, not removed.

**Out-of-window rows.** Outside a loose 09:30–15:00 window: 59,721 rows —
00:00–08:59: 19,164 · 09:00–09:29: 632 · 15:00–23:59: 39,925 (17,807 in the 15:xx
hour). By year: 2015: 4,979 · 2016: 47,801 · 2018: 332 · 2021: 6,352 · others
< 150. The 2021 rows are **one date, 2021-07-18** (a Sunday), all 15:00–15:59 —
a single extended session, UNVERIFIED. The 2018 rows are 2018-09-17.

Outside the *assumed* 10:00–14:30 window: **553,611** rows, of which 309,995 are
in the 14:xx hour (14:31–14:59) and 184,294 in the 09:xx hour (09:30–09:59):
**real trading regimes**, not errors — a 09:30 open and post-14:30 prints exist
in the data (§4.1). The single sub-threshold date is 2015-10-16 (a Friday, 7
symbols).

### 4.1 Session windows change within years, not only between them

Per-year percentiles of first/last print:

| year | first p01 | first median | last median | last p99 |
|---|---|---|---|---|
| 2015 | 00:53 | 10:49 | 14:30 | 21:09 |
| 2016 | 00:02 | 10:41 | 14:29 | 23:37 |
| 2017 | 10:02 | 10:40 | 14:30 | 14:40 |
| 2018 | 10:02 | 10:42 | 14:30 | 14:51 |
| 2019 | 10:01 | 10:37 | 14:29 | 14:30 |
| 2020 | 10:00 | 10:30 | 14:29 | 14:30 |
| 2021 | 10:00 | 10:04 | 14:29 | 14:44 |
| 2022 | 09:30 | 10:04 | 14:28 | 14:44 |
| 2023 | 10:00 | 10:11 | 14:18 | 14:29 |
| 2024 | 10:00 | 10:08 | 14:22 | 14:29 |

A per-year window is still wrong: the market-wide p05-first / p95-last window
per **date** (`qa/MINUTE_QA_SESSION_BY_DATE.csv`) shifts by ≥ 10 minutes
against its trailing 5-day median on **279 dates** — 121 of them 2016 snapshot
noise, the rest in clusters that look like Ramadan schedules each year, the
2020-03-19…24 and 2020-06…08 periods, 2021-04…08, 2022-04/05, 2022-08-24…29,
2022-11-15…17, 2023-03/04. These are interpretations; the dates are data.
Session hours per date are an owner action (D-3).

## 5. Rows per symbol-day — sparse

| | |
|---|---|
| Rows per day, median symbol | p10 **4.6** · median **52** · p90 **134** (the assumed 10:00–14:30 session, UNVERIFIED, is ~270 minutes) |
| Symbol-days with exactly 1 row / < 5 rows | 28,999 (4.3%) / 70,547 (10.5%) |
| Gap between consecutive rows within a day (median symbol) | 1 min **41%** · 2 min 26% · > 5 min 14% |
| Rows with open = high = low = close | **36,852,396 (85.5%)** — one print per minute is the norm |
| Rows with volume exactly 1 | 513,643 (1.2%): 2015: 1,622 → 2019: 54,679 → 2021: 92,621 → **2022: 143,899** → 2023: 99,599. v1 called these placeholders; the review checked them against the daily files and against price: they behave as **real single-share prints**, concentrated in high-priced names and the floor era. Treated as trades |
| Rows with volume 0 | 2,370 |

Half of all symbol-days have fewer than 52 prints. This is a sequence of trades;
any method that assumes a bar every minute is wrong on ~80% of minutes.

## 6. Missing trading days

| | |
|---|---|
| **Whole-market days in the daily calendar with zero minute rows** | **25**: 2015-10-25…29, 11-05, 11-08…10, 11-15, 11-17…19, 11-29…30 (15 days); 2018-07-25; **2020-07-30, 08-03…04, 08-06, 08-09…10, 08-12…13, 08-19** (9 days) |
| 2020 | daily calendar 208 days, minute 199 — the 9 August days are a **capture gap**; the COVID closure 2020-03-26 → 05-30 has **0 rows in both** files (real closure) |
| Per-symbol days absent inside its own span (vs the minute calendar) | 29,845 · median symbol 2.9% · 9 symbols > 20% · longest run 174 days |
| Symbol-days in the daily file but not minute, within the minute span | 9,087 |
| Symbol-days in minute but not the symbol's daily file | 18,915 = the 2015–16 snapshot dates (14,243) + the 19 SME-board symbols' missing daily history (4,528, §3) + 144 others |

## 7. Price anomalies and bad prints

| | Count | Note |
|---|---|---|
| Rows with a price ≤ 0 | **9,512** | 2016: 285 · 2017: 24 · 2018: 3,058 · 2020: 1 · 2022: 6,144 — zero prints, flagged |
| Minute-to-minute |log return| ≥ 10% inside a day | 1,220 = **845 finite** + 375 through a zero price | the 375 are the zero-price rows again, not moves |
| Spike-and-revert (±5% then back within 1%, positive prices only) | **4,501** | classic bad-print signature |
| Minute high above the daily High / minute low below the daily Low (> 0.5%) | **1,243 / 1,622** symbol-days | prints outside the official range — bad prints the daily file did not accept |

## 8. Day-over-day gaps — discontinuities and regime dates

First print of a day vs last print of the symbol's previous minute-day, against
the (unverified, empirically supported) limit band from `bdlib/config.py`:

| | Count |
|---|---|
| Gaps with a zero price at either end — excluded from everything below | 368 |
| **Beyond the band** (+0.25% tolerance) | **1,470** · beyond 20%: 263 |
| … confirmed by the daily file's own Open vs previous Close | 571 |
| … **not** beyond band in the daily file (minute first/last-print artefacts) | 688 · untestable 206 |
| … where the previous minute-day is not the adjacent trading day (multi-session move; "impossible under a daily limit" does not apply) | 360 |

The beyond-band days **cluster on dates**: 2020-03-19 (237 symbols),
2020-08-16 (149), 2020-08-05 (47), 2020-08-20 (27), 2020-03-22 (11),
2020-03-15 (7). The August dates sit on the edges of the 2020-08 capture gap
(§6). **2020-03-19 is market-wide and positive** — it is the introduction of the
**first DSE floor-price regime** (BSEC, March 2020), lifted during 2021. That
regime is **not in `config.FLOOR_ERA`**, which carries only 2022-07-28 →
2024-01-31; the daily-data research therefore treated March 2020 → mid 2021 as
a free market. Recorded as owner action **D-11** and as a HIGH finding for the
whole track, not just this dataset.

The remaining, symbol-specific beyond-band days are the same ex-date /
bonus reference-price resets that contaminated the daily work (D-4 open).

## 9. Minute ↔ daily internal consistency — corrected reading

Aggregating each symbol's minute rows to a day and comparing with the dataset's
own daily file (411 symbols, 654,089 days):

| Field | Mismatch | What it is |
|---|---|---|
| **Close**: last print vs daily Close (> 0.5%) | **131,868 days (20.2%)**; 130,101 of them have the daily Close *inside* the minute day's [low, high] | by year: 2015 39% · 2016 34% · 2017 16% · 2018 22% · 2019 34% · 2020 26% · 2021 20% · **2022 8% · 2023 3%**. Not a single closing-average rule (the review found a last-30-minute VWAP reproduces fewer than half); regime- and capture-dependent. The official close rule is **D-10, open** |
| **Volume**: sum of prints vs daily Volume (> 5%) | **80,251** = **58,363 minute > daily** (21,786 more than 2×) + 21,888 minute < daily | over-count = **snapshot rows** carrying a whole session's volume (2016: 47% of days over-counted; 2020: 9%; 2019: 5%); under-count = missed prints (2021: 5.7% of days) |
| High / Low (> 0.5%) | 30,302 / 25,917 | ~96% minute *inside* the daily range (prints the capture missed); 1,243 / 1,622 *outside* it (bad prints, §7) |
| Closing-session prints: last-10-minute prints at **one** price equal to the daily Close | share of symbol-days: 2019 7% · 2020 13% · **2021 22% · 2022 32%** · 2023 19% | consistent with a single-price closing session appearing 2021–22 (UNVERIFIED), but confounded by floor-era pinning; the detector cannot separate the two — D-10 |

v1 summarised this as "the minute file is an incomplete trade record". That
was wrong in direction for volume and too simple for close. The rule that
survives: **daily quantities must come from the daily file, never from
aggregating minutes**, and the last print is the official close only where a
closing-session regime is established (D-10).

## 10. Coverage by symbol and year

`qa/MINUTE_QA_COVERAGE_YEAR.csv`. Minute calendar days per year: 2015 (from
Oct) 42 · 2016 285 (241 + 44 snapshot dates) · 2017 248 · 2018 241 · 2019 237 ·
2020 199 (208 in the daily calendar) · 2021 240 · 2022 244 · 2023 244 · 2024 22.
Symbols starting per year: 2015 305 · 2016 11 · 2017 11 · 2018 12 · 2019 10 ·
2020 10 · 2021 17 · 2022 27 · 2023 7 · 2024 2. The dataset ends 2024-01-31 —
nothing after the second floor was lifted.

## 11. Floor era (2022-07-28 → 2024-01-31, inclusive; dates UNVERIFIED)

8,019,066 rows · 129,466 symbol-days · all 412 symbols. Single-print rows
(o=h=l=c): 85.3% inside the era vs 89.8% outside — the floor does not show as
*more* pinning at minute resolution because single prints dominate everywhere.
The **first** floor regime (from 2020-03-19, §8) is not delimited in config and
has not been separated here.

## 12. Overlap with the 392-symbol EOD universe

383 in both. Minute-only (34): 1JANATAMF, 1STPRIMFMF, ABB1STMF, ABBLPBOND,
AIBL1STIMF, APEXWEAV, APSCLBOND, BEXGSUKUK, CAPITECGBF, CAPMBDBLMF, CAPMIBBLMF,
CBLPBOND, DBH1STMF, DBLPBOND, EBL1STMF, EBLNRBMF, EXIM1STMF, GLDNJMF,
GREENDELMF, ICBEPMF1S1, IFIC1STMF, IFILISLMF1, LRGLOBMF1, MBL1STMF, NCCBLMF1,
NLI1STMF, ONEBANKLTD (alias), PF1STMF, PHPMF1, POPULAR1MF, SEMLLECMF, TRUSTB1MF,
VAMLBDMF1, YUSUFLOUR. EOD-only (9): ASIATICLAB, BESTHLDNG, CRAFTSMAN,
MAGURAPLEX, NRBBANK, SHARPIND, SIPLC, TECHNODRUG, WEBCOATS.

## 13. Severity summary

| Severity | Finding | Consequence |
|---|---|---|
| **HIGH** | **Snapshot rows** carrying a whole session's volume: 2015–16 on 47 non-session dates, plus specific 2019–2020 dates inside sessions (§4) | must be flagged before any volume or activity measure; "drop weekends" is the wrong rule |
| **HIGH** | **25 whole-market capture gaps** (2015-10/11, 2018-07-25, 2020-08) and symbol-level misses (high/low off on 4–5% of days) | the minute file is not a complete print record on those days; gaps are absence, never to be filled |
| **HIGH** | Irregular trade-minute structure (median 52 rows/day, 85% single-print rows) | every window / baseline / "gap" must be redefined — §14 |
| **HIGH** | **First floor-price regime from 2020-03-19 is missing from `config.FLOOR_ERA`** (§8) | affects the daily-data research already done, not only this dataset — owner action D-11 |
| MEDIUM | ONEBANKLTD = ONEBANKPLC (rename): all totals include one series twice | net figures given in §1; a rename map is needed |
| MEDIUM | 19 SME-board symbols: 1.4 years of trading present in minute, absent from daily/EOD (§3) | the EOD universe is thinner than it looks for 2022–23 listings |
| MEDIUM | Close ≠ last print on 20% of days; official close rule unknown; a closing-session regime may exist from 2021–22 (§9) | daily close from the daily file only; D-10 |
| MEDIUM | 571 daily-confirmed beyond-band gaps (ex-dates, unadjusted) + 688 minute-only artefacts | flag, never repair; D-4 |
| MEDIUM | 9,512 zero-price rows · 845 finite ≥10% single-minute jumps · 4,501 spike-and-reverts · 2,865 prints outside the official daily range | bad-print flags per row |
| LOW | 5 empty files, 3 of them actively traded names (coverage holes); 2 summary files in the symbol folder | exclude by name; note the holes |
| LOW | 9 symbols missing > 20% of their span; longest gap 174 days; no delisted names in the universe | suspensions; survivorship unknown (D-9) |
| OPEN | session hours per date, weekend definition, timezone, official-close rule, closing-session start, corporate actions, first floor regime dates, capture method | all UNVERIFIED — §15 |

## 14. Does the irregular structure require loader / QA changes? Yes.

Listed, **not implemented**:

1. **`QAThresholds.min_bars_per_day_ratio`** assumes an expected bar count per
   session; the median day has 52 of ~270 minutes. Redefine against each
   symbol's own print-count distribution.
2. **Rolling windows in `bdlib/features.py` are in rows** (`baseline_window=60`);
   60 rows is 20 minutes or several days depending on liquidity. Windows must be
   in clock time or sessions, with the row count as a validity guard.
3. **`flag_locked_bar` / `flag_zero_volume`** assume a grid; "no row" is the
   normal state and single-print rows are 85% of the data.
4. **A snapshot-row flag** (volume equals a session total; date absent from the
   daily calendar) is required before any volume aggregation.
5. **Day-level quantities come from the daily file**, never from minutes; the
   last print is the close only inside an established closing-session regime.
6. **Session windows per date**, data-derived (`MINUTE_QA_SESSION_BY_DATE.csv`),
   not per year and not a single constant.
7. **Two floor regimes**, not one: 2020-03-19 → 2021 (dates to be established)
   and 2022-07-28 → 2024-01-31.
8. **A rename map** (ONEBANKLTD → ONEBANKPLC) and a **whole-market calendar
   reconciliation** (daily vs minute) in the loader.
9. **No imputation of any kind.** Absence of a row is information.

## 15. Owner actions (add to the ledger)

| # | Action | Unblocks |
|---|---|---|
| D-1 | Provenance and capture method of the minute prints: why board snapshots on holidays/weekends in 2015–16 and at end-of-day on 2019–20 dates; why 25 whole days are missing | trust of every period |
| D-3 | DSE session hours **per date** 2015–2024 (Ramadan, lockdown, 2022 energy-saving, 2023) and the weekend / make-up-Saturday rule | §4.1, §14.6 |
| D-10 | Official closing-price rule, and the start date of the single-price closing session | §9 |
| D-11 | **First floor-price regime: exact start (2020-03-19 observed) and lift dates** | §8, and the daily-data regime split |
| D-12 | Daily/EOD history for the 19 SME-board symbols from 2022-08-30 | §3 |
| D-4 / D-9 | corporate-action table / delisted histories | as before |

## 16. Checks run with no finding

Tick grid (0 off-grid closes) · seconds component (all `:00`) · parser-skipped
lines (0) · duplicates within a symbol (0) · row order (0) · OHLC (0) · daily
file dates (0 unparseable, 0 duplicate) · COVID closure window 2020-03-26 →
05-30 (0 rows in minute **and** daily) · in-session market-wide batch stamping
(none found in review) · input hashes: all 417 minute files, 421 daily files and
the EOD parquet are in `manifests/minute_qa_manifest.json`.

## 17. What v1 got wrong (all corrected above)

Read "shifted timestamps" for what are snapshot rows · called two real
Saturday sessions non-sessions · missed the 25 whole-market capture gaps ·
called volume-1 rows placeholders · attributed the volume mismatch to missed
prints when 73% of it is over-counting by snapshot rows · offered a
closing-average rule as "consistent" when the data does not support one rule ·
counted zero-price rows again inside the jump and gap detectors · excluded
2024-01-31 from the floor era (midnight bound) · missed the ONEBANK rename and
the SME-board daily gap · did not see the first floor regime · three arithmetic
slips (19,164 not 18,164; 285 not 275; "302 in October") · the JSON was not
strict JSON. The pattern is the same as in the daily-data work: **the counts
were right; the readings needed hostile review.**

## Files

`qa/MINUTE_QA_ISSUES.json` (aggregate, breakdowns by year/hour/date, thresholds,
unverified flags, capped samples) · `qa/MINUTE_QA_SYMBOLS.csv` (one row per
symbol, every check incl. cross-checks and snapshot counts) ·
`qa/MINUTE_QA_COVERAGE_YEAR.csv` · `qa/MINUTE_QA_SYMBOL_DAYS.csv.gz` (674,763
symbol-days: rows, first/last print, overnight gap, band flags, snapshot flags,
closing-session flag, date-in-daily flag) · `qa/MINUTE_QA_SESSION_BY_DATE.csv`
(market-wide session window per date) · `manifests/minute_qa_manifest.json`.
