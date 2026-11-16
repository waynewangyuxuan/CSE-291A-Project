# Script index

Use `make help` and the [run guide](../docs/running.md) for normal workflows.
These scripts are grouped here by purpose. Existing filenames remain stable
because frozen export/provenance records and imports refer to them.

## Setup and repository maintenance

| Script | Purpose |
| --- | --- |
| [bootstrap-runtime.sh](bootstrap-runtime.sh) | Build the pinned llama.cpp runtime |
| [prepare-f16.py](prepare-f16.py) | Prepare the verified BF16-to-F16 model artifact |
| [prepare-q4.py](prepare-q4.py) | Derive same-source Q4_K_M or Q8_0 artifacts; defaults to the historical Q4 path |
| [check_repository.py](check_repository.py) | Check navigation links and coverage of this script index |
| [package_phase1_recovery.py](package_phase1_recovery.py) | Package restored recovery evidence or verify the committed archive |

## Phase 1 workload, review and final export

| Script | Purpose |
| --- | --- |
| [audit_phase1_workload.py](audit_phase1_workload.py) | Audit frozen task selection and checker behavior |
| [audit_phase1_append_conditions.py](audit_phase1_append_conditions.py) | Verify legitimate completion remains possible with appended payloads |
| [audit_phase1_results.py](audit_phase1_results.py) | Verify raw collection identities, requests, traces and outcomes |
| [select_phase1_review_cases.py](select_phase1_review_cases.py) | Select supplementary trace-review cases and measure coverage |
| [export_phase1_evaluation.py](export_phase1_evaluation.py) | Export a complete audited/reviewed cohort into a new immutable snapshot |
| [plot_phase1_evaluation.py](plot_phase1_evaluation.py) | Plot a fully reviewed export; accepts explicit input/output paths |

The Phase 1 report's compact figure generator is
[reports/figures/make_report_figures.py](../reports/figures/make_report_figures.py).
It reads the current recovered tables. New collection uses the package CLI,
not an additional collection script.

## Historical Phase 1 checkpoints and retries

These target original checkpoint/cohort identities. They do not select the
November reproduction automatically; see [collection scope](../results/README.md).

| Script | Purpose |
| --- | --- |
| [export_phase1_clean_checkpoint.py](export_phase1_clean_checkpoint.py) | Export the original reviewed clean-only checkpoint; also shares export helpers |
| [plot_phase1_initial.py](plot_phase1_initial.py) | Plot the early partial baseline |
| [plot_phase1_seed17.py](plot_phase1_seed17.py) | Plot the first seed's clean checkpoint |
| [plot_phase1_clean.py](plot_phase1_clean.py) | Plot the complete clean checkpoint |
| [run_phase1_error_retries.py](run_phase1_error_retries.py) | Run the original outcome-selected retry protocol |
| [analyze_phase1_error_retries.py](analyze_phase1_error_retries.py) | Audit and summarize that retry collection |

## Historical prompt-reminder diagnostics

The first-wave driver and analyzer live in `src/agent_tradeoffs/prompt_defense.py`
and `prompt_defense_analysis.py`. The following selected-case follow-up requires
original first-wave raw evidence and collection-machine paths. Read the
[prompt protocol](../docs/prompt-defense-first-wave.md) before invoking it.

| Script | Purpose |
| --- | --- |
| [run_prompt_defense_seed29_diagnostics.py](run_prompt_defense_seed29_diagnostics.py) | Run the frozen 20-episode seed-29 follow-up |
| [audit_prompt_defense_seed29_diagnostics.py](audit_prompt_defense_seed29_diagnostics.py) | Audit selected-case identities and evidence |
| [analyze_prompt_defense_seed29_diagnostics.py](analyze_prompt_defense_seed29_diagnostics.py) | Summarize paired follow-up outcomes |

## Historical task and attack investigations

These derive the [research investigation](../results/research-investigation-v1/README.md)
from historical prompt-study data. They are interpretation/export tools, not
new model evaluations.

