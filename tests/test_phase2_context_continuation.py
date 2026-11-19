import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "continuation_contracts", ROOT / "scripts/phase2_context_continuation.py"
)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


@pytest.fixture
def case():
    runtime = study.checkpoint.runtime_for("v1.2.2", "workspace")
    env = runtime.suite.environment_type.model_validate(
        {
            "inbox": {
                "account_email": "alice@example.com",
                "initial_emails": [
                    {
                        "id_": "0",
                        "sender": "bob@example.com",
                        "recipients": ["alice@example.com"],
                        "subject": "greeting",
                        "body": "SOURCE_ATTACK_MARKER",
                        "status": "received",
                        "read": False,
                        "timestamp": "2024-01-02T03:04:05",
                        "attachments": [],
                        "cc": [],
                        "bcc": [],
                    }
                ],
            },
            "calendar": {
                "account_email": "alice@example.com",
                "current_day": "2024-05-20",
                "initial_events": [],
            },
            "cloud_drive": {"account_email": "alice@example.com", "initial_files": []},
        }
    )
    from agent_tradeoffs.agent import load_system_message

    config = study.ExperimentConfig.model_validate(
        {
            "model": {"model": "qwen3-8b-q8", "seed": 17},
            "context": {"policy": "full", "max_context_tokens": 7040},
            "runner": {"max_tool_steps": 3},
        }
    )
    history = [
        {"role": "system", "content": [{"type": "text", "content": load_system_message()}]},
        {"role": "user", "content": [{"type": "text", "content": "Read the greeting email."}]},
    ]
    for i in range(2):
        call = {
            "function": "search_emails",
            "args": {"query": "absent"},
            "id": f"old{i}",
            "placeholder_args": None,
        }
        history += [
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {
                "role": "tool",
                "content": [{"type": "text", "content": "None"}],
                "tool_call": call,
                "tool_call_id": call["id"],
                "error": "ValueError: No emails found. Try with a different query.",
            },
        ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": f.name,
                "description": f.description,
                "parameters": f.parameters.model_json_schema(),
            },
        }
        for f in runtime.functions.functions.values()
    ]
    payload = runtime.request_payload(
        SimpleNamespace(config=config), [study.checkpoint.wire(runtime, m) for m in history], tools
    )
    snapshot = {
        "payload": payload,
        "messages": history,
        "environment": env.model_dump(mode="json"),
        "complete_group_ranges": [[2, 3], [4, 5]],
        "protected_latest_complete_group": [4, 5],
        "proposed_removed_source_message_ids": [2, 3],
        "completed_model_requests": 2,
        "request_index": 2,
        "completed_tool_steps": 2,
        "tool_errors": 2,
    }
    return {
        "id": "synthetic-contract",
        "snapshot": snapshot,
        "boundary": 4,
        "archive": [{"message_id": i, **m} for i, m in enumerate(payload["messages"][2:4], 2)],
        "initial_environment": copy.deepcopy(snapshot["environment"]),
        "attack_payloads": {"email": "SOURCE_ATTACK_MARKER"},
        "config": study.plain(config),
    }


class FakeClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.chat = SimpleNamespace(completions=self)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        output = next(self.outputs)
        response = {
            "choices": [{"message": output, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }
        return SimpleNamespace(model_dump=lambda **_: response)


class FakeCounter:
    seconds = 0

    def count(self, payload):
        return 100


def tool_output(call_id="new0"):
    return {
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "get_unread_emails", "arguments": "{}"},
            }
        ],
    }


def agent_for(tmp_path, case, arm, outputs):
    runtime = study.checkpoint.runtime_for("v1.2.2", "workspace")
    config = study.model_config(case, 41, tmp_path)
    client = FakeClient(outputs)
    agent = study.ContinuationAgent(config, tmp_path, case["attack_payloads"], client=client)
    agent.counter = FakeCounter()
    agent.memory = study.OneShotMemory(
        case, arm, "[m3] Search failed." if arm == "summary" else "", tmp_path, lambda: None
    )
    return agent, runtime, client


