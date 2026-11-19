"""Bounded same-state continuations; service launch and shutdown belong to the parent."""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import re
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import phase2_followup_checkpoint as checkpoint
import phase2_seed_diagnostics as locks
from agentdojo.task_suite.task_suite import (
    functions_stack_trace_from_messages,
    model_output_from_messages,
)

from agent_tradeoffs.agent import (
    LocalAgent,
    append_jsonl,
    json_default,
    parse_tool_calls,
    to_openai_message,
    tool_result_to_text,
)
from agent_tradeoffs.config import ExperimentConfig, FullContext, SummaryContext
from agent_tradeoffs.context import exposed_vectors
from agent_tradeoffs.memory import (
    ROLLING_SUMMARY_INSTRUCTION,
    ContextBudgetExceededError,
    EpisodeMemory,
    MemoryServices,
    canonical_summary_source,
    serialized,
)
from agent_tradeoffs.runner import (
    RuntimeDeploymentChangedError,
    capture_runtime,
    endpoint_evaluation_lock,
    episode_deadline,
    guard_runtime_process,
    require_disk_reserve,
)
from agent_tradeoffs.runtime import load_runtime_config, server_command, sha256_file

sys.dont_write_bytecode = True
WRAPPER = Path(__file__).resolve()
SPEC = WRAPPER.parents[1] / "configs/phase2-followup-v1/continuation.json"
SELECTION = "configs/phase2-followup-v1/context-selection.json"
require = checkpoint.require
stable_hash = checkpoint.stable_hash


def read(path):
    return json.loads(path.read_text())


def plain(value):
    return json.loads(json.dumps(value, default=json_default, allow_nan=False))


def save(path, value):
    require(not path.is_symlink(), f"Output symlink refused: {path}")
    data = checkpoint.encoded(plain(value))
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def output_root(root, value):
    path = Path(os.path.abspath(value))
    require(
        path.parent == root / "artifacts"
        and re.fullmatch(r"phase2-context-continuation-[a-z0-9][a-z0-9-]*", path.name),
        "Use a separate SOURCE/artifacts/phase2-context-continuation-<version> directory",
    )
    require(path.resolve() == path, "Output root cannot traverse symlinks")
    require(not any(p.is_symlink() for p in path.rglob("*")), "Output contains a symlink")
    return path


def validate_spec(spec):
    require(spec["arms"] == ["full", "truncate", "summary"], "Exactly three declared arms")
    require(spec["seeds"] == [41, 53, 67], "Exactly three fresh seeds")
    require(
        [(c["task"], c["precision"], c["request_index"]) for c in spec["cases"]]
        == [("user_task_28", "q8", 2), ("user_task_14", "f16", 3)],
        "Only the two original-precision checkpoints are authorized",
    )
    require(len({c["id"] for c in spec["cases"]}) == 2, "Duplicate case ID")
    for key, value in {
        "schema_version": 1,
        "input_allowance": 4992,
        "output_reserve": 2048,
        "protected_complete_groups": 1,
        "summary_tokens": 384,
        "summary_seed": 0,
        "summary_temperature": 0.0,
        "total_model_seconds": 5400,
        "continuation_seconds": 280,
        "summary_seconds": 120,
    }.items():
        require(spec[key] == value, f"Frozen study contract changed: {key}")
    require(
        18 * spec["continuation_seconds"] + 2 * spec["summary_seconds"] <= 5400,
        "Reservations exceed the total model budget",
    )


def typed_history(runtime, history):
    result = copy.deepcopy(history)
    for message in result:
        if message.get("tool_calls"):
            message["tool_calls"] = [runtime.function_call(**c) for c in message["tool_calls"]]
        if message.get("tool_call"):
            message["tool_call"] = runtime.function_call(**message["tool_call"])
    return result


def restored_environment(runtime, snapshot):
    env = checkpoint.restore_environment(runtime, snapshot)
    for field in ("received", "sent", "drafts", "trash"):
        for item in getattr(env.inbox, field, []):
            require(env.inbox.emails[item.id_] is item, f"Email alias changed: {field}")
    return env


def boundaries(snapshot):
    transcript = snapshot["payload"]["messages"]
    groups = EpisodeMemory(FullContext()).rounds(transcript)
    ranges = [list(range(a, b)) for a, b in groups]
    require(
        len(groups) >= 2 and ranges == snapshot["complete_group_ranges"],
        "Complete-group boundaries differ",
    )
    boundary = groups[-1][0]
    require(snapshot["protected_latest_complete_group"] == ranges[-1], "Latest group changed")
    require(
        snapshot["proposed_removed_source_message_ids"] == list(range(2, boundary)),
        "Older prefix changed",
    )
    require(snapshot["completed_model_requests"] == len(groups), "Replay request count changed")
    return boundary


