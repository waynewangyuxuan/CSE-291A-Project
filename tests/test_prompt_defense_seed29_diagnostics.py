"""Offline contracts for selected-case seed29 diagnostics; no live service calls."""

from __future__ import annotations

import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tradeoffs.prompt_defense import selected_config
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import RuntimeDeploymentChangedError, run_episode

PATH = Path(__file__).resolve().parents[1] / "scripts/run_prompt_defense_seed29_diagnostics.py"
spec = importlib.util.spec_from_file_location("seed29_diagnostics", PATH)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def test_exact_mapping_and_only_operational_paths_plus_seed_change():
    plan = d.build_plan()
    d.validate_plan(plan)
    assert len(plan["schedule"]) == 20
    assert len({e["episode_id"] for e in plan["schedule"]}) == 20
    assert sum(e["arm"] == "default" for e in plan["schedule"][::2]) == 5
    assert sum(e["condition"] == "attack" for e in plan["schedule"]) == 8
    first = d.load(d.FIRST / "manifest.json")["identity_inputs"]["plan"]
    for entry in plan["schedule"]:
        new = selected_config(plan, entry).model_dump(mode="json")
        old = selected_config(first, entry).model_dump(mode="json")
        assert new["model"].pop("seed") == 29
        assert old["model"].pop("seed") == 17
        for key in ["output_root", "runtime_metadata_path"]:
            new["runner"].pop(key)
            old["runner"].pop(key)
        assert new == old
        assert (
            d.digest(Path(entry["reference"]["result_path"])) == entry["reference"]["result_sha256"]
        )
    modified = copy.deepcopy(plan)
    modified["schedule"][0]["seed"] = 17
    with pytest.raises(ValueError, match="exact authorized"):
        d.validate_plan(modified)


