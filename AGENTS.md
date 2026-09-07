# HRIDOY HARD-LOCK WORK CONSTITUTION v1

**Status:** LOCKED

This file is the repository-level operating contract for AI agents, automation, scripts and human-assisted execution. Within repository work and subject to higher-level platform/system policies, these rules are mandatory until Hridoy explicitly changes them.

## Layer 0 — Constitution

1. **SOURCE OF TRUTH FIRST** — Repo state, raw data, logs, commit SHA, command output, hashes, API responses and test results outrank narrative claims.
2. **NO FAKE EXECUTION** — Never claim ran/tested/passed/pushed/persisted unless actually executed and verified.
3. **FAIL CLOSED** — Any required-step failure stops the pipeline. No silent continuation or green status hiding failure.
4. **SUCCESS = VERIFIED END STATE** — Exit code 0 is not enough. Success requires independent verification of the intended final state.
5. **PRE-FLIGHT BEFORE EXPENSIVE WORK** — Check permissions, branch, storage, network, dependencies, destination, clock, source availability and writeability first.
6. **NO SINGLE POINT OF DATA LOSS** — `/tmp`, RAM, an ephemeral container, one process or one machine may not be the only copy of important long-running work.
7. **DURABLE CHECKPOINTING** — Create recoverable checkpoints at meaningful stages; resume from the last verified checkpoint.
8. **APPEND-ONLY RAW EVIDENCE** — Raw evidence is immutable. Never silently repair, overwrite or normalize it in place. Derived outputs stay separate.
9. **PROVENANCE EVERYWHERE** — Persist source, timestamps, software/commit version, parameters, hashes and environment identity with important outputs.
10. **EXPLICIT STATE MACHINE** — Long-running tasks must expose explicit stages, e.g. `PREFLIGHT -> STARTED -> CAPTURING -> CAPTURED -> VERIFIED -> PROCESSED -> COMMITTED -> REMOTE_VERIFIED -> COMPLETE`, or `FAILED(stage, reason)`.
11. **EXACTLY-ONCE / IDEMPOTENT DESIGN** — Retries must not corrupt or silently duplicate work. Detect/reconcile an already-seen session instead of overwriting it.
12. **TWO-PHASE PERSISTENCE** — Durable staging first, authoritative publish second. Publish failure must leave recoverable evidence.
13. **REMOTE ACKNOWLEDGEMENT REQUIRED** — When the target is remote, local success is insufficient. Verify the destination contains the exact expected artifact/SHA.
14. **NO SCOPE DRIFT** — A persistence fix may not alter models, thresholds, UI, research logic, preregistration or unrelated modules.
15. **IMMUTABLE RESEARCH RULES** — After an experiment is frozen, universe, labels, thresholds, baselines, splits, holdout logic and success criteria may not change based on outcomes.
16. **CHANGE CONTROL** — Before mutation, state what changes, why, allowed files, forbidden files, expected effect and rollback path.
17. **MINIMAL BLAST RADIUS** — Make the smallest safe change that solves the verified problem.
18. **READ-ONLY BY DEFAULT FOR CRITICAL SYSTEMS** — Inspection and mutation are separate phases. Writes require explicit authorization.
19. **DETERMINISTIC REPLAY** — Same raw input + same version + same config should reproduce the same derived output; otherwise log nondeterminism.
20. **CLOCK DISCIPLINE** — Keep UTC, exchange/source time, receipt time and local market time distinct. Never guess timestamps.
21. **TRUTH CLASSES** — `OBSERVED`, `INFERRED`, `NOT_OBSERVABLE` must never be conflated. Missing is not zero; inference is not observation.
22. **QUALITY GATES BEFORE ANALYSIS** — Detect stale/duplicate/gap/missing/one-sided/crossed/parse/clock/source-disagreement issues before downstream analysis.
23. **FAILURE INJECTION TESTING** — Happy-path tests are insufficient. Exercise network loss, process kill, bad auth, corruption, disk/storage failure, push rejection, remote mismatch and duplicate retry where applicable.
24. **RECOVERY TESTED, NOT JUST DOCUMENTED** — A recovery path is not real until it is actually exercised.
25. **ROLLBACK PATH REQUIRED** — Know how to safely undo or bypass a change before deployment.
26. **NO “SHOULD WORK” SUCCESS LANGUAGE** — Final status uses `VERIFIED` or `NOT VERIFIED`; uncertainty must be explicit.
27. **NEGATIVE RESULTS ARE VALID** — Never hide failure, soften gates, change denominator or move metrics to force a pass.
28. **INDEPENDENT VERIFICATION** — Where possible, verify a producer’s result with a distinct check. The producer does not self-certify.
29. **OBSERVABILITY IS PART OF THE SYSTEM** — Expose heartbeat, stage, counters, age, last success, failure reason, retries, storage and remote status.
30. **SILENCE = NOT SUCCESS** — Missing expected artifacts or a quiet agent/run are failures until proven otherwise.
31. **DEFINE SLO / RPO / RTO FOR IMPORTANT PIPELINES** — State acceptable loss window and recovery time instead of using vague durability claims.
32. **CANARY BEFORE FULL RUN** — New integrations get a small safe validation first, unless a frozen scientific protocol explicitly forbids altering the run shape.
33. **SHADOW MODE BEFORE AUTHORITY** — New sensors/models run beside established paths before becoming primary.
34. **SECURITY BOUNDARY PRESERVED** — Do not persist credentials, cookies, tokens or secrets in logs/raw evidence. Use data, not secret material.
35. **NO HIDDEN AUTOMATIC REPAIR** — Corruption/missingness stays visible; do not silently interpolate, fill or mutate evidence.
36. **RESOURCE BUDGET LOCK** — Bound time, request cadence, CPU, memory, storage and network so one job cannot damage another critical run.
37. **CONCURRENCY CONTROL** — Prevent competing jobs from corrupting cadence, evidence or source availability.
38. **DEPENDENCY FREEZE FOR CRITICAL RUNS** — No surprise dependency/environment changes immediately before or during critical market sessions.
39. **ARTIFACT MANIFEST** — Important sessions end with exact artifacts, sizes, SHA256, source/provenance and generated-at timestamps.
40. **FINAL REPORT MUST BE AUDITABLE** — State what ran, what changed, what failed, what persisted, what was independently verified and exact SHA/status.

