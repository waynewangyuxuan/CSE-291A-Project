"""Reproducible F16 conversion from immutable, verified original BF16 weights.

The source shards are held in RAM, one at a time. This avoids a second 16 GB
copy on disk; all mapping, tensor conversion and GGUF writing use the pinned
upstream llama.cpp converter unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import struct
import sys
import tarfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.request import Request, urlopen

SOURCE_REPO = "Qwen/Qwen3-8B"
SOURCE_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
CONVERTER_COMMIT = "b6334e37e6ef286fc5091a41b6561b2113d915fb"
CONVERTER_ARCHIVE_SHA256 = "fe1145f1433136d4e73ca2214a9787c2bf3f99773042971b3547a69cf331c8d3"
SOURCE_SHARDS = {
    "model-00001-of-00005.safetensors": (
        3996250744,
        "31d6a825ae35f11fb85b195b4c42c146c051e446433125a215336abdf95cbf5f",
    ),
    "model-00002-of-00005.safetensors": (
        3993160032,
        "5991236cea6fe21f3d43cab0f0e84448734fbbe0789816202989f2ddc9d18282",
    ),
    "model-00003-of-00005.safetensors": (
        3959604768,
        "c5185c4794be2d8a9784d5753c9922db38df478ce11f9ed0b415b7304d896836",
    ),
    "model-00004-of-00005.safetensors": (
        3187841392,
        "b5ee7de71fbf17db3d5704e0c8f2bc7d005ca9e1d7ca2aeb19827b0cfcaa917a",
    ),
    "model-00005-of-00005.safetensors": (
        1244659840,
        "20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff",
    ),
}
# Regular files use their canonical Git blob IDs; tokenizer.json uses LFS SHA256.
SOURCE_METADATA = {
    "config.json": "d46195ac87f837ad233d02b2f80f148bf7c005e0",
    "generation_config.json": "20a8a9156fc8c3f25295ca067f61fdf120d517c5",
    "model.safetensors.index.json": "2b85c00f1b118961cd7a477e2bba0fe197a4ce1a",
    "tokenizer_config.json": "417d038a63fa3de29cfde265caedae14d1a58d92",
    "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    "vocab.json": "4783fe10ac3adce15ac8f358ef5462739852c569",
    "merges.txt": "31349551d90c7606f325fe0f11bbb8bd5fa0d7c7",
    "README.md": "ecc3ebd0849aa08d9484bd911dddfd5261b10d30",
    "LICENSE": "6634c8cc3133b3848ec74b9f275acaaa1ea618ab",
}
LOG = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exact(handle: BinaryIO, size: int) -> bytes:
    result = handle.read(size)
    if len(result) != size:
        raise ValueError("truncated GGUF")
    return result


def _string(handle: BinaryIO, keep: bool = True) -> str | None:
    size = struct.unpack("<Q", _exact(handle, 8))[0]
    if size > 128 * 1024 * 1024:
        raise ValueError("unreasonable GGUF string size")
    if keep:
        return _exact(handle, size).decode("utf-8")
    handle.seek(size, 1)
    return None


def _value(handle: BinaryIO, kind: int, keep: bool = False) -> Any:
    formats = {
        0: "B",
        1: "b",
        2: "H",
        3: "h",
        4: "I",
        5: "i",
        6: "f",
        7: "?",
        10: "Q",
        11: "q",
        12: "d",
    }
    if kind in formats:
        result = struct.unpack("<" + formats[kind], _exact(handle, struct.calcsize(formats[kind])))[
            0
        ]
        return result if keep else None
    if kind == 8:
        return _string(handle, keep)
    if kind == 9:
        child, count = struct.unpack("<IQ", _exact(handle, 12))
        if count > 100_000_000:
            raise ValueError("unreasonable GGUF array length")
        if child in formats and not keep:
            handle.seek(count * struct.calcsize(formats[child]), 1)
            return None
        values = [_value(handle, child, keep) for _ in range(count)]
        return values if keep else None
    raise ValueError(f"unknown GGUF metadata type {kind}")


def inspect_gguf(path: Path) -> dict[str, Any]:
    """Inspect all tensor types and shapes without importing model libraries."""
    with path.open("rb") as handle:
        if _exact(handle, 4) != b"GGUF":
            raise ValueError("not a little-endian GGUF")
        version, tensor_count, metadata_count = struct.unpack("<IQQ", _exact(handle, 20))
        if version != 3 or tensor_count > 1_000_000 or metadata_count > 100_000:
            raise ValueError("unsupported or malformed GGUF header")
        selected = {
            "general.architecture",
            "general.name",
            "general.file_type",
            "general.alignment",
            "general.parameter_count",
            "qwen3.context_length",
        }
        metadata = {}
        for _ in range(metadata_count):
            key = _string(handle)
            kind = struct.unpack("<I", _exact(handle, 4))[0]
            value = _value(handle, kind, key in selected)
            if key in selected:
                metadata[key] = value
        types: Counter[str] = Counter()
        parameter_count = 0
        max_tensor_end = 0
        all_floating = True
        for _ in range(tensor_count):
            _string(handle, False)
            dimensions = struct.unpack("<I", _exact(handle, 4))[0]
            if not 1 <= dimensions <= 4:
                raise ValueError("invalid GGUF tensor dimensions")
            shape = struct.unpack("<" + "Q" * dimensions, _exact(handle, 8 * dimensions))
            if any(size == 0 for size in shape):
                raise ValueError("empty GGUF tensor")
            kind, offset = struct.unpack("<IQ", _exact(handle, 12))
            count = math.prod(shape)
            parameter_count += count
            types[{0: "F32", 1: "F16", 30: "BF16"}.get(kind, f"type_{kind}")] += 1
            if kind in {0, 1, 30}:
                max_tensor_end = max(max_tensor_end, offset + count * (4 if kind == 0 else 2))
            else:
                all_floating = False
        alignment = metadata.get("general.alignment", 32)
        if not isinstance(alignment, int) or alignment <= 0:
            raise ValueError("invalid GGUF alignment")
        data_start = (handle.tell() + alignment - 1) // alignment * alignment
        if all_floating and data_start + max_tensor_end > path.stat().st_size:
            raise ValueError("truncated GGUF tensor data")
    return {
        "version": version,
        "metadata": metadata,
        "tensor_count": tensor_count,
        "parameter_count": parameter_count,
        "tensor_types": dict(sorted(types.items())),
        "quantized_tensor_count": sum(
            v for k, v in types.items() if k not in {"F16", "BF16", "F32"}
        ),
    }


def parse_safetensors_header(data: bytes | bytearray) -> tuple[dict[str, Any], int]:
    if len(data) < 8:
        raise ValueError("truncated safetensors header")
    size = int.from_bytes(data[:8], "little")
    if size > 16 * 1024 * 1024 or len(data) < size + 8:
        raise ValueError("invalid safetensors header size")
    metadata = json.loads(data[8 : 8 + size])
    tensors = {name: value for name, value in metadata.items() if name != "__metadata__"}
    for name, value in tensors.items():
        if value["dtype"] != "BF16":
            raise ValueError(f"canonical tensor {name} must be BF16")
        start, end = value["data_offsets"]
        if start < 0 or end - start != math.prod(value["shape"]) * 2:
            raise ValueError(f"invalid safetensors offsets for {name}")
    return tensors, size + 8


def source_url(filename: str) -> str:
    return f"https://huggingface.co/{SOURCE_REPO}/resolve/{SOURCE_REVISION}/{filename}"


def _metadata_files(directory: Path) -> dict[str, dict[str, str]]:
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, expected in SOURCE_METADATA.items():
        path = directory / name
        if not path.is_file():
            with urlopen(source_url(name), timeout=120) as response:
                data = response.read(32 * 1024 * 1024)
            path.write_bytes(data)
        data = path.read_bytes()
        actual = (
            hashlib.sha256(data).hexdigest()
            if len(expected) == 64
            else hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        )
        if actual != expected:
            raise RuntimeError(f"source metadata hash mismatch: {name}")
        result[name] = {"sha256": hashlib.sha256(data).hexdigest(), "upstream_object": expected}
    return result


class VerifiedRamShards:
    """Cache the current full shard in RAM; never expose unverified weight bytes."""

    def __init__(self) -> None:
        self.headers: dict[str, tuple[dict[str, Any], int]] = {}
        self.tensors: dict[str, tuple[str, dict[str, Any], int]] = {}
        self.verified: set[str] = set()
        self.current_name: str | None = None
        self.current_data: bytearray | None = None

    def index(self) -> None:
        for filename in SOURCE_SHARDS:
            request = Request(
                source_url(filename) + "?download=true", headers={"Range": "bytes=0-262143"}
            )
            with urlopen(request, timeout=120) as response:
                data = response.read(262144)
            metadata, offset = parse_safetensors_header(data)
            self.headers[filename] = (metadata, offset)
            for name, tensor in sorted(metadata.items()):
                if name in self.tensors:
                    raise RuntimeError(f"duplicate source tensor: {name}")
                self.tensors[name] = (filename, tensor, offset)

    def load(self, filename: str) -> bytearray:
        if self.current_name == filename and self.current_data is not None:
            return self.current_data
        self.current_data = None
        self.current_name = None
        size, expected = SOURCE_SHARDS[filename]
        for attempt in range(3):
            data = bytearray(size)
            digest = hashlib.sha256()
            position = 0
            reported = 0
            LOG.info("Downloading %s into RAM (%s bytes), attempt %d", filename, size, attempt + 1)
            try:
                with urlopen(source_url(filename), timeout=120) as response:
                    while position < size:
                        view = memoryview(data)[position : min(position + 8 * 1024 * 1024, size)]
                        count = response.readinto(view)
                        if not count:
                            raise RuntimeError(f"incomplete source shard: {filename}")
                        digest.update(view[:count])
                        position += count
                        if position - reported >= 512 * 1024 * 1024:
                            LOG.info("RAM download %s: %.1f%%", filename, 100 * position / size)
                            reported = position
                    if response.read(1):
                        raise RuntimeError(f"source shard larger than expected: {filename}")
                if digest.hexdigest() != expected:
                    raise RuntimeError(f"source shard SHA256 mismatch: {filename}")
                if parse_safetensors_header(data) != self.headers[filename]:
                    raise RuntimeError(f"source header changed: {filename}")
                self.current_name, self.current_data = filename, data
                self.verified.add(filename)
                LOG.info("Verified %s: %s", filename, expected)
                return data
            except Exception:
                if attempt == 2:
                    raise
                LOG.warning("Retrying failed RAM download of %s", filename, exc_info=True)
                del data
                time.sleep(2)
        raise AssertionError("unreachable")

    def tensor(self, name: str) -> Any:
        import torch

        filename, metadata, base = self.tensors[name]
        data = self.load(filename)
        start, end = metadata["data_offsets"]
        return torch.frombuffer(
            data, dtype=torch.bfloat16, count=(end - start) // 2, offset=base + start
        ).reshape(metadata["shape"])


def _converter_source(root: Path) -> Path:
    runtime = root / ".cache/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    archive = runtime / "llama-b11678-source.tar.gz"
    if not archive.is_file():
        url = f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{CONVERTER_COMMIT}"
        with (
            urlopen(url, timeout=120) as response,
            archive.with_suffix(".part").open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        archive.with_suffix(".part").replace(archive)
    if sha256_file(archive) != CONVERTER_ARCHIVE_SHA256:
        raise RuntimeError("pinned converter archive SHA256 mismatch")
    # Re-extract each time so manually edited cached source cannot change conversion.
    with tarfile.open(archive) as source:
        source.extractall(runtime, filter="data")
    return runtime / f"llama.cpp-{CONVERTER_COMMIT}"


def prepare_f16(root: Path) -> dict[str, Any]:
    """Create a verified derived artifact; never substitute a quantized source."""
    root = root.resolve()
    adapter_sha256 = sha256_file(Path(__file__))
    target = root / ".cache/models/qwen3-8b-f16/Qwen3-8B-F16.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    provenance_path = target.with_suffix(".provenance.json")
    if target.exists():
        if not provenance_path.is_file():
            raise RuntimeError("existing F16 has no provenance; inspect it before replacing it")
        manifest = json.loads(provenance_path.read_text())
        if manifest["sha256"] != sha256_file(target):
            raise RuntimeError("existing derived F16 checksum mismatch")
        return manifest
    partial = target.with_suffix(".gguf.part")
    partial.unlink(missing_ok=True)
    reserve = 800 * 1024**2
    needed = sum(value[0] for value in SOURCE_SHARDS.values()) + reserve
    available = shutil.disk_usage(target.parent).free
    if available < needed:
        raise RuntimeError(
            f"F16 needs {needed / 1024**3:.2f} GiB free including 800 MiB reserve; "
            f"available {available / 1024**3:.2f} GiB"
        )
    source = _converter_source(root)
    source_dir = root / ".cache/models/qwen3-8b-source-metadata"
    metadata_hashes = _metadata_files(source_dir)
    config = json.loads((source_dir / "config.json").read_text())
    if config.get("quantization_config") or config.get("torch_dtype") != "bfloat16":
        raise RuntimeError("canonical checkpoint is not unquantized BF16")
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "gguf-py"))
    import gguf
    import torch
    from conversion.base import LazyTorchTensor
    from conversion.qwen import Qwen3Model

    torch.set_num_threads(8)
    shards = VerifiedRamShards()
    shards.index()
    weight_map = json.loads((source_dir / "model.safetensors.index.json").read_text())["weight_map"]
    if {name: value[0] for name, value in shards.tensors.items()} != weight_map:
        raise RuntimeError("source tensors do not match pinned checkpoint index")

    class VerifiedQwen3Model(Qwen3Model):
        model_arch = Qwen3Model.model_arch

        def index_tensors(self, remote_hf_model_id: str | None = None) -> dict[str, Any]:
            indexed = {}
            for name, (_, metadata, _) in shards.tensors.items():
                shape = tuple(metadata["shape"])

                def generate(name: str = name, shape: tuple = shape) -> Any:
                    return LazyTorchTensor(
                        meta=LazyTorchTensor.meta_with_dtype_and_shape(torch.bfloat16, shape),
                        args=(name,),
                        func=shards.tensor,
                    )

                if item := self.filter_tensors((name, generate)):
                    indexed[item[0]] = item[1]
            return indexed

    with torch.inference_mode():
        model = VerifiedQwen3Model(
            source_dir,
            gguf.LlamaFileType.MOSTLY_F16,
            partial,
            model_name="Qwen3-8B",
            eager=False,
            use_temp_file=False,
        )
        model.write()
    if shards.verified != set(SOURCE_SHARDS):
        raise RuntimeError("not every original source shard was hash-verified")
    audit = inspect_gguf(partial)
    expected_parameters = sum(math.prod(v[1]["shape"]) for v in shards.tensors.values())
    if (
        audit["metadata"].get("general.file_type") != 1
        or set(audit["tensor_types"]) != {"F16", "F32"}
        or audit["parameter_count"] != expected_parameters
        or audit["tensor_count"] != len(shards.tensors)
    ):
        raise RuntimeError(f"F16 audit failed: {audit}")
    partial.replace(target)
    manifest = {
        "kind": "derived_gguf",
        "repo_id": SOURCE_REPO,
        "revision": SOURCE_REVISION,
        "filename": target.name,
        "path": str(target),
        "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size,
        "mtime_ns": target.stat().st_mtime_ns,
        "source_dtype": "BF16",
        "output_dtype": "F16",
        "weight_quantization": "none",
        "conversion_note": "Original BF16 converted to F16; normalization tensors remain F32.",
        "source_shards": {
            name: {"size_bytes": size, "sha256": digest}
            for name, (size, digest) in SOURCE_SHARDS.items()
        },
        "source_metadata": metadata_hashes,
        "converter_commit": CONVERTER_COMMIT,
        "converter_archive_sha256": CONVERTER_ARCHIVE_SHA256,
        "adapter_sha256": adapter_sha256,
        "conversion_dependencies": {
            "torch": torch.__version__,
            "numpy": __import__("numpy").__version__,
            "transformers": __import__("transformers").__version__,
        },
        "verified_at": datetime.now(UTC).isoformat(),
        "gguf_audit": audit,
    }
    provenance_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