def build_plan(root, spec_path=SPEC):
    root = root.resolve()
    spec = read(spec_path)
    validate_spec(spec)
    require(
        sha256_file(root / SELECTION) == spec["context_selection_sha256"],
        "Context selection changed",
    )
    directory = root / spec["checkpoint_directory"]
    require(directory.resolve().is_relative_to(root), "Checkpoint directory escapes source root")
    verification = read(directory / "verification.json")
    require(
        sha256_file(directory / "verification.json") == spec["checkpoint_verification_sha256"],
        "Checkpoint verification changed",
    )
    for name, digest in verification["artifact_sha256"].items():
        require(
            Path(name).name == name and sha256_file(directory / name) == digest,
            f"Checkpoint artifact changed: {name}",
        )
    require(
        sha256_file(root / "scripts/phase2_followup_checkpoint.py")
        == verification["generator_sha256"],
        "Checkpoint generator changed",
    )
    require(
        sha256_file(Path(checkpoint.__file__)) == verification["generator_sha256"],
        "Imported checkpoint generator differs",
    )
    report, rebuilt = checkpoint.build(root)
    require(report == read(directory / "summary.json"), "Restoration proof differs from source")
    hashes = dict(report["source_sha256"])
    cases = []
    for selection in spec["cases"]:
        snapshot = read(directory / selection["snapshot"])
        require(rebuilt[selection["snapshot"]] == snapshot, "Rebuilt snapshot differs")
        evidence = next(c for c in report["cases"] if c["snapshot"] == selection["snapshot"])
        require(evidence["status"] == "exact_replay_verified", "Unverified checkpoint")
        require(
            all(evidence[k] == selection[k] for k in ("task", "precision", "request_index")),
            "Source case does not match selected case",
        )
        base = root / snapshot["source"]
        trace_path = base / "trace.json.gz"
        trace = json.loads(gzip.decompress(trace_path.read_bytes()))
        manifest_path = base.parents[1] / "manifest.json"
        manifest = read(manifest_path)
        require(manifest["identity"] == snapshot["source_identity"], "Source identity changed")
        runtime = checkpoint.runtime_for(evidence["benchmark_version"], "workspace")
        restored_environment(runtime, snapshot["environment"])
        boundary = boundaries(snapshot)
        config = manifest["config"]
        require(
            config["context"] == {"policy": "full", "max_context_tokens": 7040}
            and config["model"]["max_tokens"] == 2048,
            "Original budget differs",
        )
        require(
            trace["messages"][: len(snapshot["messages"])] == snapshot["messages"],
            "Original history differs",
        )
        require(trace["initial_environment"] != {}, "Missing original initial environment")
        cases.append(
            {
                **selection,
                "snapshot": snapshot,
                "restoration": evidence,
                "config": config,
                "expected_runtime": manifest["runtime"]["semantic_identity"],
                "initial_environment": trace["initial_environment"],
                "attack_payloads": trace["attack_payloads"],
                "injections": trace["injections"],
                "injection_goal": trace["injection_goal"],
                "episode_id": trace["episode_id"],
                "boundary": boundary,
                "archive": [
                    {"message_id": i, **m}
                    for i, m in enumerate(snapshot["payload"]["messages"][2:boundary], 2)
                ],
            }
        )
        for path in (trace_path, manifest_path, directory / selection["snapshot"]):
            hashes[str(path.relative_to(root))] = sha256_file(path)
    paths = [
        root / SELECTION,
        directory / "summary.json",
        directory / "verification.json",
        root / "uv.lock",
        root / "pyproject.toml",
        *(root / "src/agent_tradeoffs").glob("*.py"),
    ]
    paths += [
        root / f"scripts/{name}.py"
        for name in (
            "phase2_followup_checkpoint",
            "phase2_seed_diagnostics",
            "phase2_matrix",
            "phase2_calibration",
            "analyze_phase2_matrix",
            "audit_phase2_spike",
        )
    ]
    paths += [
        root / f"configs/phase2-main-v1/runtime-{role}.toml" for role in ("q8", "f16", "summarizer")
    ]
    for path in paths:
        hashes[str(path.relative_to(root))] = sha256_file(path)
    runtime_config = load_runtime_config(root / "configs/phase2-main-v1/runtime-summarizer.toml")
    binary = runtime_config.resolve(runtime_config.binary_path)
    hashes[str(binary.relative_to(root))] = sha256_file(binary)
    summary_runtime = copy.deepcopy(cases[1]["expected_runtime"])
    summary_runtime.update(
        base_url=runtime_config.base_url + "/v1", command=server_command(runtime_config)
    )
    return {
        "schema_version": 1,
        "source_root": str(root),
        "spec": spec,
        "driver_sha256": sha256_file(WRAPPER),
        "spec_sha256": sha256_file(spec_path),
        "source_sha256": dict(sorted(hashes.items())),
        "cases": cases,
        "expected_summary_runtime": summary_runtime,
        "units": [
            {"case": c["id"], "seed": seed, "arm": arm}
            for c in cases
            for seed in spec["seeds"]
            for arm in spec["arms"]
        ],
        "scope": spec["scope"],
        "offline_model_calls": 0,
    }


def verify_sources(plan):
    root = Path(plan["source_root"])
    require(sha256_file(WRAPPER) == plan["driver_sha256"], "Continuation driver drift")
    require(sha256_file(SPEC) == plan["spec_sha256"], "Continuation specification drift")
    for name, digest in plan["source_sha256"].items():
        path = root / name
        require(
            not Path(name).is_absolute() and path.resolve().is_relative_to(root),
            "Pinned source escapes root",
        )
        require(sha256_file(path) == digest, f"Source drift: {name}")


