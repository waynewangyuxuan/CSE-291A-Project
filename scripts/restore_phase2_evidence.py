"""Restore an analyzed checkpoint in isolation and reproduce its measurements offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from phase2_collect import verify_archive

PUBLIC = Path("results/phase2-main-v1")
ARCHIVES = Path("artifacts/phase2-main-v1/archives")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def destination(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Restore path escapes its destination: {name}")
    return path


def archived_path(name):
    path = Path(name)
    if not path.is_absolute():
        return name
    marker = ("artifacts", "phase2-main-v1", "runtime")
    matches = [i for i in range(len(path.parts) - 2) if path.parts[i : i + 3] == marker]
    if len(matches) != 1:
        raise ValueError(f"Unexpected absolute archive path: {name}")
    return str(Path(*path.parts[matches[0] :]))


def restore(output):
    output = output.resolve()
    if Path.cwd().is_relative_to(output):
        raise ValueError("Restore into a separate directory, not this checkout or its parent")
    measurements = PUBLIC / "analysis/measurements.json"
    report = json.loads(measurements.read_text())
    analyzer = Path("scripts/analyze_phase2_matrix.py")
    if digest(analyzer.read_bytes()) != report["analysis_sha256"]:
        raise ValueError("Refresh the analyzed checkpoint before restoring it")
    if not report["blocks"]:
        raise ValueError("The checkpoint has no archived blocks")
    snapshots, copies, hashes, relocated = [], [analyzer], {}, {}
    for block in report["blocks"]:
        block_id = block["block"]
        archive = ARCHIVES / f"{block_id}.tar.gz"
        inventory = ARCHIVES / f"{block_id}.manifest.json"
        record = json.loads(inventory.read_text())
        if record["archive_sha256"] != block["archive_sha256"]:
            raise ValueError("Archive differs from the analyzed checkpoint")
        verify_archive(archive, record)
        snapshots.append(archive)
        copies.extend((archive, inventory))
        for member in record["members"]:
            name, sha = archived_path(member["path"]), member["sha256"]
            if name != member["path"]:
                relocated[member["path"]] = name
            if name in hashes and hashes[name] != sha:
                raise ValueError(f"Archived snapshots disagree: {name}")
            hashes[name] = sha
        review = PUBLIC / "reviews" / f"{block_id}.csv"
        current = digest(review.read_bytes()) if review.exists() else None
        if current != block["review_sha256"]:
            raise ValueError("Review differs from the analyzed checkpoint")
        copies.extend(p for p in (PUBLIC / "reviews").glob(f"{block_id}.*") if p.is_file())
    for path in copies:
        sha = digest(path.read_bytes())
        if str(path) in hashes and hashes[str(path)] != sha:
            raise ValueError(f"Checkpoint sources disagree: {path}")
        hashes[str(path)] = sha
    for name, sha in hashes.items():
        path = destination(output, name)
        if path.exists() and (not path.is_file() or digest(path.read_bytes()) != sha):
            raise ValueError(f"Existing file differs; choose a new restore directory: {path}")
    for archive in snapshots:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not member.isfile():
                    raise ValueError("Recovery archives must contain regular files only")
                path = destination(output, archived_path(member.name))
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(bundle.extractfile(member).read())
    for source in copies:
        path = destination(output, str(source))
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.read_bytes())
    for name, sha in hashes.items():
        if digest(destination(output, name).read_bytes()) != sha:
            raise ValueError(f"Restored file differs: {name}")
    env = os.environ | {"PYTHONPATH": str(output / "src")}
    (output / "restored-audits").mkdir(exist_ok=True)
    for block in report["blocks"]:
        block_id = block["block"]
        complete = block["reviewed"] == block["episodes"]
        subprocess.run(
            [
                sys.executable,
                "scripts/audit_phase2_spike.py",
                "--plan",
                f"configs/phase2-main-v1/{block_id}.json",
                "--annotations",
                str(PUBLIC / "reviews" / f"{block_id}.csv"),
                "--output",
                f"restored-audits/{block_id}.json",
                *([] if complete else ["--partial"]),
            ],
            cwd=output,
            env=env,
            check=True,
        )
    subprocess.run(
        [
            sys.executable,
            str(analyzer),
            *(["--final"] if report["status"] == "final_reviewed" else []),
        ],
        cwd=output,
        env=env,
        check=True,
    )
    reproduced = {}
    for name in ("measurements.json", "episodes.json", "episodes.csv"):
        path = PUBLIC / "analysis" / name
        original = digest(path.read_bytes())
        if digest((output / path).read_bytes()) != original:
            raise ValueError(f"Restored analysis does not reproduce: {name}")
        reproduced[name] = original
    receipt = {
        "status": "reproduced_offline_without_inference",
        "checkpoint_sha256": digest(measurements.read_bytes()),
        "blocks": len(report["blocks"]),
        "episodes": report["included_episodes"],
        "reviewed_episodes": report["reviewed_episodes"],
        "restored_files": len(hashes),
        "relocated_runtime_paths": relocated,
        "reproduced": reproduced,
    }
    (output / "recovery-verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "relocated_runtime_paths"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    restore(parser.parse_args().output)
