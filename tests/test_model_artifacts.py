"""Check non-quantized artifact auditing and source-header safety boundaries."""

import hashlib
import io
import json
import struct
from pathlib import Path

import pytest

from agent_tradeoffs import model_artifacts
from agent_tradeoffs.model_artifacts import inspect_gguf, parse_safetensors_header


def _string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def _gguf(path: Path, tensor_type: int = 1, truncate: bool = False) -> None:
    metadata = _string("general.file_type") + struct.pack("<II", 4, 1)
    tensor = _string("weight") + struct.pack("<IQQIQ", 2, 2, 3, tensor_type, 0)
    data = b"GGUF" + struct.pack("<IQQ", 3, 1, 1) + metadata + tensor
    data += b"\0" * (-len(data) % 32)
    data += b"\0" * (12 if tensor_type == 1 else 24)
    path.write_bytes(data[:-1] if truncate else data)


def test_audit_inspects_actual_tensor_types_and_parameters(tmp_path: Path) -> None:
    path = tmp_path / "floating.gguf"
    _gguf(path)
    report = inspect_gguf(path)
    assert report["tensor_types"] == {"F16": 1}
    assert report["parameter_count"] == 6
    assert report["quantized_tensor_count"] == 0
    _gguf(path, tensor_type=12)
    assert inspect_gguf(path)["quantized_tensor_count"] == 1


def test_audit_rejects_truncated_tensor_data(tmp_path: Path) -> None:
    path = tmp_path / "truncated.gguf"
    _gguf(path, truncate=True)
    with pytest.raises(ValueError, match="truncated"):
        inspect_gguf(path)


def test_safetensors_header_requires_bf16_and_valid_ranges() -> None:
    header = {"weight": {"dtype": "BF16", "shape": [2, 3], "data_offsets": [0, 12]}}

    def encode() -> bytes:
        payload = json.dumps(header).encode()
        return len(payload).to_bytes(8, "little") + payload

    assert parse_safetensors_header(encode())[0] == header
    header["weight"]["dtype"] = "F16"
    with pytest.raises(ValueError, match="must be BF16"):
        parse_safetensors_header(encode())
    header["weight"]["dtype"] = "BF16"
    header["weight"]["data_offsets"] = [0, 10]
    with pytest.raises(ValueError, match="offsets"):
        parse_safetensors_header(encode())


def test_ram_provider_checks_entire_source_before_exposing_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = {"weight": {"dtype": "BF16", "shape": [2], "data_offsets": [0, 4]}}
    encoded = json.dumps(header).encode()
    payload = len(encoded).to_bytes(8, "little") + encoded + b"\x01\x02\x03\x04"
    expected = hashlib.sha256(payload).hexdigest()
    filename = "model-00001-of-00001.safetensors"
    monkeypatch.setattr(model_artifacts, "SOURCE_SHARDS", {filename: (len(payload), expected)})
    monkeypatch.setattr(model_artifacts, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    monkeypatch.setattr(model_artifacts.time, "sleep", lambda _: None)
    provider = model_artifacts.VerifiedRamShards()
    provider.index()
    assert provider.load(filename) == payload
    assert provider.verified == {filename}
    monkeypatch.setattr(model_artifacts, "SOURCE_SHARDS", {filename: (len(payload), "0" * 64)})
    provider = model_artifacts.VerifiedRamShards()
    provider.index()
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        provider.load(filename)
    assert provider.verified == set()
    assert provider.current_data is None
