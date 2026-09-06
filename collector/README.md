# collector — all-symbol public DSE acquisition

Raw-first, rerunnable, provenance-complete. Public/open endpoints only; normal browser
flows are reproduced (anti-forgery token, referer, XHR headers). Nothing authenticated,
paywalled or CAPTCHA-protected is touched, and no field is invented.

```bash
# everything, every symbol (depth from both public sensors, cumulative tape, company
# fundamentals, and the official day-end archive from 2015 to today)
python3 -m collector.dse_public_collector --out data --all-depth --all-tape --company \
        --history-start 2015-01-01

# market-wide surfaces only (fast: universe, watch, grid, circuit, market stats, block board)
python3 -m collector.dse_public_collector --out data

# a 5-symbol smoke run
python3 -m collector.dse_public_collector --out /tmp/smoke --all-depth --all-tape --company --max-symbols 5
```

Flags: `--min-gap` seconds between requests (default 0.4, one serialized polite client),
`--timeout`, `--max-symbols` (debug), `--history-end`.

## Output

```
data/raw/<source>/<utc>_<name>.<ext>[.gz]   byte-exact response bodies (gzip above 64 KB, round-trip verified)
data/normalized/
  symbols.csv                  the union universe: which surface each symbol appears on, company id,
                               sector/tick/limits from the circuit table
  market_watch.parquet         all-symbol L1 with per-instrument exchange timestamps (LankaBD)
  market_grid.parquet          all-symbol grid (LankaBD)
  dse_latest.parquet           all-symbol L1 + day totals (dsebd.org official)
  market_depth.parquet         one row per displayed level: symbol, side, level, price, volume, source
  market_depth_summary.parquet per symbol per sensor: best bid/ask, level counts, day totals
  trades_or_tape.parquet       exchange-stamped CUMULATIVE intraday rows (~1/min) — not prints
  tape_interval_flow.parquet   interval trades/volume/value/VWAP, INFERRED from consecutive rows
  market_stats.parquet         market totals and breadth
  circuit_limits.parquet       per-symbol upper/lower limit, tick size, breaker %
  block_market.parquet         block-board prints
  company_fundamentals.parquet sector, category, market cap, free-float market cap, outstanding
                               securities, capital, face value, market lot, P/E (audited and
                               un-audited kept apart), shareholding %, 52-week and day range
  company_financials.parquet   yearly EPS / dividend / dividend yield
  company_pe_history.parquet   every dated P/E column, labelled by basis
  company_shareholding.parquet sponsor / govt / institute / foreign / public %, per as-on date
  historical_prices.parquet    official day-end archive: date, symbol, OHLC, LTP, YCP, trades, value, volume
data/metadata/
  sources.json, observability.json, schema.json, field_coverage.json,
  validation.json, failures.json, manifest.json, duplicate_raw_payloads.json,
  depth_crosscheck.csv, sample_records.json, sessions.json
```

Every normalized row carries `source`, `endpoint`, `symbol`, `exchange`, the exchange
timestamp when the source has one, `receipt_utc`, `raw_path`, `raw_sha256`, `http_status`
and `truth`.

## Truth classes

`OBSERVED` the source delivered it · `OBSERVED_CUMULATIVE` day totals, not prints ·
`INFERRED` derived here (interval flow from cumulative differences, free-float share of
market cap) · `NOT_AVAILABLE` no obtained public source carries it: individual trade
prints, trade side, number of orders per level, order ids, queue position, intra-interval
add/cancel netting, and any exchange timestamp on the book itself (depth pages carry none;
the receipt time is recorded instead).

`data/metadata/observability.json` states this per field for the run that produced it.

## Notes

- The depth pages are **snapshots** of the displayed book. Two independent sensors
  (dsebd.org and LankaBD) are pulled for every symbol and compared in
  `metadata/depth_crosscheck.csv`.
- The LankaBD company feed is **cumulative**: a row carries the day's running totals at an
  exchange timestamp. `tape_interval_flow.parquet` differences consecutive rows and is
  labelled `INFERRED_FROM_CUMULATIVE`; a negative difference is kept and flagged
  `monotone_break` rather than repaired.
- Failures are never dropped silently: they are in `failures.json` and in `manifest.json`
  with status, error and the raw file when a body arrived.
- Parsers are covered by `tests/test_collector.py` against archived real payloads.
