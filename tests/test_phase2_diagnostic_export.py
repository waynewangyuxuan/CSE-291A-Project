"""Offline evidence relocation and drift boundaries for local seed comparisons."""

import hashlib
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("analyze_phase2_seed_diagnostics")


def test_restored_evidence_never_reads_original_path(tmp_path, exporter):
    original, restored = tmp_path / "original", tmp_path / "restored"
    original.mkdir()
    restored.mkdir()
    (original / "result.json").write_bytes(b"untrusted original")
    (restored / "result.json").write_bytes(b"restored evidence")
    source = exporter.Evidence(restored, original)
    assert source.read(original / "result.json") == b"restored evidence"
    assert source.hashes == {"result.json": hashlib.sha256(b"restored evidence").hexdigest()}


@pytest.mark.parametrize("name", ["../outside", "/outside"])
def test_evidence_rejects_escape(tmp_path, exporter, name):
    with pytest.raises(ValueError):
        exporter.Evidence(tmp_path, Path("/original")).read(name)


def test_evidence_rejects_symlink_escape(tmp_path, exporter):
    restored = tmp_path / "restore"
    restored.mkdir()
    (tmp_path / "outside").write_bytes(b"outside")
    (restored / "result").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink escapes"):
        exporter.Evidence(restored, Path("/original")).read("result")


def test_evidence_rejects_change_during_export(tmp_path, exporter):
    path = tmp_path / "result"
    path.write_bytes(b"first")
    source = exporter.Evidence(tmp_path, Path("/original"))
    assert source.read("result") == b"first"
    path.write_bytes(b"second")
    with pytest.raises(ValueError, match="changed during export"):
        source.read("result")


def test_evidence_rejects_changed_parent_pin(tmp_path, exporter):
    (tmp_path / "result").write_bytes(b"changed")
    source = exporter.Evidence(
        tmp_path, Path("/original"), pins={"result": hashlib.sha256(b"frozen").hexdigest()}
    )
    with pytest.raises(ValueError, match="Frozen parent evidence changed"):
        source.read("result")


def test_comparison_refuses_formal_root_without_output(tmp_path, exporter):
    output = tmp_path / "export"
    with pytest.raises(ValueError, match="diagnostic artifact root"):
        exporter.export(Path("artifacts/phase2-main-v1"), tmp_path, output)
    assert not output.exists()


@pytest.fixture(params=("agent", "summary"))
def captured_sampling_case(request):
    import json

    from agent_tradeoffs.config import ExperimentConfig

    plan = json.loads(Path("configs/phase2-main-v1/main-011.json").read_text())
    entry = next(r for r in plan["runs"] if r["run_id"] == "f16-summary-travel9")
    config = ExperimentConfig.model_validate(entry["config"])
    if request.param == "agent":
        model = config.model.model_copy(update={"seed": 29})
        payload = {
            "model": "qwen3-8b-f16",
            "temperature": 0.7,
            "seed": 29,
            "max_tokens": 2048,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": False,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "messages": [],
            "tools": [],
            "tool_choice": "auto",
        }
        budget = 2048
    else:
        model = config.context.summarizer
        payload = {
            "model": "qwen3-8b-f16",
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": 384,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": False,
            "top_k": 1,
            "messages": [],
        }
        budget = 384
    return [{"event": "request", "payload": payload}], model, budget


def test_accepts_agent29_and_fixed_summary0_headers(exporter, captured_sampling_case):
    traffic, model, budget = captured_sampling_case
    exporter.verify_sampling(traffic, model, budget)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("seed", 17),
        ("max_tokens", 4096),
        ("temperature", 0.3),
        ("model", "different-model"),
        ("cache_prompt", True),
        ("chat_template_kwargs", {"enable_thinking": True}),
        ("unfrozen_extra_parameter", True),
    ],
)
def test_rejects_changed_sampling_header(exporter, captured_sampling_case, field, changed):
    traffic, model, budget = captured_sampling_case
    traffic[0]["payload"][field] = changed
    with pytest.raises(ValueError, match="Captured request sampling differs"):
        exporter.verify_sampling(traffic, model, budget)


def test_rejects_missing_sampling_seed(exporter, captured_sampling_case):
    traffic, model, budget = captured_sampling_case
    del traffic[0]["payload"]["seed"]
    with pytest.raises(ValueError, match="Captured request sampling differs"):
        exporter.verify_sampling(traffic, model, budget)
