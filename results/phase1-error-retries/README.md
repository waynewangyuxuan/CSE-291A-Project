# Phase 1 error recovery diagnostic

This supplement repeats the original 25 runtime error episodes twice, yielding
50 additional full episodes with the same original seeds and settings. The
original 600 episode evaluation remains unchanged. The selection covers only
6 distinct suite and task pairs, so this is an error recovery diagnostic rather
than an independent expansion of the evaluation workload.

The [frozen protocol](../../docs/phase1-error-retries-protocol.md) and
[selection plan](../../configs/phase1-error-retries/plan.json) define the full
workload before collection. Both rounds run regardless of earlier outcomes.
Raw evidence and the protected original file inventory are stored locally at
`artifacts/phase1-error-retries/`. Analysis requires all 50 attempts to finalize
and preserves error scores as unknown. Normal return recovery, task utility,
attack success and all additional costs are reported separately.

The collection has been launched. Final results will be added here after both
rounds and the evidence audit finish.
