# Mechanism map → family modules (49 mechanisms; each is a registered `Mechanism` subclass)

Score ∈ [0,1] is an evidence strength computed from rolling windows on `MarketState` + `StateHistory`.
`evidence["direction"]` ∈ {+1, −1, 0} when the mechanism implies a price direction (used by the lifecycle
to mark episodes resolved/failed on the realised mid move). Every module has `tests/tower/test_mech_<family>.py`
with (a) a synthetic scenario that drives the score ≥ active_threshold and (b) a null scenario that keeps it
< build_threshold, plus a check that evidence values are computed (change when inputs change).

| # | mechanism | name (registry) | family module | key inputs |
|---|---|---|---|---|
| 1 | Queue pull / stack | `queue_pull_stack` | queue_family | touch qty series, order counts if any, no trades while qty falls (pull) / rises (stack) |
| 2 | Quote refresh churn | `quote_refresh_churn` | queue_family | best price/qty change rate with net zero drift |
| 30 | Displayed-liquidity instability / layering-like | `layering_like` | queue_family | size away from touch appearing/cancelling repeatedly, cancel-away share |
| 31 | Repeated hidden-liquidity-like replenishment | `hidden_replenishment` | queue_family | touch qty refilled to similar size repeatedly after consumption |
| 33 | Order splitting | `order_splitting` | queue_family | repeated same-size prints / same-size touch consumption cadence |
| 3 | Liquidity sweep | `liquidity_sweep` | sweep_family | levels consumed through, mid jump, volume burst |
| 4 | Failed sweep / rejection | `failed_sweep` | sweep_family | sweep followed by price return within window |
| 5 | Exhaustion | `exhaustion` | sweep_family | trade intensity high + price velocity decaying to zero + depth rebuilding against |
| 14 | Liquidity vacuum | `liquidity_vacuum` | sweep_family | visible depth collapse both/one side, no replenishment |
| 15 | Vacuum + snapback | `vacuum_snapback` | sweep_family | vacuum followed by fast mid reversal and depth return |
| 20 | Stop / liquidity-run-like | `liquidity_run` | sweep_family | fast directional prints through thin levels then stall |
| 21 | Momentum expansion / ignition | `ignition` | sweep_family | price velocity + acceleration + trade acceleration all positive, spread expansion |
| 34 | Liquidity depletion | `liquidity_depletion` | sweep_family | touch/topK depth declining share over window without price move |
| 6 | Passive accumulation | `passive_accumulation` | accumulation_family | bid replenishment absorbing sells, flat price, rising bid depth share |
| 7 | Passive distribution | `passive_distribution` | accumulation_family | mirror |
| 11 | Large-print / block absorption | `block_absorption` | accumulation_family | large interval volume vs baseline, small price change |
| 12 | Inventory rebalancing | `inventory_rebalancing` | accumulation_family | flow sign flips with symmetric volume, price mean-reverting |
| 13 | Adverse-selection retreat | `adverse_retreat` | accumulation_family | depth pulled on the side just traded against, spread widening |
| 28 | Stealth accumulation | `stealth_accumulation` | accumulation_family | persistent net buy flow with low intensity and flat price |
| 29 | Stealth distribution | `stealth_distribution` | accumulation_family | mirror |
| 32 | Absorption | `absorption` | accumulation_family | large aggressive flow vs touch with price unchanged and touch refilled |
| 38 | Accumulation-like state | `accumulation_like` | accumulation_family | composite of 6/28/32 over a longer window |
| 39 | Distribution-like state | `distribution_like` | accumulation_family | mirror |
| 8 | Pegged / passive repricing | `pegged_repricing` | participation_family | touch quote re-posted one tick behind price moves repeatedly |
| 9 | Participation-style footprint | `participation_footprint` | participation_family | interval volume tracking a stable fraction of market/symbol volume |
| 10 | Metaorder / participation trajectory | `metaorder_trajectory` | participation_family | persistent one-sided flow with concave price impact path |
| 43 | Metaorder impact trajectory | `metaorder_impact` | participation_family | impact vs cumulative signed flow fit (sqrt-like concavity) |
| 19 | High-activity low-progress churn | `churn_anomaly` | divergence_family | trade intensity z high, |price velocity| low |
| 22 | Book-vs-trade pressure divergence | `book_trade_divergence` | divergence_family | book pressure sign ≠ trade pressure sign, both strong |
| 23 | Depth–price divergence | `depth_price_divergence` | divergence_family | depth migrating one way, price the other |
| 24 | Flow–impact divergence | `flow_impact_divergence` | divergence_family | signed flow large, price impact small (or vice versa) vs baseline |
| 25 | Resilience asymmetry | `resilience_asymmetry` | divergence_family | recovery_asymmetry persistent |
| 26 | Compression → expansion | `compression_expansion` | divergence_family | spread/velocity compression then expansion |
| 27 | False breakout / failed pressure | `false_breakout` | divergence_family | mid beyond recent range then back inside with pressure reversal |
| 35 | Trap-like pressure | `trap_pressure` | divergence_family | strong displayed one-side pressure removed as price approaches |
| 37 | Repetitive trade-churn anomaly | `trade_churn_repetition` | divergence_family | repeated identical interval volume/trade counts with flat price |
| 40 | Order Flow Imbalance | `ofi_state` | ofi_shape_family | rolling OFI z-score & sign persistence |
| 41 | Deep-book shape / curvature | `deep_book_shape` | ofi_shape_family | slope/curvature/asymmetry regime |
| 42 | Resiliency recovery curve | `recovery_curve_state` | ofi_shape_family | curve fit speed/shape |
| 36 | Close-session pressure | `close_session_pressure` | session_family | pressure/volume in the last 30 min and the post-close session |
| 16 | Auction imbalance | `auction_imbalance` | session_family | auction imbalance side/size (or pre-open book imbalance, flagged) |
| 17 | Index / basket rebalance footprint | `basket_rebalance` | cross_family | simultaneous volume bursts across basket near close |
| 18 | Cross-stock lead / lag | `cross_lead_lag` | cross_family | leader/lag correlation from CrossEngine |
| 44 | Circuit / price-limit regime | `circuit_regime` | circuit_family | limit state, distance |
| 45 | Circuit streak behaviour | `circuit_streak` | circuit_family | consecutive limit sessions |
| 46 | Circuit pre-hit pressure | `circuit_prehit_pressure` | circuit_family | approach velocity + pressure + shares-to-door |
| 47 | Circuit lock / unlock strength | `circuit_lock_strength` | circuit_family | queue at limit growth/decay, relocks |
| 48 | Circuit break-day weakness | `circuit_break_weakness` | circuit_family | first session off the limit: queue decay, reversal |
| 49 | Circuit next-session continuation / reversal | `circuit_next_session` | circuit_family | opening state vs prior lock |
