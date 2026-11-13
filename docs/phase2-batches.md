# Phase 2 batches

Execution plan, 2026-11-12. The current spike uses F16, Q8_0 and Q4_K_M from the
same checkpoint, crossed with Full, whole-turn truncation and rolling summary.
Each task/configuration has one clean and one attacked episode at seed 17.
The [preparation roadmap](phase2-preparation.md) explains research questions,
task qualification and final deliverables. Its earlier two-precision counts are
superseded here by the three-precision workload.

| Batch | Work | Planned episodes | Checkpoint before continuing |
| --- | --- | ---: | --- |
| B0 | Prepare all three weights; verify exact template counting and context transformations | No benchmark episodes | Same-source hashes; actual token usage agrees; protected tool groups remain complete; rolling update uses prior summary plus new groups |
| B1 | Development spike: Travel 17 × three precisions × three policies × clean/attack | 18 | Inspect every trace and all errors; verify actual compaction, retention records, summary cost and overflow behavior |
| B2 | Cross-suite development calibration, first two task pairs across all nine configurations | 36 initially | Select tasks by workflow before running; calibrate budget and summary cap using development only; time estimate and freeze protocol before main runs |
| B3 | Known-task comparison, provisionally 54 tasks, in three-task blocks | 972; 18 blocks of 54 | After each block: collection integrity, unknown outcomes, runtime identity and complete pairs; intermediate metric tables at 18/36/54 tasks |
| B4 | Untuned confirmation, up to nine qualified candidates, same frozen protocol | Up to 162 | Qualification and semantic review first; report separately; no tuning against these outcomes |
| B5 | Infrastructure-error retries | Data-dependent; at most two extra attempts per failed cell | Same config/task/seed, separate attempt IDs; preserve original unknowns and report denominators; deterministic budget or capability failures are not infrastructure retries |
| B6 | Targeted long-tail / configuration-flip confirmation | Typically 16–32 | Name hypothesis and matched configurations first; seed 29 (and one further seed only if needed); rerun both sides of a comparison |
| B7 | Complete trace reviews, analyze and package delivery | No routine new episodes | Review gate, representative failures, interaction and dominance analysis, reproducible plots and archived raw evidence |

B1 and the initial B2 would add 54 episodes to the provisionally 1,134-episode
main/confirmation matrix: 1,188 before retries and targeted diagnostics. This is
a planning envelope, not a promise that all nine candidates qualify. Development
membership and the 54-task main set still require the task-contract checkpoint.
Travel 17 is now committed to development and must not enter confirmation/main.
A [source-only scorer follow-up](../results/phase2-preparation/semantic-followup.md)
flags Workspace 13/25: both accept incomplete legitimate behavior. If excluded,
confirmation would be at most seven currently reviewed candidates (126 episodes);
no revised cohort is frozen yet.
There are more tasks in upstream AgentDojo; 60 was our selected Phase 1 workload,
not the entire benchmark. Phase 1 ran 600 episodes on those 60 tasks.

## Current executable batch

The [reviewed B1 checkpoint](../results/phase2-spike/README.md) contains all 18
finalized episodes and explains why development calibration is still required.

[The frozen spike plan](../configs/phase2-spike/plan.json) contains all 18 episode
configurations. Run `make phase2-spike`; inspect `make phase2-spike-status`.
The run target first verifies all three local artifacts and registers their
runtime verification manifests. Quantization provenance alone does not satisfy
the existing service launch gate.
Only this development batch is executable. Later stages proceed after their
technical checkpoints, without changing main results in response to observations.

The spike total context budget is 7,680 tokens, reserving 2,048 for completion:
5,632 input tokens including system/user messages, all tool schemas, chat template
and retained history. This is a development stress setting, not the formal study
budget. Full preserves all history and explicitly stops when it exceeds budget;
it never silently truncates. The server context allocation remains 32,768 with
context shift disabled, so the common harness enforces the smaller study budget.
An overflow is recorded as an error/unknown outcome, never successful resistance.

Truncation removes oldest complete assistant/tool-result groups and keeps at
least the most recent complete group. The original system/user messages remain.
Rolling summary uses a fixed F16 summarizer at seed 0, greedy decoding, a
32,768-token summarizer context and 256-token output cap. It consumes only the
previous summary plus newly removed groups; original source messages remain in
raw evidence. All policies use identical tool schemas/permissions. Summary is
inserted as a synthetic historical tool exchange, never an executable new tool.
Its provenance instruction is part of this summary policy, so conclusions cannot
isolate compression alone from the policy's handling of source attribution.

Every request view records original source IDs retained, removed and summarized;
exact rendered token counts; literal attack-payload matches now and previously
seen; original requests and raw summary requests/responses. Literal absence does
not establish semantic removal: trace review must identify paraphrased malicious
instructions and distinguish removal before first exposure from later removal.
Both summary and agent tokens/time are counted. Each run also samples aggregate
RSS of the two resident inference services plus the orchestrator; this is sampled
process RSS, not GPU allocated memory or unique physical RAM; summing RSS can
double-count shared pages. The fixed summarizer remains resident for
Full and Truncate too. Report component memory as well as this controlled shared
resident setup; it is not the minimum possible deployment footprint per policy.

The B1 orchestration has a two-hour wall-clock budget, at most ten minutes per
episode and fifteen agent tool steps. It runs serially with no SDK retries.
Compatible resume skips finalized episodes; changed implementation/configuration
requires a new cohort. A laptop sleep can interrupt or stall collection; keep the
machine powered and awake during measured batches. B1 results are plumbing and
calibration evidence, not estimates of security or quantization effects.

## Reading intermediate results

At each checkpoint distinguish finalization from successful task completion.
Show completed/error/unknown counts; legitimate task success; attack success over
all attacked episodes and separately clean-completable pairs; reviewed false
refusals; latency, agent/summary token totals and sampled peak RSS. Inspect
truncation/summary trigger rates and attack visibility before interpreting ASR.
Do not change the frozen main protocol because a particular arm looks bad.
Infrastructure problems get a versioned correction or separate retry cohort.

The final analysis follows three questions: quantization within each policy,
context information loss and attack retention, then interactions and deployment
tradeoffs. Four main figures cover paired success/security effects, retention
mechanisms, precision-by-policy interactions and cost/capability/security
frontiers. A finding of little precision effect is valid; a single seed and
shared task families limit certainty and must remain visible in the report.
