# Curated Phase 1 evaluation

This directory contains reviewed report artifacts for the unquantized F16,
full-context baseline. Raw requests, simulated state snapshots, service logs,
per-episode outcomes and resumable manifests stay under ignored `artifacts/`.

The frozen plan is `configs/phase1/campaign.json`: 60 distinct tasks, four suites,
two seeds, two attack templates and two suite-specific goals, totaling 600
episodes. Task selection is fixed before examining F16 outcomes.

Every selected result export must retain the campaign ID, configuration and
deployment identity, source run IDs, denominators, unknown/error counts, and
measurement coverage. Partial collection must be labeled partial. Trace review
annotations identify their reviewer and quote or locate the inspected evidence;
they do not replace the programmatic utility and attack checkers.

`f16-development-validation.json` preserves the five pre-evaluation F16 episodes
from four development runs: four successful clean examples and one scored
banking attack, with no runtime errors. It is excluded from the 600-episode
formal cohort. See [the development validation note](../../docs/f16-development-validation.md).

`collection-provenance.json` records the frozen formal campaign identity and
actual deployment/hardware at launch. Its progress field is a launch-time
snapshot, not a completion claim. Read the live campaign manifest or
`make phase1-status` for current progress.

`review-<suite>.csv` files contain incremental evidence-based annotations of
finalized formal traces. Coverage is partial until all required rows have been
reviewed under [the frozen protocol](../../docs/trace-review-protocol.md).
