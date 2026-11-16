"""Publish a completed formal-block review only after its evidence and full gate pass."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from phase2_collect import verify_archive
from phase2_matrix import load_block, read

from agent_tradeoffs.analysis import FAILURE_CATEGORIES
from agent_tradeoffs.runtime import sha256_file

ARTIFACTS = Path("artifacts/phase2-main-v1")
PUBLIC = Path("results/phase2-main-v1/reviews")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def publish(block):
    require(re.fullmatch(r"(main|confirmation)-\d{3}", block), "Invalid formal block ID")
    _, plan, plan_path = load_block(block)
    root = ARTIFACTS / block
    receipt = read(root / "review-completion.json")
    require(receipt["status"] == "reviewed" and receipt["block"] == block, "Incomplete receipt")
    draft, findings = root / "review-draft.csv", root / "review-findings.md"

    def verify_receipt():
        require(sha256_file(draft) == receipt["csv_sha256"], "Review CSV changed after receipt")
        require(
            sha256_file(findings) == receipt["findings_sha256"], "Findings changed after receipt"
        )

    verify_receipt()
    with draft.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(
        len(rows)
        == len({(r["run_id"], r["episode_id"]) for r in rows})
        == receipt["episodes"]
        == plan["expected_episodes"],
        "Review is incomplete or duplicated",
    )
    allowed = {r["run_id"] for r in plan["runs"]}
    archive_dir = ARTIFACTS / "archives"
    inventory = read(archive_dir / f"{block}.manifest.json")
    verify_archive(archive_dir / f"{block}.tar.gz", inventory)
    frozen = {m["path"]: m["sha256"] for m in inventory["members"]}
    for row in rows:
        require(
            row["review_status"] == "reviewed"
            and row["reviewer"].strip()
            and row["evidence"].strip(),
            "Unreviewed row or missing reviewer/evidence",
        )
        require(
            row["refusal"] in {"yes", "no", "unclear"}
            and row["failure_category"] in FAILURE_CATEGORIES,
            "Invalid review label",
        )
        labels = {row["failure_category"], *re.split(r"[|;]", row["secondary_labels"])}
        require(
            "unnecessary_refusal" not in labels or row["refusal"] == "yes",
            "Unnecessary refusal requires an observed refusal",
        )
        require(row["run_id"] in allowed, "Review run is absent from frozen plan")
        directory = root / "runs" / row["run_id"]
        manifest = read(directory / "manifest.json")
        require(
            row["episode_id"] in {e["episode_id"] for e in manifest["episodes"]},
            "Review episode is absent from run manifest",
        )
        path = directory / "episodes" / row["episode_id"]
        result = read(path / "result.json")
        require(row["identity"] == result["identity"] == manifest["identity"], "Stale identity")
        evidence = row["evidence"]
        require(
            f"source=runs/{row['run_id']}/episodes/{row['episode_id']}/" in evidence,
            "Review lacks its exact source path",
        )
        for name in (
            "trace.json.gz",
            "requests.jsonl.gz",
            "context-requests.jsonl.gz",
            "result.json",
            "compression.json",
            "memory-requests.jsonl.gz",
            "summary-context.jsonl.gz",
        ):
            source = path / name
            optional = name in {"memory-requests.jsonl.gz", "summary-context.jsonl.gz"}
            if optional and not source.exists():
                continue
            digest = sha256_file(source)
            require(frozen.get(str(source)) == digest, f"Evidence changed since archive: {source}")
            content = gzip.decompress(source.read_bytes()) if name.endswith(".gz") else None
            if optional and not content.strip():
                continue
            require(f"{name} sha256={digest}" in evidence, f"Missing raw evidence hash: {source}")
            if content is not None:
                require(
                    f"{name} decompressed_sha256={hashlib.sha256(content).hexdigest()}" in evidence,
                    f"Missing decompressed evidence hash: {source}",
                )
    gate = root / "full-review-verification.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/audit_phase2_spike.py",
            "--plan",
            str(plan_path),
            "--annotations",
            str(draft),
            "--output",
            str(gate),
        ],
        check=True,
    )
    verification = read(gate)
    require(
        verification["checks"] == "passed"
        and verification["episodes_checked"] == plan["expected_episodes"],
        "Complete review gate did not pass",
    )
    verify_receipt()
    payloads = {
        PUBLIC / f"{block}.csv": draft.read_bytes(),
        PUBLIC / f"{block}.md": findings.read_bytes(),
        PUBLIC / f"{block}.verification.json": gate.read_bytes(),
        PUBLIC / f"{block}.review-source.json": (
            json.dumps(receipt | {"artifact_root": str(root)}, indent=2) + "\n"
        ).encode(),
    }
    for path, data in payloads.items():
        require(not path.exists() or path.read_bytes() == data, f"Public review differs: {path}")
    for path, data in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(data)
    return {"block": block, "reviewed": len(rows), "hashes_and_full_gate": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", required=True)
    print(json.dumps(publish(parser.parse_args().block)))
