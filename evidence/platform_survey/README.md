# What the DSE platforms actually show — and the field nobody in this repo has tested

Survey run 2026-09-07/08 after the owner asked a plain question: *if you knew someone held
200,000 shares of a 1,000,000-share company at 62.35, what would you understand from that?*
That is a 20 % position in one hand, and it is not a price-and-volume question. It sent this
survey looking for what the market's own tools publish about **ownership**, not about price.

## Method and access policy

`robots.txt` was read first for every host, and honoured.

| platform | robots.txt | used |
|---|---|---|
| StockNow (`stocknow.com.bd`) | `Allow: /` | yes |
| BullBD (`bullbd.com`) | `Allow: /`, disallows `/portfolio`, `/watch-list`, `/my/`, `/message`, `/notification`, `/login`, `/admin/` | yes — public pages only, none of the disallowed paths |
| StockSupporter (`stocksupporter.com`) | `Allow: /` | yes |
| **AmarStock (`amarstock.com`)** | **explicitly `Disallow: /` for `ClaudeBot`, `anthropic-ai`, `Claude-Web`** | **NOT fetched** |
| EcoSoft OST (`ost.ecosoftbd.com`) | login page, no robots.txt | not crawled; the owner's own HAR recordings were used instead |
| LankaBD, dsebd.org | already in `SOURCE_ACCESS_LEDGER.md` | as before |

A handful of requests per host, spaced. Public pages only.

## What each platform publishes

| platform | pages | what it is |
|---|---|---|
| StockNow | 475 URLs, 472 of them `/stocks/<code>-<name>.html` | one page per instrument: price and fundamentals |
| BullBD | 1,058 URLs — 420 `/news`, 409 `/detail`, 188 `/event`, 16 `/top-list`, plus `/index-mover`, `/floating-cap`, `/halt-list`, `/block-share`, `/sector-list`, `/live-top-list`, `/category-list`, `/advance-chart` | the widest surface of the three |
| StockSupporter | 62 URLs — `/screener`, `/beta`, `/block-tr`, `/index-mover`, `/supercharts`, `/stock-details`, `/sector` | screener and derived analytics |

All three render through JavaScript, so the tables are not in the served HTML. BullBD ships its
state object inline, and its 177 keys are the clearest statement of what a good DSE tool tracks:

* price and tape — `ltp`, `ycp`, `price_change`, `price_change_per`, `avg_value`, `minuteDataVolumeSeries`
* book — `selectedShareMarketDepth`, `selectedShareCircuitBreaker`
* fundamentals — quarterly `selectedShareEPSChart`, `selectedShareNAVChart`, `selectedShareQ1..Q4Series`, `selectedShareYearlyMaxMin`, `selectedShareDividendChart`, `betaOfSelectedShare`
* corporate calendar — `latestAGMAndRecordDates`, `upcoming_corporate_events`
* **ownership — `selectedShareHoldingChart`, `selectedShareHoldingData`**

That last line is the finding. The one thing the best public tools surface beyond price and
volume is **who holds what percentage, and whether it is changing**.

## The same data is already in this repository, and no experiment has touched it

`evidence/public/2026-09-06/normalized/company_shareholding.parquet` — collected 2026-09-06 by
`collector/dse_public_collector.py`, straight from `dsebd.org/displayCompany.php`, every row
carrying its raw path, SHA-256 and `truth = OBSERVED`.

* 1,220 rows, **419 symbols**, five columns: `sponsor_director_pct`, `govt_pct`,
  `institute_pct`, `foreign_pct`, `public_pct`
* **401 symbols carry 2+ as-on dates, 400 carry 3** — so change is computable, not just level
* consecutive gaps are ~1 month at the median (min 28 days), with a long tail back to 2001

Derived here: `shareholding_last_change.csv` — the last observed change per symbol.

| institutions added most | | institutions cut most | |
|---|---|---|---|
| SAIHAMTEX | +19.71 → 35.55 % | ARGONDENIM | −12.46 → 31.41 % |
| EXIM1STMF | +14.35 → 40.26 % | NFML | −14.03 → 8.59 % |
| ICB | +25.35 → 27.00 % | NITOLINS | −8.21 → 18.64 % |
| SHARPIND | +6.29 → 33.59 % | 1STPRIMFMF | −7.38 → 23.98 % |

68 symbols moved more than 2 points on the institutional line; the cross-sectional standard
deviation of that change is 5.22. **332 of 401 symbols have a sponsor/director block of 20 % or
more** — the owner's hypothetical is the normal case on this exchange, not an edge case.
SAIHAMTEX and SHARPIND are both inside the frozen 14-symbol micro universe.

## Why this is not a rediscovery

Every candidate in `REJECTED_CANDIDATES.md` — R2-1 volume spike + breakout, R2-2 panic rebound,
R2-3 cross-sectional mean reversion, P45-1 through P45-8 — is built from price and volume.
Ownership change has never been tested in this repository. It is a different class of input:
declared, monthly, and about who is accumulating rather than what the tape did.

