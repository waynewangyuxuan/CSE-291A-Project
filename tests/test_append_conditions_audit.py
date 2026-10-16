"""Protect source evidence when choosing a destination for the offline audit."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("audit_phase1_append_conditions")


def test_report_cannot_replace_discovered_source_or_its_symlink(driver, tmp_path):
    source = tmp_path / "fixture-input.json"
    original = b'{"fixture": "source evidence"}\n'
    source.write_bytes(original)
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    sources = driver.Sources()
    sources.add_file(source, "fixture")

    for destination in (source, alias):
        with pytest.raises(ValueError, match="overwrite a source input"):
            driver.validate_output_destination(destination, tmp_path / "other-plan.json", sources)
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "destination",
    [
        "configs/phase1/campaign.json",
        "artifacts/runs/test/result.json",
        "artifacts/campaigns/test/manifest.json",
        "artifacts/runtime/server.json",
        ".cache/models/weights.gguf",
    ],
)
def test_report_cannot_enter_experiment_or_deployment_directories(driver, tmp_path, destination):
    with pytest.raises(ValueError, match="experiment inputs and deployment"):
        driver.validate_output_destination(
            Path(destination), tmp_path / "plan.json", driver.Sources()
        )


def test_cli_rejects_plan_as_output_without_overwriting_it(tmp_path):
    source = tmp_path / "plan.json"
    original = b'{"fixture": "deliberately invalid plan"}\n'
    source.write_bytes(original)
    script = Path(__file__).resolve().parents[1] / "scripts/audit_phase1_append_conditions.py"
    result = subprocess.run(
        [sys.executable, str(script), "--plan", str(source), "--output", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "overwrite a source input" in result.stderr
    assert source.read_bytes() == original


def test_deployment_paths_stay_protected_from_another_working_directory(
    driver, tmp_path, monkeypatch
):
    destination = Path(driver.__file__).resolve().parents[1] / "artifacts/runtime/server.json"
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="experiment inputs and deployment"):
        driver.validate_output_destination(destination, tmp_path / "plan.json", driver.Sources())


def test_separate_report_destination_is_allowed(driver, tmp_path):
    driver.validate_output_destination(
        tmp_path / "report.json", tmp_path / "plan.json", driver.Sources()
    )
