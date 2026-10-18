# Phase 1 error recovery diagnostic

Rerun each of the original 25 error episodes twice with its original seed and
settings. The resulting 50 supplemental attempts measure whether those selected
errors recur after restarting the same deployment. They do not expand the
original 600 episode evaluation or provide independent new seed repetitions.

The original selection contains 3 clean and 22 attack episodes, representing
6 distinct suite and task pairs. Both additional attempts run even if the first
returns normally. Each episode starts from a fresh benchmark environment. This
is full episode repetition, not an automatic retry of an individual request.

## Frozen controls

Keep the original unquantized Qwen3 8B F16 weights, F16 KV cache, 32768 context
allocation, nonthinking mode, full history policy, original seed and sampling,
2048 output token limit, 15 executed tool call limit, 120 second request timeout
and 600 second episode timeout. Every request retains `cache_prompt=false`.
The evaluator and dependency lock remain unchanged. A new process must match
the original campaign runtime semantic identity before any supplemental run;
every run passes that original identity to `run_experiment`.

Select cases exclusively from the original exported rows with `status=error`
and verify them against their raw result identities and hashes. Group by source
run and one injection goal to avoid introducing a task and goal cross product.
Freeze 15 configurations, repeated in two rounds using 30 unique run IDs.
The driver rejects omissions, duplicates, additional cases and changed settings.

Original campaign, run evidence, public Phase 1 results, runtime metadata and
frozen implementation inputs are protected by a byte inventory. Supplemental
runtime metadata, raw evidence and summaries live under
`artifacts/phase1-error-retries/`; the new report lives under
`results/phase1-error-retries/`. The supplemental runner refuses to overwrite a
plan or existing diagnostic. Explicit resume preserves finalized outcomes.

## Reporting

Report normal return recovery separately from legitimate utility and attack
success. Error outcomes stay unknown. Provide per round results, per original
error case transitions and counts of cases returning normally in zero, one or
both attempts. Do not select the best outcome or substitute a recovered attempt
for its original unknown score.

Preserve the original same task and seed clean control score for each attack,
including unknown controls. Supplemental clean outcomes do not retroactively
change original eligibility. Report all attempt latency, returned token usage,
requests, tools, request errors and server process RSS. Missing failed request
tokens remain unknown; RSS excludes total GPU and machine memory. Runtime
startup and the tool probe are separate overheads. Costs are descriptive because
these are selected errors, a restarted service and a shared host.

## Execution

From the repository root, using the existing frozen environment:

```sh
PYTHONHASHSEED=0 .venv/bin/python scripts/run_phase1_error_retries.py prepare
PYTHONHASHSEED=0 .venv/bin/python scripts/run_phase1_error_retries.py check
PYTHONHASHSEED=0 .venv/bin/python scripts/run_phase1_error_retries.py run
```

Commit the frozen selection and configurations before launching the run.
If interrupted, inspect the diagnostic manifest and retained evidence, then use
the same run command with `--resume`. Finalized errors are retained rather than
silently retried again.

If the managed service itself stopped or startup failed, inspect its dedicated
runtime metadata and log. Start it with
`.venv/bin/python -m agent_tradeoffs.cli serve --background --config configs/phase1-error-retries/runtime.toml`
before resuming. An occupied port is an inspection condition; the driver never
terminates another service to take it.
