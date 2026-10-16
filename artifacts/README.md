# Local artifacts

Generated output belongs here and is ignored by Git. Do not put source code or
hand-written configuration here.

- `runtime/`: inference service logs and process metadata.
- `runs/<run-id>/`: one fixed experiment identity and manifest, per-episode requests,
  traces and outcomes, and regenerated summary tables.

Model weights, downloads, runtime source/builds, and dependency caches belong in
`.cache/`, not in this directory. Keep reviewed, deliberately selected report
tables in `results/`; never commit the entire artifact tree.
