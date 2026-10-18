"""Offline file guard and idle-only HTTP checks after the 600 episode driver exits."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.prompt_defense import (
    DEPLOYMENT_SHA256,
    NAME,
    ROOT,
    digest,
    shared_endpoint_lock,
    verify_frozen,
)
from agent_tradeoffs.runtime import _process_identity

ORIGINAL = Path("/Users/waynewang/CSE-291A-Project")
OUTPUT = ROOT / "results/prompt-defense-first-wave-v1"
INVENTORY = ORIGINAL / "artifacts/phase1-error-retries/protected-original-inventory.json"
EXPECTED_INVENTORY = "ed799722eb111cf16d3e2074b1f56475ef5a6840ccf06644fff24db62eed4eda"


def main():
    manifest_path = ROOT / "artifacts" / NAME / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    queue = json.loads((ROOT / "artifacts/prompt-defense-queue/state.json").read_text())
    assert manifest["status"] == "finalized_with_errors" and manifest["finalized_episodes"] == 600
    assert queue["driver_exit_code"] == queue["analysis_exit_code"] == 0
    verify_frozen(manifest["identity_inputs"])
    assert stable_hash(manifest["runtime"]["semantic_identity"]) == DEPLOYMENT_SHA256
    assert digest(INVENTORY) == EXPECTED_INVENTORY
    records = json.loads(INVENTORY.read_text())["files"]
    assert len(records) == 2631
    for record in records:
        path = ORIGINAL / record["path"]
        assert path.stat().st_size == record["bytes"] and digest(path) == record["sha256"], path
    metadata = manifest["runtime"]["metadata_snapshot"]
    with shared_endpoint_lock(manifest["identity_inputs"]["plan"]):
        process = _process_identity(metadata["pid"])
        assert process == metadata["process_identity"]
        # These management GETs never submit inference.
        with urlopen("http://127.0.0.1:8080/slots", timeout=10) as response:
            slots = json.loads(response.read())
        with urlopen("http://127.0.0.1:8080/metrics", timeout=10) as response:
            metrics = response.read().decode()
        assert slots and all(slot["is_processing"] is False for slot in slots)
        gauges = {}
        for line in metrics.splitlines():
            if line and not line.startswith("#"):
                name, value = line.rsplit(" ", 1)
                if name in ["llamacpp:requests_processing", "llamacpp:requests_deferred"]:
                    gauges[name] = float(value)
        assert set(gauges) == {"llamacpp:requests_processing", "llamacpp:requests_deferred"}
        assert all(value == 0 for value in gauges.values())
    report = {
        "status": "passed_complete",
        "captured_at": datetime.now(UTC).isoformat(),
        "campaign_identity": manifest["identity"],
        "manifest_sha256": digest(manifest_path),
        "support_sha256": digest(Path(__file__)),
        "frozen_inputs_unchanged": True,
        "protected_original_files": len(records),
        "protected_inventory_path": str(INVENTORY),
        "protected_inventory_sha256": digest(INVENTORY),
        "driver_exit_code": queue["driver_exit_code"],
        "analysis_exit_code": queue["analysis_exit_code"],
        "semantic_runtime_sha256": DEPLOYMENT_SHA256,
        "server_pid": metadata["pid"],
        "process_identity": process,
        "slots": slots,
        "processing_gauges": gauges,
        "endpoint_lock_acquired_then_released": True,
        "inference_submitted_by_this_guard": False,
    }
    (OUTPUT / "final-integrity-guard.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ["status", "protected_original_files", "server_pid", "processing_gauges"]
            }
        )
    )


if __name__ == "__main__":
    main()
