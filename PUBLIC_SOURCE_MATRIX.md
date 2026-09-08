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

## Fetched and kept raw — no committed parser yet

| # | Source | Channel | Access | Fields | Cadence | Raw format | Status | Last verified | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| 6 | **Bangladesh Bank** FX | `GET www.bb.org.bd/en/index.php/econdata/exchangerate` | public HTML | USD/BDT reference rate tables | 24 h | HTML, 46,215 B | **P** | 2026-09-08 | BB re-lays-out its tables between publications; column mapping deferred to replay |
| 7 | **Bangladesh Bank** bill rates | `GET .../monetaryactivity/bbbill` | public HTML | BB bill / policy-adjacent rates | 24 h | HTML, 49,210 B | **P** | 2026-09-08 | as above |
| 8 | **BSEC** | `GET sec.gov.bd/` | public HTML (`robots.txt`: `Disallow:` — empty, everything allowed) | regulator publications index | 24 h | HTML, 385,303 B | **P** | 2026-09-08 | enforcement orders are largely PDF; no structured listing found yet |
| 9 | **CDBL** | `GET www.cdbl.com.bd/` | public HTML | CDS / BO participation statistics index | 24 h | HTML, 82,085 B | **P** | 2026-09-08 | statistics pages not yet located behind the index |

The raw-first design makes **P** a legitimate resting state: the bytes are captured from
today onward, and a parser written next month can be re-run over every byte ever stored.
What is *not* legitimate is inventing a column mapping for a layout nobody has studied.

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
