# Script index

Use `make help` and the [run guide](../docs/running.md) for normal workflows.
These scripts are grouped here by purpose. Existing filenames remain stable
because frozen export/provenance records and imports refer to them.

## Setup and repository maintenance

| Script | Purpose |
| --- | --- |
| [bootstrap-runtime.sh](bootstrap-runtime.sh) | Build the pinned llama.cpp runtime |
| [prepare-f16.py](prepare-f16.py) | Prepare the verified BF16-to-F16 model artifact |
| [prepare-q4.py](prepare-q4.py) | Derive the Q4 artifact used by the context pilot |
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

The report's compact figure generator is
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
| [audit_phase2_candidates.py](audit_phase2_candidates.py) | Check candidate clean/attack pairs offline; does not freeze tasks or evaluate models |

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
