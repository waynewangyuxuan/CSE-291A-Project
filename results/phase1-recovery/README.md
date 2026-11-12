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

## Rebuild, inspect and verify

Follow the repository's [run guide](../../docs/running.md) for offline checks,
report generation and collection-machine re-export. The [evidence guide](../../artifacts/README.md)
explains the archive layout, checksums and restoration limits.

The final export is immutable and rejects changed inputs. The report plotter
requires the completed audit/review gate and reads these tables directly.
Historical exploratory figures are explicitly labeled; those studies were not
rerun during recovery. The local archive covers baseline and retry evidence,
not historical exploratory traces or model weights.
