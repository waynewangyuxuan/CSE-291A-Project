"""Preserve the reviewed development calibration as a verified deterministic archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from agent_tradeoffs.runner import implementation_identity

ROOT = Path.cwd()
OUTPUT = ROOT / "artifacts/phase2-calibration"
ARCHIVE = OUTPUT / "evidence.tar.gz"
MANIFEST = OUTPUT / "manifest.json"
COHORTS = (
    ("phase2-calibration", "verification.json", 12, OUTPUT / "driver-7680.py"),
    (
        "phase2-calibration-tight",
        "verification-tight.json",
        6,
        ROOT / "scripts/phase2_calibration.py",
    ),
    (
        "phase2-calibration-before-action",
        "verification-before-action.json",
        6,
        ROOT / "scripts/phase2_calibration.py",
    ),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(record):
    assert digest(ARCHIVE.read_bytes()) == record["archive_sha256"]
    assert ARCHIVE.stat().st_size == record["archive_bytes"]
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        assert set(archive.getnames()) == {item["path"] for item in record["members"]}
        for item in record["members"]:
            data = archive.extractfile(item["path"]).read()
            assert len(data) == item["bytes"] and digest(data) == item["sha256"]


def package():
    reviews = json.loads((ROOT / "results/phase2-calibration/summary-review.json").read_text())[
        "reviews"
    ]
    indexed = {(row["candidate"], row["update"]): row for row in reviews}
    assert len(indexed) == len(reviews) == 16
    for candidate in ("summary-replay", "summary-replay-v2"):
        path = OUTPUT / candidate / "report.json"
        report = json.loads(path.read_text())
        traffic = [
            json.loads(line)
            for line in (path.parent / "memory-requests.jsonl").read_text().splitlines()
        ]
        responses = [row["response"] for row in traffic if row["event"] == "response"]
        assert len(report["updates"]) == len(responses) == 8
        assert sum(row["event"] == "request" for row in traffic) == 8
        for update, response in zip(report["updates"], responses, strict=True):
            review = indexed[candidate, update["index"]]
            assert review["review_status"] == "reviewed" and review["notes"]
            assert review["report_sha256"] == digest(path.read_bytes())
            assert review["summary_sha256"] == digest(update["summary"].encode())
            assert update["summary"] == response["choices"][0]["message"]["content"]
        for key, value in report["usage"].items():
            assert value == sum(response["usage"][key] for response in responses)
    for cohort, audit_name, count, driver in COHORTS:
        state = json.loads((ROOT / "artifacts" / cohort / "campaign.json").read_text())
        audit_path = ROOT / "results/phase2-calibration" / audit_name
        audit = json.loads(audit_path.read_text())
        assert state["status"] == "completed"
        assert state["identity"]["implementation"] == implementation_identity()
        assert state["identity"]["driver_sha256"] == digest(driver.read_bytes())
        assert audit["checks"] == "passed" and not audit["partial"]
        assert audit["episodes_checked"] == audit["reviewed_episodes"] == count
        suffix = audit_name.removeprefix("verification").removesuffix(".json")
        with tempfile.TemporaryDirectory(prefix="calibration-package-audit-") as temporary:
            output = Path(temporary) / "audit.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/audit_phase2_spike.py"),
                    "--plan",
                    f"configs/phase2-calibration/plan{suffix}.json",
                    "--annotations",
                    f"results/phase2-calibration/trace-review{suffix}.csv",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
            )
            assert output.read_bytes() == audit_path.read_bytes(), "Stale calibration audit"
    paths = set()
    temporary = ARCHIVE.with_suffix(".tmp")
    excluded = {ARCHIVE, MANIFEST, temporary}
    for directory in (
        *(f"artifacts/{cohort}" for cohort, *_ in COHORTS),
        "configs/phase2-calibration",
        "results/phase2-calibration",
    ):
        for path in (ROOT / directory).rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and not path.name.startswith(".")
                and path not in excluded
                and path.name != "partial-audit.json"
                and "__pycache__" not in path.parts
            ):
                paths.add(path)
    source = json.loads((OUTPUT / "summary-replay/report.json").read_text())["source"]
    paths.update(path for path in (ROOT / source).iterdir() if path.is_file())
    paths.add(ROOT / source / "../../manifest.json")
    paths.update((ROOT / "src/agent_tradeoffs").glob("*.py"))
    for name in (
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "README.md",
        "scripts/phase2_calibration.py",
        "scripts/audit_phase2_spike.py",
        "scripts/package_phase2_calibration.py",
        "docs/phase2-batches.md",
        "tests/test_context_budget.py",
        "tests/test_calibration_status.py",
    ):
        paths.add(ROOT / name)
    for pattern in ("*.manifest.json", "*.provenance.json"):
        paths.update((ROOT / ".cache/models/qwen3-8b-f16").glob(pattern))
    rows = []
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        for path in sorted({p.resolve() for p in paths}):
            name = str(path.relative_to(ROOT))
            data = path.read_bytes()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
            rows.append({"path": name, "bytes": len(data), "sha256": digest(data)})
    temporary.replace(ARCHIVE)
    record = {
        "kind": "reviewed_development_calibration_not_formal_evaluation",
        "episodes": 24,
        "summary_replay_calls": 16,
        "archive_sha256": digest(ARCHIVE.read_bytes()),
        "archive_bytes": ARCHIVE.stat().st_size,
        "members": rows,
    }
    verify(record)
    record["verified"] = True
    MANIFEST.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        record = json.loads(MANIFEST.read_text())
        verify(record)
    else:
        record = package()
    print(json.dumps({key: value for key, value in record.items() if key != "members"}))
