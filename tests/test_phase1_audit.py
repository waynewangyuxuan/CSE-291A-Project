"""Tiny synthetic persistence fixtures, never treated as model measurements."""

import importlib.util
import json
from pathlib import Path

import pytest

from agent_tradeoffs.campaign import compact_run_artifacts, load_campaign_plan
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import implementation_identity, plan_episodes
from agent_tradeoffs.runtime import load_runtime_config, server_command

_SPEC = importlib.util.spec_from_file_location(
    "phase1_result_audit", Path(__file__).parents[1] / "scripts/audit_phase1_results.py"
)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


@pytest.fixture
def evidence(tmp_path):
    extra = {
        "chat_template_kwargs": {"enable_thinking": False},
        "cache_prompt": False,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
    }
    runtime_path = tmp_path / "runtime.toml"
    runtime_path.write_text(
        '[runtime]\nmodel_alias="qwen3-8b-f16"\ncontext_size=32768\n'
        '[model_artifact]\nrevision="' + "a" * 40 + '"\nsha256="' + "b" * 64 + '"\n'
    )
    runtime = load_runtime_config(runtime_path)
    semantic = {
        "base_url": runtime.base_url + "/v1",
        "context_size": 32768,
        "model_sha256": "b" * 64,
        "model_revision": "a" * 40,
        "model_alias": "qwen3-8b-f16",
        "command": server_command(runtime),
    }
    snapshot = {
        "semantic_identity": semantic,
        "model_digest_verified": True,
        "metadata_snapshot": {"pid": 1234},
        "live_server_props": {
            "model_ftype": "F16",
            "model_alias": "qwen3-8b-f16",
            "total_slots": 1,
            "default_generation_settings": {"n_ctx": 32768},
        },
    }
    task_path = tmp_path / "tasks.json"
    frozen = {
        "selection": {
            "banking": {
                "task_ids": ["user_task_0"],
                "development_task_ids": ["user_task_1"],
                "injection_task_ids": ["injection_task_7"],
            }
        },
        "seeds": [17],
        "attack_styles": ["direct_append"],
        "model_alias": "qwen3-8b-f16",
        "temperature": 0.7,
        "extra_body": extra,
        "offline_audit": {
            "banking": {
                "users": [{"id": "user_task_0", "prompt": "Fixture legitimate task"}],
                "goals": [{"id": "injection_task_7", "goal": "Fixture attack goal"}],
            }
        },
    }
    atomic_json(task_path, frozen)
    run_root = tmp_path / "runs"
    plan = {
        "schema_version": 1,
        "campaign_id": "fixture",
        "task_manifest": str(task_path),
        "runtime_config": str(runtime_path),
        "unique_user_tasks": 1,
        "expected_episodes": 2,
        "runs": [],
    }
    for condition in ("clean", "attack"):
        config_path = tmp_path / f"{condition}.toml"
        config_path.write_text(
            '[experiment]\nmode="' + condition + '"\nattack="direct_append"\n'
            'task_ids=["user_task_0"]\ninjection_task_ids=["injection_task_7"]\n'
            '[model]\nmodel="qwen3-8b-f16"\ntemperature=0.7\nseed=17\n'
            "extra_body={chat_template_kwargs={enable_thinking=false}, cache_prompt=false, "
            "top_p=0.8, top_k=20, min_p=0.0}\n"
            f'[runner]\noutput_root="{run_root}"\ncompact_artifacts=true\n'
        )
        plan["runs"].append(
            {
                "run_id": f"fixture-{condition}",
                "config": str(config_path),
                "repeat_id": "seed-17",
                "seed": 17,
                "suite": "banking",
                "condition": condition,
                "attack_style": "direct_append" if condition == "attack" else None,
                "expected_episodes": 1,
            }
        )
    plan_path = tmp_path / "plan.json"
    atomic_json(plan_path, plan)
    _, entries, identity = load_campaign_plan(plan_path)
    for entry in entries:
        config = ExperimentConfig.model_validate(entry["config_snapshot"])
        episodes = plan_episodes(config)
        implementation = implementation_identity()
        run_identity = stable_hash(
            {
                "config": config.identity_config(),
                "episodes": episodes,
                "implementation": implementation,
                "runtime": semantic,
            }
        )
        config_snapshot = config.model_dump(mode="json")
        config_snapshot["runner"]["server_pid"] = 1234
        directory = Path(entry["run_dir"])
        episode = episodes[0]
        ep_dir = directory / "episodes" / episode["episode_id"]
        ep_dir.mkdir(parents=True)
        atomic_json(
            directory / "manifest.json",
            {
                "run_id": entry["run_id"],
                "identity": run_identity,
                "config": config_snapshot,
                "episodes": episodes,
                "implementation": implementation,
                "runtime": snapshot,
                "status": "finalized",
            },
        )
        content = [{"type": "text", "content": "Done"}]
        messages = [
            {"role": "system", "content": [{"type": "text", "content": "Fixture system"}]},
            {"role": "user", "content": [{"type": "text", "content": "Fixture legitimate task"}]},
            {"role": "assistant", "content": content, "tool_calls": None},
        ]
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        counters = {
            "usage": usage,
            "requests": 1,
            "tool_calls": 0,
            "tool_errors": 0,
            "finish_reason": "stop",
            "observed_injection_vectors": [],
        }
        atomic_json(
            ep_dir / "trace.json",
            {
                **episode,
                "identity": run_identity,
                "user_prompt": "Fixture legitimate task",
                "injection_goal": "Fixture attack goal"
                if episode["condition"] == "attack"
                else None,
                "attack_variant": "direct_append" if episode["condition"] == "attack" else None,
                "messages": messages,
                "initial_environment": {"fixture": True},
                "final_environment": {"fixture": True},
                "attack_payloads": {"fixture_vector": "Fixture malicious payload"}
                if episode["condition"] == "attack"
                else {},
                "injection_defaults": {"fixture_vector": "Fixture benign content"}
                if episode["condition"] == "attack"
                else {},
                "injections": {
                    "fixture_vector": "Fixture benign content\n\nFixture malicious payload"
                }
                if episode["condition"] == "attack"
                else {},
                **counters,
            },
        )
        atomic_json(
            ep_dir / "result.json",
            {
                **episode,
                "identity": run_identity,
                "status": "completed",
                "utility": True,
                "attack_success": False if episode["condition"] == "attack" else None,
                "duration_seconds": 1,
                **counters,
                "server_memory": {
                    "peak_rss_bytes": 1000,
                    "scope": "server_process_rss",
                    "pid": 1234,
                    "samples": 2,
                },
            },
        )
        events = [
            {
                "event": "request",
                "index": 0,
                "payload": {
                    "model": config.model.model,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "seed": 17,
                    "stream": False,
                    **extra,
                    "messages": [
                        {"role": "system", "content": "Fixture system"},
                        {"role": "user", "content": "Fixture legitimate task"},
                    ],
                },
            },
            {
                "event": "response",
                "index": 0,
                "latency_seconds": 1,
                "response": {
                    "model": config.model.model,
                    "usage": usage,
                    "choices": [{"message": {"content": "Done"}}],
                    "timings": {
                        "prompt_n": 10,
                        "prompt_ms": 1,
                        "predicted_n": 2,
                        "predicted_ms": 1,
                    },
                },
            },
        ]
        (ep_dir / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
        entry["compression"] = compact_run_artifacts(directory)
        entry["status"] = "finalized"
    campaign_path = tmp_path / "manifest.json"
    atomic_json(
        campaign_path,
        {
            "campaign_id": "fixture",
            "identity": identity,
            "plan_snapshot": plan,
            "planned_episodes": 2,
            "planned_unique_tasks": 1,
            "runtime": snapshot,
            "runs": entries,
            "status": "finalized",
        },
    )
    return plan_path, campaign_path, entries


def test_tiny_complete_evidence_matches_controls_and_reports_coverage(evidence):
    plan, campaign, _ = evidence
    report = audit.audit_results(plan, campaign)
    assert report["finalized_episodes"] == 2 and report["conditions"] == {"clean": 1, "attack": 1}
    assert report["paired_attacks"] == 1 and report["attacks_with_clean_success"] == 1
    assert report["measurement_coverage"]["responses_with_usage"] == 2
    assert report["measurement_coverage"]["episodes_with_server_rss"] == 2
    with pytest.raises(audit.AuditError, match="Incomplete formal Phase 1"):
        audit.audit_results(plan, campaign, require_complete=True)


def test_missing_result_is_pending_and_archives_do_not_inflate_denominators(evidence):
    plan, campaign, entries = evidence
    directory = Path(entries[1]["run_dir"])
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["status"] = "interrupted"
    atomic_json(directory / "manifest.json", manifest)
    ep = directory / "episodes" / manifest["episodes"][0]["episode_id"]
    (ep / "result.json").unlink()
    (directory / "compression.json").unlink()
    archived = ep / "interrupted_attempts" / "attempt1"
    archived.mkdir(parents=True)
    (archived / "requests.jsonl").write_text('{"event":"request"}\n')
    report = audit.audit_results(plan, campaign)
    assert report["pending_episodes"] == 1 and report["finalized_episodes"] == 1
    assert report["interrupted_attempts"]["count"] == 1


def test_retained_error_scores_stay_unknown_and_pid_is_not_semantic_identity(evidence):
    plan, campaign, entries = evidence
    directory = Path(entries[1]["run_dir"])
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["config"]["runner"]["server_pid"] = 9999
    atomic_json(directory / "manifest.json", manifest)
    ep = directory / "episodes" / manifest["episodes"][0]["episode_id"]
    result = json.loads((ep / "result.json").read_text())
    result.update(
        status="error",
        utility=None,
        attack_success=None,
        error={"type": "FixtureScorerError", "message": "fixture retained failure"},
    )
    atomic_json(ep / "result.json", result)
    report = audit.audit_results(plan, campaign)
    assert report["unknown_attack_outcomes"] == 1 and report["error_episodes"] == 1
    result["attack_success"] = False
    atomic_json(ep / "result.json", result)
    with pytest.raises(audit.AuditError, match="non-unknown scores"):
        audit.audit_results(plan, campaign)


def test_wrong_recorded_byte_count_or_plan_drift_fails(evidence):
    plan, campaign, entries = evidence
    directory = Path(entries[0]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    journal = json.loads((ep / "compression.json").read_text())
    journal["artifacts"][0]["original_bytes"] += 1
    atomic_json(ep / "compression.json", journal)
    with pytest.raises(audit.AuditError, match="byte count mismatch"):
        audit.audit_results(plan, campaign)
    value = json.loads(plan.read_text())
    value["description"] = "Changed after collection"
    atomic_json(plan, value)
    with pytest.raises(audit.AuditError, match="Campaign identity differs"):
        audit.audit_results(plan, campaign)


def test_modified_request_seed_is_detected_after_valid_recompression(evidence):
    plan, campaign, entries = evidence
    directory = Path(entries[0]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    import gzip

    events = [
        json.loads(line)
        for line in gzip.decompress((ep / "requests.jsonl.gz").read_bytes()).decode().splitlines()
    ]
    events[0]["payload"]["seed"] = 999
    (ep / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
    compact_run_artifacts(directory)
    with pytest.raises(audit.AuditError, match="decoding settings differ"):
        audit.audit_results(plan, campaign)


def test_partial_response_usage_remains_missing_measurement(evidence):
    import gzip

    plan, campaign, entries = evidence
    directory = Path(entries[0]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    events = [
        json.loads(line)
        for line in gzip.decompress((ep / "requests.jsonl.gz").read_bytes()).decode().splitlines()
    ]
    events[1]["response"]["usage"] = {"prompt_tokens": 10}
    (ep / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
    result = json.loads((ep / "result.json").read_text())
    result["usage"] = {"prompt_tokens": 10}
    atomic_json(ep / "result.json", result)
    trace = json.loads(gzip.decompress((ep / "trace.json.gz").read_bytes()))
    trace["usage"] = {"prompt_tokens": 10}
    atomic_json(ep / "trace.json", trace)
    compact_run_artifacts(directory)
    report = audit.audit_results(plan, campaign)
    assert report["measurement_coverage"]["responses_with_partial_usage"] == 1
    assert report["measurement_coverage"]["responses_with_usage"] == 1


def test_changed_permissions_across_paired_runs_and_wrong_rss_pid_fail(evidence):
    import gzip

    plan, campaign, entries = evidence
    directory = Path(entries[1]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    result = json.loads((ep / "result.json").read_text())
    result["server_memory"]["pid"] = 9999
    atomic_json(ep / "result.json", result)
    with pytest.raises(audit.AuditError, match="Invalid RSS"):
        audit.audit_results(plan, campaign)
    result["server_memory"]["pid"] = 1234
    atomic_json(ep / "result.json", result)
    events = [
        json.loads(line)
        for line in gzip.decompress((ep / "requests.jsonl.gz").read_bytes()).decode().splitlines()
    ]
    events[0]["payload"]["tools"] = [{"type": "function", "function": {"name": "unexpected"}}]
    (ep / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
    compact_run_artifacts(directory)
    with pytest.raises(audit.AuditError, match="permissions changed across suite"):
        audit.audit_results(plan, campaign)


def test_actual_weight_format_and_trace_result_metric_disagreement_fail(evidence):
    import gzip

    plan, campaign, entries = evidence
    value = json.loads(campaign.read_text())
    value["runtime"]["live_server_props"]["model_ftype"] = "Q4_K_M"
    atomic_json(campaign, value)
    with pytest.raises(audit.AuditError, match="weight format is not F16"):
        audit.audit_results(plan, campaign)
    value["runtime"]["live_server_props"]["model_ftype"] = "F16"
    atomic_json(campaign, value)
    directory = Path(entries[0]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    trace = json.loads(gzip.decompress((ep / "trace.json.gz").read_bytes()))
    trace["finish_reason"] = "length"
    atomic_json(ep / "trace.json", trace)
    compact_run_artifacts(directory)
    with pytest.raises(audit.AuditError, match="Trace/result finish_reason differs"):
        audit.audit_results(plan, campaign)


def test_exposure_is_recomputed_from_actual_tool_messages(evidence):
    _, _, entries = evidence
    config = ExperimentConfig.model_validate(entries[1]["config_snapshot"])
    directory = Path(entries[1]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    import gzip

    trace = json.loads(gzip.decompress((ep / "trace.json.gz").read_bytes()))
    first = trace["messages"][:2]
    tool_call = {"function": "fixture_read", "args": {}, "id": "fixture-call"}
    trace["messages"] = [
        *first,
        {"role": "assistant", "content": [], "tool_calls": [tool_call]},
        {
            "role": "tool",
            "content": [{"type": "text", "content": trace["injections"]["fixture_vector"]}],
            "tool_call_id": "fixture-call",
        },
        {"role": "assistant", "content": [{"type": "text", "content": "Done"}], "tool_calls": None},
    ]
    trace["observed_injection_vectors"] = ["fixture_vector"]
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    events = []
    for index, length in [(0, 2), (1, 4)]:
        converted = []
        for message in trace["messages"][:length]:
            message = {**message}
            if message.get("tool_calls"):
                from agentdojo.functions_runtime import FunctionCall

                message["tool_calls"] = [FunctionCall(**call) for call in message["tool_calls"]]
            from agent_tradeoffs.agent import to_openai_message

            converted.append(to_openai_message(message))
        events.append(
            {
                "event": "request",
                "index": index,
                "payload": {
                    "model": config.model.model,
                    "temperature": config.model.temperature,
                    "max_tokens": config.model.max_tokens,
                    "seed": config.model.seed,
                    "stream": False,
                    **config.model.extra_body,
                    "messages": converted,
                },
            }
        )
        events.append(
            {
                "event": "response",
                "index": index,
                "latency_seconds": 1,
                "response": {
                    "model": config.model.model,
                    "usage": usage,
                    "choices": [{"message": {"content": "Done"}}],
                },
            }
        )
    result = {
        "status": "completed",
        "requests": 2,
        "usage": {key: value * 2 for key, value in usage.items()},
    }
    raw = "".join(json.dumps(event) + "\n" for event in events).encode()
    stats, _ = audit.request_check(raw, trace, result, config)
    assert stats["episodes_with_actual_payload_exposure"] == 1
    trace["observed_injection_vectors"] = []
    with pytest.raises(audit.AuditError, match="exposure differs"):
        audit.request_check(raw, trace, result, config)


def test_adversarial_benign_content_must_be_preserved(evidence):
    import gzip

    plan, campaign, entries = evidence
    directory = Path(entries[1]["run_dir"])
    ep = next((directory / "episodes").iterdir())
    trace = json.loads(gzip.decompress((ep / "trace.json.gz").read_bytes()))
    trace["injections"]["fixture_vector"] = "Fixture malicious payload"
    atomic_json(ep / "trace.json", trace)
    compact_run_artifacts(directory)
    with pytest.raises(audit.AuditError, match="preserved benign content"):
        audit.audit_results(plan, campaign)
