# Working agreement with Hridoy

`AGENTS.md` (LOCKED) holds the execution constitution. This file holds how the work is
*reported back*. Both apply; neither overrides the other.

## How every task must end (standing instruction, given 2026-09-07)

Finish every task with these two things, in this order:

1. **A copy-paste block.** The full report goes inside a fenced code block so Hridoy can
   copy it out in one action. Plain language, no jargon he has to decode. Facts only:
   what was asked, what was actually done, what was found, what is verified vs not,
   what is still blocked, what the next step is. Numbers and file names belong here.
   Never split the report across several blocks — one block, one copy.

2. **A 3-4 line summary in his language** (Bengali / Banglish), after the block. Very
   short. Just the outcome and the one thing he should do next. No repetition of the
   block's detail.

Rules that do not change: never claim something ran or passed unless it did; say
`VERIFIED` or `NOT VERIFIED` plainly; if something is blocked, name the exact blocker
instead of softening it; do not offer speculative extra work he did not ask for.

## Language

Reply in Bengali/Banglish, matching how he writes. Technical identifiers (file names,
endpoints, commit SHAs) stay in English inside the report.

## Do not touch

His separate running project (খাতা G-1/G-4, PR #304) is off limits unless he explicitly
asks. Frozen research artefacts (`micro/MICRO_PREREG.json`, universe, thresholds, splits,
holdout) are immutable per `AGENTS.md`.