## UPDATE 2026-09-08 — the blocker below is cleared

The price gap that made this untestable has been closed. `data/extend_dse_eod.py` pulls the
missing months from DSE's day-end archive through `bdshare` and appends them to the bar table
after verifying the two sources agree.

| | before | after |
|---|---|---|
| price table ends | 2026-01-22 | **2026-09-07** |
| rows | 862,073 | **917,206** |
| **symbols with 2+ holding dates inside the price range** | **22** | **371** |
| holding observations with forward prices | — | **1,113**, median 46 trading days forward |

The overlap was checked rather than assumed: on 2026-01-01…22, 1,182 rows across 74 symbols,
the archive and the owner's CSVs matched to the last decimal on open, high, low, close and
volume — 100 % on every field. Every row carries its `source`, so the two can never be silently
mixed. `RAW_EXTENSION_MANIFEST.json` holds the hashes, counts and that overlap check.

The ownership question is now measurable **in principle**. Measuring the denominator immediately
afterwards showed it is not measurable **in practice** — see the next section. No pre-registration
was written, because writing rules for a test the data cannot run is theatre.

## The real blocker: DSE publishes three as-on dates per company, and keeps no archive

Before writing any rules, the usable sample was counted. It collapses.

Signal date taken as the first trading day at or after `as_on + 21 days` (a conservative stand-in
for publication, since the page carries no publication timestamp — only "as on"):

| forward horizon | observations | symbols | distinct as-on dates | concentration |
|---|---|---|---|---|
| 20 trading days | 208 | 184 | 29 | **162 of 208 (78 %) share the single as-on month 2026-06** |
| 40 trading days | 46 | 24 | 28 | scattered singles across 2021–2025 |

208 rows that mostly share one month are not 208 independent observations; they are close to one
event. Whatever the market did after June 2026 would be the entire result, dressed up as a signal.
At 40 days there are 46 rows over 24 symbols, which decides nothing either.

The cause was checked at the source rather than inferred. `dsebd.org/displayCompany.php` exposes
exactly **three** as-on dates per company — the last year-end plus the two most recent months:

```
SAIHAMTEX : Jun 30, 2025 (year ended) | Jul 31, 2026 | Aug 31, 2026
BEXIMCO   : Jun 30, 2024 (year ended) | Jun 30, 2026 | Jul 31, 2026
GP        : Dec 31, 2025 (year ended) | Jul 31, 2026 | Aug 31, 2026
```

So each symbol yields at most two changes, both from the last few months, and the most recent ones
have no forward price yet. There is no historical archive of monthly shareholding on the exchange's
site to backfill from. BullBD's `selectedShareHoldingData` came back empty for the symbol checked.

**Status: BLOCKED_BY_DATA, and extending the price table did not fix it** — that fix was real and
worth doing (22 → 371 symbols joinable), but the binding constraint turned out to be the *ownership*
history, not the price history.

### What would actually unblock it

Collect the snapshot every month from now on. `collector/dse_public_collector.py` already captures
these five columns; it simply has to run monthly, because the exchange overwrites the window rather
than archiving it. Each run adds one as-on date per symbol, and a year of that is a real panel:
~400 symbols × 12 dates, spread across 12 independent months instead of concentrated in one.

That is a slow clock — one observation per symbol per month — and it should be started rather than
argued about. Nothing about the idea is testable before it has run for several months, and no
number computed from today's data would mean anything.

## The blocker as it stood on 2026-09-07 (kept for the record)

The join was checked rather than assumed, and it does not hold up yet.

| | |
|---|---|
| EOD price table ends | **2026-01-22** |
| holding as-on dates run to | 2026-08-31 |
| holding rows falling inside the price range | 470 rows across 417 symbols |
| symbols with **2+ holding dates inside the price range** | **30** |
| symbol overlap between the two tables | 365 of 419 |

Thirty symbols is the real denominator, and it is far too small to test anything. Almost every
usable change — the 2026-06 → 07 → 08 steps that carry the interesting moves, SAIHAMTEX and
ARGONDENIM among them — lands **after** the price history ends, so there is no forward return to
score it against.

**Status: BLOCKED_BY_DATA, on a fixable dependency.** The fix is not more analysis, it is
extending the EOD price table from 2026-01-22 to the present with `data/ingest_dse_eod.py`.
Roughly seven months of daily bars for ~392 symbols would take the testable set from 30 symbols
toward the full 401. Until that is done, any number computed from this join is noise dressed up
as a finding.

## Other limits, stated before anyone gets excited

* Monthly-to-quarterly cadence. This can never be a same-day trading signal.
* At most 3 as-on dates per symbol here, so the history per symbol is very short even once the
  price gap is closed.
* A difference between two dates is not evidence of anything. It needs a pre-registered test with
  a real forward outcome and the same falsification battery as everything else, or it is data
  mining.
* Nothing here is a result. This file records what is available, what is blocked, and what has
  not been asked.
