# PUBLIC SOURCE MATRIX — every public Bangladesh market surface, and what it actually returned

Every row was requested from this container on **2026-09-08** (market closed) and the
response inspected. `STATUS` is what happened on the wire, not what a site claims to offer.
A route that was looked for and not found keeps its row with a 404 rather than being
deleted, because a deleted row disappears from `PUBLIC_DATA_GAPS.md` too.

Grades: **V** fetched and parsed here · **P** fetched, no committed parser yet ·
**F** reachable but intermittently failing · **B** blocked, with the blocker named ·
**X** excluded on policy.

## Working — parsed, tested, wired into the engine

| # | Source | Channel | Access | Fields | Cadence | Delay | Raw format | Status | Last verified | Limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **StockNow** | `GET stocknow.com.bd/api/v1/instruments` | public JSON API, no auth | 473 instruments: code, name, sector_id, category, open/high/low/close, ycp, trades, volume, value, yearly high/low, **7d/15d/30d/90d/180d/365d reference prices**, floor flag, sme flag, `updated_at` | 120 s open / 3600 s closed | EOD-stamped after close (`updated_at` 2026-09-07 14:09:30 = the 14:10 close) | JSON, 345,202 B | **V** | 2026-09-08 | no book, no tape |
| 2 | **CSE** | `GET www.cse.com.bd/market/current_price` | public HTML | 388 rows: stock code, LTP, open, high, low, YCP, trade, value(MN), volume | 120 s open / 3600 s closed | not stated | HTML, 433,763 B | **V** | 2026-09-08 | code is rendered one letter per node; no book |
| 3 | **BullBD** | `GET bullbd.com/detail/<TICKER>` | public HTML, inline `window.__INITIAL_STATE__` | company (ticker, name, exchange, **MIC XDHA**, sector, category, yearEnd); marketSnapshot (ltp, changeAbs/Per, ycp, o/h/l, volume, valueCrore, trades, **ISO-8601 UTC timestamp**); fundamentals (**annualizedEps, auditedNav, annualizedPe, priceToNav, paidupCapital, outstandingShares**) | 6 h | snapshot stamped at the previous close | HTML, 34,940 B | **V** | 2026-09-08 | one request per symbol; depth/minute slots are socket-filled (row 12) |
| 4 | **LankaBD** ×7 | `/Home/MarketDepthData`, `/api/datafeed/IndexLiveData/LiveStockWatchData`, `/api/Company/MkSecondDataSymbol`, `/api/datafeed/IndexLiveData/LiveDSETradeStatistics`, `/api/APIMarket/GetLatestBlockMarket`, `/Home/CircuitBreaker`, `/api/APIMarket/GetDataGrid` | anti-forgery token + session | top-N book, all-symbol L1 with exchange stamps, ~1/min cumulative tape, market totals, block prints, circuit limits | 20–3600 s | live | JSON / HTML | **V** | circuit re-verified 2026-09-08 | no order counts, no prints, no queue |
| 5 | **DSE** `cbul.php` | `GET www.dsebd.org/cbul.php` | public HTML | 636 rows: breaker %, tick, open adj. price, lower/upper limit | 1 h | reference | HTML, 580,782 B | **V** | 2026-09-08 | broken TLS chain upstream → host-scoped fallback |

## Parsed and tested — Stage 1 completion, 2026-09-08

| # | Source | Channel | Fields parsed | Cadence | Raw format | Status | Limitations |
|---|---|---|---|---|---|---|---|
| 6 | **Bangladesh Bank** bills | `GET .../monetaryactivity/bbbill` | per auction: issue date, ISIN, tenor, bids received (count / face value Cr / yield range), bids accepted (count / face value / sale value / yield range / weighted avg price), `any_bid_accepted` | 24 h | HTML, 44,460 B | **V** | a rejected auction row is shorter than an accepted one; the parser tolerates the variable width rather than assuming a column count |
| 7 | **BSEC** publications | `GET sec.gov.bd/` | 35 publications, 30 dated: `announcement_date`, `announcement_type` (Directive / Notification / Amendment / Order), title, category (law_order_directive / press_release / draft_rule / circular / download), PDF URL | 24 h | HTML, 385,303 B | **V** | the parser records that a publication exists, not what the PDF says. Extracting a penalty or a symbol from the PDF is a separate job and is not guessed |
| 8 | **CDBL** statistics | `GET www.cdbl.com.bd/` | depository participants **560**, BO accounts operable **1,661,613**, enlisted ISINs **829**, CDS market value **3,115,786**, shares in CDS **105,084** | 24 h | HTML, 82,085 B | **V** | figures are label/value pairs, not a grid; every pair seen is kept so a wording change is visible rather than silently unmatched |
| 9 | **DSE ownership** | `GET dsebd.org/displayCompany.php?name=<SYM>` | one row per as-on date: sponsor/director, govt, institution, foreign, public % | **30 d, per symbol** | HTML, ~310,000 B | **V** | DSE publishes exactly **3** as-on dates per company and keeps no archive — this cadence is the only remedy, and it pays off in months, not today |

