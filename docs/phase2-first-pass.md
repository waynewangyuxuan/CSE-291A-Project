# Quantization × context: exploratory first pass

This single-seed mechanism pilot compares same-source F16/Q4_K_M agents using the
original AgentDojo system prompt. It adds no agent defense prefix. It deliberately
compresses early: existing Banking/Slack histories seldom approach 8K tokens.
Results therefore describe an early-memory intervention, not a natural long-context
budget threshold, a new defense, or a held-out confirmatory study.

## Frozen questions and scope

- How does weight quantization change official task completion U and attack goal A?
- What does discarding history, summarizing it, or retrieving it preserve or lose?
- Does precision change the agent's ability to use those memory systems?

`configs/phase2/first-pass.json` freezes 12 tasks × 2 precisions × 5 memory policies
× (clean + two goals under ignore_previous_append), 360 episodes, seed 17 only.
Banking: 1, 3, 12, 15. Slack: 2, 4, 5, 9, 10, 13, 14, 16. Selection covers reference
lookup/action paths, file-based instructions and multi-channel/member enumeration;
it deliberately includes long reference paths. Prior outcomes are known, but no
success/failure filter was applied. No first-pass result will change this list.
These are previously studied tasks, not a new held-out split. Banking 12 has broad
file-following authorization: official A alone does not resolve that ambiguity.

Only the existing ignore-previous attack wording and each suite's original two
attack goals are used. Full-history results are rerun under this harness and
runtime; historical Phase 1 runs are context, not substituted paired controls.

## Memory contract

The original system/user messages and tool schemas are retained. The raw scoring
transcript remains append-only. Only the request view changes. A round is one
assistant tool-call response plus ALL its corresponding tool results, including
parallel calls. Before the next model request after rounds 2, 4, 6, ...:

- Full: keep everything.
- Truncate: retain the newest complete round, archive/remove earlier rounds.
- Long summary: retain the newest round and a source-labeled summary of the entire
  removed ORIGINAL prefix (not recursive summaries). Output cap is min(512 tokens,
  50% of source tokens, space available after the memory envelope).
- Short summary: same, cap min(128 tokens, 15% of source tokens, envelope space).
- Short summary + search: identical short-summary rule plus `search_history(query)`.

Sizes use the pinned model tokenizer on serialized message content, not an
estimate of the full rendered chat template. Actual request usage is separately
recorded. Prefixes too small for a 16-token summary after envelope overhead are
explicitly deferred; summaries that would expand the prefix are also deferred.
There is no silent fallback. All triggered, applied and deferred events remain
in trace.context_memory. Applied cases are a descriptive, post-treatment subset;
the full frozen workload is the primary denominator.

A summary is delivered in a synthetic assistant/tool exchange named
`context_memory` in the request view, with source message IDs. It is not a
system/user message or a real executed benchmark tool call. The synthetic exchange
never enters the official scoring transcript or tool-step count. The summarizer
is instructed to preserve attribution and not execute the quoted transcript;
that instruction is fixed across all summary arms. No attack text is prefiltered.
Token-capped summaries can end at length; the raw completion and finish reason
are retained. Malformed/empty summaries produce unknown episode results.

## Search contract

The RAG arm exposes one additional tool from the first request. This is a memory
system comparison that also changes the tool schema; it is not a pure compression
algorithm comparison. Short-summary without search is its ablation.

Search is deterministic capped term overlap, normalized by chunk length, over
JSON representations of ORIGINAL archived messages in the current episode.
Chunks are 1,024 characters with 128-character overlap. Ties use original message
ID and offset. Query length is 1–256 characters; up to three matches, 512 total
returned tokens and four calls per episode. Responses include message IDs, roles,
offsets and exact excerpts, which may themselves contain injection content.
There is no embedding model, access to another episode, or access to future tools.
Empty archives/misses yield empty matches. Invalid/budget-exhausted searches yield
visible tool errors. Search calls count against the same 15 executed tool-call
limit as benchmark tools; search time and output tokens are reported separately.
No hidden expansion of action budget favors RAG.

## Deployment and budgets

Both weight variants derive from canonical Qwen/Qwen3-8B revision
b968826d9c46dd6066d109eabc6255188de91218. Q4_K_M is produced by pinned llama.cpp
b11678's quantizer from the exact Phase 1 F16 file, not the downloaded Q4 pilot.
The preparation receipt records source/output hashes, quantizer hash and GGUF
tensor metadata. KV remains F16 and runtime allocation remains 32,768 tokens.

Agent: temperature .7, top_p .8, top_k 20, min_p 0, non-thinking, seed 17,
2,048 completion tokens, 120s/request, 600s/episode, 15 executed tool calls.
Summarizer: fixed F16, greedy, seed 17, non-thinking, same pinned tokenizer/runtime.
It runs on isolated loopback port 8082; the agent runs on 8083. The summarizer
stays loaded in ALL arms, including full/truncate, to keep the co-resident service
condition fixed. Agent requests and summary requests execute serially.

The pilot runs F16 then Q4, each in the same policy/suite order. This minimizes
model reloads but leaves run-order/thermal confounding; it cannot establish that
all observed differences were caused by quantization. No stochastic repeats are
added. Formal cumulative run budget is 12 hours, with a full episode's budget
reserved before starting another. Preparation/verification are separate.
Errors remain unknown and finalized episodes are not retried for a better score.
Interrupted attempts are retained by the harness. Never merge development checks
into formal metrics.

Episode wall time includes tokenization, summaries and search. Summary HTTP
payloads/responses live in memory-requests.jsonl; agent requests remain separate.
Report agent and summary tokens separately and together. Whole-deployment memory
is sampled across evaluator + agent + summarizer during each run and labeled SUM
OF PROCESS RSS, not physical unified memory (shared pages can be counted twice).
Per-server RSS, startup time and model residency are also retained. This first
pass does not measure energy or solve optimal model offloading.

## Reading results

`make phase2-report` exports the completed subset without changing measurements:
U and A with explicit denominator/unknown counts, A on each configuration's own
clean-success tasks, compact/search coverage and per-episode costs. Pair on suite,
task, condition and goal, never on row position. Show U×A, source-removal/summary/
retrieval evidence and first observable trajectory divergences. Exact payload
matching is an exposure indicator, not a semantic detector or proof of refusal.

Small task groups and one seed support exploratory patterns only. A lower A with
lower U may be lost capability. A lower A after content removal is not evidence
of resisting visible instructions. The 30% capability gate remains descriptive:
report clean U with unknown bounds and task counts, not a hidden task filter.

## Reproduction

```
make phase2-model
make phase2-prepare
make phase2-verify-up
make phase2-verify
make phase2-verify-down
make check
make phase2
make phase2-report
```

Verification uses real services, historical real tool transcripts and a separate
read-only integration prompt explicitly requesting history search. It proves
wiring, not spontaneous RAG use. Formal runs preserve their original user tasks.
Resume uses the same `make phase2` entry; plan, code and deployment identities
must match. The runner owns an OS campaign lock and only stops its recorded
processes. It stops its services on normal completion, interruption or budget end.
