# Local artifacts

Generated output belongs here and is ignored by Git. Do not put source code or
hand-written configuration here.

- `runtime/`: inference service logs and process metadata.
- `campaigns/<campaign-id>/`: frozen campaign snapshot, progress, and generated analysis.
- `model-preparation/`: verified source download and conversion logs.
- `phase1/`: offline workload audits and environment snapshots.
- `runs/<run-id>/`: one fixed experiment identity and manifest, per-episode requests,
  traces and outcomes, and regenerated summary tables.

Phase 1 request/trace evidence is losslessly gzip-compressed after each finalized
episode. Each episode's `compression.json` preserves both compressed and original
SHA256 digests before the plain source is removed. Model outcomes remain plain
`result.json`, and compression never changes a programmatic score.

Model weights, downloads, runtime source/builds, and dependency caches belong in
`.cache/`, not in this directory. Keep reviewed, deliberately selected report
tables in `results/`; never commit the entire artifact tree.
