#!/usr/bin/env python3
"""Run with ``uv run --group modelprep python scripts/prepare-f16.py``."""

import json
import logging
from pathlib import Path

from agent_tradeoffs.model_artifacts import prepare_f16

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
print(json.dumps(prepare_f16(Path(__file__).resolve().parents[1]), indent=2))