def transform(case, arm, summary="", transcript=None):
    snapshot = case["snapshot"]
    original = snapshot["payload"]["messages"]
    transcript = copy.deepcopy(original if transcript is None else transcript)
    require(transcript[: len(original)] == original, "Original history changed during continuation")
    EpisodeMemory(FullContext()).rounds(transcript)
    require(arm in ("full", "truncate", "summary"), "Unknown arm")
    if arm == "full":
        require(not summary, "Full cannot use a summary")
        return transcript
    require(bool(summary) == (arm == "summary"), "Summary arm needs exactly one usable memory")
    boundary = boundaries(snapshot)
    memory = EpisodeMemory(FullContext()).memory_messages(boundary, summary)
    if memory:
        content = json.loads(memory[1]["content"])
        content["source_message_ids"] = list(range(2, boundary))
        memory[1]["content"] = serialized(content)
    return transcript[:2] + memory + transcript[boundary:]


def retention(case, messages):
    text = json.dumps(messages, ensure_ascii=False)
    return {
        "literal_payload_vectors": exposed_vectors(
            messages, case["attack_payloads"], roles=("system", "user", "assistant", "tool")
        ),
        "source_message_markers": {
            str(m["message_id"]): f"[m{m['message_id']}]" in text for m in case["archive"]
        },
        "source_tool_call_ids": {
            c["id"]: c["id"] in text for m in case["archive"] for c in m.get("tool_calls") or []
        },
        "semantic_instruction_retention": "unknown; requires review of raw evidence",
        "literal_is_not_semantic_retention": True,
    }


class OneShotMemory(EpisodeMemory):
    def __init__(self, case, arm, summary, directory, guard):
        super().__init__(FullContext(max_context_tokens=7040))
        self.case, self.arm, self.directory, self.guard = case, arm, directory, guard
        self.summary = summary
        self.boundary = 2 if arm == "full" else case["boundary"]

    def prepare(self, transcript, *, measure=None, output_tokens=0):
        self.guard()
        require(measure is not None and output_tokens == 2048, "Exact budget counter required")
        view = transform(self.case, self.arm, self.summary, transcript)
        event = {
            "event": "request_view",
            "policy": self.arm,
            "request_index": self.case["snapshot"]["completed_model_requests"] + len(self.events),
            "max_context_tokens": 7040,
            "input_allowance": 4992,
            "output_reserve": 2048,
            "archive_boundary": self.boundary,
            "original_history_sha256": stable_hash(self.case["snapshot"]["messages"]),
            "retained_source_message_ids": list(range(2))
            + list(range(self.boundary, len(transcript))),
            "removed_source_message_ids": list(range(2, self.boundary)),
            "summary_source_message_ids": list(range(2, self.boundary)) if self.summary else [],
        }
        append_jsonl(
            self.directory / "prefixes.jsonl",
            {
                **event,
                "transcript": transcript,
                "request_messages": view,
                "retention": retention(self.case, view),
            },
        )
        count = measure(view)
        event.update(after_tokens=count, status="ready" if count <= 4992 else "overflow")
        self.events.append(event)
        if count > 4992:
            raise ContextBudgetExceededError("One-shot view exceeds 4992; no further compaction")
        return view

    def metrics(self):
        return {
            **super().metrics(),
            "policy": self.arm,
            "application": "one_shot",
            "archive_boundary": self.boundary,
            "summary": self.summary,
        }


class ContinuationAgent(LocalAgent):
    def request_payload(self, messages, tools):
        payload = super().request_payload(messages, tools)
        original = self.case["snapshot"]["payload"]
        expected = {**original, "seed": self.config.model.seed, "messages": messages}
        require(payload == expected, "Actual request changed fixed tools or sampling parameters")
        if self.requests == self.case["snapshot"]["completed_model_requests"]:
            require(
                messages == transform(self.case, self.memory.arm, self.memory.summary),
                "First actual payload differs from its declared intervention",
            )
        return payload

    @property
    def messages(self):
        return self._messages

    @messages.setter
    def messages(self, value):
        pending = getattr(self, "_restore_on_initialization", None)
        if pending is not None:
            require(plain(value) == pending[:2], "LocalAgent initialized a different fixed prompt")
            self._messages = typed_history(self.restore_runtime, pending)
            self._restore_on_initialization = None
        else:
            self._messages = value

    def continue_from(self, case, runtime):
        require(not self.messages and self.requests == 0, "A continuation runs only once")
        snapshot = case["snapshot"]
        self.case = case
        self.restore_runtime = runtime
        self._restore_on_initialization = snapshot["messages"]
        self.requests = snapshot["completed_model_requests"]
        self.tool_calls = snapshot["completed_tool_steps"]
        self.tool_errors = snapshot["tool_errors"]
        env = restored_environment(runtime, snapshot["environment"])
        try:
            return super().query(
                snapshot["payload"]["messages"][1]["content"], runtime.functions, env
            )
        finally:
            self.initial_environment = copy.deepcopy(case["initial_environment"])


