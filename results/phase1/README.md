# Curated Phase 1 evaluation

This directory contains reviewed report artifacts for the unquantized F16,
full-context baseline. Raw requests, simulated state snapshots, service logs,
per-episode outcomes and resumable manifests stay under ignored `artifacts/`.

The **complete 600-episode evaluation** is in
[evaluation-assessment.md](evaluation-assessment.md): 120 clean, 480 attacks,
60 tasks, two seeds and 24 finalized runs. All 25 errors remain unknown rather
than being dropped. All three review obligations passed, with 168 distinct
reviewed episodes after overlap. Read the assessment for findings and Phase 2
direction, and [evaluation-verification.json](evaluation-verification.json) for
the actual audit, independent comparison and visual-check evidence.

The final data files are `evaluation-measurements.json`,
`evaluation-episodes.csv`, `evaluation-requests.csv`, `collection-audit.json`
and `review-coverage.json`. `evaluation-outcomes` and `evaluation-costs` PNG/SVG
figures describe this complete cohort; `evaluation.plots.json` pins their inputs.
The native source/output archive is `artifacts/phase1/final-evaluation/`.
Public CSV record terminators are LF; native archived CSVs use CRLF. Ordered
headers and cell contents are retained, with both representations verified.

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

`review-<suite>.csv` files preserve the original clean-review inputs. The final
canonical union is `reviewed-traces.csv`, validated under
[the frozen protocol](../../docs/trace-review-protocol.md).

`clean-baseline-assessment.md`, `clean-baseline-measurements.json`,
`clean-baseline-episodes.csv` and `clean-baseline-repeat-overview` PNG/SVG cover
the complete **120-clean / 60-task / two-seed** cohort, with all 120 traces
reviewed. The full evaluation separately includes all 480 attacks and their
final review obligations. The clean export preserves the native analyzer's
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

The clean exporter reads the four `review-<suite>.csv` sources. Final attack
reviews belong in `reviewed-traces.csv`, preserving the per-suite clean inputs.
Re-export requires the original source paths and bytes, including the code and
clean evidence. Retain the existing snapshot and its identity if those inputs change.

The fixed analysis-process hash seed stabilizes JSON label-key order; it does
not alter sampling or statistical estimates.

`reviewed-traces.csv` is the canonical analyzer input: 120 clean annotations
validated against the four per-suite sources and 48 final attack annotations.
The original clean bytes remain an exact prefix. Final samples were selected
after collection by the frozen rules and verified unchanged after review.
The 30-trace native sample reuses 12 clean episodes; the 30 supplementary attacks
exclude the native sample. `make phase1-analyze` includes this canonical input.

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
The supplementary `utility_success_costs` section selects completed episodes
with official utility=true and retains its own episode/task counts. Native
`costs_by_condition_completed` includes utility=false normal returns; it is not
a successful-task cost estimate. Success-conditioned costs retain the native
measurement functions and are descriptive of the selected task mix.

Run with `PYTHONHASHSEED=0`, optionally `--check-only` to validate without writing.
The source and full native output snapshot is `artifacts/phase1/final-evaluation/`.
`scripts/plot_phase1_evaluation.py` consumes the successful export and also
requires the complete 600-episode cohort before writing final outcome/cost figures.
The export's `full_cohort_evaluation_export_complete` flag certifies collection,
reviews and the data export. Final figures, visual checks and the written
assessment remain separate completion requirements.

Reproduce the complete analysis and exported figures from unchanged local evidence:

```bash
PYTHONHASHSEED=0 .venv/bin/python -m agent_tradeoffs.cli analyze \
  artifacts/campaigns/phase1-f16-v1 \
  --annotations results/phase1/reviewed-traces.csv --require-review --compact
PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py --check-only
PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py
uv run --no-project --isolated --with matplotlib==3.11.2 python scripts/plot_phase1_evaluation.py
```

Keep the ignored raw evidence and immutable snapshot with the curated files for
submission. The final verification records actual executions; earlier prepared
helper tests and partial audits retain their historical scopes. Native
confidence intervals are preserved, not independently recomputed by the raw
point-metric comparison. The final assessment explains benchmark and measurement
limitations alongside each result.

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
A Workspace answer-contamination case records an injected date/time copied into
the final answer without creating the attacker's calendar event. Its clean
control also fails, so it demonstrates an answer-level effect outside the
selected action goal without establishing attack-caused utility loss.
Two simulated mail-success cases compare clean/direct/ignore trajectories for
the same tasks. One completes the legitimate calendar change but adds an
unauthorized target email; the other already fails the clean task. They explain
why all-attack and clean-completable ASR have different numerators.

`refusal-adjudication.md`/`.json` records a consistency review of 47 clean
episodes, including corrected positive and uncertain labels under the original
broad definition. Its cohort is partial; use its explicit counts and uncertainty
rather than the earlier provisional zero-refusal interpretation.
