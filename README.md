# Security–Performance Tradeoffs in Resource-Constrained Agents

CSE 291A, Fall 2026 · Wayne Wang

How do **weight quantization** and **context management** change a small local
tool-using agent's legitimate task completion, its resistance to prompt
injection, and its latency / token / memory cost? Phase 1 builds the harness,
freezes an audited paired clean/adversarial workload, and measures one fully
specified baseline. Capability and security are always reported separately.

Phase 1 report: [`reports/phase1-report.pdf`](reports/phase1-report.pdf)
(updated with the reviewed November reproduction; ACL two-column; source in [`reports/phase1-report.tex`](reports/phase1-report.tex)).

## Start here (for graders)

| Question | Where to look |
| --- | --- |
| What is the baseline and what did it measure? | [`results/phase1-recovery/evaluation-assessment.md`](results/phase1-recovery/evaluation-assessment.md) |
| The 600 episodes and 2,947 terminal request events | [`results/phase1-recovery/evaluation-episodes.csv`](results/phase1-recovery/evaluation-episodes.csv), [`evaluation-requests.csv`](results/phase1-recovery/evaluation-requests.csv) |
| Every metric with denominators, unknown counts and 95% CIs | [`results/phase1-recovery/evaluation-measurements.json`](results/phase1-recovery/evaluation-measurements.json) |
| Which 60 tasks, which attacks, and why | [`configs/phase1/task-manifest.json`](configs/phase1/task-manifest.json), [`docs/phase1-plan.md`](docs/phase1-plan.md) |
| How metrics are defined | [`docs/metrics.md`](docs/metrics.md), [`docs/experiment-protocol.md`](docs/experiment-protocol.md) |
| 168 agent-reviewed traces | [`results/phase1-recovery/reviewed-traces.csv`](results/phase1-recovery/reviewed-traces.csv), protocol in [`docs/trace-review-protocol.md`](docs/trace-review-protocol.md) |
| Historical task-feature and checker analyses | [`results/phase1/behavior-understanding/README.md`](results/phase1/behavior-understanding/README.md) |
| Exploratory follow-ups (prompt reminder, Q4 × context) | [`results/prompt-defense-first-wave-v1/README.md`](results/prompt-defense-first-wave-v1/README.md), [`results/phase2-first-pass-v1/analysis/README.md`](results/phase2-first-pass-v1/analysis/README.md) |
| Model provenance (BF16 → F16, hashes) | [`docs/model-provenance.md`](docs/model-provenance.md) |
| Recovered baseline and retry evidence | [`artifacts/phase1-recovery/evidence.tar.gz`](artifacts/phase1-recovery/evidence.tar.gz), [inventory](artifacts/phase1-recovery/manifest.json) |

The recovered baseline and retry traces are packaged with per-file checksums
under `artifacts/phase1-recovery/`. Original October summaries remain unchanged
in `results/phase1/`; their lost raw traces are not the November reproduction.
See [rebuild and evidence scope](results/phase1-recovery/README.md).

## What Phase 1 did

**Setup.** Qwen3-8B, converted from the verified BF16 checkpoint to F16 GGUF
(0 quantized tensors), served by pinned llama.cpp with Metal on an M2 Max
(64 GiB). One slot, 32,768-token context, F16 KV cache, no prompt caching,
non-thinking tool calling, temperature 0.7, seeds 17 and 29. AgentDojo v1.2.2 at
a pinned commit supplies simulated tools and deterministic checkers. No defense
prompt, detector or approval gate is added: this is an undefended baseline.

**Workload.** 60 legitimate tasks (Banking 8, Slack 16, Workspace 24,
Travel 12), selected by source inspection before any F16 result was seen.
Each task × seed has one clean episode and four attacked episodes: two
suite-specific attack goals × two fixed templates (`direct_append`,
`ignore_previous_append`). Our `_append` variant keeps the benign tool content
and appends the payload, so the legitimate task stays solvable. Total
60 × 2 × 5 = **600 episodes** in 24 runs. A source-level audit found and
excluded several upstream checker defects; an offline audit confirmed all 240
task/goal/template conditions remain legitimately completable with the payload
present and that completing them never satisfies the attack goal.

