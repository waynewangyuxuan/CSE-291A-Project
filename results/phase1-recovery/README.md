# Phase 1 recovered baseline

This is the reviewed November reproduction used by the final Phase 1 report.
The October baseline in `results/phase1` is preserved unchanged. The original
raw baseline was unavailable; these traces are new executions, not recovered
copies of the historical traces.

- [Assessment and historical comparison](evaluation-assessment.md)
- [Final measurements, intervals and provenance](evaluation-measurements.json)
- [600 episodes](evaluation-episodes.csv)
- [2,947 terminal request events](evaluation-requests.csv): 2,927 responses and
  20 errors. One additional request had no terminal event at the episode deadline.
- [168 trace annotations](reviewed-traces.csv) and [review gate](review-coverage.json)
- [54 supplemental retries](error-retries/summary.json)
- [Checksummed evidence archive](../../artifacts/phase1-recovery/evidence.tar.gz)

The native analyzer and complete exporter both passed. Review covers all 120
clean traces plus 48 attacks, including the final base30 and additional30
obligations and all 16 attack cells. Annotations are agent inspection with
historical labels as references, not independent human annotation.

## Rebuild and verify

From the repository root, with the existing evaluation environment:

```sh
uv venv .cache/report-env --python .venv/bin/python
uv pip install --python .cache/report-env/bin/python matplotlib==3.11.2
make phase1-report
make phase1-recovery-verify
```

The plotter requires the complete export gate and reads the recovered tables
directly. The original prompt-reminder and quantization/context figures use
historical summaries; those studies were not rerun. The report's PDF is built
with Tectonic; its first build may download LaTeX resources.

`make phase1-recovery-export` reruns the immutable exporter on this machine.
It updates only the recovery directory's offline analyzer to the repository
version; the seven execution-harness files and frozen campaign are unchanged.
The analyzer is not part of the execution-harness identity. Existing snapshots
reject changed inputs rather than overwriting evidence.

`make phase1-recovery-package` rebuilds the deterministic archive. Its external
manifest records every member's size and SHA256; `make phase1-recovery-verify`
checks all members and requires 600 baseline plus 54 retry results. Neither
command launches inference. The archive excludes model weights and runtime
binaries. It contains the pinned execution source, configuration, dependency
lock, raw results/traces/request logs, review annotations and export snapshot.

For recovery, extract into an **empty directory**, preserving member paths.
Original absolute provenance paths are intentionally retained; another host
must relocate those references before re-exporting. Archive verification and
rebuilding report figures from curated files do not need those absolute paths.

This is a local evidence archive, not an off-device backup. Historical exploratory
raw traces are still unavailable; the archive covers the reproduced baseline
and supplemental retries only.
