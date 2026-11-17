# Security–Performance Tradeoffs in Resource-Constrained Agents

CSE 291A, Fall 2026 · Wayne Wang

How do weight quantization and context management change a local tool-using
agent's task completion, prompt-injection resistance, and inference cost?
This repository contains the experiment harness, frozen workloads, reviewed
results, and report.

**Start with the [Phase 2 report](reports/phase2-report.pdf)** and its
[reviewed results](results/phase2-main-v1/README.md).

The [Phase 1 report](reports/phase1-report.pdf) supplies the baseline. Its current
baseline is the November reproduction: 600 episodes, 168 reviewed traces,
and 54 supplemental error retries. Clean utility is 58/120; exact-goal attack
success is 92/480, or 55/232 when the paired clean control succeeds.
Errors retain unknown scores. The [assessment](results/phase1-recovery/evaluation-assessment.md)
explains the denominators and comparison with the original October baseline.

**Phase 2:** all **1,098 formal episodes and trace reviews are complete**:
54 main tasks (972 episodes) and seven separate confirmation tasks (126 episodes),
using F16/Q8/Q4 × Full/Truncate/Summary at a fixed 7,040-token total budget.
The [reviewed results](results/phase2-main-v1/README.md) include final figures,
all 21 complete gates and offline recovery proof. The [Phase 2 report](reports/phase2-report.pdf)
is complete, including the separate [24-episode selected-case seed diagnostic](results/phase2-seed-diagnostics-v1/README.md).
The main matrix finds small aggregate precision differences, substantial context
and planning failures, and no demonstrated general compression defense. The only
officially successful confirmation task is a reviewed scorer false positive;
reviewed clean-conditional security is therefore undefined in that cohort.

## Run a first check

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required. Run commands from
the repository root:

```sh
make                         # list available commands; does not install or run anything
make setup                   # install the locked development dependencies
make check                   # lint, offline tests, and navigation checks
make phase1-recovery-verify   # verify the committed evidence archive
```

These commands need no model or GPU. For report generation, local model setup,
and campaign collection, follow [Running the project](docs/running.md).
Local inference uses Apple Silicon, llama.cpp with Metal, and Qwen3-8B F16.

## Find what you need

| You want to… | Start here |
| --- | --- |
| Read the reports | [Phase 1 PDF](reports/phase1-report.pdf), [Phase 2 PDF](reports/phase2-report.pdf), [Phase 2 source](reports/phase2-report.tex) |
| Inspect results, reviews, and follow-ups | [Phase 1 baseline](results/phase1-recovery/README.md), [Phase 2 formal study](results/phase2-main-v1/README.md) |
| Revisit Phase 2 findings and follow-up questions | [Post-study research memo (中文)](docs/phase2-post-study-review.md) |
| Understand what is current versus historical | [Results index](results/README.md) |
| Find raw model requests, tool actions, and state changes | [Evidence layout](artifacts/README.md) |
| Run checks, rebuild figures, or start inference | [Run guide](docs/running.md), `make help` |
| Understand or change the implementation | [Architecture and module ownership](docs/architecture.md) |
| Find a campaign-specific script | [Script index](scripts/README.md) |
| Understand the workload and evaluation | [Phase 1 plan](docs/phase1-plan.md), [metrics](docs/metrics.md), [trace-review protocol](docs/trace-review-protocol.md) |

## Repository map

| Directory | Responsibility |
| --- | --- |
| `src/agent_tradeoffs/` | Reusable agent, runtime, campaign, context-policy, and analysis code |
| `configs/` | Pinned deployment settings and frozen experiment plans |
| `scripts/` | Named setup, audit, export, and experiment entry points |
| `tests/` | Offline behavior checks using temporary fixtures and committed reference data |
| `docs/` | Run guide, architecture, protocols, and historical validation records |
| `results/` | Reviewed measurements, annotations, plots, and interpretations |
| `reports/` | Report source, generated PDF, and compact report figures |
| `artifacts/` | Committed recovery archive plus locally generated raw experiment evidence |
| `.cache/`, `.venv/` | Ignored models, runtime builds, restored working evidence, and environments |

## Experiment boundaries

The Phase 1 baseline uses 60 fixed AgentDojo tasks, two seeds, and four attacks
per clean control. Task completion and security are separate measurements;
unknown scores never establish resistance. Trace review is qualitative agent
inspection, not independent human annotation.

Original baseline tables and exploratory prompt-reminder/context studies remain
historical collections. Their original raw traces were lost; the November
archive contains newly reproduced baseline and retry evidence. See the
[results index](results/README.md) before combining collections.

Changing model settings, prompts, context policies, or execution code creates a
new experiment identity. Keep old results intact and use a new run ID. The
[architecture guide](docs/architecture.md) identifies the relevant modules and
the [run guide](docs/running.md) distinguishes a new run from exact reproduction.
