"""Pinned local llama.cpp runtime, model verification, and API readiness checks.

Only model acquisition needs the network. Inference binds to loopback and writes
its process metadata and logs below ``artifacts/runtime``.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import signal
import socket
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LLAMA_RELEASE = "b11678"
LLAMA_COMMIT = "b6334e37e6ef286fc5091a41b6561b2113d915fb"
LLAMA_ARCHIVE_SHA256 = "81c98d485d430272895ed216ffd5537b400a36519e9da1fa412c5dbb3d1e0f4c"
MODEL_REVISION = "7c41481f57cb95916b40956ab2f0b139b296d974"
MODEL_SHA256 = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"


@dataclass(frozen=True)
class ModelArtifactConfig:
    repo_id: str = "Qwen/Qwen3-8B-GGUF"
    revision: str = MODEL_REVISION
    filename: str = "Qwen3-8B-Q4_K_M.gguf"
    sha256: str = MODEL_SHA256
    local_dir: str = ".cache/models/qwen3-8b"
    acquisition: str = "download"

    def __post_init__(self) -> None:
        if self.acquisition not in {"download", "derived"}:
            raise ValueError("model_artifact.acquisition must be download or derived")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("model_artifact.revision must be an immutable 40-character commit")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("model_artifact.sha256 must be a lowercase SHA256 digest")
        filename = Path(self.filename)
        if filename.is_absolute() or ".." in filename.parts:
            raise ValueError("model_artifact.filename must stay within local_dir")


@dataclass(frozen=True)
class RuntimeConfig:
    binary_path: str = ".cache/runtime/llama-b11678/llama-server"
    host: str = "127.0.0.1"
    port: int = 8080
    context_size: int = 16384
    threads: int = 8
    gpu_layers: int = 99
    seed: int = 0
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    model_alias: str = "qwen3-8b-q4_k_m"
    output_root: str = "artifacts/runtime"
    model_artifact: ModelArtifactConfig = field(default_factory=ModelArtifactConfig)
    project_root: Path = field(default_factory=Path.cwd)

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("runtime.host must be a loopback address (127.0.0.1 or ::1)")
        if not 1 <= self.port <= 65535:
            raise ValueError("runtime.port must be between 1 and 65535")
        if self.context_size <= 0 or self.threads <= 0 or self.gpu_layers < 0:
            raise ValueError("context_size and threads must be positive; gpu_layers nonnegative")
        allowed_cache_types = {"f16", "bf16", "q8_0", "q4_0", "q4_1", "q5_0", "q5_1", "iq4_nl"}
        if (
            self.cache_type_k not in allowed_cache_types
            or self.cache_type_v not in allowed_cache_types
        ):
            raise ValueError("unsupported KV cache type")

    def resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.project_root / path

    @property
    def model_path(self) -> Path:
        return self.resolve(self.model_artifact.local_dir) / self.model_artifact.filename

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as handle:
        data = tomllib.load(handle)
    unknown = set(data) - {"runtime", "model_artifact"}
    if unknown:
        raise ValueError(f"unknown runtime config sections: {sorted(unknown)}")
    project_root = next(
        (parent for parent in source.parents if (parent / "pyproject.toml").is_file()),
        source.parent,
    )
    artifact = ModelArtifactConfig(**data.get("model_artifact", {}))
    return RuntimeConfig(
        **data.get("runtime", {}), model_artifact=artifact, project_root=project_root
    )


def _config(config: RuntimeConfig | str | Path) -> RuntimeConfig:
    return config if isinstance(config, RuntimeConfig) else load_runtime_config(config)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _process_identity(pid: int) -> dict[str, str] | None:
    command = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    )
    if command.returncode != 0:
        return None
    started = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, check=False
    )
    return {"command": command.stdout.strip(), "started": started.stdout.strip()}


def download_model(config: RuntimeConfig | str | Path) -> dict[str, Any]:
    """Download one immutable GGUF and verify its official LFS SHA256."""
    cfg = _config(config)
    artifact = cfg.model_artifact
    provenance = None
    if artifact.acquisition == "derived":
        provenance = _derived_provenance(cfg)
    if not cfg.model_path.is_file():
        from huggingface_hub import hf_hub_download

        downloaded = Path(
            hf_hub_download(
                repo_id=artifact.repo_id,
                revision=artifact.revision,
                filename=artifact.filename,
                local_dir=cfg.resolve(artifact.local_dir),
            )
        )
        if downloaded.resolve() != cfg.model_path.resolve():
            raise RuntimeError(f"unexpected model path: {downloaded}")
    actual_digest = sha256_file(cfg.model_path)
    if actual_digest != artifact.sha256:
        raise RuntimeError(
            f"model SHA256 mismatch: expected {artifact.sha256}, got {actual_digest}; "
            f"remove {cfg.model_path} and retry the download"
        )
    manifest = {
        "repo_id": artifact.repo_id,
        "revision": artifact.revision,
        "filename": artifact.filename,
        "path": str(cfg.model_path.resolve()),
        "sha256": actual_digest,
        "size_bytes": cfg.model_path.stat().st_size,
        "mtime_ns": cfg.model_path.stat().st_mtime_ns,
        "verified_at": datetime.now(UTC).isoformat(),
        "acquisition": artifact.acquisition,
    }
    if provenance is not None:
        manifest["provenance"] = provenance
    _write_json(cfg.model_path.with_suffix(".manifest.json"), manifest)
    return manifest


def _derived_provenance(cfg: RuntimeConfig) -> dict[str, Any]:
    path = cfg.model_path.with_suffix(".provenance.json")
    if not cfg.model_path.is_file() or not path.is_file():
        raise RuntimeError("Derived model is missing; run make model-f16 to prepare canonical F16")
    provenance = json.loads(path.read_text())
    artifact = cfg.model_artifact
    expected = {
        "kind": "derived_gguf",
        "repo_id": artifact.repo_id,
        "revision": artifact.revision,
        "filename": artifact.filename,
        "sha256": artifact.sha256,
        "output_dtype": "F16",
        "weight_quantization": "none",
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Derived model provenance does not match the requested F16 artifact")
    audit = provenance.get("gguf_audit", {})
    if (
        audit.get("quantized_tensor_count") != 0
        or audit.get("metadata", {}).get("general.file_type") != 1
    ):
        raise RuntimeError("Derived model audit does not establish unquantized F16 weights")
    return provenance


def server_command(config: RuntimeConfig | str | Path) -> list[str]:
    cfg = _config(config)
    return [
        str(cfg.resolve(cfg.binary_path)),
        "--model",
        str(cfg.model_path),
        "--alias",
        cfg.model_alias,
        "--host",
        cfg.host,
        "--port",
        str(cfg.port),
        "--ctx-size",
        str(cfg.context_size),
        "--no-context-shift",
        "--threads",
        str(cfg.threads),
        "--threads-batch",
        str(cfg.threads),
        "--gpu-layers",
        str(cfg.gpu_layers),
        "--parallel",
        "1",
        "--seed",
        str(cfg.seed),
        "--cache-type-k",
        cfg.cache_type_k,
        "--cache-type-v",
        cfg.cache_type_v,
        "--flash-attn",
        "on",
        "--cache-ram",
        "0",
        "--jinja",
        "--chat-template-kwargs",
        '{"enable_thinking":false}',
        "--reasoning",
        "off",
        "--offline",
        "--no-webui",
        "--metrics",
    ]


def _request_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 10) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _server_props(cfg: RuntimeConfig) -> dict[str, Any]:
    props = _request_json(cfg.base_url + "/props")
    checks = {
        "model path": Path(props.get("model_path", "")).resolve() == cfg.model_path.resolve(),
        "model alias": props.get("model_alias") == cfg.model_alias,
        "context size": props.get("default_generation_settings", {}).get("n_ctx")
        == cfg.context_size,
        "parallel slots": props.get("total_slots") == 1,
        "runtime build": props.get("build_info") == "b11678-b6334e37e",
    }
    mismatches = [name for name, valid in checks.items() if not valid]
    if mismatches:
        raise RuntimeError(f"running server differs from runtime config: {', '.join(mismatches)}")
    return props


def doctor(config: RuntimeConfig | str | Path) -> dict[str, Any]:
    """Check local prerequisites without loading a multi-GB model."""
    cfg = _config(config)
    binary = cfg.resolve(cfg.binary_path)
    report: dict[str, Any] = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "binary_path": str(binary),
        "binary_exists": binary.is_file() and os.access(binary, os.X_OK),
        "model_path": str(cfg.model_path),
        "model_exists": cfg.model_path.is_file(),
        "base_url": cfg.base_url + "/v1",
        "server_command": server_command(cfg),
    }
    if report["binary_exists"]:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=30
        )
        report["runtime_version"] = (result.stdout + result.stderr).strip()
        report["runtime_version_ok"] = (
            result.returncode == 0 and "11678" in report["runtime_version"]
        )
    else:
        report["runtime_version_ok"] = False
    manifest_path = cfg.model_path.with_suffix(".manifest.json")
    report["model_verified"] = False
    if report["model_exists"]:
        report["model_size_bytes"] = cfg.model_path.stat().st_size
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report["model_verified"] = (
                manifest.get("sha256") == cfg.model_artifact.sha256
                and manifest.get("size_bytes") == cfg.model_path.stat().st_size
                and manifest.get("mtime_ns") == cfg.model_path.stat().st_mtime_ns
                and manifest.get("revision") == cfg.model_artifact.revision
            )
        if cfg.model_artifact.acquisition == "derived":
            try:
                report["model_provenance"] = _derived_provenance(cfg)
            except (OSError, ValueError, RuntimeError) as error:
                report["model_verified"] = False
                report["provenance_error"] = str(error)
    try:
        report["health"] = _request_json(cfg.base_url + "/health", timeout=2)
    except (OSError, URLError, HTTPError):
        report["health"] = None
    report["ready"] = bool(
        report["runtime_version_ok"] and report["model_exists"] and report["model_verified"]
    )
    return report


def probe(config: RuntimeConfig | str | Path) -> dict[str, Any]:
    """Exercise a real OpenAI-compatible tool call and validate its arguments."""
    cfg = _config(config)
    health = _request_json(cfg.base_url + "/health")
    models = _request_json(cfg.base_url + "/v1/models")
    props = _server_props(cfg)
    if cfg.model_alias not in {model.get("id") for model in models.get("data", [])}:
        raise RuntimeError(f"expected model alias {cfg.model_alias!r} is not loaded: {models}")
    started = time.monotonic()
    response = _request_json(
        cfg.base_url + "/v1/chat/completions",
        {
            "model": cfg.model_alias,
            "messages": [
                {"role": "user", "content": "Call runtime_probe with value exactly runtime-ok."}
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "runtime_probe",
                        "description": "Verify that native tool calls are returned correctly.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "runtime_probe"}},
            "temperature": 0,
            "seed": cfg.seed,
            "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=120,
    )
    try:
        calls = response["choices"][0]["message"]["tool_calls"]
        function = calls[0]["function"]
        arguments = json.loads(function["arguments"])
        valid = (
            len(calls) == 1
            and function["name"] == "runtime_probe"
            and arguments == {"value": "runtime-ok"}
        )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"server did not return a valid native tool call: {response}") from error
    if not valid:
        raise RuntimeError(f"native tool call did not match the probe: {response}")
    report = {
        "health": health,
        "models": models,
        "effective_server_props": props,
        "tool_call_valid": valid,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "response": response,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    _write_json(cfg.resolve(cfg.output_root) / "probe.json", report)
    metadata_path = cfg.resolve(cfg.output_root) / "server.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("base_url") == cfg.base_url + "/v1":
            metadata["effective_server_props"] = props
            _write_json(metadata_path, metadata)
            _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
            _write_json(Path(metadata["log_path"]).with_name("server.json"), metadata)
    return report


def start_server(config: RuntimeConfig | str | Path, wait_timeout: float = 180) -> dict[str, Any]:
    """Start an isolated background server and wait for model readiness."""
    cfg = _config(config)
    report = doctor(cfg)
    if not report["ready"]:
        raise RuntimeError("runtime is incomplete; run make runtime and make model first")
    family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as check:
        if check.connect_ex((cfg.host, cfg.port)) == 0:
            raise RuntimeError(
                f"port {cfg.port} is already in use; inspect the existing process first"
            )
    run_name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_root = cfg.resolve(cfg.output_root) / run_name
    run_root.mkdir(parents=True)
    log_path = run_root / "server.log"
    command = server_command(cfg)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=cfg.project_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    metadata: dict[str, Any] = {
        "pid": process.pid,
        "command": command,
        "base_url": cfg.base_url + "/v1",
        "log_path": str(log_path),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "starting",
        "model_revision": cfg.model_artifact.revision,
        "model_sha256": cfg.model_artifact.sha256,
        "llama_release": LLAMA_RELEASE,
        "llama_commit": LLAMA_COMMIT,
        "process_identity": _process_identity(process.pid),
        "runtime_version": report["runtime_version"],
        "context_size": cfg.context_size,
        "model_alias": cfg.model_alias,
        "model_acquisition": cfg.model_artifact.acquisition,
        "model_provenance": report.get("model_provenance"),
    }
    metadata_path = run_root / "server.json"
    _write_json(metadata_path, metadata)
    _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
    _write_json(cfg.resolve(cfg.output_root) / "server.json", metadata)
    deadline = time.monotonic() + wait_timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited ({process.returncode}); inspect {log_path}"
                )
            try:
                health = _request_json(cfg.base_url + "/health", timeout=2)
                if health.get("status") == "ok":
                    metadata["effective_server_props"] = _server_props(cfg)
                    metadata["status"] = "ready"
                    metadata["ready_at"] = datetime.now(UTC).isoformat()
                    metadata["startup_seconds"] = (
                        datetime.fromisoformat(metadata["ready_at"])
                        - datetime.fromisoformat(metadata["started_at"])
                    ).total_seconds()
                    _write_json(metadata_path, metadata)
                    _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
                    _write_json(cfg.resolve(cfg.output_root) / "server.json", metadata)
                    return metadata
            except (OSError, URLError, HTTPError):
                pass
            time.sleep(0.5)
        raise TimeoutError(
            f"server did not become ready within {wait_timeout}s; inspect {log_path}"
        )
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        metadata["status"] = "failed"
        metadata["exit_code"] = process.returncode
        _write_json(metadata_path, metadata)
        _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
        _write_json(cfg.resolve(cfg.output_root) / "server.json", metadata)
        raise


def serve(config: RuntimeConfig | str | Path, background: bool = False) -> dict[str, Any]:
    """Start the server; foreground mode owns and shuts down its process."""
    cfg = _config(config)
    metadata = start_server(cfg)
    if background:
        return metadata
    print(f"llama-server ready at {metadata['base_url']} (PID {metadata['pid']})", flush=True)
    print(f"Log: {metadata['log_path']}", flush=True)
    try:
        while True:
            reaped, status = os.waitpid(metadata["pid"], os.WNOHANG)
            if reaped:
                metadata["status"] = "exited"
                metadata["exit_code"] = os.waitstatus_to_exitcode(status)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        os.kill(metadata["pid"], signal.SIGTERM)
        os.waitpid(metadata["pid"], 0)
        metadata["status"] = "stopped"
    except ProcessLookupError:
        metadata["status"] = "exited"
    _write_json(Path(metadata["log_path"]).with_name("server.json"), metadata)
    _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
    _write_json(cfg.resolve(cfg.output_root) / "server.json", metadata)
    return metadata


def stop_server(config: RuntimeConfig | str | Path, timeout: float = 15) -> dict[str, Any]:
    """Stop only the process identified by this config's recorded launch."""
    cfg = _config(config)
    metadata_path = cfg.resolve(cfg.output_root) / "server.json"
    if not metadata_path.is_file():
        return {"status": "not_running", "reason": "no server metadata"}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    pid = metadata.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError("server metadata does not contain a valid PID")
    if metadata.get("base_url") != cfg.base_url + "/v1" or metadata.get(
        "command"
    ) != server_command(cfg):
        raise RuntimeError(
            "server metadata does not match this runtime config; refusing to signal it"
        )
    identity = _process_identity(pid)
    if identity is None:
        metadata["status"] = "not_running"
    else:
        if not metadata.get("process_identity") or identity != metadata["process_identity"]:
            raise RuntimeError(
                "PID identity changed since launch; refusing to signal an unrelated process"
            )
        if identity["command"] != " ".join(metadata["command"]):
            raise RuntimeError(
                "running process command differs from the expected server; refusing to signal it"
            )
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Reap if this caller also launched it; otherwise the OS reaps it.
            try:
                reaped, _ = os.waitpid(pid, os.WNOHANG)
                if reaped:
                    break
            except ChildProcessError:
                pass
            if _process_identity(pid) is None:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError(f"server PID {pid} did not stop after SIGTERM within {timeout}s")
        metadata["status"] = "stopped"
    metadata["stopped_at"] = datetime.now(UTC).isoformat()
    _write_json(metadata_path, metadata)
    _write_json(cfg.resolve(cfg.output_root) / "latest.json", metadata)
    _write_json(Path(metadata["log_path"]).with_name("server.json"), metadata)
    return metadata
