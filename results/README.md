# Results index

Start with the current report's baseline. Other directories are distinct
collections; matching task IDs do not make their outcomes interchangeable.
Raw requests and traces are described in the [evidence layout](../artifacts/README.md).

| Collection | Status and scope |
| --- | --- |
| [phase1-recovery](phase1-recovery/README.md) | **Current report baseline:** November reproduction, 600 episodes, 168 trace reviews, 54 supplemental retries |
| [phase1](phase1/README.md) | Historical October baseline, earlier checkpoints and behavior/checker analyses; kept for comparison |
| [phase1-error-retries](phase1-error-retries/README.md) | Historical 50-attempt diagnostic of the original 25 errors; not the November retry set |
| [prompt-defense-first-wave-v1](prompt-defense-first-wave-v1/README.md) | Historical paired default/reminder study: 300 pairs, 600 episodes |
| [prompt-defense-seed29-diagnostics-v1](prompt-defense-seed29-diagnostics-v1/README.md) | Historical 20-episode selected-case follow-up, not a full second seed |
| [research-investigation-v1](research-investigation-v1/README.md) | Historical task-contract, attack-stage and stance analyses derived from the prompt study |
| [phase2-first-pass-v1](phase2-first-pass-v1/README.md) | Historical single-seed, 12-task quantization/context pilot; not the final Phase 2 grid |

The original raw traces for historical collections are unavailable. The committed
archive covers newly reproduced Phase 1 baseline/retry evidence. Historical
curated results remain preserved and must be labeled as such when cited.

Each collection's README is the entry point for its tables and methodology.
Current measurements retain run IDs, denominators, unknown outcomes and source
hashes. Development smoke results belong in `artifacts/`, not in these published
benchmark tables. New experiments get new result directories; do not overwrite
a historical export to update the current report.

Some historical `support/` and `behavior-understanding/` directories contain
source snapshots used to derive their publications. They remain attached to
those results for provenance; reusable implementation belongs in `src/`, and
active operational scripts are listed in [scripts](../scripts/README.md).
