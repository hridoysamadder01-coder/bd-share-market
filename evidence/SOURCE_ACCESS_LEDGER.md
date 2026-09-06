# SOURCE ACCESS LEDGER — what was actually reached, from where, on 2026-09-06

Every row was tested first-hand from the research container on 2026-09-06 (UTC 00:36–01:45,
market closed; live rows are re-verified by the capture runner from 03:50 UTC). Grades:
**V** fetched and parsed here · **B** blocked by an external dependency named exactly ·
**X** excluded on policy.

| # | Source | Channel | Result | What it observes | Not observable here |
|---|---|---|---|---|---|
| 1 | **dsebd.org — exchange website, public depth** | `POST /ajax/load-instrument.php {inst}` (the AJAX behind `mkt_depth_3.php`) | **V** — 200, Buy/Sell two-column tables + Price Statistics; TLS chain broken upstream → verified-then-fallback (recorded per request) | top-N bid/ask price+qty, LTP, O/H/L, published close, yclose, day trades/volume/value | order counts, prints, side, exchange timestamp |
| 2 | dsebd.org latest share price | `GET /latest_share_price_scroll_l.php` | **V** — 395 rows parsed with the DSE-AI-TRADER parser (vendored) | all-symbol LTP/H/L/YCP/trades/volume/value | book, prints |
| 3 | dsebd.org holidays & sessions | `GET /hts.php` | **V** — 2026 holiday table; sessions: Public/Spot/Debt continuous 10:00–14:00, close session 14:00–14:10, pre-open "Not Applicable" | reference calendar | — |
| 4 | **LankaBD (LankaBangla Securities) market depth** | `POST /Home/MarketDepthData` (anti-forgery token) | **V** — 200 in ~0.4 s with a persistent session; identical table shape to #1 | as #1 + buy/sell %, total buy/sell volume | as #1 |
| 5 | LankaBD live stock watch (all symbols) | `GET /api/datafeed/IndexLiveData/LiveStockWatchData` | **V** — 638 instruments, per-instrument **exchange last-modification time to the second**, public/spot split, market category; portal UI refreshes it every 15 s | all-symbol L1-lite with exchange stamp | book |
| 6 | LankaBD per-company second data | `GET /api/Company/MkSecondDataSymbol?cid=&tradeCounts=` | **V** — exchange-stamped cumulative (trades, volume, value, price) rows, ~1 per minute per symbol, whole day in one call | minute-grade tape (INFERRED intervals) | individual prints |
| 7 | LankaBD market statistics | `GET /api/datafeed/IndexLiveData/LiveDSETradeStatistics` | **V** | market totals, breadth, exchange stamp | — |
| 8 | LankaBD block market | `GET /api/APIMarket/GetLatestBlockMarket` | **V** | block-board prints per symbol (daily list) | intraday block timing |
| 9 | LankaBD circuit breaker | `GET /Home/CircuitBreaker` | **V** — 636 DSE rows: breaker %, tick, lower/upper limit, reference date | dated limit reference (Q12/Q13) | — |
| 10 | LankaBD data grid | `GET /api/APIMarket/GetDataGrid`, `/Home/DataMatrix` | **V** (HTML matrix parsed; JSON kept raw, schema learned on replay) | all-symbol L1 + fundamentals | — |
| 11 | DSE-AI-TRADER live deployment | https://dse-ai-trader.onrender.com | **B** — Render returns "Service Suspended" (HTTP 503). Dependency: the owner's Render service must be un-suspended (billing/plan) | would have given 2-minute L1 ticks history | — |
| 12 | DSE-Mobile / DSE Investor / M-Invest (DSE-FlexTP terminals) | native app / web terminal, TREC-broker registration + 2FA | **B** — no account credentials in this environment; two ingestion paths are built and tested: HAR import of the account holder's own web-terminal session (`seeing/capture/adapters/har_import.py`) and broker Level-II / T&S export import (`broker_export.py`). Dependency: owner's login + the platform's written permission to record own session (D-14) | Market Depth (top 10), Time & Sales, pre-open view | — |
| 13 | Authorized broker Level-II / Time & Sales export or API | CSV/JSON files | **B** — adapter built and tested on synthetic files; no export in hand. Dependency: a broker export or API entitlement | order counts if the export carries them; per-print tape with side | — |
| 14 | DSE MDS / BHOMS / FIX market data | FIX 4.4 35=A/V/W/X | **B** — parser, book applier and session code built and tested on synthetic messages (`fix_md.py`); `connect()` refuses without: host, port, SenderCompID, TargetCompID, credentials, market-data entitlement, DSE FIX dictionary | full depth with NumberOfOrders (346), trade prints with aggressor | — |
| 15 | ICE / LSEG / Bloomberg professional L1/L2 | commercial licence | **B** — no licence; would enter through #14 (FIX) or a file export (#13) | L2 real-time | — |
| 16 | AmarStock (public SignalR quote stream, depth pages) | `www.amarstock.com` | **X** — reachable (200), but `robots.txt` disallows `anthropic-ai` / `ClaudeBot` and the bundle names its hub in a way that reads as a refusal of automated access (design doc §4); not used | — | — |
| 17 | StockNow, StockSupporter | public sites | not used — no depth or tape endpoint identified; StockSupporter company page returned 404 | — | — |
| 18 | Muntasib-creator/DSE_dataset (real trade-minute data 2015-10 → 2024-01, MIT) | sparse git checkout of 6 symbols (`/home/user/data_ext/dse_minute`) | **V** — real previously captured dynamic DSE data; adapter `minute_dataset.py` tested on the BRACBANK file | minute OHLCV tape | book |
| 19 | `evidence/lankabd_marketdepthdata_BRACBANK_2026-09-02_after-close.json` | earlier manual fetch in this repository | **V** — used as a parser fixture | one after-close book | — |

