# Same-seed error recovery assessment

The two additional attempts did not restore normal pipeline returns for any
of the 25 selected original errors. All 50 attempts finalized with error status:
38 `APITimeoutError` and 12 `ToolStepLimitError`. Their official utility and
attack success scores remain unknown. The original 600 episode evaluation,
annotations and denominators remain unchanged.

## Research question and controls

Can complete same-seed repetitions under unchanged request, episode and tool
budgets recover the original runtime errors, and what additional cost do they
incur? This is the measurement validity question of error recoverability.
The variant is two additional full episodes per original error, each starting
from a fresh benchmark environment; both rounds ran regardless of outcomes.

The [frozen plan](../../configs/phase1-error-retries/plan.json) selects exactly
the original 25 error rows: 3 clean and 22 attack episodes across 6 distinct
suite and task pairs. Fifteen configurations in two rounds produced 30 new run
IDs and 50 episodes without introducing extra task and goal combinations.
Original seeds 17/29 and initial request payloads were preserved. Every run
required the original parent campaign's runtime semantic identity.

Controls retain Qwen3 8B F16 weights, F16 KV, 32768 context, nonthinking mode,
full history, `cache_prompt=false`, temperature 0.7, top-p 0.8, top-k 20,
min-p 0, 2048 output tokens, 15 executed tools, 120 seconds per request and
600 seconds per episode. The evaluator, runtime implementation and dependency
lock were unchanged. See the [protocol](../../docs/phase1-error-retries-protocol.md).

## Outcome evidence

| Cohort | Episodes | Normal returns | API timeout | Tool limit | Episode timeout |
|---|---:|---:|---:|---:|---:|
| Original selected errors | 25 | 0 | 18 | 6 | 1 |
| Additional round 1 | 25 | 0 | 19 | 6 | 0 |
| Additional round 2 | 25 | 0 | 19 | 6 | 0 |
| Additional attempts combined | 50 | 0 | 38 | 12 | 0 |

All 25 cases returned normally in neither attempt; none recovered in one or
both. The original episode timeout case became an API timeout in each repeat,
still close to 600 seconds. Of the 38 failed requests, 21 had request timeouts
shortened by the remaining episode budget and 17 retained the full 120 seconds.
These observations do not identify a causal root cause.

All 6 clean utility scores and all 44 attack utility and attack success scores
are unknown. There are no known-score denominators; their unconstrained outcome
bounds remain [0, 1]. A zero count of observed successes must not be read as
zero utility or zero ASR. Normal return status is also separate from legitimate
goal completion. Official benchmark utility U is retained; independent positive
goal completion L and broader unauthorized effects H were not newly evaluated.

The frozen exposure check confirms attack text in 44/44 attack attempts. Their
original same-task, same-seed clean controls remain 4 true, 18 false and 22
unknown when counted across the two rounds. Supplemental clean attempts do not
backfill those controls or modify the original attack eligibility.

## Additional costs

| Cohort | Episode seconds, summed | Returned tokens | Requests / responses / request errors | Executed tools |
|---|---:|---:|---:|---:|
| Original selected 25 | 9,275.83 | 2,233,638 | 269 / 250 / 19 | 251 |
| Additional round 1 | 9,215.51 | 2,270,451 | 271 / 252 / 19 | 253 |
| Additional round 2 | 9,356.99 | 2,391,808 | 277 / 258 / 19 | 259 |
| Additional 50 combined | 18,572.50 | 4,662,259 | 548 / 510 / 38 | 512 |

The supplement consumed 5.16 hours of summed episode time, including every
failed attempt. Median episode time was 363.81 seconds and p95 was 600.02
seconds. Returned usage was 4,650,010 prompt plus 12,249 completion tokens;
all 510 returned responses supplied usage and native timings. The 512 executed
tool calls include 70 tool error events. Server process RSS was observed in
all 50 episodes, with peak 21,258,321,920 bytes (19.80 GiB).

Pooled returned-response measurements give 309.66 native prefill tokens/second,
15.89 native decode steps/second and 0.774 completion tokens per response second.
These use matching returned-response masks; failed request token usage and
backend timing remain unknown. Native decode steps exclude the initial token,
so they differ from completion tokens. RSS describes the server process, not
whole-machine or total GPU memory. Full distributions and clean/attack splits
are in [summary.json](summary.json); completed and utility-success cost subsets
are empty.

These episode costs exclude startup, the tool probe, orchestration and audits.
Successful service startup took 51.46 seconds. An earlier sandbox startup
failure occurred before any episode and was retained separately. Model and OS
caches were not cold-boot controlled; costs on the shared host are descriptive.

## Validation and remaining gaps

The [primary validation](validation.json) and separate
[direct raw audit](independent-audit.json) actually passed after all 50 results
finalized. They reconcile exact case mapping, all 30 run identities, 100 gzip
files and decoded digests, initial inputs, request ordering, original clean
controls and the published summaries and 50 CSV rows. The independent audit
also verified 422 scoped source hashes before and after its checks. A separate
final guard matched all 2631 protected original files to their frozen bytes.

The driver exited with code 0. Before releasing inference ownership, a fresh
snapshot matched the original runtime identity and confirmed all slots idle
with zero processing or deferred requests. The service was retained for the
separate defense experiment. This records the handoff instant, not later state.
The helper tests passed (15 tests), and the added Python files passed Ruff.

These 50 attempts are correlated same-seed repeats of outcome-selected cases,
not independent new-seed trials or 50 new tasks. No binomial confidence interval
is asserted. The observed lack of recovery applies to this protocol; it does
not establish an error rate on fresh tasks, isolate a budget or precision effect,
or show whether partial actions before errors fulfilled or harmed a user goal.
Different budgets, independent L scoring and causal error diagnosis remain
separate work. Evidence consistency checks are not independent human scoring.

To regenerate the primary export from retained local raw evidence:

```sh
PYTHONHASHSEED=0 .venv/bin/python scripts/analyze_phase1_error_retries.py
```

The published audit binds the current export hashes. A regenerated export has
a new capture timestamp and requires fresh audit evidence before publication.
