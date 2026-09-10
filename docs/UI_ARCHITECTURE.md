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