## Blocked by a bot challenge — recorded, not worked around

| # | Source | Channel | What actually comes back | Status |
|---|---|---|---|---|
| 10 | **Bangladesh Bank** FX | `GET .../econdata/exchangerate` | **HTTP 200, 44,427 B, and the body is a CAPTCHA** — "This question is for testing whether you are a human visitor", a support ID, and an image code. Reproduced on every attempt on 2026-09-08. | **B** |

This is why `bot_challenge` exists in the failure taxonomy: before it, this page counted as a
**WORKING** source in the status file, because it answered 200 with a large HTML body. Solving
a bot challenge is not something this system does, so `bb_exchange_rate` is registered blocked
with that reason and `fx_rate` stays NOT_OBSERVABLE.

## Reachable but intermittently failing

| # | Source | Channel | Observed | Status |
|---|---|---|---|---|
| 10 | **DSE** all-symbol | `GET www.dsebd.org/latest_share_price_scroll_l.php` | 200 with 548,390 B on a 60 s timeout with retry; `ConnectionResetError(104)` on two of four attempts from this container | **F** |
| 11 | **DSE** sessions | `GET www.dsebd.org/hts.php` | 200 with 294,990 B on retry; reset on the engine's first pass | **F** |

Both are recorded as `connect_error` GAPs when they fail, so the hole is in the record
rather than silently absent. The `POST /ajax/load-instrument.php` depth route is unchanged
and stays wired; it was not re-exercised here because the market was closed.

## Blocked and excluded — kept in the registry on purpose

| # | Source | Blocker | Status |
|---|---|---|---|
| 12 | **BullBD** depth / minute series / circuit | `selectedShareMarketDepth`, `minuteDataVolumeSeries` and `selectedShareCircuitBreaker` ship as **empty or hollow** slots in the server-rendered state (the circuit slot arrives as `{"ul":0,"ll":0,"fl":0,"date":""}`) and are filled client-side over a socket (`global.socketData`). A plain GET observes no book. | **B** |
| 13 | **AmarStock** | `robots.txt` names `ClaudeBot`, `anthropic-ai` and `Claude-Web` under `Disallow: /`. **Not fetched, at any cadence, for any reason.** | **X** |
| 14 | Broker terminal / FIX / commercial L2 | unchanged from `evidence/SOURCE_ACCESS_LEDGER.md` rows 12–15 — adapters built and tested, awaiting an entitlement or an owner HAR | **B** |

## Routes searched for and not found (404) — recorded so they are not retried blindly

`stocknow.com.bd/api/v1/` + `stocks`, `latest-share-price`, `todays-share-price`,
`market-depth/<SYM>`, `stock/<SYM>`, `prices`, `market/status`, `market-status`, `indices`
— all **404, 35 B JSON**. Only `/instruments` exists on that base.
`www.cse.com.bd/market/top_gainer` and `/market/inst_news` — **404, 1,130 B**.
`www.dse.com.bd/data/data_center_data` — **404** (see `evidence/circuit_proposal_review/`).

## Engine run, 2026-09-08 02:5x UTC, session phase CLOSED

`./run_public_market_engine` → **8 WORKING · 2 DEGRADED · 7 UNTRIED · 2 BLOCKED**,
9 raw records, hash chain verified. UNTRIED are the trading-phase-only book and tape
sources, correctly not polled with the market shut.