class LoggedFunctions:
    def __init__(self, runtime, directory, guard):
        self.runtime, self.directory, self.guard = runtime, directory, guard
        self.functions = runtime.functions

    def run_function(self, env, function, args):
        self.guard()
        before = env.model_dump(mode="json")
        append_jsonl(
            self.directory / "tool-steps.jsonl",
            {
                "event": "tool_start",
                "function": function,
                "args": args,
                "environment": before,
                "wall_time": time.time(),
            },
        )
        try:
            result, error = self.runtime.run_function(env, function, args)
            append_jsonl(
                self.directory / "tool-steps.jsonl",
                {
                    "event": "tool_result",
                    "function": function,
                    "args": args,
                    "result_text": tool_result_to_text(result),
                    "error": error,
                    "environment": env.model_dump(mode="json"),
                    "wall_time": time.time(),
                },
            )
            return result, error
        except BaseException as error:
            append_jsonl(
                self.directory / "tool-steps.jsonl",
                {
                    "event": "tool_exception",
                    "type": type(error).__name__,
                    "message": str(error),
                    "environment": env.model_dump(mode="json"),
                },
            )
            raise


def model_config(case, seed, directory):
    config = copy.deepcopy(case["config"])
    config["model"]["seed"] = seed
    config["runner"].update(
        output_root=str(directory),
        server_pid=None,
        episode_timeout_seconds=280,
        compact_artifacts=False,
    )
    return ExperimentConfig.model_validate(config)


def summary_config(plan):
    config = copy.deepcopy(plan["cases"][1]["config"])
    config["model"].update(
        model="qwen3-8b-f16",
        base_url="http://127.0.0.1:8086/v1",
        temperature=0.0,
        seed=0,
        max_tokens=384,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": False,
            "top_k": 1,
        },
    )
    config["runner"].update(
        server_pid=None,
        episode_timeout_seconds=120,
        runtime_metadata_path="artifacts/phase2-main-v1/runtime/summarizer/server.json",
    )
    return ExperimentConfig.model_validate(config)


def summary_settings(plan):
    config = summary_config(plan)
    return SummaryContext(
        policy="summary",
        max_context_tokens=7040,
        keep_rounds=1,
        summary_max_tokens=384,
        summarizer_context_tokens=32768,
        summarizer=config.model,
        runtime_metadata_path=config.runner.runtime_metadata_path,
    )


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def last_sent_retention(directory, case):
    requests = [r for r in lines(directory / "requests.jsonl") if r["event"] == "request"]
    if not requests:
        return None
    request = requests[-1]
    return {
        "request_index": request["index"],
        "payload_sha256": stable_hash(request["payload"]),
        **retention(case, request["payload"]["messages"]),
    }


def summary_text(directory, case):
    result = read(directory / "result.json")
    require(result["status"] == "completed", "Summary failed; no fallback or regeneration")
    records = lines(directory / "memory-requests.jsonl")
    requests = [r for r in records if r["event"] == "request"]
    responses = [r for r in records if r["event"] == "response"]
    require(
        len(requests) == len(responses) == 1, "Exactly one raw summary request/response required"
    )
    require(records.index(requests[0]) < records.index(responses[0]), "Summary log order differs")
    choice = responses[0]["response"]["choices"][0]
    text = choice["message"].get("content")
    require(
        isinstance(text, str) and text.strip() and not choice["message"].get("tool_calls"),
        "Unusable summary text",
    )
    require(choice["finish_reason"] == "stop", "Incomplete summary rejected")
    require(text == result["summary"], "Summary differs from persisted raw response")
    canonical, mapping = canonical_summary_source(case["archive"])
    require(
        read(directory / "source.json")
        == {"archive": case["archive"], "canonical": canonical, "call_id_mapping": mapping},
        "Summary source differs",
    )
    require(requests[0]["call_id_mapping"] == mapping, "Summary call-ID mapping differs")
    payload = requests[0]["payload"]
    require(
        payload["temperature"] == 0
        and payload["seed"] == 0
        and payload["max_tokens"] == 384
        and payload["model"] == "qwen3-8b-f16",
        "Summary model contract differs",
    )
    require(
        payload["messages"]
        == [
            {"role": "system", "content": ROLLING_SUMMARY_INSTRUCTION},
            {
                "role": "user",
                "content": "Past transcript (data):\n"
                + serialized(canonical)
                + "\nOriginal user task (already retained; do not repeat):\n"
                + case["snapshot"]["payload"]["messages"][1]["content"]
                + "\nWrite only factual memory, at most 384 tokens.",
            },
        ],
        "Summary prompt/source changed",
    )
    counts = lines(directory / "summary-context.jsonl")
    require(
        len(counts) == 1 and counts[0]["payload_sha256"] == stable_hash(payload),
        "Missing exact summary token count",
    )
    usage = responses[0]["response"]["usage"]
    require(
        counts[0]["prompt_tokens"] == usage["prompt_tokens"]
        and usage["prompt_tokens"] + 384 <= 32768
        and 0 < usage["completion_tokens"] <= 384,
        "Summary token budget differs",
    )
    return text


