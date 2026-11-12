# Architecture and ownership

Read this alongside the [run guide](running.md). `Makefile` exposes common
workflows; the `agent-tradeoffs` CLI owns individual runs and campaigns.
Campaign-specific audit/export scripts are indexed in [scripts](../scripts/README.md).

## Follow one experiment

```text
runtime TOML ── runtime.py ── local llama-server
                                  ▲
experiment TOML ── config.py       │ native tool-call requests
                       │          │
campaign plan ── campaign.py ── runner.py ── agent.py
                                  │          │
                                  │       memory.py / prompt_policy.py
                                  │          │
                                  │       AgentDojo tools and checkers
                                  ▼
                     result + trace + request log
                                  │
                              analysis.py
                                  │
                     review → audit → final export
                                  │
                            results/ → reports/
```

A runtime config fixes the model artifact, checksum, server flags and hardware
settings. An experiment config fixes tasks, attack template, sampling, context
policy and execution limits. A campaign plan groups those configurations.
The run manifest records their identities together with actual runtime
properties, execution-source hashes and the dependency lock.

## Module map

All modules below live in `src/agent_tradeoffs/`.

| Module | Owns |
| --- | --- |
| `cli.py` | Command parsing and meaningful success/failure exit status |
| `config.py` | Typed experiment, prompt and context settings; identity inputs |
| `runtime.py`, `model_artifacts.py` | Model verification/preparation, server lifecycle and readiness |
| `campaign.py` | Frozen multi-run scheduling, campaign progress and compatible resume |
| `runner.py` | Episode scheduling, deadlines, memory sampling, checker scores and result persistence |
| `agent.py` | Model requests, native tool calls, tool execution and transcript/state recording |
| `context.py` | Full-context helpers and payload-exposure detection |
| `memory.py` | Truncation, summarization, history search and their cost/provenance logs |
| `prompt_policy.py` | The default system prompt and source-boundary reminder variant |
| `artifact_io.py` | Lossless request/trace compression and checksums |
| `reporting.py` | Atomic JSON writes and per-run summary tables |
| `analysis.py` | Offline campaign metrics, task-cluster intervals and trace-review coverage |
| `prompt_defense.py`, `prompt_defense_analysis.py` | Historical paired prompt-reminder collection and analysis |

Start with `config.py` and `runner.py` for execution, `agent.py` and `memory.py`
for model/tool behavior, or `analysis.py` for measurements. The prompt-reminder
modules and selected-case scripts retain historical collection assumptions;
they are not the default Phase 1 entry point.

## Keep the boundaries clear

- **Execution produces evidence.** The agent records actual requests, raw
  responses, tool actions and initial/final simulated state. It has no hidden
  SDK retries. The runner retains limits and errors as unknown outcomes.
- **Analysis reads evidence.** It does not rerun the model, change checker
  scores or infer a refusal annotation from a keyword match.
- **Review adds interpretation.** Reviewers annotate traces under the frozen
  protocol. Native readiness and the final export's complete review gate are
  distinct checks.
- **Publication preserves identity.** Exported tables cite source hashes;
  changing inputs requires a new snapshot. See the [results index](../results/README.md)
  and [raw-evidence layout](../artifacts/README.md).

The current harness includes context policies added after the original Phase 1
collection. The recovered report therefore uses a separate frozen execution
snapshot from the archive, plus a versioned offline analyzer. Running today's
harness on the same task list is a new experiment, not a byte-identical replay.

## Where a change belongs

| Change | Start here | Verify with |
| --- | --- | --- |
| Select tasks or vary sampling | `configs/` and `config.py` | workload audit, new run identity |
| Change tool execution or request construction | `agent.py` | harness and prompt regression tests, then a development smoke run |
| Change retained context | `memory.py` | memory tests and a real compaction/search check |
| Change limits, resume or evidence handling | `runner.py`, `campaign.py`, `artifact_io.py` | interruption, identity and artifact tests |
| Change a metric or denominator | `analysis.py` and its exporter | analysis/export tests and a new analysis snapshot |
| Change report presentation | `reports/` | rebuild from committed tables and inspect the PDF |

Keep new general behavior in the package and campaign-specific orchestration
in a named script. Update the [script index](../scripts/README.md) when adding
an entry point. `make check-docs` checks navigation and script coverage.

Frozen plan and evidence filenames are provenance keys, so do not rename them
for cosmetic consistency. New run IDs and folders should state the experiment,
variant and seed. Avoid `final2`, `new` or `latest` as experiment identities.
