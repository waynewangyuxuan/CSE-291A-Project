#!/usr/bin/env python3
"""Audit stored Phase 1 evidence without contacting or loading the inference service."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentdojo.functions_runtime import FunctionCall

from agent_tradeoffs.agent import to_openai_message
from agent_tradeoffs.campaign import load_campaign_plan
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.context import exposed_vectors
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import implementation_identity, plan_episodes
from agent_tradeoffs.runtime import load_runtime_config, server_command

EPISODE_FIELDS = ("episode_id", "condition", "user_task_id", "injection_task_id")
FORMAL_COUNTS = {"runs": 24, "episodes": 600, "clean": 120, "attack": 480, "tasks": 60}


class AuditError(ValueError):
    """Stored evidence disagrees with its frozen provenance or internal contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    require(isinstance(value, dict), f"Expected JSON object: {path}")
    return value


def number(value: Any, *, integer: bool = False) -> bool:
    return (
        isinstance(value, int if integer else (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def canonical_config(value: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(value))
    normalized["runner"].pop("server_pid", None)
    return normalized


def runtime_check(snapshot: dict[str, Any], semantic: dict[str, Any], runtime_config) -> None:
    require(snapshot["semantic_identity"] == semantic, "Mixed runtime semantic identities")
    require(
        snapshot.get("model_digest_verified") is True, "Runtime lacks model digest verification"
    )
    props = snapshot["live_server_props"]
    require(props.get("model_ftype") == "F16", "Recorded live weight format is not F16")
    require(props.get("model_alias") == runtime_config.model_alias, "Live model alias differs")
    require(props.get("total_slots") == 1, "Recorded runtime was not a single inference slot")
    require(
        props.get("default_generation_settings", {}).get("n_ctx") == 32768,
        "Recorded live context is not 32768",
    )
    require(
        semantic.get("context_size") == runtime_config.context_size == 32768,
        "Frozen runtime context differs from 32768",
    )
    require(
        semantic.get("model_sha256") == runtime_config.model_artifact.sha256,
        "Runtime model digest differs from pinned artifact",
    )
    require(
        semantic.get("model_revision") == runtime_config.model_artifact.revision,
        "Runtime model revision differs from pinned artifact",
    )
    require(
        semantic.get("model_alias") == runtime_config.model_alias,
        "Runtime semantic model alias differs",
    )
    require(
        semantic.get("base_url") == runtime_config.base_url + "/v1",
        "Runtime endpoint differs from pinned local service",
    )
    require(
        semantic.get("command") == server_command(runtime_config),
        "Runtime command differs from pinned runtime configuration",
    )
    require(
        runtime_config.cache_type_k == runtime_config.cache_type_v == "f16",
        "Frozen KV cache is not F16",
    )


def artifact_bytes(
    directory: Path, filename: str, records: dict[str, dict[str, Any]], *, compressed_required: bool
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Verify both original and compressed digests, including a compaction-in-progress source."""
    source, target = directory / filename, directory / (filename + ".gz")
    record = records.get(target.name)
    if record is not None:
        require(target.is_file(), f"Recorded gzip is missing: {target}")
        compressed = target.read_bytes()
        original = gzip.decompress(compressed)
        require(
            digest(compressed) == record["compressed_sha256"], f"Gzip digest mismatch: {target}"
        )
        require(
            digest(original) == record["original_sha256"], f"Original digest mismatch: {target}"
        )
        require(
            len(compressed) == record["compressed_bytes"], f"Gzip byte count mismatch: {target}"
        )
        require(
            len(original) == record["original_bytes"], f"Original byte count mismatch: {target}"
        )
        if source.is_file():
            require(source.read_bytes() == original, f"Source differs from its gzip: {source}")
        return original, record
    if source.is_file():
        original = source.read_bytes()
        if target.is_file():
            require(
                gzip.decompress(target.read_bytes()) == original,
                f"Unjournaled gzip differs from retained original: {target}",
            )
        require(not compressed_required, f"Final compression journal missing: {source}")
        return original, None
    require(not target.is_file(), f"Gzip has no compression journal or original: {target}")
    return None, None


def compression_records(directory: Path) -> dict[str, dict[str, Any]]:
    path = directory / "compression.json"
    if not path.is_file():
        return {}
    value = read_json(path)
    require(
        value.get("format") == "gzip" and value.get("lossless") is True,
        f"Invalid compression journal: {path}",
    )
    rows = value["artifacts"]
    records = {row["path"]: row for row in rows}
    require(len(rows) == len(records), f"Duplicate compression records: {path}")
    require(
        set(records) <= {"requests.jsonl.gz", "trace.json.gz"},
        f"Unexpected compression artifact path: {path}",
    )
    return records


def request_check(
    raw: bytes | None, trace: dict[str, Any], result: dict[str, Any], config: ExperimentConfig
) -> tuple[dict[str, int], str | None]:
    stats: Counter = Counter()
    exposure = set()
    if raw is None:
        require(
            result["status"] == "error" and result["requests"] == 0,
            "Missing request evidence for an episode that made requests",
        )
        require(trace["observed_injection_vectors"] == [], "Exposure without a model request")
        return dict(stats), None
    events = [json.loads(line) for line in raw.decode().splitlines()]
    requests, terminals = {}, {}
    converted = []
    for message in trace.get("messages", []):
        message = {**message}
        if message.get("tool_calls"):
            message["tool_calls"] = [FunctionCall(**call) for call in message["tool_calls"]]
        converted.append(to_openai_message(message))
    usage: Counter = Counter()
    tool_fingerprint = None
    for event in events:
        kind, index = event.get("event"), event.get("index")
        require(kind in {"request", "response", "request_error"}, "Unknown request-log event")
        require(number(index, integer=True), "Invalid request index")
        if kind == "request":
            require(
                index == len(requests) and index not in requests,
                "Request indices must be unique and contiguous from zero",
            )
            require(
                index == 0 or index - 1 in terminals,
                "Another request started before the preceding request finished",
            )
            payload = event["payload"]
            expected = {
                "model": config.model.model,
                "temperature": config.model.temperature,
                "max_tokens": config.model.max_tokens,
                "seed": config.model.seed,
                "stream": False,
                **config.model.extra_body,
            }
            require(
                all(payload.get(key) == value for key, value in expected.items()),
                f"Request {index} deployment, seed or decoding settings differ",
            )
            require(
                payload.get("cache_prompt") is False,
                f"Request {index} silently enables prompt caching",
            )
            require(
                payload.get("chat_template_kwargs", {}).get("enable_thinking") is False,
                f"Request {index} thinking mode differs",
            )
            messages = payload["messages"]
            exposure.update(exposed_vectors(messages, trace["attack_payloads"]))
            require(
                len(messages) >= 2
                and messages[1].get("role") == "user"
                and messages[1].get("content") == trace["user_prompt"],
                f"Request {index} legitimate objective differs from trace",
            )
            require(
                messages == converted[: len(messages)],
                f"Request {index} actual messages differ from accepted trace prefix",
            )
            current_tools = stable_hash(payload.get("tools", []))
            if tool_fingerprint is None:
                tool_fingerprint = current_tools
            require(current_tools == tool_fingerprint, "Tool permissions changed within episode")
            requests[index] = payload
            stats["requests"] += 1
            continue
        require(
            index in requests and index not in terminals,
            "Response/error has a missing or duplicate request",
        )
        require(number(event.get("latency_seconds")), "Invalid response/error latency")
        terminals[index] = kind
        stats[kind] += 1
        if kind == "request_error":
            require(result["status"] == "error", "Completed episode has transport error")
            continue
        response = event["response"]
        require(response.get("model") == config.model.model, "Response model differs from request")
        measured_usage = response.get("usage") or {}
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        for key in keys:
            if measured_usage.get(key) is not None:
                require(number(measured_usage[key], integer=True), f"Invalid response usage: {key}")
                usage[key] += measured_usage[key]
        if all(number(measured_usage.get(key), integer=True) for key in keys):
            stats["responses_with_usage"] += 1
        elif any(measured_usage.get(key) is not None for key in keys):
            stats["responses_with_partial_usage"] += 1
        timing = response.get("timings") or {}
        for key in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms"):
            if timing.get(key) is not None:
                require(number(timing[key]), f"Invalid native timing: {key}")
        if all(number(timing.get(key)) for key in ("predicted_n", "predicted_ms")):
            stats["responses_with_native_decode_timing"] += 1
        if all(number(timing.get(key)) for key in ("prompt_n", "prompt_ms")):
            stats["responses_with_native_prefill_timing"] += 1
        # Invalid raw model responses are legitimate retained errors, not corrupted logs.
        choices = response.get("choices") or []
        if not choices:
            require(result["status"] == "error", "Completed episode has an empty raw completion")
            stats["raw_responses_without_choices"] += 1
    require(len(requests) <= result["requests"], "Request log contains more calls than result")
    if result["status"] == "completed":
        require(
            len(requests) == result["requests"] and len(terminals) == len(requests),
            "Completed episode request counts or terminal events differ",
        )
        require(dict(usage) == result["usage"], "Completed episode response/result usage differs")
    elif dict(usage) != result["usage"]:
        stats["errored_episode_usage_mismatch"] += 1
    stats["unanswered_logged_requests"] += len(requests) - len(terminals)
    stats["unlogged_counted_requests"] += result["requests"] - len(requests)
    require(
        sorted(exposure) == trace["observed_injection_vectors"],
        "Actual-request exposure differs from recorded vectors",
    )
    stats["episodes_with_actual_payload_exposure"] += bool(exposure)
    return dict(stats), tool_fingerprint


def archive_records(
    directory: Path, episode: dict[str, Any], identity: str
) -> list[dict[str, Any]]:
    reports = []
    for attempt in sorted((directory / "interrupted_attempts").glob("*")):
        require(attempt.is_dir(), f"Unexpected archive entry: {attempt}")
        files = [
            {
                "path": str(path.relative_to(attempt)),
                "sha256": digest(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
            for path in sorted(attempt.rglob("*"))
            if path.is_file()
        ]
        row = {
            "path": str(attempt),
            "files": len(files),
            "bytes": sum(x["bytes"] for x in files),
            "inventory_sha256": stable_hash(files),
            "status": "interrupted_without_result",
        }
        result_path = attempt / "result.json"
        if result_path.is_file():
            archived = read_json(result_path)
            require(archived.get("identity") == identity, f"Archived identity differs: {attempt}")
            require(
                all(archived.get(key) == episode[key] for key in EPISODE_FIELDS),
                f"Archived episode metadata differs: {attempt}",
            )
            row["status"] = archived["status"]
            row["retryable"] = archived.get("retryable", False)
            row["scores_unknown"] = (
                archived.get("utility") is None and archived.get("attack_success") is None
            )
        reports.append(row)
    return reports


def completion_gate(report: dict[str, Any]) -> None:
    actual = {
        "runs": report["finalized_runs"],
        "episodes": report["finalized_episodes"],
        "clean": report["conditions"].get("clean", 0),
        "attack": report["conditions"].get("attack", 0),
        "tasks": report["finalized_unique_tasks"],
    }
    require(
        actual == FORMAL_COUNTS and report["pending_episodes"] == 0,
        f"Incomplete formal Phase 1 collection: {actual}; expected {FORMAL_COUNTS}",
    )
    require(
        report["campaign_status"] in {"finalized", "finalized_with_errors"},
        "Campaign is not finalized",
    )
    require(report["paired_attacks"] == 480, "Formal attack/control pairing is incomplete")


def audit_results(
    plan_path: Path, campaign_path: Path, *, require_complete: bool = False
) -> dict[str, Any]:
    plan, entries, campaign_identity = load_campaign_plan(plan_path)
    path = campaign_path / "manifest.json" if campaign_path.is_dir() else campaign_path
    campaign = read_json(path)
    require(
        campaign.get("identity") == campaign_identity,
        "Campaign identity differs from current frozen plan/config/source/lock",
    )
    require(campaign.get("plan_snapshot") == plan, "Campaign frozen plan snapshot differs")
    require(campaign["campaign_id"] == plan["campaign_id"], "Campaign ID differs")
    require(
        campaign["planned_episodes"] == plan["expected_episodes"], "Planned episode total differs"
    )
    require(
        campaign["planned_unique_tasks"] == plan["unique_user_tasks"], "Planned task total differs"
    )
    require(
        [row["run_id"] for row in campaign["runs"]] == [row["run_id"] for row in entries],
        "Campaign run order/membership differs from frozen plan",
    )
    task_manifest_path = Path(plan["task_manifest"])
    frozen = read_json(task_manifest_path)
    runtime_config = load_runtime_config(plan["runtime_config"])
    semantic = campaign["runtime"]["semantic_identity"]
    runtime_check(campaign["runtime"], semantic, runtime_config)
    current_implementation = implementation_identity()
    totals: Counter = Counter()
    conditions: Counter = Counter()
    planned_conditions: Counter = Counter()
    errors: Counter = Counter()
    measurements: Counter = Counter()
    finalized_tasks, planned_units, finalized_units = set(), set(), set()
    clean_controls, attack_pairs = {}, []
    runs, pending, archives, inventory = [], [], [], []
    tools_by_suite: dict[str, str] = {}
    expected_units = set()
    for suite, spec in frozen["selection"].items():
        require(
            not set(spec["task_ids"]) & set(spec["development_task_ids"]),
            f"Development/evaluation overlap: {suite}",
        )
        for seed in frozen["seeds"]:
            for user_id in spec["task_ids"]:
                expected_units.add((suite, seed, user_id, "clean", None, None))
                for style in frozen["attack_styles"]:
                    for goal in spec["injection_task_ids"]:
                        expected_units.add((suite, seed, user_id, "attack", goal, style))
    for expected, stored in zip(entries, campaign["runs"], strict=True):
        for field in (
            "run_id",
            "config_path",
            "config_snapshot",
            "run_dir",
            "planned_episodes",
            "repeat_id",
            "seed",
            "suite",
            "condition",
            "attack_style",
        ):
            require(
                stored.get(field) == expected.get(field),
                f"Campaign run {field} differs: {expected['run_id']}",
            )
        config = ExperimentConfig.model_validate(expected["config_snapshot"])
        require(config.model.base_url == semantic["base_url"], "Run endpoint differs from baseline")
        spec = frozen["selection"][config.experiment.suite]
        require(
            list(config.experiment.task_ids) == spec["task_ids"],
            "Task selection differs from frozen manifest",
        )
        require(
            config.model.model == frozen["model_alias"] == runtime_config.model_alias,
            "Configured model alias differs from baseline",
        )
        require(
            config.model.temperature == frozen["temperature"]
            and config.model.extra_body == frozen["extra_body"],
            "Frozen decoding settings differ",
        )
        require(config.context.policy == "full", "Phase 1 context policy differs")
        episodes = plan_episodes(config)
        for episode in episodes:
            unit = (
                config.experiment.suite,
                config.model.seed,
                episode["user_task_id"],
                episode["condition"],
                episode["injection_task_id"],
                config.experiment.attack if episode["condition"] == "attack" else None,
            )
            require(unit not in planned_units, f"Duplicate planned episode unit: {unit}")
            planned_units.add(unit)
            planned_conditions[episode["condition"]] += 1
        directory = Path(stored["run_dir"])
        manifest_path = directory / "manifest.json"
        run_report = {
            "run_id": stored["run_id"],
            "planned": len(episodes),
            "finalized": 0,
            "completed": 0,
            "errors": 0,
            "status": "pending",
        }
        runs.append(run_report)
        if not manifest_path.is_file():
            pending.extend(f"{stored['run_id']}/{episode['episode_id']}" for episode in episodes)
            continue
        manifest = read_json(manifest_path)
        run_report["status"] = manifest["status"]
        run_report["manifest_sha256"] = digest(manifest_path.read_bytes())
        require(manifest["run_id"] == stored["run_id"], "Run manifest ID differs")
        require(manifest["episodes"] == episodes, f"Run episode plan differs: {directory}")
        require(
            canonical_config(manifest["config"])
            == canonical_config(config.model_dump(mode="json")),
            f"Run configuration snapshot differs: {directory}",
        )
        require(
            manifest["implementation"] == current_implementation,
            f"Run implementation/lock provenance differs: {directory}",
        )
        runtime_check(manifest["runtime"], semantic, runtime_config)
        for observation in manifest.get("resume_runtime_observations", []):
            runtime_check(observation, semantic, runtime_config)
        run_identity = stable_hash(
            {
                "config": config.identity_config(),
                "episodes": episodes,
                "implementation": current_implementation,
                "runtime": semantic,
            }
        )
        require(
            manifest["identity"] == run_identity, f"Run identity recomputation differs: {directory}"
        )
        recorded_global = []
        known_ids = {episode["episode_id"] for episode in episodes}
        require(
            all(
                path.parent.name in known_ids
                for path in (directory / "episodes").glob("*/result.json")
            ),
            f"Unplanned episode result exists: {directory}",
        )
        for episode in episodes:
            episode_dir = directory / "episodes" / episode["episode_id"]
            archives.extend(archive_records(episode_dir, episode, run_identity))
            result_path = episode_dir / "result.json"
            if not result_path.is_file():
                pending.append(f"{stored['run_id']}/{episode['episode_id']}")
                continue
            result = read_json(result_path)
            require(
                result.get("identity") == run_identity, f"Result identity differs: {result_path}"
            )
            require(
                all(result.get(field) == episode[field] for field in EPISODE_FIELDS),
                f"Result episode metadata differs: {result_path}",
            )
            require(
                result.get("status") in {"completed", "error"},
                f"Invalid result status: {result_path}",
            )
            completed = result["status"] == "completed"
            require(
                number(result.get("duration_seconds")), f"Invalid episode duration: {result_path}"
            )
            require(
                all(
                    number(result.get(field), integer=True)
                    for field in ("requests", "tool_calls", "tool_errors")
                ),
                f"Invalid episode counters: {result_path}",
            )
            if completed:
                require(
                    isinstance(result.get("utility"), bool),
                    f"Completed utility unknown: {result_path}",
                )
                require(
                    isinstance(result.get("attack_success"), bool)
                    if episode["condition"] == "attack"
                    else result.get("attack_success") is None,
                    f"Invalid completed attack score: {result_path}",
                )
            else:
                require(
                    result.get("utility") is None and result.get("attack_success") is None,
                    f"Errored episode has non-unknown scores: {result_path}",
                )
                require(
                    isinstance(result.get("error"), dict),
                    f"Error episode lacks reason: {result_path}",
                )
                errors[result["error"].get("type", "unknown")] += 1
            records = compression_records(episode_dir)
            compressed_required = require_complete and config.runner.compact_artifacts
            trace_raw, trace_record = artifact_bytes(
                episode_dir, "trace.json", records, compressed_required=compressed_required
            )
            require(trace_raw is not None, f"Finalized episode trace missing: {episode_dir}")
            trace = json.loads(trace_raw)
            require(trace.get("identity") == run_identity, f"Trace identity differs: {episode_dir}")
            require(
                all(trace.get(field) == episode[field] for field in EPISODE_FIELDS),
                f"Trace episode metadata differs: {episode_dir}",
            )
            for field in (
                "usage",
                "requests",
                "tool_calls",
                "tool_errors",
                "finish_reason",
                "observed_injection_vectors",
            ):
                require(
                    field in trace and field in result and trace[field] == result[field],
                    f"Trace/result {field} differs: {episode_dir}",
                )
            prompt = next(
                user["prompt"]
                for user in frozen["offline_audit"][stored["suite"]]["users"]
                if user["id"] == episode["user_task_id"]
            )
            require(
                trace.get("user_prompt") == prompt,
                f"Trace legitimate prompt differs: {episode_dir}",
            )
            if episode["condition"] == "attack":
                goal = next(
                    goal["goal"]
                    for goal in frozen["offline_audit"][stored["suite"]]["goals"]
                    if goal["id"] == episode["injection_task_id"]
                )
                require(
                    trace.get("injection_goal") == goal
                    and trace.get("attack_variant") == config.experiment.attack,
                    f"Trace attack goal/template differs: {episode_dir}",
                )
                payloads, defaults, combined = (
                    trace[field]
                    for field in ("attack_payloads", "injection_defaults", "injections")
                )
                require(
                    set(payloads) == set(defaults) == set(combined),
                    f"Injection vector keys differ: {episode_dir}",
                )
                require(
                    all(
                        combined[key] == defaults[key] + "\n\n" + payloads[key] for key in payloads
                    ),
                    f"Attack removed or changed preserved benign content: {episode_dir}",
                )
            else:
                require(
                    trace.get("injection_goal") is None and trace.get("attack_variant") is None,
                    f"Clean trace contains attack metadata: {episode_dir}",
                )
                require(
                    all(
                        trace[field] == {}
                        for field in ("attack_payloads", "injection_defaults", "injections")
                    ),
                    f"Clean trace contains injected content: {episode_dir}",
                )
            request_raw, request_record = artifact_bytes(
                episode_dir, "requests.jsonl", records, compressed_required=compressed_required
            )
            request_stats, tool_fingerprint = request_check(request_raw, trace, result, config)
            measurements.update(request_stats)
            if tool_fingerprint is not None:
                prior_tools = tools_by_suite.setdefault(stored["suite"], tool_fingerprint)
                require(
                    prior_tools == tool_fingerprint,
                    f"Tool schemas/permissions changed across suite runs: {stored['suite']}",
                )
            measurements["episodes_with_response_usage"] += bool(result.get("usage"))
            memory = result.get("server_memory") or {}
            if memory.get("peak_rss_bytes") is not None:
                allowed_pids = {
                    observation["metadata_snapshot"]["pid"]
                    for observation in [
                        manifest["runtime"],
                        *manifest.get("resume_runtime_observations", []),
                    ]
                }
                require(
                    number(memory["peak_rss_bytes"], integer=True)
                    and memory.get("scope") == "server_process_rss"
                    and memory.get("pid") in allowed_pids
                    and number(memory.get("samples"), integer=True)
                    and memory["samples"] > 0,
                    "Invalid RSS measurement",
                )
                measurements["episodes_with_server_rss"] += 1
            else:
                measurements["episodes_without_server_rss"] += 1
            measurements["episodes_with_both_compression_records"] += bool(
                trace_record and request_record
            )
            for record in records.values():
                recorded_global.append(
                    {
                        **record,
                        "path": str(Path("episodes") / episode["episode_id"] / record["path"]),
                    }
                )
            inventory.append(
                {
                    "episode": f"{stored['run_id']}/{episode['episode_id']}",
                    "result_sha256": digest(result_path.read_bytes()),
                    "trace_sha256": digest(trace_raw),
                    "requests_sha256": digest(request_raw) if request_raw is not None else None,
                }
            )
            totals["finalized"] += 1
            totals["completed" if completed else "errors"] += 1
            conditions[episode["condition"]] += 1
            run_report["finalized"] += 1
            run_report["completed" if completed else "errors"] += 1
            finalized_tasks.add((stored["suite"], episode["user_task_id"]))
            unit = (
                stored["suite"],
                stored["seed"],
                episode["user_task_id"],
                episode["condition"],
                episode["injection_task_id"],
                config.experiment.attack if episode["condition"] == "attack" else None,
            )
            finalized_units.add(unit)
            pair = stored["suite"], stored["seed"], episode["user_task_id"]
            common_config = {
                key: value for key, value in config.identity_config().items() if key != "experiment"
            }
            if episode["condition"] == "clean":
                require(pair not in clean_controls, f"Ambiguous clean control: {pair}")
                clean_controls[pair] = {"config": common_config, "result": result}
            else:
                attack_pairs.append((pair, common_config, result))
        if manifest["status"] == "finalized":
            require(run_report["finalized"] == len(episodes), "Finalized run has missing episodes")
            totals["finalized_runs"] += 1
        compression_path = directory / "compression.json"
        if compression_path.is_file():
            global_records = read_json(compression_path)["artifacts"]
            require(
                sorted(global_records, key=lambda row: row["path"])
                == sorted(recorded_global, key=lambda row: row["path"]),
                f"Run/episode compression journals differ: {directory}",
            )
        elif require_complete:
            raise AuditError(f"Final run compression journal missing: {directory}")
    require(
        planned_units == expected_units,
        "Planned tasks/seeds/goals/styles differ from frozen Cartesian workload",
    )
    require(len(planned_units) == plan["expected_episodes"], "Planned episode unit total differs")
    require(len(finalized_units) == totals["finalized"], "Duplicate finalized episode units")
    paired = eligible = unknown_attacks = 0
    for pair, common_config, result in attack_pairs:
        if pair in clean_controls:
            require(
                clean_controls[pair]["config"] == common_config,
                "Attack/control decoding or runner controls differ",
            )
            paired += 1
            eligible += clean_controls[pair]["result"].get("utility") is True
        unknown_attacks += result.get("attack_success") is None
    report = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "status": "passed_complete" if require_complete else "passed_partial",
        "campaign_id": plan["campaign_id"],
        "campaign_status": campaign["status"],
        "campaign_identity": campaign_identity,
        "plan_sha256": digest(plan_path.read_bytes()),
        "task_manifest_sha256": digest(task_manifest_path.read_bytes()),
        "auditor_sha256": digest(Path(__file__).read_bytes()),
        "runtime_semantic_sha256": stable_hash(semantic),
        "planned_runs": len(entries),
        "finalized_runs": totals["finalized_runs"],
        "planned_episodes": len(planned_units),
        "finalized_episodes": totals["finalized"],
        "completed_episodes": totals["completed"],
        "error_episodes": totals["errors"],
        "pending_episodes": len(pending),
        "pending": pending,
        "pending_conditions": {
            key: planned_conditions[key] - conditions[key] for key in ("clean", "attack")
        },
        "conditions": dict(conditions),
        "planned_conditions": dict(planned_conditions),
        "finalized_unique_tasks": len(finalized_tasks),
        "clean_controls": len(clean_controls),
        "paired_attacks": paired,
        "attacks_without_finalized_clean_control": len(attack_pairs) - paired,
        "attacks_with_clean_success": eligible,
        "unknown_attack_outcomes": unknown_attacks,
        "unknown_attack_outcomes_including_pending": (
            unknown_attacks + planned_conditions["attack"] - conditions["attack"]
        ),
        "errors_by_type": dict(errors),
        "measurement_coverage": dict(measurements),
        "tool_schema_sha256_by_suite": tools_by_suite,
        "evidence_inventory_sha256": stable_hash(inventory),
        "runs": runs,
        "interrupted_attempts": {"count": len(archives), "attempts": archives},
        "interpretation": (
            "Errors and pending episodes are unknown outcomes, not successful resistance. "
            "RSS covers the server process only, using launch or recorded resume PIDs. "
            "Runtime checks use captured evidence and pinned hashes; "
            "no live service or multi-GB model read is performed."
        ),
    }
    if require_complete:
        completion_gate(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("configs/phase1/campaign.json"))
    parser.add_argument("--campaign", type=Path, default=Path("artifacts/campaigns/phase1-f16-v1"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_results(args.plan, args.campaign, require_complete=args.require_complete)
        code = 0
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        report = {
            "schema_version": 1,
            "status": "failed",
            "error": str(error),
            "error_type": type(error).__name__,
        }
        code = 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output, report)
    compact = {
        key: value
        for key, value in report.items()
        if key not in {"runs", "pending", "interrupted_attempts", "interpretation"}
    }
    print(json.dumps(compact, indent=2))
    if code:
        print(f"Audit failed: {report['error']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
