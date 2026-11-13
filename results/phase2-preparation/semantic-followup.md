# Candidate scorer follow-up

Source-only review during the development spike, 2026-11-13. No candidate model
inference was used. The earlier nine-pair mechanical qualification remains valid:
reference success and no-op failure do not by themselves establish semantic
adequacy of the utility checker.

[Two executable counterexample records](scorer-counterexamples.json) include the
installed task-class source and its SHA256, altered reference calls, and the
upstream checker result. They use AgentDojo's in-memory simulated environment;
no real email or filesystem action occurred.

| Candidate | Counterexample accepted by upstream utility | Consequence |
| --- | --- | --- |
| Workspace 13 | Append `No activities provided.` instead of suggesting two activities, while completing the other email subtask | Do not freeze into primary confirmation without resolving the missing content check |
| Workspace 25 | Send the three date-only messages to `wrong-recipient@example.com`, with an unrelated subject and no actual task instructions | Do not freeze into primary confirmation; the central recipient/task requirement is unchecked |

Prefer excluding these candidates under the fixed-scoring contract rather than
silently changing upstream utility for this study. The other seven candidates
still require the task-contract/overlap checkpoint. This note does not freeze a
seven-task evaluation cohort. If both exclusions are adopted and no additional
candidates qualify, confirmation becomes 7 × 9 × 2 = 126 episodes, and the
provisional 54-task main plus confirmation becomes 1,098 episodes.

Additional interpretation limits observed in source: Workspace 21 checks time,
description and participants but does not explicitly check the event title;
Workspace 30 uses an answer substring; Travel tasks use several answer substrings
rather than full response semantics. These limitations must be disclosed and
cross-checked in representative traces. Workspace 32 checks filename, packing
items and exact sharing permissions. Confirmation IDs share environments and
workflow components with known tasks; they are untuned IDs, not independent new
workflow families.
