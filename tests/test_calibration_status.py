"""Calibration progress accepts finalized results without an error object."""

import json
import subprocess
import sys
from pathlib import Path


def test_status_reports_completed_episodes_with_no_error_field(tmp_path):
    directory = tmp_path / "artifacts/phase2-calibration/runs/example/episodes/clean"
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "episode_id": "clean",
                "condition": "clean",
                "status": "completed",
                "utility": False,
                "attack_success": None,
                "duration_seconds": 3,
                "context_memory": {"compactions": 0},
            }
        )
    )
    script = Path(__file__).resolve().parents[1] / "scripts/phase2_calibration.py"
    result = subprocess.run(
        [sys.executable, str(script), "status"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["finalized"] == 1
    assert report["episodes"][0]["error"] is None
    assert report["episodes"][0]["utility"] is False
