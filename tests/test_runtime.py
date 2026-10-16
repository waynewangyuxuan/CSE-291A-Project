"""Small contract tests; live model verification is the separate probe command."""

import hashlib
import json
from pathlib import Path

import pytest

from agent_tradeoffs.runtime import (
    ModelArtifactConfig,
    RuntimeConfig,
    download_model,
    load_runtime_config,
    server_command,
    stop_server,
)


def test_command_fixes_inference_controls(tmp_path: Path) -> None:
    config = RuntimeConfig(project_root=tmp_path)
    command = server_command(config)
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--seed") + 1] == "0"
    assert command[command.index("--cache-type-k") + 1] == "f16"
    assert command[command.index("--cache-type-v") + 1] == "f16"
    assert json.loads(command[command.index("--chat-template-kwargs") + 1]) == {
        "enable_thinking": False
    }
    assert "--offline" in command
    assert "--no-context-shift" in command


@pytest.mark.parametrize("host", ["0.0.0.0", "example.com", "192.168.1.1"])
def test_runtime_refuses_nonlocal_binding(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        RuntimeConfig(host=host)


def test_requires_immutable_model_source() -> None:
    with pytest.raises(ValueError, match="immutable"):
        ModelArtifactConfig(revision="main")
    with pytest.raises(ValueError, match="within local_dir"):
        ModelArtifactConfig(filename="../model.gguf")


def test_runtime_config_resolves_paths_at_project_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / "configs").mkdir()
    path = tmp_path / "configs/runtime.toml"
    path.write_text("[runtime]\nport=8089\n[model_artifact]\n")
    config = load_runtime_config(path)
    assert config.project_root == tmp_path
    assert config.port == 8089
    assert config.model_path == tmp_path / ".cache/models/qwen3-8b/Qwen3-8B-Q4_K_M.gguf"


def test_download_verifies_existing_model_and_writes_manifest(tmp_path: Path) -> None:
    model = tmp_path / "test.gguf"
    model.write_bytes(b"test model content")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    artifact = ModelArtifactConfig(filename=model.name, local_dir=".", sha256=digest)
    config = RuntimeConfig(project_root=tmp_path, model_artifact=artifact)
    manifest = download_model(config)
    assert manifest["sha256"] == digest
    assert manifest["size_bytes"] == model.stat().st_size
    assert (
        json.loads(model.with_suffix(".manifest.json").read_text())["revision"] == artifact.revision
    )
    model.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        download_model(config)


def test_stop_refuses_reused_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = RuntimeConfig(project_root=tmp_path)
    output_root = tmp_path / config.output_root
    output_root.mkdir(parents=True)
    metadata = {
        "pid": 12345,
        "base_url": config.base_url + "/v1",
        "command": server_command(config),
        "process_identity": {"command": "old server", "started": "yesterday"},
    }
    (output_root / "server.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(
        "agent_tradeoffs.runtime._process_identity",
        lambda pid: {"command": "unrelated process", "started": "today"},
    )
    signals = []
    monkeypatch.setattr("agent_tradeoffs.runtime.os.kill", lambda *args: signals.append(args))
    with pytest.raises(RuntimeError, match="identity changed"):
        stop_server(config)
    assert signals == []


def test_derived_missing_model_is_not_downloaded_as_a_nonexistent_hf_file(tmp_path: Path) -> None:
    config = RuntimeConfig(
        project_root=tmp_path,
        model_artifact=ModelArtifactConfig(acquisition="derived"),
    )
    with pytest.raises(RuntimeError, match="make model-f16"):
        download_model(config)


def test_derived_model_requires_matching_nonquantized_provenance(tmp_path: Path) -> None:
    import hashlib

    payload = b"unit-test-artifact-only"
    digest = hashlib.sha256(payload).hexdigest()
    artifact = ModelArtifactConfig(acquisition="derived", sha256=digest)
    config = RuntimeConfig(project_root=tmp_path, model_artifact=artifact)
    config.model_path.parent.mkdir(parents=True)
    config.model_path.write_bytes(payload)
    provenance = {
        "kind": "derived_gguf",
        "repo_id": artifact.repo_id,
        "revision": artifact.revision,
        "filename": artifact.filename,
        "sha256": digest,
        "output_dtype": "F16",
        "weight_quantization": "none",
        "gguf_audit": {"quantized_tensor_count": 0, "metadata": {"general.file_type": 1}},
    }
    path = config.model_path.with_suffix(".provenance.json")
    path.write_text(json.dumps(provenance))
    assert download_model(config)["provenance"]["weight_quantization"] == "none"
    provenance["gguf_audit"]["quantized_tensor_count"] = 1
    path.write_text(json.dumps(provenance))
    with pytest.raises(RuntimeError, match="unquantized F16"):
        download_model(config)
