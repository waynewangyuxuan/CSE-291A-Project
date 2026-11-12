# Raw evidence and local artifacts

## Committed recovery evidence

[phase1-recovery/evidence.tar.gz](phase1-recovery/evidence.tar.gz) contains the
November reproduction: **600 baseline episodes and 54 supplemental retries**,
review annotations, frozen configuration/execution source and the final export.
Its [manifest](phase1-recovery/manifest.json) records every member's byte length
and SHA256. Model weights and runtime binaries are excluded.

```sh
make phase1-recovery-verify
```

Verification reads the archive without extracting it or running inference.
To inspect a trace directly:

```sh
tar -xOf artifacts/phase1-recovery/evidence.tar.gz \
  .cache/recovery/artifacts/runs/phase1-f16-s17-banking-clean/episodes/user_task_1__clean/trace.json.gz \
  | gzip -dc | python3 -m json.tool
```

For a full restore, extract into an empty directory. Member paths are relative
to the repository root; original absolute provenance paths inside files remain
unchanged. Re-export on another host needs deliberate relocation. The archive
captures source/docs as delivered; current navigation docs can evolve separately.
The [run guide](../docs/running.md) covers report rebuilding and exact re-export.

## Per-episode layout

| File | Contents |
| --- | --- |
| `result.json` | Official scores, error/status, duration, tool count, usage and observed exposure |
| `trace.json.gz` | System/user/assistant messages, tool calls/results, injected content and initial/final simulated state |
| `requests.jsonl.gz` | Actual model request payloads and response/error events with timings |
| `compression.json` | Checksums and sizes of both compressed and original bytes |

A run's `manifest.json` binds its configuration, runtime, execution code and
planned episodes. Join exported rows to raw evidence with `run_id` and
`episode_id`. Review evidence cites zero-based message and request indices.
Unknown results remain unknown; compaction never changes a programmatic score.

## Generated working directories

| Path | Purpose |
| --- | --- |
| `runs/<run-id>/` | New runs from the current harness |
| `campaigns/<campaign-id>/` | Campaign plan snapshot, progress and generated analysis |
| `runtime/` | Service logs and process metadata |
| `model-preparation/` | Download/conversion verification logs |
| `phase1/` | Workload audits and historical export snapshots |
| `recovery/` | Local recovery analysis, final export snapshot and PDF QA |
| `.cache/recovery/artifacts/` (from repository root) | Restored working baseline and retry files |

The archive and inventory are tracked. `.gitignore` permits deliberately selected
raw-run directories, while service logs, locks and scratch analysis remain
ignored. Do not commit the entire artifact tree. Put reviewed tables in
`results/`, source in `src/` or `scripts/`, and weights/builds in `.cache/`.
Historical exploratory raw traces were not recreated by the baseline recovery;
see the [results index](../results/README.md) for collection scope.
