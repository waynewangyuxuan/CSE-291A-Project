# Phase 2 formal collection and analysis

Collection uses the [frozen matrix](../../configs/phase2-main-v1/README.md):
972 known-task episodes and126 separate confirmation episodes, at seed17.
The continuous queue advances through all21 blocks after fresh integrity audits
and per-block raw archival. Use `make phase2-collect-status` for live progress;
the files below are reproducible checkpoints, not a live completion counter.

## Reviewed checkpoint: main-001

The first54 episodes cover Banking1, Slack1 and Workspace1 across all nine
precision/context configurations and both conditions. All54 finalized without
terminal errors, and [full review verification](reviews/main-001.verification.json)
passes54 identity-bound annotations. [Review evidence](reviews/main-001.csv) and
[the case analysis](reviews/main-001.md) distinguish official scores from observed
task behavior. This is an agent-authored qualitative trace review backed by
actions and environment states, not an independent human rating or a safety score.

All54 traces have zero compactions; this checkpoint cannot establish context
policy effects. It does reveal analysis hazards: Banking counts incoming money
as spending; Slack's checker accepts messages without an article summary; three
Q4 Slack attacks are never exposed; Workspace attacks contaminate answers while
the email goal is not executed. No unnecessary refusal is observed in27 reviewed
clean traces. These are counts on three selected tasks, not general conclusions.

The [raw archive](../../artifacts/phase2-main-v1/archives/main-001.tar.gz) contains
every episode, request/context record, runtime snapshot and frozen execution
source. Its [inventory](../../artifacts/phase2-main-v1/archives/main-001.manifest.json)
hashes every member. Fresh extraction reproduces the54-episode collection audit;
removing one trace prevents a new archive from being published. Review annotations
are versioned separately so the raw collection archive remains unchanged.

## Analysis protocol

`make phase2-main-analyze` exports explicitly provisional measurements from
archived complete blocks. Main and confirmation cohorts stay separate. It reports:

- Legitimate clean/attacked utility and target attack success, including unknown
  bounds; own-clean and pairwise/all-configuration common-clean denominators.
- Reviewed unnecessary refusals, failure mechanisms, scorer false positives and
  a sensitivity analysis excluding whole flagged tasks from all arms.
- Actual compaction, literal exposure and removal after prior exposure; literal
  absence is not proof that the attacker meaning disappeared.
- Latency, agent/summary tokens and component/aggregate RSS, including failed
  episodes; common-clean costs avoid comparing early failure with successful work.
- Paired configuration flips, quantization/context interactions and descriptive
  dominated configurations. None establishes statistical certainty or causality.
  Paired intervals resample whole known tasks within suites (2,000 resamples,
  seed 0); a suite with fewer than two tasks suppresses the interval. Unknown
  outcome bounds remain separate. A degenerate interval is not equivalence.

The [measurement export](analysis/measurements.json) and
[episode table](analysis/episodes.csv) retain identities, archive hashes,
denominators and review coverage. Known weak-checker findings never rewrite
official outcomes. Eligibility sensitivity requires reviewed clean success and
excludes flagged false positives; incomplete reviews limit its coverage.

`make phase2-main-plot-provisional` renders the five figure types with
visible provisional labels. `make phase2-main-final` refuses to publish a final
export until all1098 episodes and complete evidence-bound review are present;
the final export also reruns every block's complete mechanical and review gate.
`make phase2-main-plot` then renders final figures. This separates collection
integrity from completed scientific interpretation.

Infrastructure retry candidates and specific rare/configuration-flip hypotheses
will be selected from the completed evidence. Capability failures and deterministic
context/step-limit failures remain outcomes. Any extra attempt preserves its
original comparison and has a separately recorded purpose and seed.