| Script | Purpose |
| --- | --- |
| [build_research_task_contracts.py](build_research_task_contracts.py) | Extract legitimate task contracts and checker semantics |
| [build_research_task_patterns.py](build_research_task_patterns.py) | Summarize task-behavior patterns |
| [build_attack_stage_annotations.py](build_attack_stage_annotations.py) | Annotate attack progression from observed actions |
| [build_attack_visible_stance_annotations.py](build_attack_visible_stance_annotations.py) | Annotate visible stance toward injected instructions |
| [build_research_attack_patterns.py](build_research_attack_patterns.py) | Summarize attack-pattern annotations |
| [build_research_case_cards.py](build_research_case_cards.py) | Generate selected trace case cards |
| [build_research_figures.py](build_research_figures.py) | Generate research figures |
| [validate_research_investigation.py](validate_research_investigation.py) | Check that the derived publication matches its inputs |

## Phase 2 first-pass pilot

For the new study, start with [the preparation plan](../docs/phase2-preparation.md)
and `make phase2-qualification`.

| Script | Purpose |
| --- | --- |
| [audit_phase2_spike.py](audit_phase2_spike.py) | Verify exact sent context views, costs and raw evidence for the development spike |
| [phase2_budget_spike.py](phase2_budget_spike.py) | Freeze, run and inspect the development-only 18-episode budget spike |
| [phase2_calibration.py](phase2_calibration.py) | Profile declared development tasks and validate rolling summaries before broad collection |
| [package_phase2_calibration.py](package_phase2_calibration.py) | Preserve and verify the 24 reviewed calibration episodes and summary replays |
| [phase2_matrix.py](phase2_matrix.py) | Freeze balanced formal task blocks; launch, resume and inspect one block |
| [phase2_collect.py](phase2_collect.py) | Continue all frozen blocks and verify a raw evidence archive after each block |
| [phase2_seed_diagnostics.py](phase2_seed_diagnostics.py) | Prepare and run explicitly selected seed-29 matched pairs only after the completed, fully reviewed 1,098-episode collection; isolated roots, no automatic selection or infrastructure retries |
| [import_phase2_review.py](import_phase2_review.py) | Verify a completed review receipt, evidence hashes and full gate before publishing its annotations |
| [analyze_phase2_matrix.py](analyze_phase2_matrix.py) | Export paired formal metrics, exposure/cost accounting and reviewed scorer sensitivity |
| [analyze_phase2_seed_diagnostics.py](analyze_phase2_seed_diagnostics.py) | Reproduce case-level seed17/29 comparisons from complete source-bound reviews, including isolated restored evidence; no pooled inference |
| [plot_phase2_matrix.py](plot_phase2_matrix.py) | Render cohort-separated outcomes, context mechanisms, tradeoffs and interactions |
| [restore_phase2_evidence.py](restore_phase2_evidence.py) | Restore an archived checkpoint in isolation and reproduce audits/measurements without inference |
| [audit_phase2_candidates.py](audit_phase2_candidates.py) | Check candidate clean/attack pairs offline; does not freeze tasks or evaluate models |

The seed-29 adapter requires an externally supplied selection JSON with exactly
`schema_version: 1`, `seed: 29`, the SHA256 of the final reviewed
`results/phase2-main-v1/analysis/episodes.csv` as `reviewed_table_sha256`, and
`hypotheses`. Each hypothesis has an `id`, a nonempty `question`, exactly two
distinct `sides` (`precision`: `f16`/`q8`/`q4`, `policy`:
`full`/`truncate`/`summary`), and `tasks`. Each task names `suite`, `task`
(`user_task_N`), original `block` (`main-NNN` or `confirmation-NNN`), and two
`source_runs` in the same order as the sides. Unknown fields, duplicate
task/configuration pairs, mismatched references and selections above 32 episodes
are rejected. Every selected task contributes both clean/attack episodes for
both named sides. The adapter does not choose hypotheses or launch runs during preparation.

From the repository root, after final collection and review:

