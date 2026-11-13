# Phase 2 preparation and execution order

Status: preparation checkpoint, 2026-11-12. No model episodes collected and no
formal evaluation manifest frozen. This plan implements the course's controlled
quantization/context study; Phase 1 supplies background and development evidence.
The roadmap now separates the broad known-task comparison from untouched-task
confirmation. The earlier 108-episode calculation described only nine candidate
tasks across six configurations; it was neither a smoke budget nor the whole study.

## Scope and defaults

The primary policies are Full, whole-turn truncation and one structured rolling
summary. Observation masking is a conditional diagnostic. The core formats are
same-source F16 and Q4_K_M; Q8_0 is a conditional middle level. Preserve the same
checkpoint, harness, tools, scorers, decoding, F16 KV and cache settings.

Use one clean and one attacked episode per task/configuration, with main seed 17.
The provisional existing attack is `ignore_previous_append`, with the first
previously audited goal per suite. Freeze the actual payloads before model runs;
do not select attacks by evaluation outcomes. Extra seeds are paired diagnostics,
not a second mandatory matrix. New defenses, RAG tools and engine optimizations
are outside the core study.

The preparation evidence is in [the checkpoint](../results/phase2-preparation/README.md).
The [candidate input](../configs/phase2/preparation-candidates.json) is deliberately
not an execution configuration. Existing `make phase2-prepare` and `make phase2`
still refer to the historical pilot and must not be used to launch this study.

## Measured preparation

- Nine candidate pairs pass reference execution, nonvacuous utility, injectable
  vectors, attack-checker positive/negative controls and appended-condition checks.
  Reference paths use 1–8 calls, within the existing 15-call limit. This is
  synthetic-benchmark ground-truth execution, not model capability evidence.
- F16 and pinned runtime pass the existing local doctor check. No live inference
  smoke was run. Derived Q4 weights are absent from the expected local path.
- The pinned quantizer advertises Q4_K_M and Q8_0. Q8 model production and inference
  are unverified. Core work does not depend on adding Q8.
- Existing context policies use round triggers, not an exact total-token budget;
  existing summaries reread the original prefix instead of rolling the last summary.

## Ordered work packages

| Stage | Concrete work | Exit evidence | Bound |
| --- | --- | --- | --- |
| 0 Preparation | Candidate audit, asset check, scope and gates | This checkpoint; repeatable audit | One session, zero model episodes; complete |
| 1 Task contract | Define development/main/confirmation membership; review candidate scorers, authorized effects and source families; record exclusions | Reviewed task table, overlap disclosure and paired attack compatibility | One focused session; no confirmation model calls |
| 2 Budgeted execution and smoke | Extend the existing memory implementation with actual rendered-request counting, explicit overflow, whole-turn truncation, rolling summary and source mapping; restore same-source Q4 | One development clean/attack pair through every core format/policy; request, summary and cost records inspect correctly | Start with Full + Truncate, then Summary; 12 smoke episodes across six configurations |
| 3 Development calibration | Choose B, output reserve, summary cap and recent-history rule; measure compaction coverage and real per-cell costs; decide optional Q8 before freezing | Frozen protocol, usable interventions, cost-based campaign budget and pilot analysis export | First batch at most 12 episodes / 2 hours; up to 36 calibration episodes in bounded batches; stop to reassess if inadequate |
| 4 Known-task main comparison | Rerun all core configurations on the fixed known-task set, including contemporaneous Full controls | Broad paired comparison across four suites, complete statuses/costs/retention records | Proposed 54 tasks × 6 configurations × 2 conditions = 648 episodes |
| 5 Untuned-task confirmation | Evaluate the frozen candidate set once, with the exact same configurations, budget and scoring | Separately reported confirmation results, including disagreements with the main study | Up to 9 currently audited candidates × 6 × 2 = 108 episodes |
| 6 Targeted explanation | Resolve important configuration flips and representative mechanisms; optional budget or masking ablation only for a named question | Paired extra-seed results, source-linked trace evidence, explicit limits of explanation | Example: 4–8 tasks × 2 compared configurations × clean/attack × 1 extra seed = 16–32 episodes |
| 7 Analysis and delivery | Complete trace-review gates, four main plots, recommendations, report and reproducibility package | Full metric table, reviewed cases, reproducible plots and verified raw archive | A separate closeout stage; collection alone is not completion |