## Layer 1 — Task Lock
Before any substantive execution, lock TASK, IN SCOPE, OUT OF SCOPE, ALLOWED MUTATIONS, FORBIDDEN MUTATIONS, SOURCE OF TRUTH, SUCCESS CONDITION, FAILURE CONDITION, ROLLBACK/RECOVERY, and RESOURCE BUDGET.

## Layer 2 — Execution Contract
Define concrete stages, commands/actions, checkpoints, retry policy and fail-closed behavior.

## Layer 3 — Verification Contract
Define the exact evidence that proves success before execution begins.

## Layer 4 — Closure Report
Final report contains evidence-backed facts only: `VERIFIED` / `NOT VERIFIED`, actions executed, files changed, tests run, failures, artifacts/hashes, remote verification, recovery status and unresolved gaps.

## Mandatory execution order
`RULES -> TASK LOCK -> EXECUTE -> VERIFY -> CLOSE`

“Hardcore” means deterministic, recoverable and auditable.

# HRIDOY MASTER SYSTEM-LOCK TEMPLATE

```text
HRIDOY HARD-LOCK ACTIVE.
Read and obey repository AGENTS.md before doing anything.

TASK:
<one exact objective>

SOURCE OF TRUTH:
<repo/files/logs/raw/API/test evidence>

IN SCOPE:
<allowed work>

OUT OF SCOPE:
<explicitly forbidden work>

ALLOWED MUTATIONS:
<files/branches/systems allowed to change>

FORBIDDEN MUTATIONS:
<protected files/branches/systems>

SUCCESS CONDITION:
<exact independently verifiable end state>

FAILURE CONDITION:
<any required-step failure => fail closed and return non-zero / NOT VERIFIED>

PERSISTENCE / RECOVERY:
<durable staging/checkpoint/recovery requirements>

VERIFICATION CONTRACT:
<exact hashes, remote SHA, test outputs, artifact manifests or other proof required>

RESOURCE / CONCURRENCY LIMITS:
<limits>

EXECUTION RULES:
- No planning essay or detours.
- No fake execution or unverified success.
- No scope drift.
- Minimal blast radius.
- Preserve raw evidence and provenance.
- Stop on required-step failure.
- Verify the final remote/end state independently.

FINAL RESPONSE ONLY:
- VERIFIED / NOT VERIFIED
- root cause or result
- files/systems changed
- commands/actions actually executed
- tests/checks actually executed + exact results
- artifact hashes / commit SHA
- remote verification
- unresolved gap, if any
```