@pytest.mark.parametrize("arm", ["full", "truncate", "summary"])
def test_real_localagent_loop_restores_state_and_preserves_later_injection(tmp_path, case, arm):
    agent, runtime, client = agent_for(
        tmp_path, case, arm, [tool_output(), {"content": "Read the greeting."}]
    )
    agent.continue_from(case, runtime)
    requests = [r for r in study.lines(tmp_path / "requests.jsonl") if r["event"] == "request"]
    expected = copy.deepcopy(case["snapshot"]["payload"])
    expected.update(seed=41, messages=study.transform(case, arm, agent.memory.summary))
    assert requests[0]["payload"] == expected
    assert [r["index"] for r in requests] == [2, 3]
    assert requests[1]["payload"]["messages"][-1]["content"].find("SOURCE_ATTACK_MARKER") >= 0
    assert study.plain(agent.messages[:6]) == case["snapshot"]["messages"]
    assert len(client.requests) == 2
    assert agent.tool_calls == 3
    assert agent.initial_environment["inbox"]["emails"]["0"]["read"] is False
    assert agent.environment.inbox.emails["0"].read is True
    assert agent.environment.inbox.emails["0"] is agent.environment.inbox.initial_emails[0]
    assert agent.environment.inbox.emails["0"] is agent.environment.inbox.received[0]
    assert agent.memory.boundary == (2 if arm == "full" else 4)
    prefixes = study.lines(tmp_path / "prefixes.jsonl")
    assert len(prefixes) == 2
    assert prefixes[0]["transcript"] == case["snapshot"]["payload"]["messages"]
    assert agent.memory.summary == ("[m3] Search failed." if arm == "summary" else "")


def test_original_tool_limit_includes_replayed_calls(tmp_path, case):
    from agent_tradeoffs.agent import ToolStepLimitError

    agent, runtime, _ = agent_for(tmp_path, case, "full", [tool_output(), tool_output("new1")])
    with pytest.raises(ToolStepLimitError, match="Exceeded 3"):
        agent.continue_from(case, runtime)
    assert agent.tool_calls == 3
    assert len([m for m in agent.messages[6:] if m["role"] == "tool"]) == 1
    assert agent.environment.inbox.emails["0"].read is True


def test_fixed_prompt_mismatch_rejects_before_model_call(tmp_path, case):
    case["snapshot"]["messages"][0]["content"][0]["content"] = "changed"
    agent, runtime, client = agent_for(tmp_path, case, "full", [])
    with pytest.raises(ValueError, match="different fixed prompt"):
        agent.continue_from(case, runtime)
    assert client.requests == []


def test_one_shot_budget_overflow_preserves_prefix_without_model_call(tmp_path, case):
    agent, runtime, client = agent_for(tmp_path, case, "truncate", [])
    agent.counter.count = lambda payload: 4993
    with pytest.raises(study.ContextBudgetExceededError, match="no further compaction"):
        agent.continue_from(case, runtime)
    assert client.requests == []
    assert (
        study.lines(tmp_path / "prefixes.jsonl")[0]["transcript"]
        == case["snapshot"]["payload"]["messages"]
    )
    assert agent.memory.events[-1]["status"] == "overflow"


def test_transform_rejects_changed_original_history_or_incomplete_group(case):
    transcript = copy.deepcopy(case["snapshot"]["payload"]["messages"])
    transcript[3]["content"] = "rewritten"
    with pytest.raises(ValueError, match="Original history changed"):
        study.transform(case, "truncate", transcript=transcript)
    case["snapshot"]["complete_group_ranges"][-1] = [4]
    with pytest.raises(ValueError, match="boundaries differ"):
        study.transform(case, "truncate")


def test_removed_prefix_and_latest_group_are_identical_across_interventions(case):
    original = case["snapshot"]["payload"]["messages"]
    short = study.transform(case, "truncate")
    summary = study.transform(case, "summary", "[m3] Search failed.")
    assert short == original[:2] + original[4:]
    assert summary[:2] == original[:2]
    assert summary[4:] == original[4:]
    assert json.loads(summary[3]["content"]) == {
        "kind": "summary_of_past_messages",
        "source_message_ids": [2, 3],
        "memory": "[m3] Search failed.",
    }
    assert study.transform(case, "full") == original


def test_environment_roundtrip_rejects_broken_alias_state(case):
    runtime = study.checkpoint.runtime_for("v1.2.2", "workspace")
    snapshot = copy.deepcopy(case["snapshot"]["environment"])
    snapshot["inbox"]["emails"]["0"]["read"] = True
    with pytest.raises(ValueError, match="Typed initial environment changed"):
        study.restored_environment(runtime, snapshot)


def test_repeated_or_crashed_attempt_never_retries_model(tmp_path):
    study.save(
        tmp_path / "budget.json",
        {
            "attempts": {
                "interrupted": {
                    "charged_seconds": 280,
                    "reserved_seconds": 280,
                    "status": "reserved",
                }
            }
        },
    )
    with pytest.raises(ValueError, match="no automatic retries"):
        with study.reservation(tmp_path, "interrupted", 280):
            pytest.fail("must not enter an existing attempt")
    assert study.read(tmp_path / "budget.json")["attempts"]["interrupted"]["charged_seconds"] == 280


