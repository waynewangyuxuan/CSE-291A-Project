# Frozen Phase 2 formal matrix

The [selected calibration protocol](../../results/phase2-calibration/selected-protocol.json)
is materialized as 18 known-task blocks (972 episodes) and three confirmation
blocks (126 episodes). Each full block has three tasks × three precisions ×
three context policies × clean/attack = 54 episodes; confirmation-003 has one
task and 18 episodes. The ordered manifest is `matrix.json`; each block has its
own plan with complete resolved experiment configurations.

The first block is Banking1, Slack1 and Workspace1. Task lists are interleaved
by suite and split into triples, without selecting against model outcomes.
All six precision permutations cycle over the 18 main blocks; policy orders
rotate by block and precision identity. Thus every precision occupies each
position six times, and every policy occupies each position six times within
each precision. Task order rotates within a policy; clean precedes attack in
each pair. Confirmation order is cyclic but cannot be exactly balanced with
only three blocks. Task families, order and warm-state effects remain limitations.

All configurations retain the selected 7,040-token total budget, 2,048-token
completion reserve, 384-token summary cap, seed17 agent and greedy seed0 F16
summarizer. The same model checkpoint, F16 KV cache, disabled prefix cache and
32,768 server allocation remain fixed. Runtime is pinned llama.cpp, with agent
port8087 and summarizer port8086; output is isolated under
`artifacts/phase2-main-v1/`. The existing execution harness/scorers are unchanged.

The agent reloads at each precision transition; the F16 summarizer stays
resident throughout the block, including Full/Truncate. There is no synthetic
warmup. Service startup metadata and ordered run manifests identify cold first
requests, warm subsequent requests and resume boundaries. Record these effects;
latency is not a randomized independent replication. Per-run memory samples
sum both services and the controller; RSS can double-count shared pages.

## Run one block

```sh
make phase2-matrix-status PHASE2_BLOCK=main-001
make phase2-matrix-launch PHASE2_BLOCK=main-001
```

Launch detaches one block and prevents idle sleep on macOS. Closing the laptop
can still put it to sleep. Progress and logs are under
`artifacts/phase2-main-v1/main-001/`. Keep the machine powered and open.

`make phase2-matrix-run PHASE2_BLOCK=main-001` runs in the foreground or resumes
the same block after interruption. A shared lock prevents concurrent blocks;
code/configuration drift is rejected. Completed episodes, including deterministic
errors, are skipped; interrupted attempt evidence is retained. Different retry
seeds or infrastructure retries belong in separately declared cohorts. The
10-hour per-block collection limit is a ceiling, not a duration estimate.

At block completion, the driver runs the existing mechanical auditor and requires
every expected episode. It then stops both owned services. This partial-mode
audit is explicitly a collection-integrity check: complete evidence-bound trace
review is still pending. Later analysis must separate official scores, semantic
scope, action-state witnesses, attack visibility and unknown outcomes, with
ASR denominators from all cases, own-clean and common-clean subsets.

Only the requested block launches. Subsequent blocks advance after reviewing
integrity/errors/runtime identities. Intermediate analysis checkpoints remain
18, 36 and 54 known tasks. Confirmation stays separate and receives no tuning.

`make phase2-matrix-prepare` deterministically regenerates missing plans and
rejects differences in existing plans. Do not edit a frozen plan to continue a
run; use a new version when the protocol changes.
