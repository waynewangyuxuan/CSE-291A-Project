# Running the project

Run commands from the repository root. `make` or `make help` lists the supported
entry points. Ordinary development, report generation, and live inference have
separate requirements.

Phase 2 is complete: [1,098 formal episodes](../results/phase2-main-v1/README.md)
and [24 selected diagnostic episodes](../results/phase2-seed-diagnostics-v1/README.md),
all trace-reviewed. Use the offline report and recovery commands below to inspect
them. The [batch plan](phase2-batches.md) records the frozen collection design and
completion. `make phase2-spike-status` refers only to the 18-episode development
spike; `phase2-prepare` and `phase2` remain historical pilot commands.

## Development checks: no model required

Install Python 3.12 and uv, then:

```sh
make setup
make check
make phase1-recovery-verify
```

`setup` installs `uv.lock` into `.venv`. `check` runs Ruff, the complete offline
pytest suite, and navigation checks. Tests create their own temporary data;
they do not depend on the original collection machine or launch a model.
The prompt regression test reads the frozen original agent from the committed
recovery archive. Synthetic diagnostic fixtures are test inputs, not experiment
results. `phase1-recovery-verify` separately checks every archived member's
checksum and requires 600 baseline plus 54 retry results.

## Rebuild the Phase 1 report: no inference

Install [Tectonic](https://tectonic-typesetting.github.io/) for LaTeX compilation.
Its first build may download fonts and packages. After `make setup`:

```sh
make phase1-report-setup
make phase1-report
```

The first command installs `configs/report-requirements.txt` into the isolated
`.cache/report-env` environment. The second rebuilds the full-size result plots,
compact report figures, and `reports/phase1-report.pdf` from committed tables.
It updates those generated files; inspect the diff and rendered PDF before
committing. To regenerate only figures, use `make phase1-report-figures`.

The report uses `results/phase1-recovery` for the baseline and explicitly labeled
historical summaries for exploratory studies. Plot provenance identifies the
input files. No restored `.cache/recovery` tree or model weights are needed.
Keep the report plotting environment separate from `uv.lock`: the latter is part
of frozen execution identities. The Phase 2 pilot has its own historical plotting
requirements in `configs/phase2/analysis-requirements.txt`.

## Rebuild and recover Phase 2: no inference

After ordinary setup and installing Tectonic, use the same isolated plotting
environment as the Phase 1 report (the setup target retains its historical name):

```sh
make phase1-report-setup
make phase2-main-plot
MPLCONFIGDIR=.cache/matplotlib .cache/report-env/bin/python reports/phase2-figures/make_main_figures.py
tectonic --keep-logs reports/phase2-report.tex
```

The formal plots read committed final tables. The report helper pins the
immutable 54-task main snapshot, separate final confirmation tables, and reviewed
selected diagnostics, checking their hashes before and after generation. It does
not run inference or rescore tasks. Inspect rebuilt figures/PDF before committing;
[report verification](../reports/phase2-report-verification.json) records the
completed build and all eleven rendered page checks. A fully cached Tectonic
build can use `--only-cached` after dependencies have been downloaded.

To reconstruct raw formal evidence and re-export all tables without models:

```sh
make phase2-main-restore
```

This restores 8,170 files into an isolated tree, reruns all 21 full gates, and
requires the three tables to match the committed final bytes. It refuses changed
existing destination files. The [diagnostic supplement's recovery recipe](../results/phase2-seed-diagnostics-v1/README.md)
separately restores 276 files and reproduces all 24 episode audits and three
comparison exports. The cohorts and selected diagnostics are never pooled.

## Start local inference

Requirements: Apple Silicon macOS, Apple's command-line build tools, and enough
memory/disk for Qwen3-8B F16. The reported machine was an M2 Max with 64 GiB
unified memory. Model preparation downloads roughly 16.4 GB of source weights
and writes a roughly 17.3 GB F16 artifact; allow additional space for the runtime
and evidence. The deployment is pinned in `configs/runtime-qwen3-8b-f16.toml`.

```sh
make setup
make runtime
make model
make doctor
make serve
```

`serve` runs in the foreground. In a second terminal:

```sh
make probe
make smoke
make stop
```

`probe` checks native tool calling. `smoke` runs one development clean/attack
pair and writes its manifest, requests, trace and result under `artifacts/runs/`.
Development smoke outcomes are not benchmark results. For an explicit run ID:

```sh
uv run --frozen agent-tradeoffs run --config configs/phase1/dev-smoke.toml --run-id my-smoke
```

Run IDs cannot be silently overwritten. Use `--resume` only for compatible
interrupted work; use a new ID for a new experiment. `make doctor` checks local
readiness; it does not by itself establish that a model completed a task.

## Collect and analyze a new Phase 1 campaign

With the service running:

```sh
make phase1-audit
make phase1
make phase1-status
make phase1-resume
make phase1-analyze
```

`phase1` collects the 600-episode workload using the **current** execution code.
`phase1-resume` is only needed after interruption. These commands do not recreate
the historical execution identity after the harness has changed.

`phase1-analyze` produces automatic measurements and the review sample. It does
not attach old review labels to new traces. After reviewing the new cohort, pass
its annotations explicitly:

```sh
make phase1-analyze ANNOTATIONS=path/to/reviewed-traces.csv
```

Complete the [trace-review protocol](trace-review-protocol.md) before using the
final exporter. Its default `results/phase1` destination is the immutable
historical publication; use a new `--snapshot` and `--results` destination for
a new cohort. Run `uv run --frozen python scripts/export_phase1_evaluation.py --help`
for those arguments. Never edit an identity or hash to make a changed run resume.

## Inspect or re-export the recovered baseline

The committed [archive and inventory](../artifacts/README.md) are sufficient to
verify and inspect recovered raw evidence. Extract into an empty directory.
Original absolute provenance paths are preserved; re-export on another host
requires deliberate relocation and a new snapshot rather than edits to archived
files.

On the original collection machine, with the restored working tree present:

```sh
make phase1-recovery-export
```

This uses `.cache/recovery`'s original execution harness and the repository's
offline analyzer, then checks the full collection and all review obligations.
Existing snapshots reject changed inputs. `make phase1-recovery-package` is a
maintainer operation to rebuild the archive from those working files; archive
verification does not require rebuilding it.

## Historical exploratory workflows

The [script index](../scripts/README.md) groups Phase 2 pilot, prompt-reminder,
and research-analysis tools. Their original plans and input identities remain
frozen. Read each linked protocol before running a historical driver: some
require original raw evidence and collection-machine paths that are unavailable.
This limitation does not affect offline development checks or report rebuilding.