def test_total_budget_rejects_before_work(tmp_path):
    study.save(tmp_path / "budget.json", {"attempts": {"earlier": {"charged_seconds": 5200}}})
    with pytest.raises(ValueError, match="budget exhausted"):
        with study.reservation(tmp_path, "next", 280):
            pytest.fail("must not begin over budget")


def test_outside_or_symlink_output_rejected_without_writes(tmp_path):
    (tmp_path / "artifacts").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "artifacts/phase2-context-continuation-link"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        study.output_root(tmp_path, alias)
    with pytest.raises(ValueError, match="separate"):
        study.output_root(tmp_path, tmp_path / "results")
    assert list(outside.iterdir()) == []


def test_shared_endpoint_lock_rejects_another_evaluator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with study.endpoint_evaluation_lock("http://127.0.0.1:8087/v1"):
        with pytest.raises(ValueError, match="Another evaluator"):
            with study.endpoint_evaluation_lock("http://localhost:8087/v1"):
                pytest.fail("concurrent evaluator entered")


def test_evidence_mutation_rejected(tmp_path):
    study.save(tmp_path / "result.json", {"status": "error", "utility": None})
    study.seal(tmp_path)
    study.verify_seal(tmp_path)
    study.save(tmp_path / "result.json", {"status": "completed", "utility": True})
    with pytest.raises(ValueError, match="Evidence changed"):
        study.verify_seal(tmp_path)


def test_frozen_selection_rejects_precision_and_seed_expansion():
    spec = study.read(study.SPEC)
    study.validate_spec(spec)
    spec["seeds"].append(79)
    with pytest.raises(ValueError, match="three fresh seeds"):
        study.validate_spec(spec)


def test_missing_prepare_inputs_never_change_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts/phase2-context-continuation-test"
    output.mkdir(parents=True)
    (output / "plan.json").write_text('{"preserve": true}')
    assert (
        study.main(["prepare", "--source-root", str(tmp_path), "--output-root", str(output)]) == 1
    )
    assert study.read(output / "plan.json") == {"preserve": True}


