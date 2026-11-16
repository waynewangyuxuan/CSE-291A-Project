"""Collect the frozen Phase 2 blocks continuously and archive each verified block."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from phase2_matrix import CONFIGS, OUTPUT, load_block, read, status

from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runtime import sha256_file

QUEUE = OUTPUT / "queue.json"
MATRIX = CONFIGS / "matrix.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def idle():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def verify_archive(path, record):
    if sha256_file(path) != record["archive_sha256"]:
        raise ValueError("Archive hash differs")
    with tarfile.open(path, "r:gz") as archive:
        if set(archive.getnames()) != {r["path"] for r in record["members"]}:
            raise ValueError("Archive inventory differs")
        for row in record["members"]:
            data = archive.extractfile(row["path"]).read()
            if digest(data) != row["sha256"] or len(data) != row["bytes"]:
                raise ValueError(f"Archive member differs: {row['path']}")


def archive_block(block_id):
    _, plan, plan_path = load_block(block_id)
    root = OUTPUT / block_id
    campaign = read(root / "campaign.json")
    if campaign["status"] != "completed" or (root / "invalid-deployment.json").exists():
        raise ValueError(f"Block is not ready to archive: {block_id}")
    archive_dir = OUTPUT / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    audit_path = archive_dir / f"{block_id}.audit.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/audit_phase2_spike.py",
            "--plan",
            str(plan_path),
            "--annotations",
            str(archive_dir / "no-review-annotations.csv"),
            "--partial",
            "--output",
            str(audit_path),
        ],
        check=True,
    )
    audit = read(audit_path)
    if audit["checks"] != "passed" or audit["episodes_checked"] != plan["expected_episodes"]:
        raise ValueError("Every planned episode must pass collection audit before archival")
    paths = {p for p in (root / "runs").rglob("*") if p.is_file() and not p.name.startswith(".")}
    paths.update(
        p
        for p in (
            root / "campaign.json",
            root / "run.log",
            root / "launch.json",
            root / "collection-audit.json",
        )
        if p.exists()
    )
    for launch in campaign["launches"]:
        paths.update(
            p for p in Path(launch["metadata"]["log_path"]).parent.iterdir() if p.is_file()
        )
    paths.update(CONFIGS.glob("runtime-*.toml"))
    paths.update(Path("src/agent_tradeoffs").glob("*.py"))
    paths.update(
        {
            MATRIX,
            plan_path,
            audit_path,
            Path("uv.lock"),
            Path("pyproject.toml"),
            Path("scripts/phase2_matrix.py"),
            Path("scripts/audit_phase2_spike.py"),
            Path("scripts/phase2_collect.py"),
            Path("results/phase2-calibration/selected-protocol.json"),
            Path("configs/phase2-calibration/plan-before-action.json"),
        }
    )
    members = [
        {"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in sorted(paths)
    ]
    archive = archive_dir / f"{block_id}.tar.gz"
    inventory = archive_dir / f"{block_id}.manifest.json"
    if inventory.exists():
        record = read(inventory)
        verify_archive(archive, record)
        frozen_raw = {
            r["path"]: r["sha256"]
            for r in record["members"]
            if r["path"].startswith(str(root / "runs"))
        }
        current_raw = {
            r["path"]: r["sha256"] for r in members if r["path"].startswith(str(root / "runs"))
        }
        if frozen_raw != current_raw:
            raise ValueError("Archived raw run evidence changed")
        return record
    temporary = archive.with_suffix(".tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        for row in members:
            data = Path(row["path"]).read_bytes()
            if digest(data) != row["sha256"]:
                raise ValueError("Evidence changed while packaging")
            member = tarfile.TarInfo(row["path"])
            member.size = len(data)
            member.mode = 0o644
            bundle.addfile(member, io.BytesIO(data))
    temporary.replace(archive)
    record = {
        "kind": "formal_collection_evidence_review_separate",
        "block": block_id,
        "episodes": audit["episodes_checked"],
        "collection_checks": "passed",
        "trace_review": "pending",
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256_file(archive),
        "members": members,
    }
    verify_archive(archive, record)
    atomic_json(inventory, record)
    return record


def collect():
    matrix = read(MATRIX)
    identity = {
        "matrix_sha256": sha256_file(MATRIX),
        "controller_sha256": sha256_file(Path(__file__)),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".queue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read(QUEUE) if QUEUE.exists() else {"identity": identity, "archived_blocks": []}
        if state["identity"] != identity:
            raise ValueError("Queue identity changed; review before resuming")
        state.update(status="running", pid=os.getpid())
        child = None

        def save():
            atomic_json(QUEUE, state)

        def interrupted(signum, frame):
            if child is not None and child.poll() is None:
                child.terminate()
            raise KeyboardInterrupt(f"Queue received signal {signum}")

        previous = signal.signal(signal.SIGTERM, interrupted)
        save()
        try:
            for entry in matrix["blocks"]:
                block_id = entry["block_id"]
                state.update(active_block=block_id, phase="waiting_for_existing_block")
                save()
                while not idle():
                    time.sleep(5)
                root = OUTPUT / block_id
                if (root / "invalid-deployment.json").exists():
                    raise ValueError(f"Deployment review required: {block_id}")
                current = status(block_id)
                if current["status"] != "completed":
                    if current["status"] == "error":
                        raise ValueError(f"Inspect the prior block-driver failure: {block_id}")
                    state["phase"] = "collecting"
                    save()
                    print(json.dumps({"starting_block": block_id}), flush=True)
                    root.mkdir(parents=True, exist_ok=True)
                    with (root / "run.log").open("ab") as log:
                        child = subprocess.Popen(
                            [
                                sys.executable,
                                "-u",
                                "scripts/phase2_matrix.py",
                                "run",
                                "--block",
                                block_id,
                            ],
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                        child.wait()
                        if child.returncode:
                            raise RuntimeError(
                                f"Block driver exited {child.returncode}: {block_id}"
                            )
                    child = None
                state["phase"] = "auditing_and_archiving"
                save()
                record = archive_block(block_id)
                if block_id not in state["archived_blocks"]:
                    state["archived_blocks"].append(block_id)
                save()
                print(
                    json.dumps(
                        {
                            "archived_block": block_id,
                            "episodes": record["episodes"],
                            "bytes": record["archive_bytes"],
                        }
                    ),
                    flush=True,
                )
            state.update(
                status="collection_complete", active_block=None, phase="trace_review_and_analysis"
            )
            save()
        except BaseException as error:
            state.update(
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "error",
                error=f"{type(error).__name__}: {error}",
            )
            save()
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)


def launch():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".queue-launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record_path = OUTPUT / "queue-launch.json"
        if record_path.exists():
            try:
                os.kill(read(record_path)["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError("Collection queue is already alive")
        with (OUTPUT / "queue.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-i",
                    sys.executable,
                    "-u",
                    str(Path(__file__).resolve()),
                    "run",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        record = {
            "pid": process.pid,
            "matrix_sha256": sha256_file(MATRIX),
            "log": str(OUTPUT / "queue.log"),
        }
        atomic_json(record_path, record)
        print(json.dumps(record))


def report():
    matrix = read(MATRIX)
    blocks = [
        {k: v for k, v in status(b["block_id"]).items() if k != "episodes"}
        for b in matrix["blocks"]
    ]
    return {
        "queue": read(QUEUE) if QUEUE.exists() else {"status": "not_started"},
        "finalized": sum(b["finalized"] for b in blocks),
        "expected": sum(b["expected"] for b in blocks),
        "errors": sum(b["errors"] for b in blocks),
        "blocks": blocks,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "launch", "status", "archive"))
    parser.add_argument("--block", default="main-001")
    args = parser.parse_args()
    if args.command == "run":
        collect()
    elif args.command == "launch":
        launch()
    elif args.command == "archive":
        record = archive_block(args.block)
        print(json.dumps({k: v for k, v in record.items() if k != "members"}))
    else:
        print(json.dumps(report()))
