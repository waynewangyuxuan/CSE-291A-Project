# Phase 1 error recovery diagnostic

Both rounds are complete: **0 of 50 additional full episodes returned normally**
(38 API timeouts, 12 tool limits). All 25 original error cases remained errors
in both attempts. Official utility and attack success remain unknown; this does
not measure a zero ASR or zero legitimate goal completion rate.

The supplement preserves the original seeds and settings and covers 6 distinct
suite and task pairs. It is a diagnostic of selected errors. The original
600 episode evaluation and its denominators remain unchanged.

- [Assessment](assessment.md): research question, controls, findings, costs and gaps.
- [Summary](summary.json): original selected cases, both rounds, recovery and cost distributions.
- [Episode table](episodes.csv): all 50 attempts and their original case mapping.
- [Validation](validation.json): actual primary analysis, original file guard and idle handoff evidence.
- [Independent audit](independent-audit.json): direct raw checks, source hashes and per episode evidence.
- [Frozen protocol](../../docs/phase1-error-retries-protocol.md) and
  [selection plan](../../configs/phase1-error-retries/plan.json): controls frozen before collection.

Raw evidence and local runtime snapshots remain under
`artifacts/phase1-error-retries/`. The service was retained and handed to the
separate prompt defense experiment after collection, audit and an idle check.
