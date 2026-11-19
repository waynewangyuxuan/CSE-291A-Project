# Bounded same-state mechanism continuations

This lane contains a runner and offline restoration/contract proof, **not real-model results**.
The parent owns all service launches and the subsequent real-model proof. The runner never
starts or stops a service. Frozen harness, adapters, profilers and checkpoint files are unchanged.

| Source checkpoint (zero-based request index) | Precision | Arms | Fresh seeds |
| --- | --- | --- | --- |
| Workspace28 Full request2 | Q8_0 | Full, Truncate, Summary | 41, 53, 67 |
| Workspace14 Full request3 | F16 | Full, Truncate, Summary | 41, 53, 67 |

Exactly 18 continuations. Workspace14's historical capability qualification remains unknown.
These two source-bound mechanism cases support no precision-generalization, broad capability,
profile-trigger policy or full-matrix claim.

## Contracts

`prepare` re-runs the existing checkpoint restoration and compares the complete report and
both snapshots with the pinned, repeated offline proof. It retains original initial environment,
restored environment, complete history, original exact payload, all source hashes and boundaries.
Workspace28 protects messages 4–5 and transforms messages 2–3. Workspace14 protects messages
6–7 and transforms messages 2–5. Both retain the original system/user messages and tool schemas.

A small `ContinuationAgent` subclass uses the original `LocalAgent.query` loop. Its initialization
hook substitutes typed checkpoint history only after checking the original fixed prompt. Every
actual request checks unchanged tool schemas and sampling parameters; the Full starting payload
is identical to the source except seed. Original tool calls count toward the 15-step ceiling;
new requests, attempted requests and executed tool steps are reported separately.

The restored typed environment preserves initial-object aliases and source injections, including
future tool reads. Original initial state is used for utility/attack scoring, and restored start
state is separately recorded. No new environment is initialized, no original tool output is
substituted, no future ID or timestamp is rewritten, and no current-run lookahead is used.

Each case has one F16 greedy seed0 summary, capped at 384 tokens, reused by all three Summary
seeds. The existing memory service writes the full raw request, canonical call-ID mapping and
raw response before the summary is validated and used. Empty, tool-calling, truncated or
over-budget summaries fail closed. Truncate and Summary transform the identical older prefix
once; future history stays intact. All actual agent requests must fit 4,992 input tokens plus a
2,048 completion reserve, measured with the live server's chat template and tokenizer. Later
overflow is retained as an error; it does not cause another compaction.

Each continuation reserves 280 seconds and each of two summaries reserves 120 seconds: maximum
planned reservation 5,280 seconds, below the 5,400-second cumulative ceiling. The wall-clock
alarm covers model/tool work and guards. Interrupted reservations remain charged in full.
Finalized attempts, including behavior errors, are never retried. Interrupted unsealed attempts
block continuation and need review; do not delete evidence or choose a fresh root to evade the
study budget. Service startup time is outside model-work accounting and belongs to the parent.

## Parent commands

Run from MAIN after integrating these four files. No service is needed for preparation:

```sh
cd /Users/wang/CSE-291A-Project
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
OUT="$PWD/artifacts/phase2-context-continuation-v1"
.venv/bin/python scripts/phase2_context_continuation.py prepare \
  --source-root "$PWD" --output-root "$OUT"
```

The following service operations are for the parent only. Keep the stages serial; the summarizer
does not need to remain resident during continuation. Each stage acquires the existing formal
queue/run/launch locks and both shared endpoint evaluator locks. The exact original agent runtime
semantic identity is required; the summary identity is the pinned F16 runtime at port 8086.

