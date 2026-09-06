# tower — implementation contracts (read before touching any module)

Workspace: `/home/user/bd-share-market-tower` (git worktree, branch `claude/dse-observation-tower`).
Reuse: `seeing/` (raw store, adapters, replay, truth classes, experiment harness) — import it, do not copy it.
Real data: `tests/fixtures/capture_closed/` (a real closed-market capture: two depth sensors, tape,
market, block; books are empty because the market was closed) and, when present,
`/home/user/bd-share-market/evidence/capture/2026-09-06/` (the live session capture, growing during
the day — read-only, never write there).

## Conventions (binding)
- Python 3.11, pandas/numpy allowed; no new heavy dependencies. Type hints. UTC-aware datetimes only.
- Never a silent zero: a quantity a source does not deliver is `None` (truth NOT_OBSERVABLE).
- Everything derived is INFERRED and must be **computed** — no constant scores, no placeholders,
  no "TODO". If a mechanism needs a field that is None, its reading is score 0 with
  `evidence["missing"] = [...]` naming the missing inputs.
- Causal only: a computation at time t reads states at or before t.
- Deterministic: no wall-clock reads inside engines; all times come from events.
- Tests: `tests/tower/test_<module>.py`, run with `python3 -m pytest -q tests/tower/test_<module>.py`.
  Synthetic fixtures for machinery; real fixture capture for integration. Test names carry the
  distinction: `test_machinery_*` vs `test_realdata_*`.
- Run your tests, read the output, fix, run again. Do not report "built" without a passing run.

## Fixed contracts (already written — do not change their public shape; extend by adding fields)
- `tower/events.py`  — `Event`, `EventType`, `SOURCE_PRIORITY`, `utc()`.
- `tower/state.py`   — `MarketState`, `SourceStatus`, `MechanismState`, `Transition`.
- `tower/windows.py` — `RollingSeries`, `slope`, `curvature`, `ewma`, `clamp01`, `safe_div`, `sign`.
- `tower/mechanics/base.py` — `Mechanism`, `MechanismReading`, `StateHistory`, `register`, `REGISTRY`.
- `seeing/truth.py` — `Truth`.