Each implementation stage becomes one bounded bet after its predecessor passes.
Use the existing code paths rather than creating a separate runner for Phase 2.
Start with Full + Truncate through one real pair, then add Summary to that same
path. Read-only preparation can overlap with asset work; measurements use the
inference service serially to avoid contention. No parallel-agent work is needed
for the current preparation checkpoint.

## Task allocation and study scale

The planning default is 12 development tasks: the six existing reserved development
tasks plus six drawn from the Phase 1 workload before new model runs. A balanced
allocation can add two Slack, two Workspace and two Travel tasks, giving three
development tasks per suite. Select these by workflow and reference-path coverage,
not by favorable model outcomes. Travel 17/18 are possible longer-path development
choices; their use would remove them from the main comparison. Exact IDs remain
unfrozen until the task-contract stage.

This leaves 54 of the original 60 tasks for the broad main comparison: 8 Banking,
14 Slack, 22 Workspace and 10 Travel under that allocation. They have already been
observed in Phase 1, so label this a known-task controlled comparison. Excluding
current calibration tasks does not turn the other 54 into unseen tasks.

The nine new candidate IDs are a separate confirmation pool. They stay out of all
prompt, budget and summary tuning. Source/checker qualification is allowed; if a
candidate is consumed for model debugging, remove it from confirmation explicitly.
Shared environments and workflows remain a generalization limit. Report main and
confirmation results separately and disclose all disagreements. Do not tune on a
confirmation failure and then present the rerun as the original held-out result.

| Planning scenario | Main comparison | Confirmation if all 9 qualify | Total before smoke/calibration/diagnostics |
| --- | ---: | ---: | ---: |
| Core: F16/Q4_K_M × Full/Truncate/Summary | 54 × 6 × 2 = 648 | 9 × 6 × 2 = 108 | 756 |
| With supported Q8_0 added before freeze | 54 × 9 × 2 = 972 | 9 × 9 × 2 = 162 | 1,134 |

These are proposed workloads, not authorization to start collection or a frozen
sample size. The core remains a valid two-factor study; the extra precision adds
an intermediate level when resources allow. A smaller main set must be selected
before model results using stated coverage criteria and evaluated across the full
core grid. If independent held-out generalization is a primary claim, expand and
audit that pool before freezing; neither more configurations nor more seeds can
turn nine task IDs into a large independent task sample.

The core plus 12 smoke, 12–36 calibration and 16–32 targeted-repeat episodes is
approximately 796–836 episodes, assuming every candidate qualifies. Extra-seed
diagnostics are conditional, not mandatory, so the actual total can be lower.
Do not pool smoke, tuning or selected diagnostics into the main estimates.

## Resources and decision points

The recovered Phase 1 collection summed to 12.72 hours over 600 episodes
([measurement](../results/phase1-recovery/evaluation-assessment.md)). Merely scaling
that observed average gives about 16.0 hours for 756 episodes or 24.0 hours for
1,134 episodes. This is a reference calculation, not a run-time forecast: the new
clean/attack mix, quantization, summaries, context limits and failure behavior all
change costs. It excludes implementation, model loading, trace review and report
work. Replace it with per-cell pilot costs before scheduling the main campaign.

Keep collection serial, checkpoint at small suite/configuration blocks, and inspect
execution/recording health between blocks. If a harness bug requires a protocol
change, version it and rerun affected comparisons rather than mixing versions.
Poor task success is a result, not permission to replace tasks or tune mid-campaign.

Priorities under a time limit are the complete core grid, untouched confirmation,
trace review and reproducibility. Add Q8 before freeze only if feasible. Later
diagnostics answer a named uncertainty: e.g. one second-budget sweep on a fixed
12-task known subset costs 12 × 6 × 2 = 144 additional episodes; masking on that
subset across two formats costs 12 × 2 × 2 = 48. Choose these by research value and
label outcome-selected diagnostics; do not automatically run every extension.