```sh
.venv/bin/agent-tradeoffs serve --config configs/phase2-main-v1/runtime-summarizer.toml --background
.venv/bin/python scripts/phase2_context_continuation.py run --stage summaries \
  --source-root "$PWD" --output-root "$OUT"
.venv/bin/agent-tradeoffs stop --config configs/phase2-main-v1/runtime-summarizer.toml

.venv/bin/agent-tradeoffs serve --config configs/phase2-main-v1/runtime-q8.toml --background
.venv/bin/python scripts/phase2_context_continuation.py run --stage q8 \
  --source-root "$PWD" --output-root "$OUT"
.venv/bin/agent-tradeoffs stop --config configs/phase2-main-v1/runtime-q8.toml

.venv/bin/agent-tradeoffs serve --config configs/phase2-main-v1/runtime-f16.toml --background
.venv/bin/python scripts/phase2_context_continuation.py run --stage f16 \
  --source-root "$PWD" --output-root "$OUT"
.venv/bin/agent-tradeoffs stop --config configs/phase2-main-v1/runtime-f16.toml

.venv/bin/python scripts/phase2_context_continuation.py audit \
  --source-root "$PWD" --output-root "$OUT"
```

Stop and review a rejected stage before the next command. Existing services must already match
the intended stage; the runner never kills or adopts an unrelated process. Source drift, changed
runtime identity, concurrent evaluation, path escape/symlinks, tampered artifacts or incomplete
attempts return nonzero. `audit --partial` is available between stages. Source files and this
runner must remain unchanged after preparation; integration precedes preparation.

Expected artifacts under `$OUT`:

- `plan.json`: complete source-bound snapshots, configuration, immutable source hashes and 18 units.
- `budget.json`: every reservation, elapsed/charged seconds, interrupted reservations.
- `summaries/<case>/`: original/canonical source mapping, runtime capture, raw memory requests and
  responses, rendered token-count log, validated result and file-hash receipt.
- `continuations/<case>-seed<seed>-<arm>/`: `start.json` with original/new payloads, common state,
  histories and summary receipt; `runtime.json`; `requests.jsonl`; `context-requests.jsonl`;
  `prefixes.jsonl` with every full intermediate history and transformed request view;
  `tool-steps.jsonl` with before/after environments and exact actions/results;
  `trace.json`, `result.json` and `receipt.json`.
- `audit.json`: reproducible complete coverage, common-state/history/boundary/Full-control,
  raw-summary mapping, runtime, budget, request/action and final-environment checks.

Literal attack-payload exposure, original call IDs and source-message markers are recorded;
`last_sent_retention` describes the last actual request (null when none was sent), while
`archival_retention` describes the retained audit history. These are deliberately separate.
The Summary envelope enumerates every source ID: Workspace14 records `[2, 3, 4, 5]`.
semantic retention and refusals remain unknown pending raw-trace review. Behavior errors keep
utility/attack outcomes null. `real_model_proof` requires raw responses in all 18 continuations;
an audited error or a synthetic test does not establish intervention effectiveness.

## Offline verification

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  -p no:cacheprovider tests/test_phase2_context_continuation.py tests/test_phase2_followup_checkpoint.py
.venv/bin/ruff check scripts/phase2_context_continuation.py tests/test_phase2_context_continuation.py
.venv/bin/ruff format --check scripts/phase2_context_continuation.py tests/test_phase2_context_continuation.py
.venv/bin/python scripts/phase2_context_continuation.py prepare --check-only \
  --source-root "$PWD" --output-root "$PWD/artifacts/phase2-context-continuation-v1"
```

`prepare --check-only` writes no output and performs zero model calls. It reproduces restored
environment hashes `5ada75b19282a8905779711913003c9eb2e677d195f425eaeb7eced657346838` (Workspace28)
and `aac68bef47527e3d290c46a849de55563c99154d3fc88c80dfa635a3f4e87b0a` (Workspace14).
Tests use synthetic responses only at model boundaries, with real typed environments and
the real LocalAgent/tool runtime, to test contracts. They are not real-model proof.

Worker verification on 2026-11-18: 33 targeted tests passed; the complete worktree suite passed
354 tests with 2 skips. Ruff lint/format and diff whitespace checks passed. Two consecutive
real-source preparations were identical, and all six case/seed Full starting payloads matched
the originals except seed. Plan hash:
`82489f748f68b33d1b539fce5d86621d0569c03750b00ce3b6dcabd0ee00e1ac`.
No model service or inference was started by the worker.
