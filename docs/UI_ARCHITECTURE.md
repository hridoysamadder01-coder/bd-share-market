# BD Market Intelligence OS — UI architecture

**Status.** Committed 2026-09-10. Two coexisting UIs served by one FastAPI process
in `tower/ui/server.py`:

* `/`  — **BD Market Intelligence OS shell** (new). Market-wide, multi-screen SPA
  built to `HRIDOY_DESIGN_LOCK.md`. Files: `static/shell.html`, `static/shell.js`,
  `static/shell.css`.
* `/observe` — **Observation Tower** (legacy). Single-symbol replay/live view.
  Untouched: `static/index.html`, `static/app.js`, `static/style.css`.

The shell **does not** rewrite the research engine. It exposes what the engine
already measures. Every field a source does not carry renders as `—`, never as `0`.

## Screens (routes)

| Route | Screen | Data used | Never |
|---|---|---|---|
| `#/home` | Mission Control | `/api/market/summary`, `/api/features/latest`, `/api/market/sectors`, `/api/market/universe` | invent breadth or unavailable metrics |
| `#/market` | Full-market table | `/api/market/universe` + `/api/features/latest` | reduce the universe silently |
| `#/radar` | 12-lens scanner | features `rel_volume_z`, `range_z`, `volume_price_divergence`, `accumulation_proxy`, `vol_regime_ratio`, `illiquidity_persistence`, `xs_rank_rel_volume`, `market_relative_ret`; circuit distances | invent RSI/MACD/Bollinger lenses |
| `#/sectors` | Sector map + drill-in | `/api/market/sectors` | fake sector performance for empty sectors |
| `#/watchlist` | Owner watchlist | localStorage + `/api/features/latest` | server-side sync |
| `#/alerts` | Feature-derived alerts | `/api/features/latest` | fire alerts on missing data |
| `#/events` | Corporate / regulatory | BSEC, CDBL, BB source status | invent corporate events |
| `#/evidence` | Evidence Terminal | verdict ledger + surviving leads | claim BUY / SELL |
| `#/trust` | Data Trust Center | `/api/market/sources` + `PUBLIC_DATA_COVERAGE.md` | resolve cross-source disagreement |
| `#/stock/{sym}` | Stock Command Center (10 tabs) | `/api/stock/{sym}` | fabricate depth / order counts / queue |

## API endpoints (all read-only, `null` = NOT_OBSERVABLE)

Added by `tower/ui/market_api.py::attach_market_api(app)`:

| Endpoint | Source | Truth |
|---|---|---|
| `GET /api/market/summary` | `evidence/public_engine/*/SOURCE_STATUS.json` + `dse_market_statistics_totals.parquet` + `dse_market_statistics_breadth.parquet` | OBSERVED where available, INFERRED for `dsex_change_pct` |
| `GET /api/market/universe` | `evidence/public_engine/*/extract/{instruments,circuit,latest}.csv` | OBSERVED |
| `GET /api/market/sectors` | derived from universe | INFERRED |
| `GET /api/market/sources` | `evidence/public_engine/*/SOURCE_STATUS.json` `sources` array | OBSERVED |
| `GET /api/features/latest` | `results/dse_eod_features.parquet` (latest row per symbol) | INFERRED |
| `GET /api/stock/{sym}` | combined instruments, circuit, fundamentals, ownership, features, STATE_EVENT_LOG | as above |

Existing Observation Tower endpoints (`/api/state/{sym}`, `/api/history/{sym}`,
`/api/timeline`, `/api/metrics`, `/api/cross/{sym}`, `/api/replay`, `/api/latest`,
`/api/symbols`) are **unchanged** and require the CLI flag `--store DIR`.

## Design contract

Enforced by `HRIDOY_DESIGN_LOCK.md`:

* **Provenance micro-line** (`layer · source · mode · freshness`) on every metric.
* **Truth badges** — `OBS` / `INF` / `N/A` — distinguish observed, inferred, and
  not-observable at the field level.
* **Human vs Research mode** toggle (top status bar) — Human mode paraphrases
  `rel_volume_z`, `volume_price_divergence` etc. into English; Research mode
  shows the exact feature name and numeric value.
* **State language matches the state_engine**: `CALM / DRIFT / DEPARTURE /
  EXTREME`. The engine emits no BUY / SELL / target / stop, and the UI renders none.
* **Semantic colour**: green = positive, red = negative, amber = warn/attention,
  cyan/blue = info, purple = derived-research, grey = unavailable/inert.
