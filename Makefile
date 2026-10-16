UV := UV_CACHE_DIR=.cache/uv uv
RUNTIME_CONFIG ?= configs/runtime-qwen3-8b-f16.toml
CONFIG ?= configs/phase1/dev-smoke.toml

.PHONY: setup runtime model model-f16 doctor serve stop probe smoke pilot tasks test lint check phase1-audit phase1 phase1-resume phase1-status phase1-analyze

setup:
	$(UV) sync --frozen

runtime:
	bash scripts/bootstrap-runtime.sh

model-f16:
	$(UV) run --frozen --group modelprep python scripts/prepare-f16.py

model: model-f16
	$(UV) run --frozen agent-tradeoffs model download --config $(RUNTIME_CONFIG)

doctor:
	$(UV) run --frozen agent-tradeoffs doctor --config $(RUNTIME_CONFIG)

serve:
	$(UV) run --frozen agent-tradeoffs serve --config $(RUNTIME_CONFIG)

stop:
	$(UV) run --frozen agent-tradeoffs stop --config $(RUNTIME_CONFIG)

probe:
	$(UV) run --frozen agent-tradeoffs probe --config $(RUNTIME_CONFIG)

smoke:
	$(UV) run --frozen agent-tradeoffs run --config $(CONFIG)

pilot:
	$(UV) run --frozen agent-tradeoffs run --config configs/phase1/dev-pilot.toml

phase1-audit:
	$(UV) run --frozen python scripts/audit_phase1_workload.py --output artifacts/phase1/workload-audit.json

phase1:
	$(UV) run --frozen agent-tradeoffs campaign run --plan configs/phase1/campaign.json

phase1-resume:
	$(UV) run --frozen agent-tradeoffs campaign run --plan configs/phase1/campaign.json --resume

phase1-status:
	$(UV) run --frozen agent-tradeoffs campaign status artifacts/campaigns/phase1-f16-v1

phase1-analyze:
	$(UV) run --frozen agent-tradeoffs analyze artifacts/campaigns/phase1-f16-v1

tasks:
	$(UV) run --frozen agent-tradeoffs tasks --suite banking

test:
	$(UV) run --frozen pytest

lint:
	$(UV) run --frozen ruff check src tests
	$(UV) run --frozen ruff format --check src tests

check: lint test
