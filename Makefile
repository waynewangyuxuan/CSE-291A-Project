.DEFAULT_GOAL := help

UV := UV_CACHE_DIR=.cache/uv uv
RUNTIME_CONFIG ?= configs/runtime-qwen3-8b-f16.toml
CONFIG ?= configs/phase1/dev-smoke.toml
ANNOTATIONS ?=

.PHONY: help check-docs phase1-report-setup setup runtime model model-f16 doctor serve stop probe smoke pilot tasks test lint check phase1-audit phase1 phase1-resume phase1-status phase1-analyze

help: ## List commands; all paths are relative to the repository root
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-27s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install the locked Python development environment (no model download)
	$(UV) sync --frozen

runtime: ## Build the pinned llama.cpp runtime (Apple Silicon)
	bash scripts/bootstrap-runtime.sh

model-f16:
	$(UV) run --frozen --group modelprep python scripts/prepare-f16.py

model: model-f16 ## Prepare and verify the F16 model (large download)
	$(UV) run --frozen agent-tradeoffs model download --config $(RUNTIME_CONFIG)

doctor: ## Check the local inference environment
	$(UV) run --frozen agent-tradeoffs doctor --config $(RUNTIME_CONFIG)

serve: ## Start the inference service in the foreground
	$(UV) run --frozen agent-tradeoffs serve --config $(RUNTIME_CONFIG)

stop: ## Stop the managed inference service
	$(UV) run --frozen agent-tradeoffs stop --config $(RUNTIME_CONFIG)

probe: ## Check native tool calls against the running service
	$(UV) run --frozen agent-tradeoffs probe --config $(RUNTIME_CONFIG)

smoke: ## Run one development clean/attack pair (requires service)
	$(UV) run --frozen agent-tradeoffs run --config $(CONFIG)

pilot:
	$(UV) run --frozen agent-tradeoffs run --config configs/phase1/dev-pilot.toml

phase1-audit: ## Audit the frozen 60-task workload without inference
	$(UV) run --frozen python scripts/audit_phase1_workload.py --output artifacts/phase1/workload-audit.json

phase1: ## Collect a new 600-episode campaign with the current harness
	$(UV) run --frozen agent-tradeoffs campaign run --plan configs/phase1/campaign.json

phase1-resume: ## Resume a compatible local campaign
	$(UV) run --frozen agent-tradeoffs campaign run --plan configs/phase1/campaign.json --resume

phase1-status: ## Show local campaign progress
	$(UV) run --frozen agent-tradeoffs campaign status artifacts/campaigns/phase1-f16-v1

phase1-analyze: ## Analyze a local campaign using its matching review annotations
	PYTHONHASHSEED=0 $(UV) run --frozen agent-tradeoffs analyze artifacts/campaigns/phase1-f16-v1 \
		$(if $(ANNOTATIONS),--annotations $(ANNOTATIONS)) --compact

tasks: ## List Banking tasks without inference
	$(UV) run --frozen agent-tradeoffs tasks --suite banking

test: ## Run all offline tests; no external model or private files
	$(UV) run --frozen pytest

lint: ## Check Python style
	$(UV) run --frozen ruff check src tests scripts/check_repository.py scripts/package_phase1_recovery.py reports/figures/make_report_figures.py
	$(UV) run --frozen ruff format --check src tests scripts/check_repository.py scripts/package_phase1_recovery.py reports/figures/make_report_figures.py

check: lint test check-docs ## Run lint, offline tests and repository navigation checks

.PHONY: phase2-model phase2-prepare phase2 phase2-report phase2-lint phase2-verify-up phase2-verify phase2-verify-down phase2-qualification

phase2-qualification: ## Audit new Phase 2 candidate pairs offline; no model calls
	$(UV) run --frozen python scripts/audit_phase2_candidates.py --output artifacts/phase2-preparation/qualification.json

phase2-model:
	$(UV) run --frozen python scripts/prepare-q4.py

.PHONY: phase2-model-q8
phase2-model-q8: ## Derive verified Q8_0 from the same F16 source
	$(UV) run --frozen python scripts/prepare-q4.py --quantization Q8_0

phase2-prepare: ## Freeze the historical Phase 2 pilot configuration
	$(UV) run --frozen python scripts/phase2_first_pass.py prepare

phase2: ## Run the quantization/context pilot (requires prepared models)
	$(UV) run --frozen python scripts/phase2_first_pass.py run

phase2-report:
	$(UV) run --frozen python scripts/phase2_first_pass.py report

