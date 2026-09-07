# EcoSoft OST broker terminal — market-data schema map (from a real recording)

**Source recording.** `ost.ecosoftbd.com.har`, saved by the account holder from their own
logged-in browser (DevTools → Network → Save all as HAR) on 2026-09-07, 14:22:14 → 14:30:54 UTC
(20:22 → 20:31 Dhaka; **market closed**). 80 entries, one host (`ost.ecosoftbd.com`),
78 `application/json` + 2 `text/plain`, **0 WebSocket frames**.

**How it was processed.** `python3 -m seeing.capture.har_probe <har> rich_sensor_probe_2026-09-07.ndjson`
— the market-depth capture kit's own `sanitize()`/filters (ported from `rich_sensor_probe.mjs`),
applied to **response bodies only**. Request headers, cookies, authorization, postData content and
response headers were never read. URLs are reduced to scheme+host+path (query dropped).
Result: **58 records kept**, 11 skipped as login/auth/account-class URLs, 11 skipped as not
market data, **0 sensitive-looking keys found in any kept body** (self-check `leaked_keys()` = []).
The kit itself (`capture_dse_market_depth.bat` + Node CDP probe) is Windows/Chrome-only and cannot
run in this Linux container; its logic is what ran here.

**Adapter.** `seeing/capture/adapters/ecosoft_ost.py` (`EcoSoftOSTDepthAdapter`, `EcoSoftOSTIndexAdapter`,
`frames_from_probe()`), tests in `tests/test_ecosoft_ost_adapter.py`, fixtures
`tests/fixtures/ecosoft_ost_*_closed.json` (the real payloads, sanitized). The adapter never fetches:
it parses only what the account holder recorded.

## Endpoints observed

| endpoint | transport | polls in HAR | median gap | body | note |
|---|---|---|---|---|---|
| `GET /core/api/v1/Order/MarketDepth` | XHR/fetch, JSON | 49 | 11.0 s | 992 B, stable | full snapshot every poll (not incremental) |
| `GET /core/api/v1/Analysis/IndexData` | XHR/fetch, JSON | 9 | 60.0 s | 368 B, stable | DSEX index + breadth + market totals |

No WebSocket, no SSE, no incremental/delta messages. The page re-pulls the whole 10-row book on a timer.
The symbol is selected by query string (dropped by the probe); 1 symbol (CITYGENINS) was open during the recording.

## `Order/MarketDepth` payload → canonical fields

```
{Success, StockExchange, IsBlock, TradingCode,
 Depth: {TradingCode, DateTime, Open, Ltp, Ycp, Close, High, Low, Trade, Volume, Value,
         Source, Change, ChangePercentage,
         BuySellDetails: [ {BuyPrice, BuyVolume, SellPrice, SellVolume} × 10 ]}}
```