class Client:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        result = {
            "choices": [
                {"message": {"content": "Done", "tool_calls": None}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        return SimpleNamespace(model_dump=lambda **kw: result)


def test_actual_first_http_payload_changes_only_seed(tmp_path):
    plan = d.build_plan()
    original = d.load(d.FIRST / "manifest.json")["identity_inputs"]["plan"]
    for entry in plan["schedule"][:2]:
        before, after = tmp_path / (entry["arm"] + "-17"), tmp_path / (entry["arm"] + "-29")
        before.mkdir()
        after.mkdir()
        run_episode(selected_config(original, entry), entry, before, "fixture", client=Client())
        run_episode(selected_config(plan, entry), entry, after, "fixture", client=Client())
        payloads = [
            json.loads((p / "requests.jsonl").read_text().splitlines()[0])["payload"]
            for p in [before, after]
        ]
        assert payloads[0].pop("seed") == 17 and payloads[1].pop("seed") == 29
        assert payloads[0] == payloads[1]


def test_identity_binds_driver_plan_protocol_and_reference_sources():
    inputs = d.identity_inputs(d.build_plan())
    files = {Path(p).name for p in inputs["frozen_files"]}
    assert {
        "run_prompt_defense_seed29_diagnostics.py",
        "test_prompt_defense_seed29_diagnostics.py",
        "plan.json",
        "protocol.json",
        "protected-inventory.json",
        "uv.lock",
        "user_tasks.py",
    } <= files
    d.verify_frozen(inputs)


def fixture(tmp_path, monkeypatch, callback):
    plan = d.build_plan()
    campaign = tmp_path / "campaign"
    directory = tmp_path / "config"
    directory.mkdir()
    for arms in plan["configurations"].values():
        for config in arms.values():
            config["runner"]["output_root"] = str(campaign)
            config["runner"]["runtime_metadata_path"] = str(campaign / "runtime/server.json")
    runtime = d.load(d.FIRST / "manifest.json")["runtime"]
    monkeypatch.setattr(d, "CAMPAIGN", campaign)
    monkeypatch.setattr(d, "DIRECTORY", directory)
    monkeypatch.setattr(d, "build_plan", lambda: plan)
    monkeypatch.setattr(d, "identity_inputs", lambda p: {"plan": p})
    monkeypatch.setattr(d, "verify_frozen", lambda _: None)
    monkeypatch.setattr(d, "verify_protected", lambda _: {"passed": True})
    monkeypatch.setattr(d, "shared_endpoint_lock", lambda _: nullcontext())
    monkeypatch.setattr(d, "ensure_idle", lambda: [{"is_processing": False}])
    monkeypatch.setattr(d, "capture_runtime", lambda _: (runtime, 68607))
    monkeypatch.setattr(d, "guard_runtime_process", lambda *_: None)
    monkeypatch.setattr(d, "run_episode", callback)
    atomic_json(directory / "plan.json", plan)
    atomic_json(directory / "identity.json", {"plan": plan})
    atomic_json(directory / "protected-inventory.json", {})
    return campaign


def error_result(config, entry, folder, identity, **kwargs):
    value = {
        **entry,
        "identity": identity,
        "status": "error",
        "utility": None,
        "attack_success": None,
        "requests": 0,
        "error": {"type": "FixtureError", "message": "retained unknown"},
    }
    atomic_json(folder / "trace.json", {"identity": identity})
    atomic_json(folder / "result.json", value)
    return value


def test_resume_preserves_all_terminal_unknowns(tmp_path, monkeypatch):
    calls = []

    def execute(*args, **kwargs):
        calls.append(args[1]["episode_id"])
        return error_result(*args, **kwargs)

    root = fixture(tmp_path, monkeypatch, execute)
    assert d.run()["finalized_episodes"] == 20 and len(calls) == 20
    assert d.run(resume=True)["finalized_episodes"] == 20 and len(calls) == 20
    first = next((root / "episodes").glob("*/result.json"))
    value = d.load(first)
    value["utility"] = True
    atomic_json(first, value)
    with pytest.raises(ValueError, match="remain unknown"):
        d.run(resume=True)


def test_interrupted_raw_attempt_is_archived_before_resume(tmp_path, monkeypatch):
    def interrupted(config, entry, folder, identity, **kw):
        (folder / "requests.jsonl").write_text("original-partial\n")
        raise KeyboardInterrupt()

    root = fixture(tmp_path, monkeypatch, interrupted)
    with pytest.raises(KeyboardInterrupt):
        d.run()
    monkeypatch.setattr(d, "run_episode", error_result)
    d.run(resume=True)
    saved = list((root / "episodes").glob("*/interrupted_attempts/*/requests.jsonl"))
    assert len(saved) == 1 and saved[0].read_text() == "original-partial\n"
    assert len(list((root / "episodes").glob("*/result.json"))) == 20


def test_runtime_change_is_preserved_and_cannot_silently_resume(tmp_path, monkeypatch):
    def changed(*args, **kwargs):
        value = error_result(*args, **kwargs)
        value["retryable"] = True
        value["error"] = {"type": "RuntimeDeploymentChangedError", "message": "changed"}
        atomic_json(args[2] / "result.json", value)
        return value

    root = fixture(tmp_path, monkeypatch, changed)
    with pytest.raises(RuntimeDeploymentChangedError):
        d.run()
    assert len(list((root / "episodes").glob("*/result.json"))) == 1
    with pytest.raises(ValueError, match="cannot silently resume"):
        d.run(resume=True)


def test_final_cleanup_waits_for_server_idle_without_new_episode(monkeypatch):
    calls = []

    def check():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("still processing cancelled request")
        return [{"is_processing": False}]

    monkeypatch.setattr(d, "ensure_idle", check)
    monkeypatch.setattr(d.time, "sleep", lambda _: None)
    assert d.wait_for_idle() == [{"is_processing": False}]
    assert len(calls) == 2


def test_resume_rejects_same_campaign_result_from_wrong_schedule_entry(tmp_path):
    entry = d.build_plan()["schedule"][0]
    error_result(None, entry, tmp_path, "fixture")
    assert d.checked_existing_result(tmp_path, "fixture", entry)["seed"] == 29
    value = d.load(tmp_path / "result.json")
    value["seed"] = 17
    atomic_json(tmp_path / "result.json", value)
    with pytest.raises(ValueError, match="schedule metadata"):
        d.checked_existing_result(tmp_path, "fixture", entry)


def test_prepared_identity_change_is_rejected_before_capture(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, error_result)
    monkeypatch.setattr(d, "identity_inputs", lambda p: {"plan": p, "changed": True})
    monkeypatch.setattr(
        d, "capture_runtime", lambda _: pytest.fail("must reject before service access")
    )
    with pytest.raises(ValueError, match="Prepared identity changed"):
        d.run()