PHASE2_SCRIPTS := scripts/prepare-q4.py scripts/phase2_first_pass.py scripts/verify_phase2.py scripts/analyze_phase2.py scripts/plot_phase2.py scripts/phase2_case_cards.py scripts/audit_phase2_candidates.py scripts/phase2_budget_spike.py scripts/audit_phase2_spike.py scripts/phase2_calibration.py scripts/package_phase2_calibration.py scripts/phase2_matrix.py scripts/phase2_collect.py scripts/analyze_phase2_matrix.py scripts/plot_phase2_matrix.py scripts/restore_phase2_evidence.py scripts/import_phase2_review.py

phase2-lint:
	$(UV) run --frozen ruff check $(PHASE2_SCRIPTS)
	$(UV) run --frozen ruff format --check $(PHASE2_SCRIPTS)

PLOT_PYTHON ?= .cache/phase2-analysis-env/bin/python

.PHONY: phase2-analysis-setup phase2-analyze phase2-plot

phase2-analysis-setup:
	$(UV) venv --allow-existing .cache/phase2-analysis-env
	$(UV) pip install --python $(PLOT_PYTHON) -r configs/phase2/analysis-requirements.txt

phase2-analyze: ## Analyze existing Phase 2 pilot evidence
	$(UV) run --frozen python scripts/analyze_phase2.py
	$(UV) run --frozen python scripts/phase2_case_cards.py
	$(MAKE) phase2-plot

phase2-plot:
	MPLCONFIGDIR=.cache/matplotlib $(PLOT_PYTHON) scripts/plot_phase2.py

phase2-verify-up:
	$(UV) run --frozen python scripts/verify_phase2.py up

phase2-verify:
	$(UV) run --frozen python scripts/verify_phase2.py doctor
	$(UV) run --frozen python scripts/verify_phase2.py drive

phase2-verify-down:
	$(UV) run --frozen python scripts/verify_phase2.py down

check-docs: ## Check navigation links and the script index
	$(UV) run --frozen python scripts/check_repository.py

REPORT_PYTHON ?= .cache/report-env/bin/python

phase1-report-setup: ## Install isolated, pinned report plotting dependencies
	$(UV) venv --allow-existing .cache/report-env --python .venv/bin/python
	$(UV) pip install --python $(REPORT_PYTHON) -r configs/report-requirements.txt

.PHONY: phase1-recovery-export phase1-report-figures phase1-report phase1-recovery-package phase1-recovery-verify

phase1-recovery-export: ## Re-export the restored frozen cohort on its collection machine
	cp src/agent_tradeoffs/analysis.py .cache/recovery/src/agent_tradeoffs/analysis.py
	cd .cache/recovery && PYTHONHASHSEED=0 PYTHONPATH=src ../../.venv/bin/python ../../scripts/export_phase1_evaluation.py --annotations ../../artifacts/recovery/analysis/reviewed-traces.csv --snapshot ../../artifacts/recovery/final-evaluation --results ../../results/phase1-recovery

phase1-report-figures: ## Rebuild figures from the reviewed exported tables
	MPLCONFIGDIR=.cache/matplotlib $(REPORT_PYTHON) scripts/plot_phase1_evaluation.py --measurements results/phase1-recovery/evaluation-measurements.json --episodes results/phase1-recovery/evaluation-episodes.csv --output-prefix results/phase1-recovery/evaluation
	MPLCONFIGDIR=.cache/matplotlib $(REPORT_PYTHON) reports/figures/make_report_figures.py

phase1-report: phase1-report-figures ## Rebuild the Phase 1 PDF (requires report setup and Tectonic)
	tectonic --keep-logs reports/phase1-report.tex

phase1-recovery-package: ## Rebuild the archive from restored working raw files
	$(UV) run --frozen python scripts/package_phase1_recovery.py

phase1-recovery-verify: ## Verify the committed 600+54-episode archive without extracting it
	$(UV) run --frozen python scripts/package_phase1_recovery.py --check-only

.PHONY: phase2-spike-prepare phase2-spike-assets phase2-spike phase2-spike-status

phase2-spike-prepare: ## Freeze the development-only 18-episode token-budget spike
	$(UV) run --frozen python scripts/phase2_budget_spike.py prepare

phase2-spike-assets: ## Verify local weight hashes and register runtime manifests
	$(UV) run --frozen agent-tradeoffs model download --config configs/phase2-spike/runtime-f16.toml > /dev/null
	$(UV) run --frozen agent-tradeoffs model download --config configs/phase2-spike/runtime-q8.toml > /dev/null
	$(UV) run --frozen agent-tradeoffs model download --config configs/phase2-spike/runtime-q4.toml > /dev/null

phase2-spike: phase2-spike-assets ## Run or compatibly resume the frozen development spike
	$(UV) run --frozen python scripts/phase2_budget_spike.py run

phase2-spike-status: ## Show finalized spike episodes and errors
	$(UV) run --frozen python scripts/phase2_budget_spike.py status

