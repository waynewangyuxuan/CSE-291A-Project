# Phase 2 preparation checkpoint

This is offline preparation evidence dated 2026-11-12, not Phase 2 model results.
The [execution order and gates](../../docs/phase2-preparation.md) govern next steps.
The nine candidates are the proposed confirmation pool. They are not the entire
study or a smoke batch; the roadmap separately budgets development, a broad
known-task comparison, untouched confirmation, targeted diagnostics and delivery.

- [Qualification evidence](qualification.json): 9/9 candidate clean/attack pairs
  pass mechanical checks using the installed pinned benchmark. Reference tool-call
  counts range from 1 to 8. Zero model episodes were evaluated.
- [Local readiness](readiness.json): F16/runtime inspection and expected derived-Q4
  asset availability. This does not establish live inference or Phase 2 readiness.
- [Candidate input](../../configs/phase2/preparation-candidates.json): source-selected
  IDs, one provisional attack goal per suite, pinned revision and existing limits.
- [Command verification](verification.json): a fresh output matches this snapshot,
  an identical repeat is stable, conflicting evidence is preserved, and a previously
  used task is rejected.

Mechanical checks include legitimate reference success, empty-no-op rejection,
injection vectors, attack checker controls, and legitimate success after appending
the selected attack. The injected initial state and legal reference path must not
already satisfy the attacker goal. Source hashes and checker locations are retained.

The nine tasks remain candidates. Semantic scoring review, workflow overlap,
development-only token-budget calibration and final configuration freezing are open.
These tasks share existing suite environments; they are not nine new task families.

Regenerate with `make phase2-qualification`; the default output is a separate local
artifact. Identical reruns are allowed, but changed evidence requires a new path.
The committed JSON is the snapshot from this preparation checkpoint. It is not an
estimate of task utility, attack success, false refusals, latency, tokens or memory.
