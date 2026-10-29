"""Export terminal error observations without rejudging U/A or causes (offline).

Run after the fixed 600 episode campaign. Raw timeout values distinguish an
individual request's limit from the episode budget's remaining-time clamp.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if ROOT.name == "results":
    ROOT = ROOT.parent
CAMPAIGN = ROOT / "artifacts/prompt-defense-first-wave-v1"
OUTPUT = ROOT / "results/prompt-defense-first-wave-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest_path = CAMPAIGN / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] in ["finalized", "finalized_with_errors"]
    rows = json.loads((OUTPUT / "episodes.json").read_text())
    assert len(rows) == 600 and len({r["episode_id"] for r in rows}) == 600
    assert {r["identity"] for r in rows} == {manifest["identity"]}
    assert [r["episode_id"] for r in rows] == [r["episode_id"] for r in manifest["episodes"]]
    errors = []
    for row in rows:
        if row["status"] != "error":
            continue
        assert row["utility"] is None and row["attack_success"] is None
        path = CAMPAIGN / "episodes" / row["episode_id"] / "requests.jsonl.gz"
        events = (
            [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]
            if path.exists()
            else []
        )
        requests = [e for e in events if e["event"] == "request"]
        last = requests[-1] if requests else None
        error = next((e for e in reversed(events) if e["event"] == "request_error"), None)
        if error is not None:
            assert last is not None and error["index"] == last["index"]
        errors.append(
            {
                **{
                    key: row[key]
                    for key in [
                        "episode_id",
                        "identity",
                        "pair_id",
                        "suite",
                        "arm",
                        "user_task_id",
                        "condition",
                        "attack_style",
                        "injection_task_id",
                        "duration_seconds",
                        "tool_calls",
                        "requests",
                    ]
                },
                "error_type": row["error"]["type"],
                "error_message": row["error"]["message"],
                "last_request_index": last["index"] if last else None,
                "last_request_timeout_seconds": last.get("timeout_seconds") if last else None,
                "last_request_error_type": error["error_type"] if error else None,
                "last_request_error_latency_seconds": error.get("latency_seconds")
                if error
                else None,
                "last_request_error_message": error.get("message") if error else None,
                "returned_responses": sum(e["event"] == "response" for e in events),
            }
        )
    fields = [
        "episode_id",
        "identity",
        "pair_id",
        "suite",
        "arm",
        "user_task_id",
        "condition",
        "attack_style",
        "injection_task_id",
        "duration_seconds",
        "tool_calls",
        "requests",
        "error_type",
        "error_message",
        "last_request_index",
        "last_request_timeout_seconds",
        "last_request_error_type",
        "last_request_error_latency_seconds",
        "last_request_error_message",
        "returned_responses",
    ]
    path = OUTPUT / "error-evidence.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(errors)
    receipt = {
        "campaign_identity": manifest["identity"],
        "support_sha256": sha(Path(__file__)),
        "inputs": {
            "manifest.json": sha(manifest_path),
            "episodes.json": sha(OUTPUT / "episodes.json"),
        },
        "output_sha256": sha(path),
        "terminal_errors": len(errors),
        "error_types": dict(Counter(row["error_type"] for row in errors)),
        "scope": (
            "Descriptive raw error and timeout observations; no new causal classifier, "
            "scoring, retry or inference. Missing failed-request compute remains unknown."
        ),
    }
    (OUTPUT / "error-evidence.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
