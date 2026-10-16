# Security–Performance Tradeoffs in Resource-Constrained Agents

CSE 291A, Fall 2026. Controlled measurements of small local tool-using agents:
weight quantization × context management, with capability and security reported separately.

The Phase 1 baseline uses AgentDojo, Qwen3-8B Q4_K_M, and llama.cpp with Metal
on Apple Silicon. Tools act on AgentDojo's simulated bank account and files.
No paid inference API or cloud account is required.

## Quick start

Prerequisites: Apple Silicon macOS, Python 3.12, and [uv](https://docs.astral.sh/uv/).
Run commands from the repository root. The first setup downloads dependencies,
an official runtime archive, and a **5.03 GB** model; later inference is local.

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
make pilot                                      # three development task pairs
make tasks                                      # inspect the pinned banking tasks
uv run --frozen agent-tradeoffs run --config configs/baseline.toml --run-id pilot-01
uv run --frozen agent-tradeoffs run --config configs/baseline.toml --run-id pilot-01 --resume
uv run --frozen agent-tradeoffs summarize artifacts/runs/pilot-01
make check
```

Do not run experiments concurrently against the same service. A single server
slot and serial episodes make per-episode timing and memory easier to interpret.

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
| `.cache/` | Model weights, runtime binaries, downloads and dependency caches | ignored |
| `.venv/` | Locked project Python environment | ignored |
| `.vibehub/` | Local VibeHub metadata and instructions | ignored |

Each experiment has its own run directory; rerunning a completed ID requires
explicit resume and the same experiment identity. Never scatter ad hoc scripts,
weight files or result CSVs in the root directory.

## Experiment boundaries

The included workloads are **development smoke/pilot workloads**, not a held-out
evaluation set. They establish a working measurement pipeline and do not justify
security or model-selection conclusions. The attack appends malicious text to
the original retrieved content so the legitimate task remains possible.

The implementation currently supports full context. Truncation, summarization,
multiple precision configurations, repeated seeds and a held-out evaluation
manifest are subsequent Phase 2 work, not silently enabled experiment settings.

See [the experiment protocol](docs/experiment-protocol.md) for outcome definitions
and [architecture](docs/architecture.md) for extension points and artifact ownership.
[Local validation](docs/local-validation.md) records the real development runs
and their limitations.

Course milestones from the provided assignment: Phase 1 on October 28, 2026;
final implementation and report on December 10, 2026.
