# Public DSE data acquisition — 2026-09-06

One rerunnable collector (`collector/dse_public_collector.py`), three passes on 2026-09-06
(market closed: 15:35–15:50 UTC / 21:35–21:50 Dhaka). Public endpoints only, normal browser
flows reproduced, nothing authenticated or paywalled touched, no field invented.

Dataset: `evidence/public/2026-09-06/` (normalized + metadata). Byte-exact raw bodies stay
under `data/raw/` (3,111 files, 49 MB, gzip above 64 KB with a verified round trip) and are
shipped in the artifact zip; they are not committed.

## What came back

| table | rows | what it is | truth |
|---|---:|---|---|
| symbols.csv | 725 | union universe: which surface each symbol appears on, company id, sector/tick/limits | OBSERVED |
| market_watch.parquet | 637 | all-symbol L1 with per-instrument exchange timestamps (LankaBD) | OBSERVED |
| market_grid.parquet | 412 | all-symbol grid (LankaBD) | OBSERVED |
| dse_latest.parquet | 395 | all-symbol L1 + day totals (dsebd.org) | OBSERVED |
| market_depth.parquet | 556 | displayed levels: symbol, side, level, price, volume, per sensor | OBSERVED |
| market_depth_summary.parquet | 1,450 | per symbol per sensor: best bid/ask, level counts, day totals | OBSERVED |
| trades_or_tape.parquet | 33,307 | exchange-stamped cumulative intraday rows, 390 symbols | OBSERVED_CUMULATIVE |
| tape_interval_flow.parquet | 32,917 | interval trades/volume/value/VWAP from consecutive rows | INFERRED |
| circuit_limits.parquet | 635 | LankaBD circuit table: limits, tick size, breaker % | OBSERVED |
| dse_circuit_breaker_official.parquet | 636 | the exchange's own circuit-breaker page | OBSERVED |
| block_market.parquet / dse_market_statistics_block.parquet | 35 / 35 | block board, two independent sources | OBSERVED |
| market_stats.parquet | 1 | LankaBD market totals and breadth | OBSERVED |
| dse_market_statistics_breadth / _totals | 8 / 1 | official day-end breadth per category; trades 178,150, volume 204,341,869, value 5,452,768,307.80 Tk; market cap equity 3.457 tn, mutual fund 30.2 bn, debt 3.430 tn, total 6.917 tn Tk | OBSERVED |
| company_fundamentals.parquet | 636 | sector, category, market cap, free-float market cap, outstanding securities, capital, face value, market lot, P/E (audited and un-audited kept apart), 52-week range | OBSERVED |
| company_financials.parquet | 1,954 | yearly EPS, dividend, dividend yield | OBSERVED |
| company_pe_history.parquet | 12,570 | every dated P/E column, labelled by basis | OBSERVED |
| company_shareholding.parquet | 1,220 | sponsor/govt/institute/foreign/public %, per as-on date | OBSERVED |
| dse_pe_at_a_glance.parquet | 419 | the exchange's own all-symbol P/E page | OBSERVED |
| dse_close_price.parquet | 636 | official close price page | OBSERVED |
| dse_sector_wise_company_list.parquet | 23 | sector listing | OBSERVED |
| dse_marginable_securities.parquet | 144 | margin-financeable securities | OBSERVED |
| dse_top_ten_gainer / _loser.parquet | 10 / 10 | official day-end movers | OBSERVED |
| dse_recent_market_information.parquet | 30 | recent market information | OBSERVED |
| historical_prices.parquet | 310,895 | official day-end archive, 691 symbols, 477 trading days | OBSERVED |

Total normalized rows: **400,297**. Requests: 3,043 + 57 + 17 across the three passes,
4 transport errors, 1 TLS-chain fallback (dsebd.org), 3 duplicate raw payloads.

## Verified limits of the public surface

- **The archive window is 2024-09-08 → today.** Every month before that returns a 200 with no
  data table. The walk went newest-first and stopped after twelve consecutive served-empty
  months (back to 2023-09). Older daily history is not available from this endpoint.
- **Depth is a snapshot, and the market was closed:** 171 of 725 symbols still showed resting
  levels, up to 5 levels per side. Both sensors were compared at L1 on the 93 symbols where
  both had a bid: **93/93 identical**.
- **Cumulative, not prints:** the LankaBD company feed carries the day's running totals at an
  exchange timestamp (~1 row/min, 100 % of rows stamped). Interval flow is differenced from it
  and labelled `INFERRED_FROM_CUMULATIVE`; a negative difference is kept and flagged, not repaired.
- **Company pages:** 636 of 725 returned data; 419 carry the full basic-information block
  (the rest are bonds, debentures and T-bills whose pages have no such table). 89 pages had no
  fundamentals table and were recorded as failures rather than filled with zeros.

## Not available from any public source reached (never fabricated)

individual trade prints · trade side/aggressor · number of orders per depth level · order ids ·
queue position · intra-interval add/cancel netting · an exchange timestamp on the book itself
(depth pages carry none; the receipt time is recorded instead).

These need a richer feed: a HAR recording of the owner's own broker terminal session
(`seeing/capture/adapters/har_import.py`), a broker Level-II / Time & Sales export
(`broker_export.py`), or a FIX market-data entitlement (`fix_md.py`). See
`evidence/SOURCE_ACCESS_LEDGER.md`.

## Rerun

```bash
python3 -m collector.dse_public_collector --out data --all-depth --all-tape --company \
        --extras --history-start 2024-01-01
```

Parsers are covered by `tests/test_collector.py` (8 tests on archived real payloads).
