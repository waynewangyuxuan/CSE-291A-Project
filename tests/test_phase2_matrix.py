"""The formal matrix preserves paired scope, counterbalances order and refuses drift."""

import collections
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/phase2_matrix.py"


@pytest.fixture
def matrix(tmp_path):
    for path in (
        "uv.lock",
        "pyproject.toml",
        "results/phase2-calibration/selected-protocol.json",
        "configs/phase2-calibration/plan-before-action.json",
        *(f"configs/phase2-main-v1/runtime-{p}.toml" for p in ("f16", "q8", "q4", "summarizer")),
    ):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / path, destination)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "prepare"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return tmp_path


def test_paired_scope_and_balanced_orders_are_frozen(matrix):
    root = matrix / "configs/phase2-main-v1"
    manifest = json.loads((root / "matrix.json").read_text())
    protocol = json.loads(
        (matrix / "results/phase2-calibration/selected-protocol.json").read_text()
    )
    plans = [json.loads((matrix / b["plan"]).read_text()) for b in manifest["blocks"]]
    assert sum(p["expected_episodes"] for p in plans if p["cohort"] == "main") == 972
    assert sum(p["expected_episodes"] for p in plans if p["cohort"] == "confirmation") == 126
    assert plans[0]["task_order"] == [
        ["banking", "user_task_1"],
        ["slack", "user_task_1"],
        ["workspace", "user_task_1"],
    ]
    tasks = {cohort: set() for cohort in ("main", "confirmation")}
    positions = collections.Counter()
    for plan in plans:
        pairs = collections.Counter()
        precision_order = list(dict.fromkeys(r["precision"] for r in plan["runs"]))
        for row in plan["runs"]:
            config = row["config"]
            key = (row["suite"], row["user_task_id"])
            tasks[plan["cohort"]].add(key)
            pairs[key, row["precision"], row["policy"]] += 1
            assert config["experiment"]["mode"] == "paired"
            assert len(config["experiment"]["injection_task_ids"]) == 1
            assert config["context"]["max_context_tokens"] == 7040
            assert config["model"]["seed"] == 17
            assert config["model"]["max_tokens"] == 2048
            if row["policy"] == "summary":
                assert config["context"]["summary_max_tokens"] == 384
                assert config["context"]["summarizer"]["model"] == "qwen3-8b-f16"
        assert set(pairs.values()) == {1}
        assert len(pairs) == len(plan["task_order"]) * 9
        if plan["cohort"] == "main":
            for precision in precision_order:
                positions["precision", precision, precision_order.index(precision)] += 1
                policies = list(
                    dict.fromkeys(r["policy"] for r in plan["runs"] if r["precision"] == precision)
                )
                for policy in policies:
                    positions[precision, policy, policies.index(policy)] += 1
    assert set(positions.values()) == {6}
    development = {(s, t) for s, ts in protocol["development_tasks"].items() for t in ts}
    assert not tasks["main"] & tasks["confirmation"]
    assert not (tasks["main"] | tasks["confirmation"]) & development
    assert len(tasks["main"]) == 54 and len(tasks["confirmation"]) == 7
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.glob("*.json")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "prepare"], cwd=matrix, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.glob("*.json")}


def test_changed_plan_is_rejected_before_any_launch(matrix):
    path = matrix / "configs/phase2-main-v1/main-001.json"
    plan = json.loads(path.read_text())
    plan["runs"][0]["config"]["model"]["seed"] = 29
    path.write_text(json.dumps(plan))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "run"], cwd=matrix, capture_output=True, text=True
    )
    assert result.returncode != 0 and "Frozen block changed" in result.stderr
    assert not (matrix / "artifacts").exists()


def test_stopped_service_history_is_not_rewritten(tmp_path, monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    config_path = tmp_path / "runtime.toml"
    config_path.write_text('[runtime]\noutput_root = "history"\n')
    record = tmp_path / "history/server.json"
    record.parent.mkdir()
    record.write_text(json.dumps({"status": "stopped", "pid": 99999999, "stopped_at": "original"}))
    before = record.read_bytes()
    monkeypatch.setattr(module["psutil"], "pid_exists", lambda pid: False)
    module["stop_leftover"](config_path)
    assert record.read_bytes() == before


def test_lost_resident_service_flags_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_tradeoffs.runner._process_identity", lambda pid: None)
    module = runpy.run_path(str(SCRIPT))
    with pytest.raises(module["RuntimeDeploymentChangedError"]):
        module["verify_resident"](
            {"pid": 99999999, "process_identity": {"command": "absent"}},
            tmp_path,
            "f16-full-banking1",
        )
    record = json.loads((tmp_path / "invalid-deployment.json").read_text())
    assert record["affected_run"] == "f16-full-banking1"
    assert record["role"] == "summarizer"