## Consequence for the fused state (LankaBD + dsebd public sensors)

OBSERVED: top-N book (two independent sensors, aligned on receipt time), LTP/O/H/L/close,
day totals, exchange-stamped cumulative tape rows (~1/min), all-symbol L1 with exchange
stamps, circuit limits, block prints, market totals, session calendar.
INFERRED: interval trades/volume/VWAP, trade side (quote rule), event classes
(consumed / cancelled / replenished / swept), all microstructure features and states.
NOT_OBSERVABLE: number of orders per level, individual prints, queue position, order ids,
intra-interval add/cancel netting — until #12/#13/#14 are attached. Each of those attaches
through an adapter that already exists and is tested; no new design is needed.

## 2026-09-06 scout — what it takes to obtain per-trade prints and order-count depth (no login attempted)

Method: read-only public GETs, robots.txt first per host, ≤ 15 requests/host, three scouts each adversarially
re-verified (logs and bodies under the session scratchpad `scout/`). OBSERVED = fetched and seen; INFERRED = read in
a manual/JS; NOT_VERIFIED = could not be reached.

| finding | truth | evidence |
|---|---|---|
| LankaBD public surface has **no** per-trade prints, **no** order counts, **no** push transport; depth is on-demand POST only; polls at 15/30/60/120 s; TradingView datafeed `has_intraday:false` | OBSERVED | 6 JS bundles + 4 pages re-grepped (0 hits for websocket/signalR/SSE); LiveStockFeed needs token+cookie (400 without) |
| dsebd.org: HTML pages + four AJAX endpoints, nothing finer than the displayed book | INFERRED (host TLS chain broken from here; captured via verify=False fallback) | segments `dsebd_depth__*` 200 |
| DSE-Investor / DSE-Mobile (Flextrade) terminals: login = username + password + 30-s Security Code from the registered device; one device per account; web-only still needs device registration; screens: Bid-Ask (Market Depth), Last Transaction (last two), Blotter; **no export** in either manual | OBSERVED (manuals: basl-bd.com User_Guide_DSE_Investor_v1.0.pdf Rev. 2017-04-04; islamibanksecurities.com DSE Mobile guide v1.1 2023-03) | terminal host `investor.dsetrade.com` is NXDOMAIN from public DNS (BD/broker-side resolution, NOT_VERIFIED) |
| Broker-side FlexTP dealer terminal documents "Time and Sales — view each execution", "Export Orders and Executions to Excel", "watch list export to excel" | INFERRED (2014 dealer training deck, third-party mirror) | broker terminal, not investor-facing |
| No scouted TREC broker (LankaBangla, UCB, EBL, Green Delta, BRAC EPL, IDLC, City, Shanta, Sheltech) publishes a no-login Time & Sales or order-count depth page, nor a client API; app listings mention "Market Depth (Level II) by Price and by Order" (TradeXpress) and "Time & Sale" (UCB UTrade) **behind login** | OBSERVED / INFERRED | bracepl marketdepth: no table; idlc downloads: forms only; lbsbd iBroker manual: Excel/PDF of own trades only |
| DSE certified 47 brokers for FIX API (BHOMS programme since 2020; 9 more on 2025-11-27 incl. IDLC and LankaBangla) | OBSERVED (BSS news) | broker-only entitlement |

**Exact owner inputs that would move #12/#13/#14 from B to V**
1. Which broker holds the owner's BO account (selects the DirectFN / XFL Trade / DSE-Mobile route).
2. A HAR export of one **own** web-terminal session (DevTools → Network → Export HAR) covering a Market Depth + Time & Sale screen, recorded from a network where the terminal host resolves, with the platform's written consent (D-14). Credentials, password and the registered device are prerequisites the owner uses; they are never handed over.
3. A broker's written reply to a request for a Level-II (with order counts) or Time & Sales CSV/JSON export, or a FIX market-data / drop-copy entitlement (host, port, SenderCompID, TargetCompID, credentials, dictionary).
