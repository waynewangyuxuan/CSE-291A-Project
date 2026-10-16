UV := UV_CACHE_DIR=.cache/uv uv
RUNTIME_CONFIG ?= configs/runtime-qwen3-8b-q4.toml
CONFIG ?= configs/smoke.toml

.PHONY: setup runtime model doctor serve stop probe smoke pilot tasks test lint check

setup:
	$(UV) sync --frozen

runtime:
	bash scripts/bootstrap-runtime.sh

model:
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
	$(UV) run --frozen agent-tradeoffs run --config configs/baseline.toml

tasks:
	$(UV) run --frozen agent-tradeoffs tasks --suite banking

test:
	$(UV) run --frozen pytest

lint:
	$(UV) run --frozen ruff check src tests
	$(UV) run --frozen ruff format --check src tests

check: lint test