.PHONY: phase2-calibration-prepare phase2-calibration phase2-calibration-status

phase2-calibration-prepare: ## Freeze 12 development episodes after profile and summary replay
	$(UV) run --frozen python scripts/phase2_calibration.py prepare

phase2-calibration: ## Run or compatibly resume the frozen development calibration
	$(UV) run --frozen agent-tradeoffs model download --config configs/phase2-calibration/runtime-f16.toml > /dev/null
	$(UV) run --frozen python scripts/phase2_calibration.py run

phase2-calibration-status: ## Show finalized calibration episodes
	$(UV) run --frozen python scripts/phase2_calibration.py status

.PHONY: phase2-calibration-tight phase2-calibration-tight-status

phase2-calibration-tight: ## Run the predeclared six-episode smaller-budget check
	$(UV) run --frozen python scripts/phase2_calibration.py run --plan configs/phase2-calibration/plan-tight.json --output-root artifacts/phase2-calibration-tight

phase2-calibration-tight-status: ## Show smaller-budget development progress
	$(UV) run --frozen python scripts/phase2_calibration.py status --plan configs/phase2-calibration/plan-tight.json --output-root artifacts/phase2-calibration-tight

.PHONY: phase2-calibration-before-action phase2-calibration-before-action-status

phase2-calibration-before-action: ## Test compaction before the substantive file write
	$(UV) run --frozen python scripts/phase2_calibration.py run --plan configs/phase2-calibration/plan-before-action.json --output-root artifacts/phase2-calibration-before-action

phase2-calibration-before-action-status: ## Show the final budget-check progress
	$(UV) run --frozen python scripts/phase2_calibration.py status --plan configs/phase2-calibration/plan-before-action.json --output-root artifacts/phase2-calibration-before-action

.PHONY: phase2-calibration-package phase2-calibration-verify

phase2-calibration-package: ## Archive all three reviewed development calibration batches
	$(UV) run --frozen python scripts/package_phase2_calibration.py

phase2-calibration-verify: ## Verify the calibration archive and every member
	$(UV) run --frozen python scripts/package_phase2_calibration.py --check-only

PHASE2_BLOCK ?= main-001
.PHONY: phase2-matrix-prepare phase2-matrix-launch phase2-matrix-run phase2-matrix-status

phase2-matrix-prepare: ## Freeze the ordered main and confirmation blocks
	$(UV) run --frozen python scripts/phase2_matrix.py prepare

phase2-matrix-launch: ## Start one block in the background with idle-sleep prevention
	$(UV) run --frozen python scripts/phase2_matrix.py launch --block $(PHASE2_BLOCK)

phase2-matrix-run: ## Run or compatibly resume one frozen block in the foreground
	$(UV) run --frozen python scripts/phase2_matrix.py run --block $(PHASE2_BLOCK)

phase2-matrix-status: ## Show finalized pairs and the current run for one block
	$(UV) run --frozen python scripts/phase2_matrix.py status --block $(PHASE2_BLOCK)

.PHONY: phase2-collect phase2-collect-status

phase2-collect: ## Continue all frozen blocks with per-block audits and raw archives
	$(UV) run --frozen python scripts/phase2_collect.py launch

phase2-collect-status: ## Show aggregate main and confirmation collection progress
	$(UV) run --frozen python scripts/phase2_collect.py status

.PHONY: phase2-main-analyze phase2-main-final

phase2-main-analyze: ## Export explicitly provisional metrics from archived formal blocks
	$(UV) run --frozen python scripts/analyze_phase2_matrix.py

phase2-main-final: ## Export final metrics only after complete collection and trace review
	$(UV) run --frozen python scripts/analyze_phase2_matrix.py --final

.PHONY: phase2-main-plot-provisional phase2-main-plot

phase2-main-plot-provisional: ## Render visibly provisional figures from the current export
	MPLCONFIGDIR=.cache/matplotlib $(REPORT_PYTHON) scripts/plot_phase2_matrix.py --provisional

phase2-main-plot: ## Render final reviewed formal figures
	MPLCONFIGDIR=.cache/matplotlib $(REPORT_PYTHON) scripts/plot_phase2_matrix.py

PHASE2_RESTORE_ROOT ?= .cache/phase2-main-restored
.PHONY: phase2-main-restore

phase2-main-restore: ## Restore the analyzed checkpoint and reproduce metrics without inference
	$(UV) run --frozen python scripts/restore_phase2_evidence.py --output "$(PHASE2_RESTORE_ROOT)"

.PHONY: phase2-main-import-review
phase2-main-import-review: ## Verify and publish a completed reviewer receipt for PHASE2_REVIEW_BLOCK
	$(UV) run --frozen python scripts/import_phase2_review.py --block "$(PHASE2_REVIEW_BLOCK)"
