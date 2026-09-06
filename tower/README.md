# tower — DSE Market Observation Tower

One synchronized per-symbol `MarketState` (book, tape, order/queue, liquidity, pressure, resilience,
circuit, auction, cross-symbol/sector, mechanisms, timeline) reconstructed from immutable raw capture,
identical for live tailing and deterministic replay, with an Observation Tower UI and experiment tooling.

```
# live capture (source-specific Python runner; writes seeing-format raw segments)
python3 -m seeing.capture.runner --out evidence/capture/DATE --date DATE --start 03:50 --end 08:20

# generic zero-loss ingest daemon (Go): http-poll / websocket / tcp (line, FIX SOH, len16 ITCH) / file-tail
cd tower/ingest && go build -o ingest . && ./ingest -config config.example.json -out ../../evidence/capture/DATE

# deterministic replay → state store
python3 -m tower.replay --capture evidence/capture/DATE --out results/tower/DATE [--speed 0|1|10] [--step] \
        [--symbols A,B] [--from ISO] [--to ISO]

# live: tail the growing capture into the same engine
python3 -m tower.live --capture evidence/capture/DATE --out results/tower/live

# Observation Tower UI (reads a replay or live state store)
python3 -m tower.ui.server --store results/tower/DATE --port 8765

# experiment / falsification over the state store
python3 -m tower.experiment --store results/tower/DATE --out results/tower/DATE/experiment

# tests (machinery: test_machinery_*, real data: test_realdata_*)
python3 -m pytest -q tests/tower
```

Contracts: `tower/CONTRACTS.md`. Mechanism map: `tower/mechanics/MECHANISMS.md`.
State store format: `states/<SYMBOL>.jsonl`, `timeline.jsonl`, `metrics.json`, `latest.json`, `RUN.json`.
