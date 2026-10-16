"""Final plots must not treat native base-sample readiness as all-review completion."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("incomplete_gate", ["legacy_scope_flag", "review_obligations"])
def test_native_ready_cannot_bypass_final_export_and_review_gates(
    tmp_path, monkeypatch, incomplete_gate
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    plotter = importlib.import_module("plot_phase1_evaluation")
    data = {
        "complete": True,
        "scope": {"full_cohort_evaluation_export_complete": True},
        "review_coverage": {"complete": True},
        "native_summary": {
            "planned_episodes": 600,
            "finalized_episodes": 600,
            "missing_episodes": 0,
            "overall": {"unique_tasks": 60},
            "preliminary_evaluation_ready": True,
        },
    }
    if incomplete_gate == "legacy_scope_flag":
        data["scope"] = {"full_phase1_complete": True}
    else:
        data["review_coverage"]["complete"] = False
    measurements = tmp_path / "measurements.json"
    measurements.write_text(json.dumps(data))

    # Reject incomplete evidence before opening any episode data or importing Matplotlib.
    with pytest.raises(ValueError, match="fully reviewed and audited"):
        plotter.load_inputs(measurements, tmp_path / "missing-episodes.csv")
