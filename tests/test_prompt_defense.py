"""Offline contracts for the new prompt arm and adjacent-pair collection."""

from __future__ import annotations

import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent_tradeoffs import prompt_defense as experiment
from agent_tradeoffs import runner
from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.prompt_defense_analysis import actual_sources, paired_effect, summarize
from agent_tradeoffs.prompt_policy import SOURCE_BOUNDARY_V1, prompt_identity, system_prompt
from agent_tradeoffs.reporting import atomic_json


class ScriptedClient:
    def __init__(self):
        self.index = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        message = {"content": "Done", "tool_calls": None}
        if self.index == 0:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-balance",
                        "type": "function",
                        "function": {"name": "get_balance", "arguments": "{}"},
                    }
                ],
            }
        self.index += 1
        value = {
            "choices": [
                {"message": message, "finish_reason": "tool_calls" if self.index == 1 else "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
        return SimpleNamespace(model_dump=lambda **kwargs: value)


def payloads(directory):
    return [
        row["payload"]
        for row in map(json.loads, (directory / "requests.jsonl").read_text().splitlines())
        if row["event"] == "request"
    ]


def test_default_request_payload_bytes_match_unchanged_original_agent(tmp_path, monkeypatch):
    original = experiment.ORIGINAL / "src/agent_tradeoffs/agent.py"
    spec = importlib.util.spec_from_file_location("original_baseline_agent", original)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = ExperimentConfig.model_validate({"experiment": {"mode": "clean"}})
    episode = runner.plan_episodes(config)[0]
    old, new, defense = (tmp_path / name for name in ("old", "new", "defense"))
    for directory in (old, new, defense):
        directory.mkdir()
    current = runner.LocalAgent
    monkeypatch.setattr(runner, "LocalAgent", module.LocalAgent)
    runner.run_episode(config, episode, old, "fixture", client=ScriptedClient())
    monkeypatch.setattr(runner, "LocalAgent", current)
    runner.run_episode(config, episode, new, "fixture", client=ScriptedClient())
    protected = config.model_copy(
        update={"prompt": config.prompt.model_copy(update={"policy": "source_boundary_v1"})}
    )
    runner.run_episode(protected, episode, defense, "fixture", client=ScriptedClient())
    assert json.dumps(payloads(old)) == json.dumps(payloads(new))
    expected = copy.deepcopy(payloads(new))
    for payload in expected:
        payload["messages"][0]["content"] += "\n\n" + SOURCE_BOUNDARY_V1
    assert expected == payloads(defense)


def test_prompt_is_typed_and_full_resolved_text_is_bound_to_identity():
    config = ExperimentConfig()
    protected = ExperimentConfig.model_validate({"prompt": {"policy": "source_boundary_v1"}})
    assert config.identity_config()["resolved_system_prompt"] == prompt_identity("default")
    assert protected.identity_config()["resolved_system_prompt"]["text"] == system_prompt(
        "source_boundary_v1"
    )
    assert config.fingerprint() != protected.fingerprint()
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate({"prompt": {"policy": "arbitrary"}})


def test_exact_600_schedule_and_unchanged_configs():
    plan = experiment.build_plan()
    experiment.validate_plan(plan)
    schedule = plan["schedule"]
    assert len(schedule) == len({row["episode_id"] for row in schedule}) == 600
    assert sum(row["arm"] == "default" for row in schedule) == 300
    assert sum(row["condition"] == "clean" for row in schedule) == 120
    assert len({(row["suite"], row["user_task_id"]) for row in schedule}) == 60
    assert sum(schedule[index]["arm"] == "default" for index in range(0, 600, 2)) == 150
    for index in range(0, 600, 2):
        a, b = schedule[index : index + 2]
        assert a["pair_id"] == b["pair_id"]
        assert {a["arm"], b["arm"]} == set(experiment.ARMS)
        ca, cb = (experiment.selected_config(plan, row).model_dump(mode="json") for row in (a, b))
        ca.pop("prompt")
        cb.pop("prompt")
        assert ca == cb
    changed = copy.deepcopy(plan)
    changed["schedule"][2] = changed["schedule"][0]
    with pytest.raises(ValueError, match="Schedule changed"):
        experiment.validate_plan(changed)
    changed = copy.deepcopy(plan)
    changed["configurations"]["banking"]["default"]["model"]["temperature"] = 0.2
    with pytest.raises(ValueError, match="frozen first-wave"):
        experiment.validate_plan(changed)


def test_driver_and_protocol_are_bound_to_identity():
    plan = experiment.build_plan()
    identity = experiment.frozen_identity(plan, experiment.ROOT / "configs/prompt-defense-v1")
    filenames = {Path(path).name for path in identity["frozen_files"]}
    assert {
        "prompt_defense.py",
        "plan.json",
        "protocol.json",
        "source-inventory.json",
        "task-manifest.json",
        "uv.lock",
    } <= filenames
    experiment.verify_frozen(identity)


def fixture_driver(tmp_path, monkeypatch):
    plan = experiment.build_plan()
    plan["schedule"] = plan["schedule"][:2]
    for suite in plan["configurations"].values():
        for config in suite.values():
            config["runner"]["output_root"] = str(tmp_path / "campaign")
    plan_path = tmp_path / "plan.json"
    atomic_json(plan_path, plan)
    monkeypatch.setattr(experiment, "validate_plan", lambda plan: None)
    monkeypatch.setattr(experiment, "frozen_identity", lambda plan, path: {"plan": plan})
    monkeypatch.setattr(experiment, "verify_frozen", lambda inputs: None)
    monkeypatch.setattr(experiment, "shared_endpoint_lock", lambda plan: nullcontext())
    runtime = {
        "semantic_identity": {"fixture": 1},
        "metadata_snapshot": {"process_identity": {"fixture": "one"}},
    }
    from agent_tradeoffs.config import stable_hash

    plan["required_semantic_runtime_sha256"] = stable_hash(runtime["semantic_identity"])
    atomic_json(plan_path, plan)
    monkeypatch.setattr(experiment, "capture_runtime", lambda config: (runtime, 123))
    guards = []
    monkeypatch.setattr(experiment, "guard_runtime_process", lambda *args: guards.append(args))
    calls = []

    def fake_episode(config, entry, directory, identity, *, runtime_guard):
        assert config.runner.server_pid == 123
        runtime_guard()
        calls.append(entry["episode_id"])
        atomic_json(directory / "trace.json", {"identity": identity, "fixture": True})
        result = {
            **entry,
            "identity": identity,
            "status": "error",
            "utility": None,
            "attack_success": None,
            "requests": 0,
            "error": {"type": "FixtureTimeout", "message": "offline"},
        }
        atomic_json(directory / "result.json", result)
        return result

    monkeypatch.setattr(experiment, "run_episode", fake_episode)
    return plan_path, calls, guards


def test_resume_preserves_unknown_errors_and_verifies_compressed_trace(tmp_path, monkeypatch):
    path, calls, guards = fixture_driver(tmp_path, monkeypatch)
    manifest = experiment.run(path)
    assert manifest["status"] == "finalized_with_errors"
    assert len(calls) == 2 and len(guards) == 4
    experiment.run(path, resume=True)
    assert len(calls) == 2
    directory = tmp_path / "campaign/episodes" / calls[0]
    zipped = directory / "trace.json.gz"
    zipped.write_bytes(zipped.read_bytes() + b"corruption")
    with pytest.raises((ValueError, OSError)):
        experiment.run(path, resume=True)


def test_resume_refuses_identity_change(tmp_path, monkeypatch):
    path, _, _ = fixture_driver(tmp_path, monkeypatch)
    experiment.run(path)
    monkeypatch.setattr(experiment, "frozen_identity", lambda plan, path: {"changed": True})
    with pytest.raises(ValueError, match="Resume identity"):
        experiment.run(path, resume=True)


def test_interrupted_request_remnants_are_archived(tmp_path, monkeypatch):
    path, _, _ = fixture_driver(tmp_path, monkeypatch)
    plan = json.loads(path.read_text())
    root = tmp_path / "campaign"
    root.mkdir()
    from agent_tradeoffs.config import stable_hash

    runtime = {
        "semantic_identity": {"fixture": 1},
        "metadata_snapshot": {"process_identity": {"fixture": "one"}},
    }
    inputs = {"plan": plan}
    identity = stable_hash({"inputs": inputs, "runtime": runtime["semantic_identity"]})
    atomic_json(root / "manifest.json", {"identity": identity, "status": "interrupted"})
    episode = root / "episodes" / plan["schedule"][0]["episode_id"]
    episode.mkdir(parents=True)
    (episode / "requests.jsonl").write_text("interrupted raw bytes")
    experiment.run(path, resume=True)
    archived = list((episode / "interrupted_attempts").glob("*/requests.jsonl"))
    assert len(archived) == 1 and archived[0].read_text() == "interrupted raw bytes"


def row(task, arm, condition, utility, attack_success=None, pair=0):
    return {
        "suite": "fixture",
        "user_task_id": task,
        "arm": arm,
        "condition": condition,
        "utility": utility,
        "attack_success": attack_success,
        "pair_id": pair,
        "status": "error" if utility is None else "completed",
        "duration_seconds": 1,
    }


def test_eligibility_own_controls_and_common_intersection():
    rows = [
        row("a", "default", "clean", True, pair=0),
        row("a", "source_boundary_v1", "clean", False, pair=0),
        row("b", "default", "clean", True, pair=1),
        row("b", "source_boundary_v1", "clean", True, pair=1),
        row("a", "default", "attack", False, True, pair=2),
        row("a", "source_boundary_v1", "attack", False, False, pair=2),
        row("b", "default", "attack", False, True, pair=3),
        row("b", "source_boundary_v1", "attack", None, None, pair=3),
    ]
    summary = summarize(rows)
    assert summary["common_clean_success_tasks"] == 1
    a, b = (summary["arms"][arm] for arm in experiment.ARMS)
    assert a["attack_success_own_clean_eligible"]["denominator"] == 2
    assert b["attack_success_own_clean_eligible"]["denominator"] == 1
    assert a["attack_success_common_clean_eligible"]["denominator"] == 1
    assert b["attack_success_common_clean_eligible"]["unknown_outcomes"] == 1
    assert b["attack_success"]["known_score_denominator"] == 1
    assert b["attack_success"]["known_score_rate"] == 0
    assert summary["paired"]["attack_success"]["unknown_bounds"] == [-1, -0.5]
    with pytest.raises(ValueError, match="Incomplete arm"):
        paired_effect(rows[:1], "utility")


def test_actual_source_uses_sent_tool_role_and_function_provenance():
    events = [
        {
            "event": "request",
            "index": 0,
            "payload": {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"file_path":"bill.txt"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call-1", "content": "TODO: do thing"},
                ]
            },
        }
    ]
    matches = actual_sources(
        events * 2,
        {"v": "TODO: do thing"},
        {"v": {"yaml_field_paths": [["filesystem", "files", "bill.txt"]]}},
    )
    assert len(matches) == 1 and matches[0]["function"] == "read_file"
    assert matches[0]["role"] == "tool"


def test_identical_payload_vectors_remain_ambiguous():
    events = [
        {
            "event": "request",
            "index": 0,
            "payload": {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "get_webpage",
                                    "arguments": '{"url":"first.example"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call-1", "content": "TODO: do thing"},
                ]
            },
        }
    ]
    matches = actual_sources(
        events,
        {"first": "TODO: do thing", "second": "TODO: do thing"},
        {"first": {"field": "first.example"}, "second": {"field": "second.example"}},
    )
    assert len(matches) == 1
    assert matches[0]["vector_attribution"] == "ambiguous_payload_match"
    assert matches[0]["matching_vector_candidates"] == ["first", "second"]
    assert matches[0]["function"] == "get_webpage"


def test_shared_lock_conflicts_with_original_evaluator_lock(tmp_path):
    import fcntl

    from agent_tradeoffs.config import stable_hash

    plan = experiment.build_plan()
    plan["shared_endpoint_lock_root"] = str(tmp_path)
    lock = tmp_path / (stable_hash({"host": "loopback", "port": 8080}) + ".lock")
    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still owns"):
            with experiment.shared_endpoint_lock(plan):
                pytest.fail("must never acquire an already held endpoint lock")
    with experiment.shared_endpoint_lock(plan):
        pass
