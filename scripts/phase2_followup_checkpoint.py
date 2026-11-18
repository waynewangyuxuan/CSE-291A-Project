"""Prove two source-bound AgentDojo checkpoint restorations without model calls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
MECHANISMS = "results/phase2-followup-v1/mechanisms"
CAPABILITY = "results/phase2-followup-v1/capability/qualification.json"
TASKS = ("user_task_28", "user_task_14")
REPLAY_TOOLS = frozenset(
    {"search_files_by_filename", "search_emails", "get_unread_emails", "search_contacts_by_email"}
)


class ReplayRejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReplayRejected(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def stable_hash(value):
    return digest(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


@dataclass(frozen=True)
class Runtime:
    suite: object
    functions: object
    function_call: object
    wire_message: object
    result_text: object
    request_payload: object
    config_type: object
    rounds: object
    implementation: object


def runtime_for(version, suite_name):
    from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
    from agentdojo.task_suite import get_suite

    from agent_tradeoffs.agent import LocalAgent, to_openai_message, tool_result_to_text
    from agent_tradeoffs.config import ExperimentConfig, FullContext
    from agent_tradeoffs.memory import EpisodeMemory
    from agent_tradeoffs.runner import implementation_identity

    suite = get_suite(version, suite_name)
    return Runtime(
        suite,
        FunctionsRuntime(suite.tools),
        FunctionCall,
        to_openai_message,
        tool_result_to_text,
        LocalAgent.request_payload,
        ExperimentConfig,
        EpisodeMemory(FullContext()).rounds,
        implementation_identity,
    )


def wire(runtime, message):
    value = copy.deepcopy(message)
    if value.get("tool_calls"):
        value["tool_calls"] = [runtime.function_call(**call) for call in value["tool_calls"]]
    return runtime.wire_message(value)


def restore_environment(runtime, initial):
    environment = runtime.suite.environment_type.model_validate(copy.deepcopy(initial))
    require(environment.model_dump(mode="json") == initial, "Typed initial environment changed")
    for container, initial_key, mapping_key in (
        (environment.inbox, "initial_emails", "emails"),
        (environment.calendar, "initial_events", "events"),
        (environment.cloud_drive, "initial_files", "files"),
    ):
        require(
            all(
                getattr(container, mapping_key).get(item.id_) is item
                for item in getattr(container, initial_key)
            ),
            f"Initial object aliases not restored: {initial_key}",
        )
    return environment


def replay_prefix(runtime, initial, prefix):
    messages = [wire(runtime, message) for message in prefix]
    groups = runtime.rounds(messages)
    require(len(groups) >= 2, "Checkpoint needs at least two complete tool groups")
    calls = [call for message in prefix for call in message.get("tool_calls") or []]
    require(
        all(call["function"] in REPLAY_TOOLS for call in calls), "Unsupported prefix tool effect"
    )
    require(len({c["id"] for c in calls}) == len(calls), "Duplicate prefix tool call ID")
    environment = restore_environment(runtime, initial)
    reconstructed = copy.deepcopy(prefix[:2])
    steps = []
    for start, _end in groups:
        assistant = prefix[start]
        reconstructed.append(copy.deepcopy(assistant))
        for offset, call in enumerate(assistant["tool_calls"], start + 1):
            expected = prefix[offset]
            before = environment.model_dump(mode="json")
            result, error = runtime.functions.run_function(
                environment, call["function"], call["args"]
            )
            text = runtime.result_text(result)
            expected_text = "\n".join(block["content"] for block in expected["content"])
            require(
                text == expected_text and error == expected.get("error"),
                f"Tool result mismatch at message {offset}: {call['function']}; "
                "expected content/error hash "
                f"{stable_hash([expected_text, expected.get('error')])}, "
                f"actual {stable_hash([text, error])}",
            )
            require(expected.get("tool_call") == call, f"Source call/result mismatch: {offset}")
            actual = {
                "role": "tool",
                "content": [{"type": "text", "content": text}],
                "tool_call": copy.deepcopy(call),
                "tool_call_id": call["id"],
                "error": error,
            }
            require(actual == expected, f"Tool message mismatch: {offset}")
            reconstructed.append(actual)
            after = environment.model_dump(mode="json")
            changes = [
                {
                    "id": key,
                    "before": before["inbox"]["emails"][key]["read"],
                    "after": after["inbox"]["emails"][key]["read"],
                }
                for key in before["inbox"]["emails"]
                if before["inbox"]["emails"][key]["read"] != after["inbox"]["emails"][key]["read"]
            ]
            steps.append(
                {
                    "assistant_message_index": start,
                    "result_message_index": offset,
                    "call": call,
                    "actual_content_sha256": digest(text.encode()),
                    "actual_error": error,
                    "result_exactly_matches_source": True,
                    "environment_before_sha256": stable_hash(before),
                    "environment_after_sha256": stable_hash(after),
                    "read_flag_changes": changes,
                }
            )
    require(reconstructed == prefix, "Reconstructed transcript differs from verified source")
    return {
        "messages": reconstructed,
        "wire_messages": [wire(runtime, m) for m in reconstructed],
        "environment": environment.model_dump(mode="json"),
        "steps": steps,
        "complete_group_ranges": [list(range(start, end)) for start, end in groups],
        "completed_tool_steps": len(steps),
        "tool_errors": sum(s["actual_error"] is not None for s in steps),
    }


def select_checkpoints(checkpoints):
    result = []
    for task in TASKS:
        candidates = [
            c
            for c in checkpoints
            if c["suite"] == "workspace"
            and c["task"] == task
            and c["condition"] == "attack"
            and c["policy"] == "full"
            and c["intervention_eligible"]
        ]
        require(candidates, f"No feasible Full checkpoint: {task}")
        result.append(
            min(candidates, key=lambda c: (c["request_index"], c["precision"], c["run_id"]))
        )
    return result


def load_mechanisms(root, hashes):
    def read(relative, expected=None):
        path = (root / relative).resolve()
        require(
            not Path(relative).is_absolute() and path.is_relative_to(root), "Source escapes root"
        )
        data = path.read_bytes()
        require(expected is None or digest(data) == expected, f"Source hash mismatch: {relative}")
        hashes[relative] = digest(data)
        return data

    verification = json.loads(read(f"{MECHANISMS}/verification.json"))
    source_lock = json.loads(read(f"{MECHANISMS}/sources.json", verification["source_lock_sha256"]))
    script = "scripts/profile_phase2_mechanisms.py"
    read(script, verification["script_sha256"])
    checkpoints = json.loads(
        read(f"{MECHANISMS}/checkpoints.json", verification["output_sha256"]["checkpoints.json"])
    )
    spec = importlib.util.spec_from_file_location("checkpoint_mechanisms_source", root / script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class TrackedSources(module.Sources):
        def read(self, relative):
            read(relative, self.hashes[relative])
            return super().read(relative)

    return module, TrackedSources(root, source_lock["sha256"]), checkpoints, read


def source_environment_changes(mechanisms, initial, restored):
    changes = mechanisms.changes(initial, restored)
    return {"changed": bool(changes), "changes": changes}


def build(root):
    root = root.resolve()
    sys.path.insert(0, str(root / "src"))
    hashes = {}
    mechanisms, sources, checkpoints, read = load_mechanisms(root, hashes)
    qualification = json.loads(read(CAPABILITY))
    qualified = {(t["suite"], t["task"]): t for t in qualification["tasks"]}
    rows = sources.json(mechanisms.EPISODES)
    results, snapshots = [], {}
    for checkpoint in select_checkpoints(checkpoints):
        row = next(
            r
            for r in rows
            if r["run_id"] == checkpoint["run_id"] and r["episode_id"] == checkpoint["episode_id"]
        )
        episode = mechanisms.load_episode(sources, row)
        view = next(
            v for v in episode["views"] if v["request_index"] == checkpoint["request_index"]
        )
        screened = mechanisms.checkpoint(episode, view, 4992)
        require(
            view["sent"] and screened["intervention_eligible"],
            "Checkpoint fails source pre-write screen",
        )
        for key, value in screened.items():
            require(checkpoint[key] == value, f"Checkpoint changed: {key}")
        manifest_path = str(Path(row["source"]).parents[1] / "manifest.json")
        manifest = sources.json(manifest_path)
        require(manifest["identity"] == row["identity"], "Run manifest identity mismatch")
        configuration = manifest["config"]
        runtime = runtime_for(configuration["experiment"]["benchmark_version"], row["suite"])
        actual_implementation = runtime.implementation()
        for key in ("harness_sha256", "agentdojo_version", "agentdojo_source"):
            require(
                actual_implementation[key] == manifest["implementation"][key],
                f"Installed implementation differs: {key}",
            )
        package = importlib.metadata.distribution("agentdojo")
        benchmark_sources = {}
        for file in package.files or []:
            if str(file).startswith("agentdojo/") and str(file).endswith(".py"):
                path = Path(package.locate_file(file)).resolve()
                relative = str(path.relative_to(root))
                benchmark_sources[str(file)] = digest(read(relative))
        for module in ("agent", "memory", "config", "runner"):
            read(f"src/agent_tradeoffs/{module}.py")
        capability = qualified.get((row["suite"], row["task"]))
        if capability:
            for seed in capability["per_seed"]:
                read(seed["trace_path"], seed["trace_sha256"])
        evidence = {
            "suite": row["suite"],
            "task": row["task"],
            "precision": row["precision"],
            "run_id": row["run_id"],
            "episode_id": row["episode_id"],
            "identity": row["identity"],
            "source": row["source"],
            "request_index": checkpoint["request_index"],
            "selection": "earliest eligible attacked Full request; precision tie-break f16/q4/q8",
            "historical_capability_qualified": capability["eligible"] if capability else None,
            "capability_scope": "source-bound qualification row"
            if capability
            else "not in reviewed qualification candidates; unknown",
            "benchmark_version": configuration["experiment"]["benchmark_version"],
            "installed_implementation": {
                k: actual_implementation[k]
                for k in ("harness_sha256", "agentdojo_version", "agentdojo_source")
            },
            "installed_benchmark_python_files_sha256": stable_hash(benchmark_sources),
            "source_pre_write_screen_verified": True,
        }
        trace = episode["trace"]
        initial = trace["initial_environment"]
        try:
            replay = replay_prefix(
                runtime, initial, trace["messages"][: checkpoint["prefix_end_exclusive"]]
            )
            restore_environment(runtime, replay["environment"])
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
            agent = SimpleNamespace(config=runtime.config_type.model_validate(configuration))
            payload = runtime.request_payload(agent, replay["wire_messages"], tools)
            original = episode["sent"][checkpoint["request_index"]][1]["payload"]
            require(payload == original, "Reconstructed request differs from actual sent payload")
            require(
                stable_hash(payload) == checkpoint["full_payload_sha256"],
                "Checkpoint payload hash mismatch",
            )
            require(
                len(replay["complete_group_ranges"]) == checkpoint["request_index"],
                "Completed group/request mismatch",
            )
            changed_ids = [
                str(k)
                for k in initial["inbox"]["emails"]
                if initial["inbox"]["emails"][k]["read"]
                != replay["environment"]["inbox"]["emails"][k]["read"]
            ]
            evidence.update(
                {
                    "status": "exact_replay_verified",
                    "payload_sha256": stable_hash(payload),
                    "tools_sha256": stable_hash(tools),
                    "initial_environment_sha256": stable_hash(initial),
                    "reconstructed_environment_sha256": stable_hash(replay["environment"]),
                    "completed_tool_steps": replay["completed_tool_steps"],
                    "completed_tool_exchanges": len(replay["complete_group_ranges"]),
                    "tool_errors": replay["tool_errors"],
                    "steps": replay["steps"],
                    "read_flag_changed_ids": changed_ids,
                    "state_changes": source_environment_changes(
                        mechanisms, initial, replay["environment"]
                    ),
                    "environment_matches_episode_final": replay["environment"]
                    == trace["final_environment"],
                    "final_environment_used_to_restore": False,
                    "typed_checkpoint_round_trip_verified": True,
                }
            )
            snapshot = {
                "source_identity": row["identity"],
                "source": row["source"],
                "request_index": checkpoint["request_index"],
                "payload": payload,
                "environment": replay["environment"],
                "messages": replay["messages"],
                "completed_tool_steps": replay["completed_tool_steps"],
                "completed_model_requests": checkpoint["request_index"],
                "tool_errors": replay["tool_errors"],
                "memory": {"policy": "full", "boundary": 2, "summary": ""},
                "complete_group_ranges": replay["complete_group_ranges"],
                "protected_latest_complete_group": checkpoint["protected_latest_complete_group"],
                "proposed_removed_source_message_ids": checkpoint[
                    "proposed_removed_source_message_ids"
                ],
                "pending_model_call_performed": False,
            }
            name = f"{row['run_id']}-request{checkpoint['request_index']}.json"
            snapshots[name] = snapshot
            evidence["snapshot"] = name
        except (ValueError, TypeError, KeyError) as error:
            evidence.update(status="replay_rejected", blocker=str(error))
        results.append(evidence)
    return {
        "schema_version": 1,
        "scope": "offline exact prefix replay; at most two real cases",
        "model_calls": 0,
        "cases": results,
        "source_sha256": dict(sorted(hashes.items())),
        "limits": [
            "No model continuation or causal effect evaluated; "
            "no new actions after the checkpoint.",
            "No clock freezing, generated IDs, copied tool output substitution "
            "or write-tool replay.",
            "Typed initial snapshot must round-trip exactly; "
            "arbitrary edited environments are rejected.",
            "Workspace14 has no reviewed qualification row; "
            "this replay is not capability evidence.",
        ],
    }, snapshots


def readme(report):
    lines = [
        "# 两例 checkpoint 的精确离线重建",
        "",
        "只回放已验证前缀中的模拟工具调用，不运行模型、不执行 checkpoint 后的动作。",
        "使用已安装且与原 manifest 一致的 AgentDojo 版本、commit 和 harness 哈希；",
        "直接调用 FunctionsRuntime.run_function、原 tool_result_to_text、"
        "消息转换与 request_payload。",
        "每条实际工具返回（包括错误）都必须精确匹配原始记录。",
        "",
        "| 案例 | checkpoint | 状态 | 完成工具数 | 已读变化 |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['run_id']} | {case['request_index']} | {case['status']} | "
            f"{case.get('completed_tool_steps', '—')} | {case.get('read_flag_changed_ids', [])} |"
        )
        if "blocker" in case:
            lines += ["", f"拒绝原因：{case['blocker']}"]
    lines += [
        "",
        "Workspace28 的最早可用 Full 窗口是 Q8 request2，历史能力资格为 true。",
        "Workspace14 是 F16 request3，不在能力 lane 已审阅候选中，资格仍为 unknown。",
        "请求编号从0开始。一个 assistant 的多个工具调用作为同一完整组，依次回放并保留状态。",
        "Workspace14 的邮件读取会改变真实 typed 对象；initial_emails/emails/received 的别名由",
        "原始模型校验器恢复，不从 final_environment 拷贝状态，也不替换工具输出。",
        "",
        "每个快照保存重建环境、完整历史、工具 schema、原采样参数和完成步数；summary.json",
        "记录逐步返回哈希、环境变化及源文件哈希。重建初始状态必须逐字段往返相等；",
        "前缀必须通过机制 lane 的保守 pre-write screen。发现未知工具、结果不一致或不可确定行为",
        "即拒绝，不冻结系统时间、不伪造 ID、不扩展到完整 continuation runner。",
        "",
        "```sh",
        "SOURCE=/Users/wang/CSE-291A-Project",
        'PYTHONDONTWRITEBYTECODE=1 "$SOURCE/.venv/bin/python" '
        "scripts/phase2_followup_checkpoint.py \\",
        '  --source-root "$SOURCE" --output results/phase2-followup-v1/checkpoint-restore',
        'PYTHONDONTWRITEBYTECODE=1 "$SOURCE/.venv/bin/python" -m pytest -q \\',
        "  -p no:cacheprovider tests/test_phase2_followup_checkpoint.py",
        "```",
        "",
        "成功只证明这两条已记录前缀可精确重建，不证明后续推理可复现、攻击被阻止或干预有效。",
        "结构/源校验失败返回非零且不写输出；真实回放拒绝保存 blocker 报告并返回非零。",
        "",
    ]
    return "\n".join(lines).encode()


def publish(output, report, snapshots):
    artifacts = {
        "summary.json": encoded(report),
        **{name: encoded(value) for name, value in snapshots.items()},
        "README.md": readme(report),
    }
    artifacts["verification.json"] = encoded(
        {
            "generator_sha256": digest(Path(__file__).read_bytes()),
            "model_calls": 0,
            "source_files": len(report["source_sha256"]),
            "artifact_sha256": {name: digest(data) for name, data in artifacts.items()},
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        target = output / name
        require(not target.is_symlink(), "Output symlink refused")
        if target.exists() and target.read_bytes() == data:
            continue
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        os.replace(temporary, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report, snapshots = build(args.source_root)
        publish(args.output, report, snapshots)
        statuses = {case["run_id"]: case["status"] for case in report["cases"]}
        print(json.dumps(statuses, sort_keys=True))
        return 0 if all(s == "exact_replay_verified" for s in statuses.values()) else 1
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f"Checkpoint evidence rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