## Modules and their owners (one agent per line; files are disjoint)
| module | responsibility | public API |
|---|---|---|
| `tower/normalize.py` | replayed raw tables / parsed frames → `Event`s; seq_local; session phase; t_exch; freshness; duplicate (identical payload per source+symbol), stale (age > cadence×3), gap (heartbeat/GAP records, feed-sequence holes), out-of-order flags; sorted event stream | `normalize_store(root, symbols=None, t_from=None, t_to=None) -> list[Event]`, `events_from_frames(source, frames, ...)`, `QAStats` |
| `tower/parsers.py` | ITCH-style binary framing parser (length-prefixed messages with a small documented message set: add/execute/cancel/delete/replace/trade + system), FIX 35=W/X → events (reuse `seeing.capture.adapters.fix_md`), broker exports → events | `itch_frames(bytes) -> list[dict]`, `itch_to_events(...)`, `fix_to_events(...)` |
| `tower/book.py` | `EvolvingBook`: snapshot replace + incremental apply (NEW/CHANGE/DELETE, level or price keyed), per-level first_seen/last_changed, deltas per update, deep geometry (depth by level, distance from touch, HHI concentration, weighted depth, slope, curvature, hollows, walls with persistence & migration, added/removed, side asymmetry), OFI per update, change velocity/acceleration | `EvolvingBook(tick)`, `.apply_snapshot(t, bids, asks, orders=None)`, `.apply_update(t, side, price, qty, order_count=None, action=None)`, `.geometry() -> dict`, `.fill_state(ms)` |
| `tower/tape.py` + `tower/queue.py` | `TapeState`: prints (TRADE events) or cumulative totals (CUM_TOTALS) → interval trades/volume/vwap, intensity, acceleration, direction (aggressor or quote rule vs the book at that time), signed flow window, price impact (ticks per unit signed flow), price velocity/acceleration; `QueueState`: touch-queue dynamics (qty at touch over time, pulls/stacks, refresh churn counters, order counts when present, replenishment after depletion) | `TapeState(tick)`, `.on_trade(...)`, `.on_cum_totals(...)`, `.fill_state(ms, book)`; `QueueState()`, `.on_book(ms, book)`, `.fill_state(ms)` |
| `tower/fusion.py` | multi-source reconciliation: per-source `SourceStatus` (freshness, dup, stale, coverage, counts), field-level fusion with provenance and agreement/disagreement (book from two sensors: level-by-level compare within a coalescing window; quote fields from watch vs depth) | `Fuser(coalesce_s)`, `.on_event(ev)`, `.fill_state(ms, now)` |
| `tower/circuit.py` | circuit engine: limits from REFERENCE events, else dated bands from `bdlib.config.CIRCUIT_BANDS_UNVERIFIED` on the previous close (flagged `rule_source`); distance/approach velocity & acceleration; hit/lock/unlock/relock with times and counts; queue at limit (growth/decay/persistence); volume & turnover approaching and while locked; streaks across sessions (from day-level history passed in); pre-hit state capture; continuation/weakening; break-day; next-session open/continuation/reversal; corporate-action exceptions (reference-price reset beyond band → `exception`) | `CircuitEngine()`, `.on_reference(...)`, `.on_state(ms, hist)`, `.fill_state(ms)`, `.day_summary()` |
| `tower/auction.py` | auction state separate from continuous: phase from session; indicative price/matched qty/imbalance when AUCTION events exist; otherwise pre-open book imbalance as auction pressure, flagged; transition detection | `AuctionEngine()`, `.on_event(ev)`, `.fill_state(ms)` |
| `tower/resilience.py` | shock detection (touch depth drop ≥ x% or spread widening ≥ y ticks within a burst), then recovery curve of spread/mid/depth/bid/ask, time- and updates-to-recovery, partial, overshoot, snapback, asymmetry | `ResilienceEngine()`, `.on_state(ms, hist)`, `.fill_state(ms)` |
| `tower/cross.py` | cross-symbol/sector: sector map (REFERENCE sector / watch sector_id), breadth, sector & market relative moves, leader/lag via lagged correlation of mid returns, basket synchronization, circuit clustering, simultaneous liquidity changes, synchronized expansion | `CrossEngine()`, `.on_state(ms)`, `.context_for(symbol) -> (cross: dict, sector: dict)` |
| `tower/mechanics/<family>.py` | the 49 mechanisms, grouped by family (see MECHANISMS.md); each a `Mechanism` subclass registered with `@register` | `compute(ms, hist) -> MechanismReading` |
| `tower/timeline.py` | layer state machines (pressure, liquidity, circuit, mechanism episodes) with the transition graph in the spec; durations; history; JSONL store | `Timeline()`, `.on_state(ms) -> list[Transition]`, `.history(symbol)` |
| `tower/engine.py` | orchestration: events → per-symbol engines → MarketState → mechanics → cross → timeline; metrics (ingest rate, processing rate, backlog, lag, parse failures, gaps, dups, stale) | `Engine(config)`, `.process(ev) -> MarketState|None`, `.metrics()` |
| `tower/replay.py` | deterministic replay: original timing / accelerated / step / pause-resume, symbol & time filters; state store writer; determinism check | `Replayer(...)`, CLI `python3 -m tower.replay --capture DIR --out DIR [--speed X|--step] [--symbols ..] [--from ..] [--to ..]` |
| `tower/live.py` | continuous file tailing of a growing raw store → same engine; live state store for the UI | CLI `python3 -m tower.live --capture DIR --out DIR` |
| `tower/ui/` | Observation Tower: FastAPI server (`server.py`) over a state store (live or replay) + single-page `static/index.html` + `app.js` (no build step): depth ladder, trade flow, pressure, liquidity, price response, active mechanics, circuit, timeline, cross-stock, source status, replay controls | CLI `python3 -m tower.ui.server --store DIR --port 8765` |
| `tower/experiment.py` | mechanism evaluation against simple baselines with the seeing harness + BH-FDR over mechanisms; eligible-window accounting with exclusion reasons | CLI `python3 -m tower.experiment --store DIR --out DIR` |
| `tower/ingest/` (Go) | generic zero-loss capture daemon: transports http-poll, websocket, tcp (delimiter / length-prefixed / FIX SOH framing), file-tail; writes seeing-compatible JSONL segments with META/HEARTBEAT/GAP/DATA, CRC32, sha256 chain; config JSON; `go test` | `go build ./... && ./ingest -config cfg.json` |

## State store format (shared by replay, live and the UI)
`<out>/states/<SYMBOL>.jsonl` — one `MarketState.to_dict()` per line, in event order.
`<out>/timeline.jsonl` — one `Transition` per line. `<out>/metrics.json` — engine metrics.
`<out>/latest.json` — {symbol: last state dict}. `<out>/RUN.json` — inputs (capture root, filters, sha256s) and the final per-symbol `state_hash` (determinism).
