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

.PHONY: phase2-model phase2-prepare phase2 phase2-report phase2-lint phase2-verify-up phase2-verify phase2-verify-down

phase2-model:
	$(UV) run --frozen python scripts/prepare-q4.py

phase2-prepare: ## Freeze the historical Phase 2 pilot configuration
	$(UV) run --frozen python scripts/phase2_first_pass.py prepare

phase2: ## Run the quantization/context pilot (requires prepared models)
	$(UV) run --frozen python scripts/phase2_first_pass.py run

phase2-report:
	$(UV) run --frozen python scripts/phase2_first_pass.py report

PHASE2_SCRIPTS := scripts/prepare-q4.py scripts/phase2_first_pass.py scripts/verify_phase2.py scripts/analyze_phase2.py scripts/plot_phase2.py scripts/phase2_case_cards.py

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