**Baseline results (600 episodes).** Clean utility 58/120 (48.3%). Attack
success 92/480 (19.2%) over all attacks, 55/232 (23.7%) on attacks whose
same-task same-seed clean control succeeded. `ignore_previous_append` 32.9% vs
`direct_append` 5.4% at identical exposure. 27 episodes ended in errors and are
kept as unknown scores. Median episode latency 53.7 s; 11.12 M prompt vs 0.14 M
completion tokens; prefill is 78.8% of returned native model time; peak server
RSS 20.0 GiB. Core utility, ASR and exposure rates match the original baseline.

**Failure analysis.** All 120 clean traces plus 48 attack traces were reviewed
under a protocol frozen before collection. Primary clean failure labels:
planning 43, tool use 15, task understanding 2, unnecessary refusal 2,
other/unclear 16, infrastructure 2, no failure 40. The two new timeouts explain
the planning-label change from 45 to 43. Refusal is indeterminate in 13 traces.
Zero malformed tool JSON, but 149/600 episodes had a tool execution error.
The 27 errors were rerun twice with identical seeds and budgets: 4/54 returned
normally across two clean cases, all with utility=False. The other 25 cases
errored twice; all 44 attacked retries retain unknown scores.

**Historical exploratory follow-ups (not rerun during recovery).**
- *Source-boundary prompt reminder*, 300 pairs (600 episodes across arms): exact-goal attack
  success 47/240 → 39/240 (paired 95% interval touches zero), clean utility
  26/60 → 27/60. Not evidence of a stable defense.
- *Quantization × context first pass*, 12 tasks, F16/Q4 × {full, truncate,
  long summary, short summary, short summary + search}, 360 episodes: no
  uniform Q4 degradation; truncation had the most budget-exhausted episodes (46/72 vs. 1/72 full history); lower
  observed ASR under compaction coincides with many unknown scores; retrieval
  rarely recovered the missing facts.

## What Phase 1 did not do

- No precision or context-policy **comparison claim**. The two-seed baseline
  is a single configuration; the explorations above are one seed on previously
  seen tasks and were used to exercise the Phase 2 harness.
- No new defense is claimed. The prompt reminder is a measured variant, not a
  recommendation.
- No independent human annotation or LLM judge. Trace labels are qualitative
  agent-assisted inspection; official checker scores are never modified.
- No held-out task split yet, no Q8 artifact yet, no fixed context-budget
  definition yet. These are the first Phase 2 milestones (see the report's
  appendix).

## Action traces

The recovered evidence archive includes 600 baseline episodes in 24 runs and
54 retry episodes in 32 runs. `make phase1-recovery-verify` validates the archive
checksum, every member checksum and both episode counts without extracting it.
The working raw files are also available at:

| Collection | Working raw root |
| --- | --- |
| Recovered baseline | `.cache/recovery/artifacts/runs/phase1-f16-s{17,29}-<suite>-<condition>/` |
| Supplemental retries | `.cache/recovery/artifacts/phase1-error-retries/runs/` |

Historical prompt-reminder, seed-29 diagnostic and quantization/context raw
traces remain unavailable. Their curated summaries are retained and labeled
historical in the report. A local archive is not an off-device backup.

Each run directory holds a `manifest.json` (frozen configuration, model digest,
runtime identity, dependency-lock hash, episode list) and one
`episodes/<episode_id>/` directory per episode:

| File | Contents |
| --- | --- |
| `result.json` | Official utility and attack scores, status/error, duration, tool-call count, token usage, sampled server RSS, exposure flag |
| `trace.json.gz` | The full message transcript (system, user, assistant, every tool call and tool result), the initial and final simulated environment, and the injected payloads |
| `requests.jsonl.gz` | Every HTTP request sent to the model exactly as sent (after any context policy) and every raw response, with timings |
| `compression.json` | SHA256 and byte length of each `.gz` file and of its original plain file |

Join a curated row to its trace with `run_id` + `episode_id`
(`results/phase1-recovery/evaluation-episodes.csv`, `results/phase1-recovery/reviewed-traces.csv`
and the other per-episode tables all carry both). The gzip files are plain
JSON/JSONL once decompressed:

