# Phase 2 preparation and execution order

Status: preparation checkpoint, 2026-11-12. No model episodes collected and no
formal evaluation manifest frozen. This plan implements the course's controlled
quantization/context study; Phase 1 supplies background and development evidence.

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
| 1 Task contract | Read prompts/scorers for the nine candidates; record required fields, authorized effects, source families and overlap; reject ambiguous cases with reasons | Reviewed candidate table; final eligible count; attack/task compatibility | One focused session; no held-out model calls |
| 2 Budgeted execution | Extend the existing memory implementation with actual rendered-request counting, explicit overflow, whole-turn truncation, rolling summary and source mapping; restore same-source Q4 using existing preparation path | Fast checks plus one development clean/attack pair through each policy; all requests and summary costs recorded | One vertical slice first; at most 6 initial development episodes |
| 3 Development calibration | Choose B, output reserve, summary cap and recent-history rule on previously used tasks; verify Q4 service and paired controls | Recorded intervention coverage, exact token accounting, failure review, runtime hashes and frozen settings | First batch at most 12 episodes / 2 hours, whichever comes first; includes Q4 smoke; reassess if insufficient |
| 4 Formal study | Freeze eligible tasks, one payload per task, configuration and run order; collect all paired cells with compatible resume | Complete collection and source/content-retention audit | 12N episodes for two formats × three policies; N remains unfrozen |
| 5 Explanation and delivery | Complete trace review, targeted paired repeats if needed, four plots and deployment recommendations | Review/export gates, reproducible tables/figures, packaged raw evidence | Repeat only specific unresolved comparisons; define a cap before starting them |

Each implementation stage becomes one bounded bet after its predecessor passes.
Use the existing code paths rather than creating a separate runner for Phase 2.
Start with Full + Truncate through one real pair, then add Summary to that same
path. Read-only preparation can overlap with asset work; measurements use the
inference service serially to avoid contention. No parallel-agent work is needed
for the current preparation checkpoint.

Development uses already studied tasks; Workspace 0 and Travel 17/18 are initial
candidates for short and longer paths, chosen from reference-path lengths rather
than new model outcomes. The nine qualification candidates stay out of all prompt,
budget and summary tuning. If a candidate is consumed for debugging, remove it
from the confirmation set explicitly. Shared suite environments do not establish
new task-family generalization. A small confirmation set may need to be reported
separately from a larger known-task exploratory study.

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

Formal launch requires all of the following evidence:

1. Source-audited task contracts, no tuning contamination, immutable paired manifest.
2. Same-source model hashes and real tool-calling smoke for every included format.
3. Verified token budget/overflow and source mapping; ordinary and attacked cases
   exercise a real context transformation on development data.
4. Frozen B, summary rules, seed, payloads, execution limits, run order and retry rules.
5. Collection/resume checks and an export that preserves unknown outcomes and costs.

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

With all nine candidates eventually accepted, the core matrix would contain 108
episodes. Adding Q8 would add 54; adding masking across two formats would add 36.
These are arithmetic scenarios, not a promised sample size or measured run-time
estimate. Record time and summary overhead in development before budgeting the
formal campaign.
