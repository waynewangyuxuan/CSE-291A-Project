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

Preparation generates `$OUT/configs/runtime-{q8,f16,summarizer}.toml`. Only `output_root`
differs from the frozen originals, pointing to `$OUT/runtime/<role>`. `server.json`, launch logs
and shutdown metadata stay there. Model paths, commands, flags and expected semantic identities
remain unchanged. The runner reads only these owned metadata paths.

The following parent-only wrapper holds **the same locks across startup, inference and shutdown**:
MAIN `artifacts/phase2-main-v1/{.queue.lock,.lock,.launch.lock}`, `$OUT/.lock`, and both MAIN
`artifacts/runtime/evaluator-locks/` locks keyed by the frozen runner's normalized loopback ports
8086 and 8087. It calls the existing lifecycle helpers without editing them and stops its owned
service in `finally`, including a rejected or interrupted stage. Do not invoke `run_locked`
without `study_locks`; the ordinary `run` CLI acquires these locks itself when the parent has
already launched a matching owned service.

```sh
parent_stage() {
  .venv/bin/python - "$OUT" "$1" <<'PARENT_PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import phase2_context_continuation as study
from agent_tradeoffs.runtime import start_server, stop_server

output = study.output_root(Path.cwd(), Path(sys.argv[1]))
stage = sys.argv[2]
assert stage in ("summaries", "q8", "f16")
role = "summarizer" if stage == "summaries" else stage
plan = study.read(output / "plan.json")
config = output / f"configs/runtime-{role}.toml"
with study.study_locks(output):
    study.verify_sources(plan)
    study.verify_runtime_configs(plan, output)
    start_server(config)
    try:
        study.run_locked(plan, output, stage)
    finally:
        stop_server(config)
PARENT_PY
}
parent_stage summaries || exit 1
parent_stage q8 || exit 1
parent_stage f16 || exit 1
.venv/bin/python scripts/phase2_context_continuation.py audit \
  --source-root "$PWD" --output-root "$OUT"
```

The explicit inference-only equivalent for a matching service already owned by the parent is:

```sh
.venv/bin/python scripts/phase2_context_continuation.py run --stage summaries \
  --source-root "$PWD" --output-root "$OUT"
```

Use `--stage q8` or `--stage f16` for the corresponding continuation stage and stop that owned
service before advancing. Never launch a continuation service with the original formal configs.

Stop and review a rejected stage before the next command. Existing services must already match
the intended stage; the runner never kills or adopts an unrelated process. Source drift, changed
runtime identity, concurrent evaluation, path escape/symlinks, tampered artifacts or incomplete
attempts return nonzero. `audit --partial` is available between stages. Source files and this
runner must remain unchanged after preparation; integration precedes preparation.

Expected artifacts under `$OUT`:

- `plan.json`: complete source-bound snapshots, configuration, immutable source hashes and 18 units.
- `configs/runtime-*.toml`, `runtime/<role>/`: derived owned service configuration and lifecycle evidence.
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
Semantic retention and refusals remain unknown pending raw-trace review. Behavior errors keep
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

Worker verification on 2026-11-18: 34 targeted tests passed; the complete worktree suite passed
355 tests with 2 skips. Ruff lint/format and diff whitespace checks passed. Two consecutive
real-source preparations were identical. All six case/seed Full starting payloads matched the
originals except seed; the three derived runtime configurations change only service output paths.
Final plan hash for the documented MAIN output directory:
`c848e7e7eb567a831ac449cdaafeb436d7a0b53bfcbc692bf0ebaf8dc418441c`.
No model service or inference was started by the worker.
