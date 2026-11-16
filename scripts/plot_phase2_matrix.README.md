# Formal Phase 2 figures

`plot_phase2_matrix.py` reads the existing `measurements.json` and `episodes.json`
export. It retains the five original estimands and adds `06-checker-sensitivity`.
It does not import the analyzer, rescore episodes, recompute paired effects or
bootstrap intervals, select diagnostics, or call models.

- **03 tradeoffs:** unchanged all-task clean utility versus matched-clean median
  time/RSS, colored by all-nine common-clean ASR. A table gives the actual clean
  cost counts, attacked ASR numerator/denominator/unknowns, and separate clean and
  attacked compaction coverage. RSS still spans a clean/attack pair. An arm with
  incomplete matched costs or unknown ASR remains omitted and is marked as such.
- **04 interactions:** unchanged exported DID and CI, with known/selected task
  counts and CI status (estimated, degenerate or withheld). Coverage includes only
  the four known cells per task for that outcome and condition. A task is counted
  once if any included cell applied compaction; episode counts are separate.
  No-compaction samples do not test applied compression; a zero-width CI is not
  evidence of equivalence.
- **06 sensitivity:** official versus reviewed-clean eligibility ASR, separately
  own-clean and all-nine common-clean. Counts retain unknown attacks in each
  denominator, with hatched unknown bounds rather than confidence intervals.
  Exclusions are listed per arm and for the common set. Unreviewed or labeled
  false-positive clean successes are excluded only from sensitivity. An attacked
  false-positive label alone does not remove clean eligibility. Whole-flagged-task
  exclusion and paired sensitivity intervals are not displayed.

Coverage uses cohort, precision, policy, suite, task and condition identities;
duplicate/missing pairs and disagreements with exported eligibility/counts are
rejected. Measurements and episodes must come from the same saved export. Both
inputs and the plot source are read once and bound in `provenance.json` along with
every produced PNG/SVG/PDF. Rendering occurs in a temporary sibling directory.
Input bytes/file identities are checked again before publication and immediately
after the directory swap; detected drift discards staging or restores the prior
output. This is a snapshot check, not a lock on ongoing analysis: later exports
do not change the hashes or meaning of an already published figure set. Do not
run two plot publishers to the same output directory concurrently.

For an isolated preview, first save a stable copy of **both** export files, then
set both paths explicitly (the defaults still target the public analysis tree):

```sh
MPLCONFIGDIR=/private/tmp/phase2-plot-preview/matplotlib \
  .cache/report-env/bin/python scripts/plot_phase2_matrix.py --provisional \
  --input-dir /private/tmp/phase2-plot-preview/inputs \
  --output-dir /private/tmp/phase2-plot-preview/figures
```

Omit `--provisional` only for an existing `final_reviewed` export. Rendering refuses
to publish when either input changes during the operation. It also refuses to
replace the input directory or an ancestor. No public figures need be changed to
check a preview.

Focused verification (uses already installed plotting dependencies):

```sh
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/private/tmp/phase2-plot-tests/matplotlib \
  PYTHONPATH=.cache/report-env/lib/python3.12/site-packages:src:scripts \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_phase2_matrix_plots.py tests/test_phase2_matrix_analysis.py
```

Tests cover exact cohort/four-cell selection, task versus episode coverage,
unknown outcomes, empty sets, differing official/reviewed eligibility, unchanged
exported CI states, mixed-input rejection and publication rollback on input drift.
Real-render tests check figure text and input/output hashes; they skip when the
optional Matplotlib environment is unavailable. Synthetic fixture statistics use
the unchanged analyzer; empirical plots consume only already exported statistics.
