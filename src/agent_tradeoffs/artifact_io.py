"""Lossless, resumable compression of finalized episode evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from agent_tradeoffs.reporting import atomic_json


def _stream_digest(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def compact_episode_artifacts(directory: Path) -> dict[str, Any]:
    """Verify gzip bytes and persist their original digest before removing a source.

    Every deletion has an already durable per-episode compression record. A
    stopped conversion therefore leaves either its source or a verifiable gzip
    plus its original digest. Existing gzip evidence is checked again on resume.
    """
    if not (directory / "result.json").is_file():
        raise ValueError("Only an episode with a finalized result can be compacted")
    evidence = directory / "compression.json"
    previous = json.loads(evidence.read_text()) if evidence.is_file() else {"artifacts": []}
    records = {item["path"]: item for item in previous["artifacts"]}

    def persist() -> dict[str, Any]:
        report = {"format": "gzip", "lossless": True, "artifacts": list(records.values())}
        atomic_json(evidence, report)
        return report

    for filename in ("requests.jsonl", "trace.json"):
        source = directory / filename
        target = directory / (filename + ".gz")
        if not source.is_file():
            if target.is_file():
                record = records.get(target.name)
                if record is None:
                    raise ValueError(f"Compressed artifact has no digest record: {target}")
                with gzip.open(target, "rb") as handle:
                    digest = _stream_digest(handle)
                if digest != record["original_sha256"]:
                    raise ValueError(f"Compressed artifact digest mismatch: {target}")
                with target.open("rb") as handle:
                    compressed_digest = _stream_digest(handle)
                if compressed_digest != record["compressed_sha256"]:
                    raise ValueError(f"Compressed artifact file digest mismatch: {target}")
            elif target.name in records:
                raise ValueError(f"Recorded compressed artifact is missing: {target}")
            continue
        temporary = directory / f".{target.name}.{os.getpid()}.tmp"
        with source.open("rb") as handle:
            original_digest = _stream_digest(handle)
        try:
            with (
                source.open("rb") as input_file,
                temporary.open("wb") as output_file,
                gzip.GzipFile(filename="", mode="wb", fileobj=output_file, mtime=0) as zipped,
            ):
                shutil.copyfileobj(input_file, zipped)
            with gzip.open(temporary, "rb") as handle:
                if _stream_digest(handle) != original_digest:
                    raise RuntimeError(f"Compressed artifact verification failed: {source}")
            with temporary.open("rb") as handle:
                compressed_digest = _stream_digest(handle)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            records[target.name] = {
                "path": target.name,
                "original_sha256": original_digest,
                "compressed_sha256": compressed_digest,
                "original_bytes": source.stat().st_size,
                "compressed_bytes": temporary.stat().st_size,
            }
            temporary.replace(target)
            persist()
            source.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    return persist()
