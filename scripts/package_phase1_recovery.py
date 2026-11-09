#!/usr/bin/env python3
"""Package recovered Phase 1 evidence; --check-only verifies every archived byte."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / ".cache/recovery"
OUTPUT = ROOT / "artifacts/phase1-recovery"
ARCHIVE = OUTPUT / "evidence.tar.gz"
MANIFEST = OUTPUT / "manifest.json"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def selected_files() -> list[Path]:
    results = ROOT / "results/phase1-recovery"
    measurements = json.loads((results / "evaluation-measurements.json").read_text())
    retries = json.loads((results / "error-retries/summary.json").read_text())
    if not (
        measurements["complete"]
        and measurements["review_coverage"]["complete"]
        and measurements["scope"]["finalized_episodes"] == 600
        and retries["original_evidence_unchanged"]
        and retries["overall"]["attempts"] == 54
    ):
        raise ValueError("Complete baseline export and retry audit are required")
    campaign = RECOVERY / "artifacts/campaigns/phase1-f16-v1/manifest.json"
    roots = [
        campaign,
        ROOT / "artifacts/recovery/final-evaluation",
        ROOT / "artifacts/recovery/analysis/reviewed-traces.csv",
        RECOVERY / "artifacts/phase1-error-retries",
        results / "evaluation-measurements.json",
        results / "evaluation-episodes.csv",
        results / "evaluation-requests.csv",
        results / "reviewed-traces.csv",
        results / "review-coverage.json",
        results / "evaluation-assessment.md",
        results / "README.md",
        results / "error-retries",
        *[RECOVERY / name for name in ("configs", "docs", "src", "scripts")],
        *[RECOVERY / name for name in ("pyproject.toml", "uv.lock", "RECOVERY.json")],
        *[Path(run["run_dir"]) for run in json.loads(campaign.read_text())["runs"]],
    ]
    files = set()
    for root in roots:
        if not root.exists():
            raise FileNotFoundError(root)
        for path in root.rglob("*") if root.is_dir() else [root]:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name == ".run.lock" or "interrupted_attempts" in path.parts:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError(f"Evidence must be a regular repository file: {path}")
            files.add(path)
    return sorted(files)


def verify_archive() -> dict:
    manifest = json.loads(MANIFEST.read_text())
    if digest(ARCHIVE.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("Archive checksum differs")
    actual = {}
    counts = {"baseline": 0, "retry": 0}
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for member in archive:
            path = Path(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Invalid archive member: {member.name}")
            if member.name in actual:
                raise ValueError(f"Duplicate archive member: {member.name}")
            content = archive.extractfile(member).read()
            actual[member.name] = {"bytes": len(content), "sha256": digest(content)}
            if path.name == "result.json":
                result = json.loads(content)
                if result["status"] not in {"completed", "error"}:
                    raise ValueError("Unfinalized archived episode")
                if member.name.startswith(".cache/recovery/artifacts/runs/"):
                    counts["baseline"] += 1
                elif member.name.startswith(".cache/recovery/artifacts/phase1-error-retries/runs/"):
                    counts["retry"] += 1
    if actual != manifest["files"] or counts != {"baseline": 600, "retry": 54}:
        raise ValueError("Archive inventory or episode coverage differs")
    return {
        "verified": True,
        "files": len(actual),
        "episodes": counts,
        "archive_sha256": manifest["archive_sha256"],
    }


def package() -> None:
    files = selected_files()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inventory = {}
    with tempfile.NamedTemporaryFile(dir=OUTPUT, suffix=".tar.gz", delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with gzip.GzipFile(fileobj=temporary, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for path in files:
                        content = path.read_bytes()
                        name = path.relative_to(ROOT).as_posix()
                        info = tarfile.TarInfo(name)
                        info.size = len(content)
                        info.mode = 0o644
                        archive.addfile(info, io.BytesIO(content))
                        inventory[name] = {"bytes": len(content), "sha256": digest(content)}
            temporary.flush()
            os.replace(temporary_path, ARCHIVE)
        finally:
            temporary_path.unlink(missing_ok=True)
    manifest = {
        "schema_version": 1,
        "archive": ARCHIVE.relative_to(ROOT).as_posix(),
        "archive_sha256": digest(ARCHIVE.read_bytes()),
        "scope": "600 baseline episodes, 54 retries, reviews and export sources; no weights",
        "restore": "Paths are relative to the repository root. Extract into an empty directory. "
        "Original absolute provenance paths are preserved and may require relocation to re-export.",
        "files": inventory,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not args.check_only:
        package()
    print(json.dumps(verify_archive()))


if __name__ == "__main__":
    main()