```sh
.venv/bin/python scripts/phase2_seed_diagnostics.py prepare --selection /path/to/selected-hypotheses.json --root artifacts/phase2-seed-diagnostics-v1
.venv/bin/python scripts/phase2_seed_diagnostics.py validate --root artifacts/phase2-seed-diagnostics-v1
.venv/bin/python scripts/phase2_seed_diagnostics.py run --root artifacts/phase2-seed-diagnostics-v1
.venv/bin/python scripts/phase2_seed_diagnostics.py status --root artifacts/phase2-seed-diagnostics-v1
```

Preparation freezes the selection, original evidence/identities, wrapper and
parent code, protocol, models and runtime files under the supplementary root.
Only agent seed 17→29 and isolated output/metadata paths change. Existing local
model verification manifests are read unchanged; no model downloads, probes or
launches occur during preparation/validation. Runtime configs remain beneath
the repository, so the unchanged loader resolves the same pinned model paths.
Execution calls the unchanged `phase2_matrix.run` with process-local alternate
`CONFIGS`/`OUTPUT`, holds the original queue and collection locks plus its own
lock throughout, and preserves ports 8087/8086. An occupied port or live original
service causes refusal; the adapter does not stop an existing service to make room.
The read-only `status` command remains available during collection; it does not
claim full evidence validation or rehash model files while inference is running.
Interrupted supplemental work can resume only with unchanged identities and
vacant ports; orphaned services require separate inspection before retrying.

The parent driver's collection audit remains partial with respect to review.
After reviewing every supplementary trace, use `validate --root <same-root>
--annotations <supplementary-review.csv>` to run the full existing auditor on the
supplementary plan; do not use the formal review importer. Original outcomes
are never replaced. Offline tests cover selection, locking and drift refusal;
real runtime, paired collection/resume and full-review proof await the first
selected diagnostic. No actual selection or supplementary
root is shipped by this implementation.

See [the pilot protocol](../docs/phase2-first-pass.md). This is the 12-task
exploratory grid, not the planned full Phase 2 evaluation.

| Script | Purpose |
| --- | --- |
| [phase2_first_pass.py](phase2_first_pass.py) | Prepare, execute and summarize the pilot |
| [verify_phase2.py](verify_phase2.py) | Drive real model compaction/history-search integration checks |
| [analyze_phase2.py](analyze_phase2.py) | Audit raw pilot evidence and export measurements |
| [phase2_case_cards.py](phase2_case_cards.py) | Generate representative paired trace comparisons |
| [plot_phase2.py](plot_phase2.py) | Plot exported pilot measurements |

Some publication-specific source snapshots remain under `results/*/support/`.
Those belong to their historical publication, not to the default operational
workflow. Add new reusable behavior to `src/agent_tradeoffs/`; add a script here
only when a campaign or maintenance operation needs its own entry point.

## Reviewed local seed comparisons

After the selected diagnostic collection, save its complete review as
`artifacts/phase2-seed-diagnostics-v1/review-draft.csv` and
`review-findings.md`, then run the adapter's full `validate --annotations` gate.
The offline comparison exporter requires the original selection source inside
the repository (the execution adapter also supports external selections). It
replays every episode audit, verifies the frozen parent plan/manifest, recomputes
the seed29 identity and checks captured sampling settings. It binds annotation
hashes, checks unchanged attack payloads/environments/tools against seed17, and
preserves unknown outcomes. It reports paired case rows without inferring that
equal scores prove equal behavior. Review the action-level findings separately.

```sh
.venv/bin/python scripts/analyze_phase2_seed_diagnostics.py --root artifacts/phase2-seed-diagnostics-v1 --output results/phase2-seed-diagnostics-v1
```

Its provenance lists every read input and both table hashes. `--evidence-root`
can point to an isolated restored repository tree: embedded original absolute
paths are mapped only beneath that tree, while recorded evidence bytes and
identities stay unchanged. This does not need model weights or live services.
Existing differing output files are rejected; repeating an identical export
preserves them. The adapter's earlier deployment validation remains distinct
from this offline audit/comparison proof.

The Phase 2 main-report tables and compact plots use
[reports/phase2-figures/make_main_figures.py](../reports/phase2-figures/make_main_figures.py)
on the immutable54-task checkpoint, with their own input/output provenance.