* **Command palette** at `⌘K` / `Ctrl-K` / `/` — jump to any symbol, screen, or
  scanner lens.

## Presentation law (rebuild, 2026-09-10)

The presentation layer was rebuilt to one rule: within five seconds a person
must be able to answer *is the market open, is it strong or weak, which sectors,
which stocks need attention, and why*. Nothing in the engine, the API, the
routes or the data contracts changed.

Screen order is the information hierarchy, on every route and both widths:

    L1  MARKET STATE       one dominant verdict block, largest type on the page
    L2  INDEX + BREADTH    DSEX, fell / rose / unchanged, turnover
    L3  SECTOR STATE       human sector names, turnover-weighted
    L4  STOCKS TO WATCH    real symbols, plain reason on every row
    L5  RAW EVIDENCE       the numbers, quietest, opt-in

Rules the layer enforces:

* **Never LIVE on stale or closed data.** The phase chip reads `MARKET CLOSED ·
  Last session data — <date>` whenever the feed's `session_phase` is not open.
* **Human mode is the default** and shows no feature name, no z-score and no
  internal id. Research mode adds `rel_volume_z = 3.42` back beside the plain
  sentence. Both read the same stored feature row.
* **Mobile is a separate design**, not a shrunk desktop: labelled bottom tab bar,
  single-column hero, two-up sector cards, and a row list instead of the wide
  table (the desktop table hid price and change off-screen). The treemap is a
  desktop-only device.
* **No internal ids as a label, anywhere.**

### Reading the delivered data correctly

Three things about the real feed that the presentation layer handles, without
touching the API:

1. `/api/market/universe` ships every symbol **twice** (946 rows, 473 unique) —
   de-duplicated by symbol before any count or sum.
2. A row with `ltp == 0` did not trade; its `change_pct` arrives as `-100.0`.
   Those rows are excluded from breadth, from sector change and from the movers
   lists, and the count is disclosed on the Every Stock screen.
3. Corporate bonds, debentures, treasury bonds and the three index rows
   (`DSEX`, `DS30`, `DSES`) carry a `sector` id but are not company shares, so
   they never enter equity breadth, equity turnover or the sector map. `DSEX`
   is read from the universe because `/api/market/summary` returns `dsex: null`.

### Sector id → human name (`tower/ui/static/labels.js`)

Presentation mapping only — no engine or API change. Derived by joining
StockNow's numeric `sector_id` (`extract/instruments.csv`) with BullBD's text
`sector` (`extract/fundamentals.csv`) on `symbol`: **406 symbols matched, 0
conflicts, every id resolved to exactly one name.** Cross-checked against
`evidence/public/<date>/normalized/dse_sector_wise_company_list.parquet`
(`dsebd.org/by_industrylisting.php`, truth `OBSERVED`) — StockNow's numbering is
the DSE alphabetical industry list with the G-SEC (T.Bond) row removed, plus
three StockNow-only buckets (22 Treasury Bond, 23 Market Index, 25 Life
Insurance); 22 and 23 are marked INFERRED in the file.

Sector **change and turnover** are not averaged from stocks — they are DSE's own
sector aggregate rows (`BANK`, `TEXTILE`, …), which the universe feed carries
with `sector = null`. Per-sector up/down counts come from the equities, mapped
through the table above.

### Attention rules

Seven rules over the existing feature table, each with a fixed plain-language
reason: quiet accumulation, heavy selling, abnormal volume, abnormal for days,
range squeezed, thin and fragile, activity without follow-through. They emit an
observation and a symbol — never a buy, a sell, a target or a stop.

## Run

    PYTHONPATH=. python3 -m tower.ui.server --port 8765
    #  → open http://127.0.0.1:8765/

Add `--store DIR` to also serve the Observation Tower `/observe` view against a
replay or live-tail store.

## Data files this server reads

    evidence/public_engine/<run>/SOURCE_STATUS.json
    evidence/public_engine/<run>/extract/{instruments,circuit,latest,fundamentals,ownership}.csv
    evidence/public/<date>/normalized/dse_market_statistics_{totals,breadth}.parquet
    results/dse_eod_features.parquet          (feature vector; latest row per symbol)
    results/STATE_EVENT_LOG.parquet           (state events per symbol; last 60 rendered)

Files not present are treated as unavailable — the shell renders `—`, not `0`,
and does not degrade the sections that do have data.