The course deadline and available machine time are still requested from the user.
Until known, stages and resource caps are planning defaults, not calendar promises.

## Data and acceptance contract before implementation

There is one policy decision record per actual request: source message IDs/roles,
before/after rendered token counts, retained/removed groups, summary version and
its source coverage, attack visibility and prior exposure. Preserve the unmodified
scoring transcript and the exact sent view. A summary's source IDs do not by
themselves prove semantic retention; source-linked review remains required.

B is the total model context limit for the study, with a fixed output reserve.
Count system/user text, tool schemas, history, summary and chat-template overhead.
Full retains all content and records budget overflow explicitly. Truncate/Summary
must meet the same input allowance without splitting tool-call/return groups.
An oversized indivisible group also gets explicit overflow. A larger-budget Full
reference, if added, is labeled separately. Freeze a separate feasible summarizer
input/output budget and deterministic overflow handling, and charge its entire
cost. Summary must never gain access to reference answers or unseen task facts.

Freeze summarizer checkpoint, precision and prompt across agent-quantization arms.
Include agent + summary tokens, latency and deployment memory, including failures.
Report memory components and residency assumptions separately. Do not infer reduced
allocated KV memory merely from fewer input tokens. Keep decode, cache and runtime
allocation settings fixed and balance collection order across formats.

Main/confirmation collection requires all of the following evidence:

1. Source-audited task contracts and immutable paired manifests for each cohort;
   calibration tasks excluded from main, confirmation untouched by model tuning,
   and previous Phase 1 exposure disclosed for the known-task comparison.
2. Same-source model hashes and real tool-calling smoke for every included format.
3. Verified token budget/overflow and source mapping; ordinary and attacked cases
   exercise a real context transformation on development data.
4. Frozen B, summary rules, seed, payloads, execution limits, run order and retry rules.
5. Collection/resume checks and an export that preserves unknown outcomes and costs.
6. A measured per-cell pilot cost estimate and a resource budget for collection,
   trace review, targeted diagnostics and final delivery.

## Analysis and deliverable contract

RQ1: paired quantization effects within each policy. RQ2: task/attack information
retention and representative action timelines. RQ3: interactions and deployment
tradeoffs. Produce a complete metric table, paired-effect plot, retention/exposure
plot, interaction plot, tradeoff plot, and two or three source-linked trace cases.

Report clean/attacked task success, ASR, false refusal, latency, input/output tokens
and memory. ASR includes all cases and each configuration's own clean-success
subset, with unknowns and denominators; pairwise common-clean subsets are an
additional comparison. Distinguish never exposed, previously exposed then removed,
literal attack retained and semantic derivative retained/uncertain. Exposure groups
are descriptive, not randomized causal subgroups. Use task-paired uncertainty and
report family overlap; a wide interval is not evidence of equivalence. Low cost
from early failure does not establish efficient task completion.

Every figure must link to data, configuration and analysis code. Preserve the raw
action traces, exact requests, summaries and source mapping in a new archive;
historical pilot results remain separately labeled. Targeted extra-seed runs stay
separate from the single-seed main matrix. New defense development is optional.

Review is planned before collection: review all clean traces used to claim false
refusal rates, the protocol-required attacked sample, every scored attack success
needed for mechanism claims, and all primary comparison flips. Generate a deduped
review queue from the frozen rules; add semantic attack-retention labels where
automatic literal matching is insufficient. If coverage is incomplete, disclose
it and withhold a full-cohort refusal claim. Finish the report only after the
collection, review and export gates pass and the archive verifies. A result of
little quantization effect or no robustly dominated configuration remains valid.

## Reproduce this preparation checkpoint

```sh
make phase2-qualification
make phase2-lint
make check
```

The qualification command executes only synthetic benchmark functions. It reuses
the pinned workload audit, checks the appended legal path, and refuses to overwrite
different evidence. Use the script's `--output` argument for a new audit revision.
It does not replace semantic scorer review or freeze a task set.

The nine-task qualification checkpoint supplies only the proposed confirmation
pool. The full study scale and its assumptions are specified above; 108 episodes
is not a smoke allowance and does not represent all of Phase 2.