```bash
gunzip -c .cache/recovery/artifacts/runs/phase1-f16-s17-banking-clean/episodes/user_task_1__clean/trace.json.gz | python -m json.tool | less
```

Review annotations in `results/phase1-recovery/reviewed-traces.csv` cite zero-based
`messages[i]` indices into `trace.json` and request indices into
`requests.jsonl`. Service logs, model-preparation logs, evaluator locks and
interrupted-attempt archives stay out of Git.

## Repository layout

| Location | Contents | Git |
| --- | --- | --- |
| `src/agent_tradeoffs/` | CLI, runtime management, agent loop, context/memory policies, prompt policies, analysis and reporting | tracked |
| `configs/` | Runtime TOMLs, frozen Phase 1 campaign, error-retry plan, prompt-defense plan, Phase 2 first pass | tracked |
| `scripts/` | Bootstrap, model preparation, audits, exporters, plotting, analysis entry points | tracked |
| `tests/` | Offline tests of scoring, artifacts, limits, exports and analysis (`make check`) | tracked |
| `docs/` | Plans and protocols frozen before each collection; metric definitions; provenance | tracked |
| `results/` | Curated tables, figures and assessments with run IDs and hashes | tracked |
| `reports/` | Course reports | tracked |
| `artifacts/` | Recovery evidence archive and inventory; service logs, locks and campaign scratch state remain ignored | mixed |
| `.cache/`, `.venv/` | Model weights, runtime binaries, Python environment | ignored |

Every experiment has its own run directory; a completed run ID is never
overwritten. Each `results/*` directory has a README describing its scope.

## Running it

Prerequisites: Apple Silicon macOS, Python 3.12, [uv](https://docs.astral.sh/uv/).
The first setup downloads the pinned llama.cpp archive and 16.4 GB of original
model weights, streamed through RAM into the F16 GGUF (reserve ≈17.3 GB disk).

```bash
make setup      # locked Python environment
make runtime    # pinned llama.cpp release
make model      # verified BF16 -> F16 conversion
make doctor     # environment checks
make serve      # start the local inference service (one terminal)
make probe      # verify native tool calls
make smoke      # one clean/adversarial development pair
make check      # ruff + offline tests
```

Frozen Phase 1 campaign, run against the warm service in a second terminal:

```bash
make phase1-audit     # offline: 60 tasks, checkers, 600 planned episodes
make phase1           # collect (resumable with make phase1-resume)
make phase1-status    # progress
make phase1-analyze   # cohort metrics, tables, review coverage
```

Regenerate the published Phase 1 export and figures from unchanged local
evidence (no inference):

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py --check-only
PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py
uv run --no-project --isolated --with matplotlib==3.11.2 python scripts/plot_phase1_evaluation.py
```

Other reproducible entry points: `scripts/audit_phase1_append_conditions.py`
(payload solvability audit), `scripts/analyze_phase1_error_retries.py`,
`results/phase1/behavior-understanding/analyze_task_features.py`,
`make phase2-prepare` / `make phase2` / `make phase2-analyze` (Phase 2 first
pass; see [`docs/phase2-first-pass.md`](docs/phase2-first-pass.md)), and the
prompt-defense driver described in
[`docs/prompt-defense-first-wave.md`](docs/prompt-defense-first-wave.md).

A few tests read git-ignored `artifacts/` and therefore only pass on the
collection machine; `make check` on a fresh clone reports them as failures.

## Design principles

- **Freeze before measuring.** Task manifests, attack templates, review
  protocols and analysis plans are committed before inference.
- **Keep errors.** Timeouts and tool-cap failures stay in the denominator as
  unknown outcomes; an unknown outcome is never counted as resistance.
- **Separate capability from security.** ASR is reported both over all attacks
  and over attacks the agent could otherwise complete, with exposure recorded.
- **Deterministic checks over model judgments.** Official AgentDojo checkers are
  preserved; disagreements with visible behavior are documented, not rescored.
- **Pin everything.** Model hashes, runtime commit, benchmark commit and
  dependency lock are recorded in each run manifest; a changed deployment cannot
  silently resume a campaign.

See [`docs/architecture.md`](docs/architecture.md) for module ownership and
extension points. Course milestones: Phase 1 on October 28, 2026; final
implementation and report on December 10, 2026.
