"""tower — DSE Market Observation Tower.

One synchronized, continuously evolving per-symbol MarketState built from every
obtainable market-information layer, with a market-mechanics engine, circuit
engine, auction engine, cross-symbol/sector context, resilience engine,
deterministic replay, an Observation Tower UI and experiment tooling.

Layering (each layer is a module; ``engine.py`` wires them):

    raw source input        seeing.capture.* (Python runner) · tower/ingest (Go daemon)
    → immutable raw capture seeing.capture.raw_store (JSONL segments, hash chain)
    → parsing               seeing.capture.adapters.* + tower.parsers (FIX / ITCH / exports)
    → normalized events     tower.events / tower.normalize
    → alignment + QA        tower.normalize (dedup, stale, gap, ordering)
    → evolving book         tower.book
    → trade / tape          tower.tape (+ OFI)
    → order-event / queue   tower.queue
    → reconciliation/fusion tower.fusion
    → MarketState           tower.state (+ tower.windows)
    → mechanics             tower.mechanics.*
    → circuit / auction     tower.circuit / tower.auction
    → resilience            tower.resilience
    → cross-symbol/sector   tower.cross
    → timeline              tower.timeline
    → replay (deterministic) tower.replay · live tailing tower.live
    → Observation Tower UI  tower.ui
    → experiment            tower.experiment (on top of seeing.experiment)

Every derived value is INFERRED from OBSERVED inputs (seeing.truth); a field no
source delivers stays None with truth NOT_OBSERVABLE — never a silent zero.
"""

__version__ = "0.1.0"