| source key | canonical / frame field | truth | observed value (2026-09-07) |
|---|---|---|---|
| `Depth.BuySellDetails[i].BuyPrice/BuyVolume` | `bid_levels[i]` (price, qty) | **OBSERVED** | 10 bids, 128.1/914 … 127.0/249, already sorted best-first |
| `Depth.BuySellDetails[i].SellPrice/SellVolume` | `ask_levels[i]` | **OBSERVED** | 7 asks, 128.3/949 … 139.0/5000; rows 8–10 carry Buy* keys only |
| `len(BuySellDetails)` | `book_depth_count` | **OBSERVED** | 10 (the terminal's cap; deeper levels not delivered) |
| first row | `best_bid`, `best_ask` | **OBSERVED** | 128.1 / 128.3 (spread 0.2 = 2 ticks) |
| `Depth.Ltp / Open / High / Low` | `ltp, open, high, low` | **OBSERVED** | 128.3 / 126.0 / 128.4 / 126.0 |
| `Depth.Close` | `close_published` | **OBSERVED** | 128.2 |
| `Depth.Ycp` | `yclose` | **OBSERVED** | 127.6 |
| `Depth.Trade / Volume` | `day_trades, day_volume` | **OBSERVED** | 212 / 112,750 |
| `Depth.Value` | `day_value_mn` | value **OBSERVED**, unit **INFERRED** (million BDT: 112,750 × 128.3 / 1e6 = 14.47 vs 14.376, ratio 0.994) | 14.376 |
| `Depth.Change / ChangePercentage` | `change, change_pct` | **OBSERVED** (= Ltp − Ycp) | 0.7 / 0.5486 |
| `Depth.DateTime` | `t_source_str` verbatim; `t_source_utc` | string **OBSERVED**; UTC interpretation **UNRESOLVED** (see below) | `2026-09-07T14:13:45.1505564Z` |
| HAR `startedDateTime` | `t_recv_utc` | **OBSERVED** (browser clock) | 14:22:14.127Z … |
| `Depth.Source` | `feed_source_tag` | OBSERVED string, meaning not interpretable | `"DWS"` |
| `StockExchange` | `stock_exchange_code` | OBSERVED; 1 ↔ DSE **INFERRED** from the symbol; CSE code not observed | 1 |
| `IsBlock` | `is_block` | OBSERVED | false |
| SHA-256 of body | `body_sha256` (dedup key) | **INFERRED** — the source has no sequence id | 48/48 later polls identical |
| — | `bid_orders_per_level`, `ask_orders_per_level` | **NOT_OBSERVABLE** | no order-count key anywhere in the payload |
| — | `trade_prints`, `trade_side`, `interval_*` | **NOT_OBSERVABLE** | no time-and-sales endpoint in the recording |
| — | `order_events`, `queue_position`, `level_quantity_delta`, `cancel_vs_trade_split` | **NOT_OBSERVABLE** | full snapshots only, no deltas |
| — | `sequence_id` / message id | **NOT_OBSERVABLE** | none; polls are timer-driven |
| — | `upper_limit`, `lower_limit`, `tick_size`, `breaker_pct`, `market_category` | **NOT_OBSERVABLE** here (public sensors carry them) | — |

### Timestamp convention — deliberately left unresolved
`Depth.DateTime = 2026-09-07T14:13:45.1505564Z`. Read literally (UTC) that is 20:13:45 Dhaka — six hours
after the close session ended (14:10). Read as Dhaka wall clock with a literal `Z` it is 14:13:45 Dhaka
= 08:13:45 UTC, 3 min 45 s after the close session — the plausible last book refresh. The recording
cannot decide (nothing moved while it ran), so the adapter exports **both candidates** and keeps
`t_source_utc = None`. An open-market recording resolves it in one step: compare `DateTime` against
`t_recv_utc`; then set `ecosoft_ost.T_SOURCE_TZ_CONVENTION` and record the recording here.

## `Analysis/IndexData` payload → canonical fields

```
{Success, Data: {TradingCode:"DSEX", StockExchange, IsSector, Date, Ycp, Ltp, Close, CloseOrLtp, Open, High, Low,
                 Volume, Trade, Value, Change, ChangePercentage, Gainer, Looser, Unchanged}}
```

| source key | frame field | truth | observed |
|---|---|---|---|
| `Data.Trade / Volume` | `market_trades`, `market_volume` | **OBSERVED** | 185,987 / 200,350,191 |
| `Data.Value` | `market_value_crore` (+ `market_value_mn` = ×10) | value **OBSERVED**, unit **INFERRED** (crore BDT: only ×1e7 gives a sane 28.5 BDT average price; million would mean 2.85 BDT) | 571.32463 |
| `Data.Ltp/Open/High/Low/Close/Ycp/Change/ChangePercentage` | `index_*` | **OBSERVED** | 5567.42 / 5569.40 / 5586.00 / 5491.61 / 5567.42 / 5558.45 / +8.97 / +0.16 % |
| `Data.Gainer / Looser / Unchanged` | `gainers, losers, unchanged` | **OBSERVED** | 168 / 146 / 63 (= 377 priced instruments) |
| `Data.Date` | `date_str` | OBSERVED, trading date only (no clock time, no suffix) | `2026-09-07T00:00:00` |

Note the two `Value` fields use **different units** (million vs crore) on the same terminal — both inferred
from arithmetic on this one recording and flagged on every frame; the raw number is always kept.

## Event / update semantics (what one closed-market recording can and cannot say)

* OBSERVED: each poll is a complete 10-row snapshot; no delta encoding; polling cadence ≈ 11 s (depth) and
  ≈ 60 s (index), driven by the page, not by market events.
* OBSERVED: 48 of 48 consecutive depth bodies were byte-identical (single hash) → dedup by `body_sha256` is
  the only change-detection available; `dup_payload` frames are a robustness flag, not an error.
* NOT_OBSERVABLE from this recording: whether `DateTime` advances on every book change or only on trades;
  whether the cap stays at 10 rows when the book is deeper; latency `t_recv − DateTime` (needs the tz
  convention above). All three become observable with one open-market recording (10:00–14:10 Dhaka).

## What this source adds — and does not add — over the public sensors

Same field ceiling as LankaBD `/Home/MarketDepthData` and dsebd `/ajax/load-instrument.php`: top-10
price+quantity both sides, LTP/OHLC/YCP, day totals. Cleaner (typed JSON, no HTML tables) and authenticated
(one broker's terminal rather than a public mirror), with an index/breadth endpoint. It does **not** deliver
order counts, prints, side, order ids, queue position or a sequence id. `bid_orders_per_level` stays
NOT_OBSERVABLE across every source reached so far; only the FIX path (`fix_md.py`, tag 346) makes it OBSERVED.

## Second recording — 2026-09-07 15:41:02–15:41:51 UTC (21:41 Dhaka, market closed), symbol AAMRANET

File: `rich_sensor_probe_2026-09-07T1541Z_AAMRANET.ndjson` (5 `MarketDepth` records; the probe's other
18 "text" candidates were the logged-in `/Order` HTML page and JS/CSS bundles — never evidence, and the
probe now refuses them, see `har_probe.STATIC_MIME`). Question asked of it by the owner: *compare the
depth just before and just after placing an advance order; is the order reflected?*

| what the recording contains | evidence |
|---|---|
| order-placement request | **none** — no POST to any create/place/DoAction path; the only non-list order call is `GET /core/api/v1/Order/AcceptOrder/4` → `{"Success":true,"Data":"Yes","Color":"blue"}` (a 44-byte yes/no flag, not an order) |
| the terminal's own order lists | `POST /core/api/v1/Order/AjaxSelect` ×5 and `POST /core/api/v1/AdvancedOrder/AjaxSelect` ×3, filter FromDate 06-Sep-2026, statuses {1,2,4,5,6,8,9,10} → **`total: 0`, `data: []` on every poll** |
| depth polls | 5, identical body (1 distinct SHA-256), `Depth.DateTime` fixed at `2026-09-07T14:27:45.0997961Z` |
| the book | **one level only**: bid 16.8 × 3000, no asks; LTP 17.4, Ycp 17.7, 321 trades, 244,508 volume; circuit 15.7–19.1 (from `Order/CompanyInfo.CircuitBreaker`), tick 0.1 |
| public cross-check at 15:45 UTC | LankaBD `POST /Home/MarketDepthData AAMRANET` → bids `[(16.8, 3000)]`, asks `[]`, LTP 17.4, 321 trades, 244,508 volume — **byte-for-byte the same book** |

Conclusions (OBSERVED unless marked):
* ~~`Order/MarketDepth` is the **exchange's** book as republished by the broker (`Source: "DWS"`), not a
  broker-side view: the public portal shows the identical single level at the same minute.~~
  **SUPERSEDED — see the third recording below.** This was inferred from one symbol whose book held a
  single residual level; a full book falsifies it. The claim is kept here, struck through, because raw
  evidence and the conclusions drawn from it are append-only.
* A broker-side advance order that has not reached the exchange still cannot be identified in this
  payload: it carries no owner or order id (unchanged).
* No before/after comparison is possible from this recording: no order event lies inside the 49-second
  window, and every snapshot is identical. Whether the owner's advance order equals the 16.8 × 3000 level
  is **NOT_OBSERVABLE** here — the depth payload has no owner/order-id field, and the terminal's own order
  list reported zero orders at the time.
* `Depth.DateTime` is **not** the exchange's last-modification time: LankaBD's watch feed stamps AAMRANET's
  last change at 14:06:57 Dhaka (feed end 14:14:51) and CITYGENINS at 14:00:00 Dhaka, while the broker
  stamps read 14:27:45 and 14:13:45 respectively. Read as Dhaka wall-clock, both broker stamps fall
  *after* the exchange's, i.e. a broker-side refresh time (INFERRED); read as UTC they fall in the evening.
  The UTC-vs-Dhaka convention stays UNRESOLVED; what is now settled is that this field must not be used
  as exchange event time.
* A one-level, ask-less book after the close is a real exchange state (public sensor agrees), so
  `n_ask_levels = 0` must be accepted by every consumer, never treated as a parse failure.

## Third recording — 2026-09-07 16:16:12–16:16:32 UTC (22:16 Dhaka, market closed), symbol AAMRATECH

File: `rich_sensor_probe_2026-09-07T1616Z_AAMRATECH.ndjson` (5 depth polls, 1 distinct body, 0 leaked keys).
No order-placement request; `Order/AjaxSelect` and `AdvancedOrder/AjaxSelect` again return `total: 0`.

**This recording falsifies the "terminal republishes the live exchange book" conclusion above.**

| | broker terminal, 22:16 Dhaka | LankaBD public, 22:17 Dhaka |
|---|---|---|
| AAMRATECH bids | **10 levels**, 17.20×600 … 16.20×100, total 64,746 | **0 levels** |
| AAMRATECH asks | **8 levels**, 17.40×3662 … 18.20×8014, total 21,083 | **0 levels** |
| LTP / trades / volume | 17.5 / 223 / 241,728 | 17.5 / 223 / 241,728 (identical) |

One minute apart, same symbol, same day totals, and the books disagree completely. The reading that fits
every observation so far (INFERRED, not yet proven):

* the broker's `Order/MarketDepth` while the market is closed is a **frozen snapshot taken at the close**,
  not a live view. It never changes between polls because there is nothing to change.
* the public portal shows the **live post-close state**, from which day orders have been purged.

This also explains the earlier CITYGENINS pair, which looked like a contradiction: the broker showed a
full 10×7 book at 20:22–20:31 Dhaka (frozen at its close stamp 14:13:45) while the public sensor showed
0×0 at 22:08. Nothing "emptied between 20:31 and 22:08" — the two sensors were showing different things
all along. AAMRANET matching on both sides (16.80 × 3000) is then either a genuine resting order that
survives the purge or a coincidence of a one-level book; one symbol cannot separate those.

### The timestamp convention is now settled enough to state (INFERRED, strong)

Three `Depth.DateTime` values, all carrying a literal `Z`:

| symbol | stamp | as Dhaka wall clock | as UTC |
|---|---|---|---|
| AAMRATECH | `14:11:50` | 14:11:50 — 1 min after the 14:10 post-close end | 20:11 Dhaka, nothing trades |
| CITYGENINS | `14:13:45` | 14:13:45 — 4 min after | 20:13 Dhaka, nothing trades |
| AAMRANET | `14:27:45` | 14:27:45 — 18 min after | 20:27 Dhaka, nothing trades |

All three cluster just after the close under the Dhaka reading and land in the middle of the evening under
the UTC reading. The field is **Dhaka wall clock with a literal `Z` suffix**. `T_SOURCE_TZ_CONVENTION` in
the adapter stays `None` until an open-market recording confirms it directly, because an inference from
three closed-market stamps is not the same as watching the stamp advance during trading.

### What this changes for the overnight question

The broker terminal cannot answer "are advance orders accumulating?" while closed — it is showing a frozen
picture of the close. Only the public sensor shows the live closed-market book, and that is what
`micro/engine/overnight_book_watch.py` polls. AAMRATECH has been added to its symbol list.

## Next observation that would change something
One HAR (or kit run) recorded **during continuous trading** with 3–5 symbols cycled through the depth panel.
That single recording resolves the timestamp convention, measures how often the book actually changes
between 11-s polls, and gives the microstructure engine its first non-static broker-terminal frames.
Nothing in this adapter needs to change for that — `frames_from_probe()` already replays it.
