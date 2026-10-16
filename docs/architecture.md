# Architecture and ownership

The command line is the only operational entry point. Experiment behavior lives
in importable Python modules; the shell script only provisions the pinned native
runtime. There are no notebook-only workflows or copies of experiment logic in
one-off scripts.

```text
configs/runtime-*.toml ── runtime.py ── llama-server (loopback, one slot)
                                           ▲
configs/{smoke,baseline}.toml                │ OpenAI-compatible native tools
              │                            │
              ▼                            │
        runner.py ──────────────────── agent.py
              │                            │
              │                   AgentDojo simulated tools
              │                   and state-based checkers
              ▼
 artifacts/runs/<run-id>/ ────────── reporting.py
```

Runtime configuration controls the model artifact, immutable revision and
checksum, server flags and hardware deployment. Experiment configuration
controls the task selection, attack, request settings and episode limits. These
are separate files because restarting a service and changing a workload have
different lifecycles. The run manifest connects them using the verified GGUF
digest, actual server properties and command, source identity and dependency
lock digest. A service process change interrupts collection; explicit resume
revalidates compatibility before continuing.

`agent.py` owns the actual model requests and simulated tool loop. It records the
sent context and tool schemas, the raw completion and usage, and every tool
action. It has no hidden SDK retries. `runner.py` owns scheduling, per-episode
directories, resume identity, error retention and programmatic outcomes.
`reporting.py` derives tables from episode records, keeping failed or unknown
security outcomes visible.

## Adding an experiment

1. Copy a reviewed TOML config into `configs/` and give it a descriptive name.
2. Keep the task list and deployment settings fixed while varying one factor.
3. Run a new ID; never overwrite a previous run to make a comparison.
4. Inspect raw requests and traces before exporting a selected summary to `results/`.

Phase 2 context policies should enter through `context.py`, preserving user and
system instructions, tool-call consistency, and a log of retained/removed
content. Summarization needs an explicit model, cost and provenance policy so
it does not introduce an uncontrolled extra model. Until implemented, selecting
an unsupported policy fails validation.

## Reproduction

`uv.lock` fixes Python packages and the AgentDojo source commit. The runtime
bootstrap fixes an official llama.cpp release archive and its SHA256. Runtime
TOML fixes the Hugging Face commit and GGUF SHA256. Neither `main` nor `latest`
is resolved during a normal reproduction run. Dependency/runtime upgrades
should be explicit code changes followed by a fresh smoke run.

VibeHub is local and optional for ordinary development. It does not control the
Python environment, run scheduler or output format.
