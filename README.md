# Security–Performance Tradeoffs in Resource-Constrained Agents

CSE 291A, Fall 2026. Controlled measurements of small local tool-using agents:
weight quantization × context management, with capability and security reported separately.

The formal Phase 1 baseline uses AgentDojo and **unquantized Qwen3-8B F16**,
served by pinned llama.cpp with Metal, full 32K context and non-thinking tool use.
The canonical BF16 checkpoint is converted to F16; normalization weights remain
F32. The earlier Q4 pilot is development evidence only. Tools operate on the
benchmark's simulated resources. No paid inference API or cloud account is required.

## Quick start

Prerequisites: Apple Silicon macOS, Python 3.12, and [uv](https://docs.astral.sh/uv/).
Run commands from the repository root. The first setup downloads dependencies,
an official runtime archive, and **16.4 GB of original model weights** streamed
through RAM into the F16 GGUF. Reserve approximately 17.3 GB free disk space
plus dependency installation space; a second full model copy is not stored.
See [model provenance](docs/model-provenance.md) for exact source hashes and conversion.

```bash
make setup
make runtime
make model
make doctor
```

Start the server in one terminal:

```bash
make serve
```

In another terminal, verify native tool calls and run one clean/adversarial pair:

```bash
make probe
make smoke
```

For a background service, use
`uv run --frozen agent-tradeoffs serve --background`. Stop it with `make stop`.
The default endpoint is `http://127.0.0.1:8080/v1`.

```bash
make pilot                                      # three F16 development task pairs
make tasks                                      # inspect the pinned banking tasks
uv run --frozen agent-tradeoffs run --config configs/phase1/dev-pilot.toml --run-id pilot-01
uv run --frozen agent-tradeoffs run --config configs/phase1/dev-pilot.toml --run-id pilot-01 --resume
uv run --frozen agent-tradeoffs summarize artifacts/runs/pilot-01
make check
```

An endpoint lock prevents concurrent evaluators in this project from sharing the
same local service. A single server slot and serial episodes make per-episode
timing and memory easier to interpret.

## Frozen Phase 1 campaign

```bash
make phase1-audit       # offline checks: 60 distinct tasks, source/checkers and 600 episodes
make phase1            # run the frozen campaign against the ready F16 service
make phase1-status     # inspect authoritative progress in another terminal
make phase1-resume     # resume an interrupted campaign with the same code/config/deployment
make phase1-analyze    # regenerate initial metrics, tables, review queue and latency plot
```

The campaign contains **60 distinct tasks across four suites**, two attack styles,
two suite-specific goals and two stochastic seeds: 120 clean and 480 adversarial
episodes in 24 runs. Tasks are frozen before evaluating model behavior; errors
and timeouts remain in the data. Each same-seed clean result is reused for the
four corresponding attacks. See the [frozen plan](docs/phase1-plan.md) and
[metric definitions](docs/metrics.md).

Analysis reports coverage while collection is in progress. The final preliminary
evaluation additionally requires inspection of the deterministic 30-trace sample:

```bash
uv run --frozen agent-tradeoffs analyze artifacts/campaigns/phase1-f16-v1 \
  --annotations results/phase1/reviewed-traces.csv --require-review
```

Keep the inference service unchanged during the campaign. Compatible restarts
can resume; a different checkpoint, precision, context budget or runtime flags
cannot silently join the same campaign.

## Where things belong

| Location | Contents | Git |
| --- | --- | --- |
| `src/agent_tradeoffs/` | Reusable CLI, runtime, agent, evaluation and reporting code | tracked |
| `configs/` | Reviewed TOML workload and deployment configurations | tracked |
| `tests/` | Offline tests of scoring, artifacts, limits and service lifecycle | tracked |
| `scripts/` | Small environment bootstrap entry points | tracked |
| `docs/` | Experiment protocol and architecture | tracked |
| `results/` | Deliberately selected report tables and figures | tracked |
| `artifacts/runs/<run-id>/` | Raw manifests, requests, traces, outcomes and summaries | ignored |
| `artifacts/runtime/` | Local service logs and process metadata | ignored |
| `artifacts/campaigns/<campaign-id>/` | Frozen campaign snapshot, progress and generated analysis | ignored |
| `.cache/` | Model weights, runtime binaries, downloads and dependency caches | ignored |
| `.venv/` | Locked project Python environment | ignored |
| `.vibehub/` | Local VibeHub metadata and instructions | ignored |

Each experiment has its own run directory; rerunning a completed ID requires
explicit resume and the same experiment identity. Never scatter ad hoc scripts,
weight files or result CSVs in the root directory.

## Experiment boundaries

The smoke/pilot workloads are development checks, separate from the frozen
Phase 1 evaluation workload. The attack appends malicious text to the original
retrieved content so the legitimate task remains possible. Finalized request and
trace files are compressed losslessly as `.jsonl.gz` / `.json.gz`, with verified
original digests; results and summaries stay plain JSON/CSV.

The implementation currently supports full context. Truncation, summarization,
and multiple precision configurations are subsequent Phase 2 work. The two-seed,
fixed-subset F16 campaign is the Phase 1 baseline.

See [the experiment protocol](docs/experiment-protocol.md) for outcome definitions
and [architecture](docs/architecture.md) for extension points and artifact ownership.
[F16 development validation](docs/f16-development-validation.md) records the
pre-evaluation checks. [Historical local validation](docs/local-validation.md)
preserves the earlier Q4 development runs and their limitations.

Course milestones from the provided assignment: Phase 1 on October 28, 2026;
final implementation and report on December 10, 2026.
