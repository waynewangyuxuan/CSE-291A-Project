# Curated Phase 1 evaluation

This directory contains reviewed report artifacts for the unquantized F16,
full-context baseline. Raw requests, simulated state snapshots, service logs,
per-episode outcomes and resumable manifests stay under ignored `artifacts/`.

The frozen plan is `configs/phase1/campaign.json`: 60 distinct tasks, four suites,
two seeds, two attack templates and two suite-specific goals, totaling 600
episodes. Task selection is fixed before examining F16 outcomes.

Every selected result export must retain the campaign ID, configuration and
deployment identity, source run IDs, denominators, unknown/error counts, and
measurement coverage. Partial collection must be labeled partial. Trace review
annotations identify their reviewer and quote or locate the inspected evidence;
they do not replace the programmatic utility and attack checkers.

`f16-development-validation.json` preserves the five pre-evaluation F16 episodes
from four development runs: four successful clean examples and one scored
banking attack, with no runtime errors. It is excluded from the 600-episode
formal cohort. See [the development validation note](../../docs/f16-development-validation.md).

`collection-provenance.json` records the frozen formal campaign identity and
actual deployment/hardware at launch. Its progress field is a launch-time
snapshot, not a completion claim. Read the live campaign manifest or
`make phase1-status` for current progress.

`review-<suite>.csv` files contain incremental evidence-based annotations of
finalized formal traces. Coverage is partial until all required rows have been
reviewed under [the frozen protocol](../../docs/trace-review-protocol.md).

`clean-baseline-assessment.md`, `clean-baseline-measurements.json`,
`clean-baseline-episodes.csv` and `clean-baseline-repeat-overview` PNG/SVG cover
the complete **120-clean / 60-task / two-seed** cohort, with all 120 traces
reviewed. The 480 attacks and their final review obligations remain separate
requirements of the parent campaign. The export preserves the native analyzer's
statistics, exact source hashes, raw-evidence digests, historical parent snapshot
and canonical annotations. Native readiness applies only to this clean subset.

Reproduce the export and paired-outcome figure from the unchanged local evidence:

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_clean_checkpoint.py
uv run --no-project --isolated --with matplotlib==3.11.2 python scripts/plot_phase1_clean.py
```

The exporter rejects unfinished clean collection/review and mismatched existing
snapshots. `--check-only` validates without writing. Raw source snapshots and
full native analysis remain at `artifacts/phase1/clean120-checkpoint/`.
The fixed analysis-process hash seed stabilizes JSON label-key order; it does
not alter sampling or statistical estimates.

`reviewed-traces.csv` is the canonical analyzer input. It initially consolidates
all 120 clean annotations, validated against the four per-suite sources and
their episode identities. Final attack-review annotations will be appended only
after the frozen final samples are selected. A 120-row clean-only annotation
file does not satisfy the complete campaign's attack-review obligations.
`make phase1-analyze` includes this canonical input while reporting pending
episodes and provisional review-sample coverage during collection.

`append-conditions-summary.json` records supplementary source validation performed
during collection. All 240 unique task/goal/template conditions retain official
ground-truth utility=true using 1–9 tools; their selected attack goals are false
both before and after that legitimate completion. It pins the full offline
report, whose provenance contains 81 source-file hashes, and the reproduction script.
The full report belongs in `artifacts/phase1/append-conditions-audit.json`.
This adds no model episodes, changes no recorded scores and does not certify
semantic checker completeness or the model's ability to find the known path.

`scripts/export_phase1_evaluation.py` is the final 600-episode export entry point.
It is prepared during collection and refuses until the complete evidence audit,
native review gate and all three protocol review obligations pass. Successful
export publishes `evaluation-measurements.json`, `evaluation-episodes.csv`,
`evaluation-requests.csv`, `collection-audit.json` and `review-coverage.json`.
The latter records the overlap and distinct union of required reviews, both
selection hashes and verified suite/goal/style coverage. Statistics reuse the
native analyzer; raw results and official scores remain unchanged.

Run with `PYTHONHASHSEED=0`, optionally `--check-only` to validate without writing.
The source and full native output snapshot is `artifacts/phase1/final-evaluation/`.
`scripts/plot_phase1_evaluation.py` consumes the successful export and also
requires the complete 600-episode cohort before writing final outcome/cost figures.

`initial-observations.md`, `initial-clean-measurements.json` and the
`initial-clean-overview` PNG/SVG cover only the first 24 completed clean episodes
(banking and Slack, seed 17). Their frozen latency data is
`initial-latencies.csv`. They contain no formal attack estimate and do not claim
that the 600-episode campaign is complete.

Reproduce that figure in a separate plotting environment with Matplotlib 3.11.2:

```bash
uv run --no-project --isolated --with matplotlib==3.11.2 python scripts/plot_phase1_initial.py
```

The plotting script reads only curated files, validates run identities and
summary agreement, and embeds input/script digests and the plotting version in
the SVG/PNG metadata. This optional plotting environment does not change the
evaluation dependency lock or project environment.

`case-studies/` preserves separately scoped, inspected examples beyond the first
24-episode overview. Its first case documents a 600-second workspace budget
exhaustion with unknown scores and incomplete final-request token usage.
The paired Workspace seed-29 case explains a wrong answer scored successful.
Two preselected Banking attacks distinguish exact-goal success from an observed
unauthorized state change; these diagnostics do not replace the final attack
review samples or estimate population risk.
Two Slack tool-budget errors additionally show unrelated membership changes
before interruption, while retaining unknown official outcomes and separating
pre-existing inbox instructions from the selected attack payloads.

`refusal-adjudication.md`/`.json` records a consistency review of 47 clean
episodes, including corrected positive and uncertain labels under the original
broad definition. Its cohort is partial; use its explicit counts and uncertainty
rather than the earlier provisional zero-refusal interpretation.