def test_source_drift_rejected_before_work(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("original")
    plan = {
        "source_root": str(tmp_path),
        "driver_sha256": study.sha256_file(study.WRAPPER),
        "spec_sha256": study.sha256_file(study.SPEC),
        "source_sha256": {"source.txt": study.sha256_file(source)},
    }
    study.verify_sources(plan)
    source.write_text("changed")
    with pytest.raises(ValueError, match="Source drift"):
        study.verify_sources(plan)


def test_changed_model_parameters_rejected_before_actual_request(tmp_path, case):
    case["config"]["model"]["temperature"] = 0.1
    agent, runtime, client = agent_for(tmp_path, case, "full", [])
    with pytest.raises(ValueError, match="sampling parameters"):
        agent.continue_from(case, runtime)
    assert client.requests == []


def test_tool_evidence_links_restored_state_to_actual_final_state(tmp_path, case):
    agent, runtime, _ = agent_for(tmp_path, case, "full", [tool_output(), {"content": "Done."}])
    runtime = SimpleNamespace(
        **{
            **runtime.__dict__,
            "functions": study.LoggedFunctions(runtime.functions, tmp_path, lambda: None),
        }
    )
    agent.continue_from(case, runtime)
    trace = study.plain(agent.trace())
    result = {
        "status": "completed",
        "new_tool_steps": 1,
        "replayed_tool_steps": 2,
        "total_tool_steps": 3,
    }
    study.audit_tools(tmp_path, case, trace, result)
    trace["final_environment"]["inbox"]["emails"]["0"]["read"] = False
    with pytest.raises(ValueError, match="Final environment differs"):
        study.audit_tools(tmp_path, case, trace, result)


def synthetic_summary(tmp_path, case, monkeypatch):
    class SyntheticServices(study.MemoryServices):
        def __init__(self, settings, directory, remaining):
            super().__init__(settings, directory, remaining)
            self.client = FakeClient([{"content": "[m3] Search failed."}])

            def count(payload):
                study.append_jsonl(
                    directory / "summary-context.jsonl",
                    {"payload_sha256": study.stable_hash(payload), "prompt_tokens": 100},
                )
                return 100

            self.counter = SimpleNamespace(count=count)

    monkeypatch.setattr(study, "MemoryServices", SyntheticServices)
    plan = {"cases": [case, case]}
    study.run_summary(plan, tmp_path, case, {"synthetic_contract_only": True}, lambda: None)
    return tmp_path / "summaries" / case["id"]


def test_summary_raw_response_and_source_mapping_persist_before_use(tmp_path, case, monkeypatch):
    directory = synthetic_summary(tmp_path, case, monkeypatch)
    assert study.summary_text(directory, case) == "[m3] Search failed."
    study.verify_seal(directory)
    assert study.read(directory / "source.json")["call_id_mapping"] == [
        {"original": "old0", "canonical": "message_2_call_0"}
    ]
    result = study.read(directory / "result.json")
    result["summary"] = "invented replacement"
    study.save(directory / "result.json", result)
    with pytest.raises(ValueError, match="persisted raw response"):
        study.summary_text(directory, case)


def test_summary_source_rewrite_rejected_even_with_unchanged_response(tmp_path, case, monkeypatch):
    directory = synthetic_summary(tmp_path, case, monkeypatch)
    source = study.read(directory / "source.json")
    source["archive"][1]["content"] = "rewritten"
    study.save(directory / "source.json", source)
    with pytest.raises(ValueError, match="Summary source differs"):
        study.summary_text(directory, case)


def test_invalid_model_output_is_retained_unknown_without_retry(tmp_path, case, monkeypatch):
    synthetic_summary(tmp_path, case, monkeypatch)
    case.update(
        injections={"email": "SOURCE_ATTACK_MARKER"},
        injection_goal="synthetic goal",
        episode_id="user_task_14__injection_task_0__attack",
        task="user_task_14",
    )
    plan = {"cases": [case, case]}
    unit = {"case": case["id"], "seed": 41, "arm": "full"}
    client = FakeClient([{"content": ""}])
    original = study.ContinuationAgent

    def create(config, directory, injections):
        agent = original(config, directory, injections, client=client)
        agent.counter = FakeCounter()
        return agent

    monkeypatch.setattr(study, "ContinuationAgent", create)
    deployment = {"semantic_identity": {"synthetic_contract_only": True}}
    study.run_continuation(plan, tmp_path, case, unit, deployment, lambda: None)
    directory = tmp_path / "continuations" / study.unit_name(unit)
    result = study.read(directory / "result.json")
    assert result["status"] == "error"
    assert result["utility"] is None and result["attack_success"] is None
    assert result["error"]["type"] == "InvalidModelOutputError"
    assert result["requests"] == 1 and result["new_tool_steps"] == 0
    assert result["replayed_tool_steps"] == 2
    before = study.inventory(directory)
    study.run_continuation(plan, tmp_path, case, unit, deployment, lambda: None)
    assert study.inventory(directory) == before
    assert len(client.requests) == 1


def test_boundary_six_summary_enumerates_every_source_message(case):
    snapshot = case["snapshot"]
    extra = copy.deepcopy(snapshot["messages"][-2:])
    extra[0]["tool_calls"][0]["id"] = "old2"
    extra[1]["tool_call"]["id"] = "old2"
    extra[1]["tool_call_id"] = "old2"
    snapshot["messages"] += extra
    runtime = study.checkpoint.runtime_for("v1.2.2", "workspace")
    snapshot["payload"]["messages"] = [
        study.checkpoint.wire(runtime, m) for m in snapshot["messages"]
    ]
    snapshot.update(
        complete_group_ranges=[[2, 3], [4, 5], [6, 7]],
        protected_latest_complete_group=[6, 7],
        proposed_removed_source_message_ids=[2, 3, 4, 5],
        completed_model_requests=3,
    )
    view = study.transform(case, "summary", "[m3] First error. [m5] Second error.")
    assert json.loads(view[3]["content"])["source_message_ids"] == [2, 3, 4, 5]
    assert view[4:] == snapshot["payload"]["messages"][6:]
    assert view[:2] == snapshot["payload"]["messages"][:2]


def test_retention_uses_last_sent_payload_not_archived_history(tmp_path, case):
    case["snapshot"]["payload"]["messages"][3]["content"] = "SOURCE_ATTACK_MARKER"
    payload = {**case["snapshot"]["payload"], "messages": study.transform(case, "truncate")}
    assert study.last_sent_retention(tmp_path, case) is None
    study.append_jsonl(
        tmp_path / "requests.jsonl", {"event": "request", "index": 2, "payload": payload}
    )
    visible = study.last_sent_retention(tmp_path, case)
    assert visible["literal_payload_vectors"] == []
    assert study.retention(case, case["snapshot"]["payload"]["messages"])[
        "literal_payload_vectors"
    ] == ["email"]
    payload = copy.deepcopy(payload)
    payload["messages"].append({"role": "tool", "content": "SOURCE_ATTACK_MARKER"})
    study.append_jsonl(
        tmp_path / "requests.jsonl", {"event": "request", "index": 3, "payload": payload}
    )
    visible = study.last_sent_retention(tmp_path, case)
    assert visible["request_index"] == 3
    assert visible["literal_payload_vectors"] == ["email"]