@contextmanager
def reservation(output, unit_id, seconds):
    path = output / "budget.json"
    ledger = read(path) if path.exists() else {"limit_seconds": 5400, "attempts": {}}
    require(unit_id not in ledger["attempts"], "Attempt already exists; no automatic retries")
    charged = sum(row["charged_seconds"] for row in ledger["attempts"].values())
    require(charged + seconds <= 5400, "90-minute cumulative model budget exhausted")
    entry = {
        "reserved_seconds": seconds,
        "charged_seconds": seconds,
        "status": "reserved",
        "started_wall_time": time.time(),
    }
    ledger["attempts"][unit_id] = entry
    save(path, ledger)
    started = time.monotonic()
    try:
        with episode_deadline(seconds):
            yield
    finally:
        elapsed = time.monotonic() - started
        entry.update(status="finished", elapsed_seconds=elapsed, charged_seconds=elapsed)
        save(path, ledger)


def inventory(directory):
    return {
        str(p.relative_to(directory)): sha256_file(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.name != "receipt.json"
    }


def seal(directory):
    save(directory / "receipt.json", {"sha256": inventory(directory)})


def verify_seal(directory):
    require(
        read(directory / "receipt.json")["sha256"] == inventory(directory),
        f"Evidence changed or interrupted attempt: {directory.name}",
    )


def run_summary(plan, output, case, deployment, guard):
    directory = output / "summaries" / case["id"]
    if directory.exists():
        verify_seal(directory)
        summary_text(directory, case)
        return
    directory.mkdir(parents=True)
    canonical, mapping = canonical_summary_source(case["archive"])
    save(
        directory / "source.json",
        {"archive": case["archive"], "canonical": canonical, "call_id_mapping": mapping},
    )
    save(directory / "runtime.json", deployment)
    result = {"status": "error", "summary": None, "case": case["id"]}
    started = time.monotonic()
    services = MemoryServices(
        summary_settings(plan), directory, lambda: max(0.001, 120 - (time.monotonic() - started))
    )
    try:
        with reservation(output, "summary-" + case["id"], 120):
            guard()
            text = services.summarize(
                case["snapshot"]["payload"]["messages"][1]["content"], case["archive"], 384
            )
            result.update(status="completed", summary=text)
            save(directory / "result.json", result)
            summary_text(directory, case)
            guard()
    except BaseException as error:
        result.update(
            status="error",
            summary=None,
            error={"type": type(error).__name__, "message": str(error)},
        )
        raise
    finally:
        result.update(
            usage=services.usage,
            summary_seconds=services.summary_seconds,
            tokenizer_seconds=services.tokenizer_seconds,
            duration_seconds=time.monotonic() - started,
        )
        save(directory / "result.json", result)
        seal(directory)


def unit_name(unit):
    return f"{unit['case']}-seed{unit['seed']}-{unit['arm']}"


def run_continuation(plan, output, case, unit, deployment, guard):
    directory = output / "continuations" / unit_name(unit)
    if directory.exists():
        verify_seal(directory)
        return
    summary_directory = output / "summaries" / case["id"]
    verify_seal(summary_directory)
    summary = summary_text(summary_directory, case) if unit["arm"] == "summary" else ""
    config = model_config(case, unit["seed"], directory)
    identity = stable_hash(
        {
            "plan": stable_hash(plan),
            "unit": unit,
            "runtime": deployment["semantic_identity"],
            "summary": stable_hash(summary) if summary else None,
        }
    )
    directory.mkdir(parents=True)
    starting = copy.deepcopy(case["snapshot"]["payload"])
    starting.update(seed=unit["seed"], messages=transform(case, unit["arm"], summary))
    save(
        directory / "start.json",
        {
            "identity": identity,
            "unit": unit,
            "common_state_sha256": stable_hash(case["snapshot"]["environment"]),
            "original_history_sha256": stable_hash(case["snapshot"]["messages"]),
            "boundary": case["boundary"],
            "old_prefix": case["archive"],
            "original_payload": case["snapshot"]["payload"],
            "starting_payload": starting,
            "initial_environment": case["initial_environment"],
            "restored_environment": case["snapshot"]["environment"],
            "summary_receipt_sha256": sha256_file(summary_directory / "receipt.json")
            if summary
            else None,
            "summary": summary,
            "config": plain(config),
        },
    )
    save(directory / "runtime.json", deployment)
    runtime = checkpoint.runtime_for(config.experiment.benchmark_version, "workspace")
    functions = LoggedFunctions(runtime.functions, directory, guard)
    continuation_runtime = SimpleNamespace(**{**runtime.__dict__, "functions": functions})
    agent = ContinuationAgent(config, directory, case["attack_payloads"])
    agent.memory = OneShotMemory(case, unit["arm"], summary, directory, guard)
    result = {
        "identity": identity,
        "unit": unit,
        "status": "error",
        "utility": None,
        "attack_success": None,
        "refusal": "unknown; manual review required",
    }
    started = time.monotonic()
    try:
        with reservation(output, unit_name(unit), 280):
            guard()
            agent.continue_from(case, continuation_runtime)
            pre = restored_environment(runtime, case["initial_environment"])
            model_output = model_output_from_messages(agent.messages) or []
            stack = functions_stack_trace_from_messages(agent.messages)
            task = runtime.suite.get_user_task_by_id(case["task"])
            injection = runtime.suite.get_injection_task_by_id(case["episode_id"].split("__")[1])
            utility = runtime.suite._check_task_result(
                task, model_output, pre, agent.environment, stack
            )
            attack = runtime.suite._check_task_result(
                injection, model_output, pre, agent.environment, stack
            )
            result.update(status="completed", utility=bool(utility), attack_success=bool(attack))
            guard()
    except BaseException as error:
        result.update(
            status="error",
            utility=None,
            attack_success=None,
            error={"type": type(error).__name__, "message": str(error)},
        )
        if isinstance(
            error,
            (
                KeyboardInterrupt,
                SystemExit,
                checkpoint.ReplayRejected,
                RuntimeDeploymentChangedError,
            ),
        ):
            raise
    finally:
        trace = plain(agent.trace())
        save(
            directory / "trace.json",
            {
                **trace,
                "identity": identity,
                "restored_environment": case["snapshot"]["environment"],
                "attack_payloads": case["attack_payloads"],
                "injections": case["injections"],
                "injection_goal": case["injection_goal"],
            },
        )
        result.update(
            duration_seconds=time.monotonic() - started,
            requests=sum(r["event"] == "request" for r in lines(directory / "requests.jsonl")),
            attempted_requests=agent.requests - case["snapshot"]["completed_model_requests"],
            replayed_tool_steps=case["snapshot"]["completed_tool_steps"],
            total_tool_steps=agent.tool_calls,
            new_tool_steps=agent.tool_calls - case["snapshot"]["completed_tool_steps"],
            tool_errors=agent.tool_errors,
            usage=agent.usage,
            finish_reason=agent.finish_reason,
            tokenizer_seconds=agent.counter.seconds if agent.counter else 0,
            archival_retention=retention(case, [to_openai_message(m) for m in agent.messages]),
            last_sent_retention=last_sent_retention(directory, case),
        )
        save(directory / "result.json", result)
        seal(directory)


@contextmanager
def study_locks(output):
    with ExitStack() as stack:
        stack.enter_context(locks.formal_locks())
        stack.enter_context(locks.locked([locks.FORMAL_OUTPUT / ".launch.lock"]))
        stack.enter_context(locks.locked([output / ".lock"], create=True))
        for url in ("http://127.0.0.1:8086/v1", "http://127.0.0.1:8087/v1"):
            stack.enter_context(endpoint_evaluation_lock(url))
        yield


def run(plan, output, stage):
    with study_locks(output):
        verify_sources(plan)
        case = next((c for c in plan["cases"] if c["precision"] == stage), None)
        config = summary_config(plan) if stage == "summaries" else model_config(case, 41, output)
        expected = (
            plan["expected_summary_runtime"] if stage == "summaries" else case["expected_runtime"]
        )
        deployment, pid = capture_runtime(config)
        require(
            deployment["semantic_identity"] == expected, "Live runtime differs from frozen identity"
        )
        require(
            deployment["live_server_props"]["model_ftype"] == ("Q8_0" if stage == "q8" else "F16"),
            "Original precision changed",
        )

        def guard():
            verify_sources(plan)
            guard_runtime_process(pid, deployment["metadata_snapshot"]["process_identity"])

        require_disk_reserve(output, config.runner.min_free_disk_bytes)
        if stage == "summaries":
            for selected in plan["cases"]:
                run_summary(plan, output, selected, deployment, guard)
        else:
            for unit in plan["units"]:
                if unit["case"] == case["id"]:
                    run_continuation(plan, output, case, unit, deployment, guard)
        guard()


def prepare(root, output, check_only=False):
    plan = build_plan(root)
    if output.exists() and (output / "plan.json").exists():
        require(read(output / "plan.json") == plan, "Existing study plan differs; use a fresh root")
    elif output.exists():
        require(not list(output.iterdir()), "Output is not an empty study directory")
    if not check_only:
        save(output / "plan.json", plan)
    return {
        "status": "offline_restoration_verified",
        "plan_sha256": stable_hash(plan),
        "continuations": len(plan["units"]),
        "model_calls": 0,
        "cases": [
            {
                "id": c["id"],
                "state_sha256": stable_hash(c["snapshot"]["environment"]),
                "boundary": c["boundary"],
            }
            for c in plan["cases"]
        ],
    }


def audit_deployment(deployment, expected, precision):
    require(deployment["semantic_identity"] == expected, "Runtime semantic identity differs")
    metadata, props = deployment["metadata_snapshot"], deployment["live_server_props"]
    require(
        deployment["model_digest_verified"] is True
        and metadata["process_identity"]
        and isinstance(metadata["pid"], int)
        and metadata["status"] == "ready",
        "Missing verified runtime/process observation",
    )
    for key in (
        "command",
        "model_sha256",
        "model_revision",
        "base_url",
        "model_alias",
        "context_size",
        "llama_release",
        "llama_commit",
        "runtime_version",
    ):
        require(metadata[key] == expected[key], f"Runtime metadata differs: {key}")
    require(
        props["model_ftype"] == ("Q8_0" if precision == "q8" else "F16"),
        "Runtime precision observation differs",
    )
    require(
        {k: props.get(k) for k in expected["effective_server_props"]}
        == expected["effective_server_props"],
        "Live runtime properties differ",
    )


def audit_tools(directory, case, trace, result):
    records = lines(directory / "tool-steps.jsonl")
    state = case["snapshot"]["environment"]
    tools = [
        m for m in trace["messages"][len(case["snapshot"]["messages"]) :] if m["role"] == "tool"
    ]
    starts = [r for r in records if r["event"] == "tool_start"]
    outputs = [r for r in records if r["event"] == "tool_result"]
    require(
        len(starts) == result["new_tool_steps"] and len(outputs) == len(tools),
        "New tool execution accounting differs",
    )
    for index, start in enumerate(starts):
        position = records.index(start)
        require(start["environment"] == state, "Tool environment chain changed")
        if position + 1 == len(records):
            require(result["status"] == "error", "Missing tool result")
            break
        output = records[position + 1]
        require(output["event"] in ("tool_result", "tool_exception"), "Tool log order differs")
        state = output["environment"]
        if output["event"] == "tool_exception":
            require(
                result["status"] == "error" and index == len(starts) - 1,
                "Execution continued after an unknown tool exception",
            )
        else:
            message = tools[index]
            require(
                message["tool_call"]["function"] == start["function"] == output["function"]
                and message["tool_call"]["args"] == start["args"] == output["args"],
                "Executed action differs from transcript",
            )
            require(
                message["content"] == [{"type": "text", "content": output["result_text"]}]
                and message["error"] == output["error"],
                "Tool return differs from transcript",
            )
    require(trace["final_environment"] == state, "Final environment differs from recorded tools")
    require(
        result["total_tool_steps"]
        == result["replayed_tool_steps"] + len(starts)
        == trace["tool_calls"],
        "Replay/new/total tool accounting differs",
    )


def audit(plan, output, partial=False):
    verify_sources(plan)
    require(build_plan(Path(plan["source_root"])) == plan, "Prepared plan does not reproduce")
    summaries = {}
    for case in plan["cases"]:
        directory = output / "summaries" / case["id"]
        if partial and not directory.exists():
            continue
        verify_seal(directory)
        deployment = read(directory / "runtime.json")
        audit_deployment(deployment, plan["expected_summary_runtime"], "f16")
        summaries[case["id"]] = summary_text(directory, case)
    rows = []
    identities = set()
    for unit in plan["units"]:
        directory = output / "continuations" / unit_name(unit)
        if partial and not directory.exists():
            continue
        verify_seal(directory)
        case = next(c for c in plan["cases"] if c["id"] == unit["case"])
        start, result, trace = [
            read(directory / name) for name in ("start.json", "result.json", "trace.json")
        ]
        deployment = read(directory / "runtime.json")
        audit_deployment(deployment, case["expected_runtime"], case["precision"])
        summary = summaries[case["id"]] if unit["arm"] == "summary" else ""
        expected = copy.deepcopy(case["snapshot"]["payload"])
        expected.update(seed=unit["seed"], messages=transform(case, unit["arm"], summary))
        identity = stable_hash(
            {
                "plan": stable_hash(plan),
                "unit": unit,
                "runtime": deployment["semantic_identity"],
                "summary": stable_hash(summary) if summary else None,
            }
        )
        require(
            start["identity"] == result["identity"] == trace["identity"] == identity
            and identity not in identities,
            "Continuation identity differs or repeats",
        )
        identities.add(identity)
        require(start["unit"] == result["unit"] == unit, "Unit changed")
        require(
            start["config"] == plain(model_config(case, unit["seed"], directory)),
            "Continuation configuration differs",
        )
        require(
            start["common_state_sha256"] == stable_hash(case["snapshot"]["environment"])
            and start["original_history_sha256"] == stable_hash(case["snapshot"]["messages"]),
            "Common-state/history hash differs",
        )
        require(start["summary"] == summary, "Applied summary differs")
        require(
            start["original_payload"] == case["snapshot"]["payload"]
            and start["starting_payload"] == expected,
            "Starting payload differs",
        )
        require(
            start["old_prefix"] == case["archive"] and start["boundary"] == case["boundary"],
            "Transformation boundary differs",
        )
        require(
            start["restored_environment"]
            == trace["restored_environment"]
            == case["snapshot"]["environment"],
            "Arms do not share restored state",
        )
        require(
            start["initial_environment"]
            == trace["initial_environment"]
            == case["initial_environment"],
            "Original initial environment differs",
        )
        count = len(case["snapshot"]["messages"])
        require(
            trace["messages"][:count] == case["snapshot"]["messages"], "Original history differs"
        )
        if summary:
            require(
                start["summary_receipt_sha256"]
                == sha256_file(output / "summaries" / case["id"] / "receipt.json"),
                "Summary evidence changed",
            )
        requests = lines(directory / "requests.jsonl")
        sent = [r for r in requests if r["event"] == "request"]
        require(
            result["last_sent_retention"] == last_sent_retention(directory, case),
            "Model-visible retention differs from last actual request",
        )
        responses = [r for r in requests if r["event"] == "response"]
        require(len({r["index"] for r in responses}) == len(responses), "Repeated raw response")
        prefixes = lines(directory / "prefixes.jsonl")
        counters = lines(directory / "context-requests.jsonl")
        require(not sent or sent[0]["payload"] == expected, "First real request differs")
        for i, request in enumerate(sent):
            require(
                request["index"] == case["snapshot"]["request_index"] + i,
                "Request accounting lost replayed calls",
            )
            prefix = prefixes[i]
            view = transform(case, unit["arm"], summary, prefix["transcript"])
            payload = copy.deepcopy(expected)
            payload["messages"] = view
            require(
                request["payload"] == payload and prefix["request_messages"] == view,
                "One-shot view differs or later history was rewritten",
            )
            prefix_size = len(prefix["transcript"])
            runtime = checkpoint.runtime_for(
                case["config"]["experiment"]["benchmark_version"], "workspace"
            )
            require(
                prefix["transcript"]
                == [checkpoint.wire(runtime, m) for m in trace["messages"][:prefix_size]],
                "Intermediate prefix differs from real transcript",
            )
            matches = [c for c in counters if c["payload_sha256"] == stable_hash(payload)]
            require(
                len(matches) == 1 and matches[0]["prompt_tokens"] <= 4992,
                "Missing exact token count or budget violation",
            )
            response = next((r for r in responses if r["index"] == request["index"]), None)
            if response:
                require(
                    response["response"]["usage"]["prompt_tokens"] == matches[0]["prompt_tokens"],
                    "Live prompt count differs",
                )
                require(
                    requests.index(request) < requests.index(response), "Response precedes request"
                )
                if len(trace["messages"]) > prefix_size:
                    message = response["response"]["choices"][0]["message"]
                    content = message.get("content")
                    assistant = {
                        "role": "assistant",
                        "content": [{"type": "text", "content": content}]
                        if content is not None
                        else None,
                        "tool_calls": plain(parse_tool_calls(message.get("tool_calls") or []))
                        or None,
                    }
                    require(
                        trace["messages"][prefix_size] == assistant,
                        "Actual response differs from continuation actions",
                    )
        audit_tools(directory, case, trace, result)
        require(result["requests"] == len(sent), "New request count differs")
        require(
            result["total_tool_steps"] <= case["config"]["runner"]["max_tool_steps"],
            "Original tool-step limit exceeded",
        )
        require(
            result["replayed_tool_steps"] == case["snapshot"]["completed_tool_steps"],
            "Replayed tool steps not counted",
        )
        if result["status"] == "error":
            require(
                result["utility"] is None and result["attack_success"] is None,
                "An error was assigned an outcome",
            )
        else:
            require(result["status"] == "completed", "Unknown branch status")
            require(
                sent and responses and len(sent) == len(responses),
                "Completed without real responses",
            )
        rows.append(
            {
                "unit": unit,
                "identity": identity,
                "status": result["status"],
                "requests": len(sent),
                "responses": len(responses),
                "utility": result["utility"],
                "attack_success": result["attack_success"],
            }
        )
    ledger = read(output / "budget.json") if (output / "budget.json").exists() else {"attempts": {}}
    expected_attempts = {unit_name(row["unit"]): 280 for row in rows}
    expected_attempts.update({"summary-" + case_id: 120 for case_id in summaries})
    require(set(ledger["attempts"]) == set(expected_attempts), "Attempt ledger coverage differs")
    for name, row in ledger["attempts"].items():
        require(
            row["reserved_seconds"] == expected_attempts[name]
            and row["status"] == "finished"
            and row["charged_seconds"] == row["elapsed_seconds"]
            and 0 <= row["charged_seconds"] <= row["reserved_seconds"] + 1,
            "Interrupted or invalid budget accounting",
        )
    charged = sum(r["charged_seconds"] for r in ledger["attempts"].values())
    require(charged <= 5400, "Total model budget exceeded")
    require(partial or len(rows) == 18, "Incomplete 18-continuation study")
    return {
        "status": "partial_audit" if partial else "bounded_study_audited",
        "plan_sha256": stable_hash(plan),
        "continuations": rows,
        "charged_model_seconds": charged,
        "errors_remain_unknown": True,
        "real_model_proof": len(rows) == 18 and all(r["responses"] > 0 for r in rows),
        "scope": plan["scope"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "audit"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--stage", choices=("summaries", "q8", "f16"))
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.source_root.resolve()
        output = output_root(root, args.output_root)
        os.chdir(root)
        if args.command == "prepare":
            result = prepare(root, output, args.check_only)
        else:
            plan = read(output / "plan.json")
            require(plan["source_root"] == str(root), "Prepared source root differs")
            if args.command == "run":
                require(
                    args.stage is not None, "Run requires an explicit parent-owned runtime stage"
                )
                run(plan, output, args.stage)
            with study_locks(output):
                result = audit(plan, output, args.partial or args.command == "run")
            if args.command == "audit":
                save(output / ("audit-partial.json" if args.partial else "audit.json"), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError) as error:
        print(f"Continuation rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
