"""Profile declared development tasks and validate the rolling context protocol."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from pathlib import Path

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite

from agent_tradeoffs.agent import LocalAgent, load_system_message, tool_result_to_text
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.memory import MemoryServices
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.request_tokens import RequestTokenCounter
from agent_tradeoffs.runner import (
    ServerMemorySampler,
    implementation_identity,
    make_injections,
    plan_episodes,
    run_experiment,
)
from agent_tradeoffs.runtime import sha256_file, start_server, stop_server

ROOT = Path("artifacts/phase2-calibration")
DEVELOPMENT = {
    "banking": [0, 2, 4],
    "slack": [0, 10, 14],
    "workspace": [0, 20, 34],
    "travel": [0, 17, 18],
}
INJECTION_GOALS = {"banking": 4, "slack": 1, "workspace": 0, "travel": 0}
PLAN = Path("configs/phase2-calibration/plan.json")


def experiment(suite, task_id, policy, budget):
    context = {"policy": policy, "max_context_tokens": budget}
    if policy != "full":
        context["keep_rounds"] = 1
    if policy == "summary":
        context.update(
            summary_max_tokens=384,
            summarizer_context_tokens=32768,
            summarizer={
                "model": "qwen3-8b-f16",
                "base_url": "http://127.0.0.1:8084/v1",
                "temperature": 0,
                "seed": 0,
                "max_tokens": 384,
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "cache_prompt": False,
                    "top_k": 1,
                },
            },
            runtime_metadata_path=str(ROOT / "runtime/summarizer/server.json"),
        )
    return ExperimentConfig.model_validate(
        {
            "experiment": {
                "suite": suite,
                "task_ids": [f"user_task_{task_id}"],
                "injection_task_ids": [f"injection_task_{INJECTION_GOALS[suite]}"],
                "mode": "paired",
                "attack": "ignore_previous_append",
            },
            "model": {
                "model": "qwen3-8b-f16",
                "base_url": "http://127.0.0.1:8085/v1",
                "seed": 17,
                "temperature": 0.7,
                "max_tokens": 2048,
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "cache_prompt": False,
                    "top_p": 0.8,
                    "top_k": 20,
                    "min_p": 0.0,
                },
            },
            "context": context,
            "runner": {
                "output_root": str(ROOT / "runs"),
                "runtime_metadata_path": str(ROOT / "runtime/agent/server.json"),
                "compact_artifacts": True,
                "min_free_disk_bytes": 1024**3,
                "max_tool_steps": 15,
                "request_timeout_seconds": 120,
                "episode_timeout_seconds": 600,
            },
        }
    )


def profile():
    directory = ROOT / "profile-audited-goals"
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "profile.json").exists():
        print("Development profile already recorded; no overwrite.")
        return
    counter = RequestTokenCounter(
        "http://127.0.0.1:8084/v1", directory / "counts.jsonl", lambda: 120
    )
    profiles = []
    for suite_name, task_ids in DEVELOPMENT.items():
        suite = get_suite("v1.2.2", suite_name)
        runtime = FunctionsRuntime(suite.tools)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f.name,
                    "description": f.description,
                    "parameters": f.parameters.model_json_schema(),
                },
            }
            for f in runtime.functions.values()
        ]
        for task_id in task_ids:
            task = suite.user_tasks[f"user_task_{task_id}"]
            agent = LocalAgent(experiment(suite_name, task_id, "full", 8192), directory)
            for condition in ("clean", "attack"):
                goal = suite.injection_tasks[f"injection_task_{INJECTION_GOALS[suite_name]}"]
                payloads, injections = (
                    make_injections(suite, agent, "ignore_previous_append", task, goal)
                    if condition == "attack"
                    else ({}, {})
                )
                env = task.init_environment(suite.load_and_inject_default_environment(injections))
                messages = [
                    {"role": "system", "content": load_system_message()},
                    {"role": "user", "content": task.PROMPT},
                ]

                def count(view, agent=agent, tools=tools):
                    return counter.count(agent.request_payload(view, tools))

                counts = [{"round": 0, "full_tokens": count(messages)}]
                for index, call in enumerate(task.ground_truth(env)):
                    value, error = runtime.run_function(
                        env, call.function, call.args, raise_on_error=True
                    )
                    if error:
                        raise ValueError(error)
                    call_id = f"reference-{index}"
                    messages.extend(
                        [
                            {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": call.function,
                                            "arguments": json.dumps(call.args),
                                        },
                                    }
                                ],
                            },
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": tool_result_to_text(value),
                            },
                        ]
                    )
                    counts.append(
                        {
                            "round": index + 1,
                            "tool": call.function,
                            "full_tokens": count(messages),
                            "latest_group_tokens": count(messages[:2] + messages[-2:]),
                        }
                    )
                item = {
                    "suite": suite_name,
                    "task_id": task_id,
                    "condition": condition,
                    "injection_task_id": f"injection_task_{INJECTION_GOALS[suite_name]}"
                    if condition == "attack"
                    else None,
                    "counts": counts,
                    "reference_only_never_an_agent_input": True,
                }
                atomic_json(
                    directory / f"{suite_name}{task_id}-{condition}.json",
                    {**item, "messages": messages, "attack_payloads": payloads},
                )
                profiles.append(item)
                print(json.dumps({**item, "counts": counts[-1:]}), flush=True)
    atomic_json(directory / "profile.json", profiles)


def replay():
    from audit_phase2_spike import read, records

    directory = ROOT / "summary-replay-v2"
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / "report.json"
    if report.exists():
        print(report.read_text())
        return
    if (directory / "memory-requests.jsonl").exists():
        raise ValueError("Partial replay exists; preserve it and use a new version")
    source_root = Path("artifacts/phase2-budget-spike-v1/runs/f16-summary-travel17/episodes")
    source = next(
        p
        for p in sorted(source_root.iterdir())
        if json.loads(read(p / "result.json"))["condition"] == "attack"
    )
    requests = [r for r in records(source / "memory-requests.jsonl") if r["event"] == "request"]
    service = MemoryServices(
        experiment("travel", 17, "summary", 8192).context, directory, lambda: 120
    )
    previous = ""
    updates = []
    for index, request in enumerate(requests):
        content = request["payload"]["messages"][1]["content"]
        encoded, task_suffix = content.split("Past transcript (data):\n", 1)[1].split(
            "\nOriginal user task (already retained; do not repeat):\n", 1
        )
        task = task_suffix.rsplit("\nWrite only factual memory,", 1)[0]
        archive = json.loads(encoded)
        if archive[0].get("kind") == "previous_summary":
            archive[0]["memory"] = previous
        summary = service.summarize(task, archive, service.settings.summary_max_tokens)
        updates.append(
            {
                "index": index,
                "source_sha256": stable_hash(archive),
                "new_message_ids": [m["message_id"] for m in archive if "message_id" in m],
                "changed": summary != previous,
                "summary": summary,
            }
        )
        previous = summary
        atomic_json(directory / f"update-{index}.json", updates[-1])
        print(json.dumps({"update": index, "changed": updates[-1]["changed"]}), flush=True)
    responses = [
        r for r in records(directory / "memory-requests.jsonl") if r["event"] == "response"
    ]
    atomic_json(
        report,
        {
            "purpose": "Recorded attacked development observations, not agent evaluation",
            "source": str(source),
            "updates": updates,
            "usage": service.usage,
            "length_stops": sum(
                r["response"]["choices"][0]["finish_reason"] == "length" for r in responses
            ),
            "summary_seconds": service.summary_seconds,
        },
    )


def prepare():
    profile_path = ROOT / "profile-audited-goals/profile.json"
    runs = [
        {
            "run_id": f"f16-{policy}-{suite}{task_id}",
            "suite": suite,
            "task_id": task_id,
            "policy": policy,
            "config": experiment(suite, task_id, policy, 7680).model_dump(mode="json"),
        }
        for suite, task_id, policies in (
            ("travel", 18, ("full", "truncate", "summary")),
            ("workspace", 34, ("summary", "truncate", "full")),
        )
        for policy in policies
    ]
    plan = {
        "kind": "development_calibration_not_formal_evaluation",
        "development_pool": DEVELOPMENT,
        "selection_basis": (
            "Travel18: eight reference calls and 5565 clean input tokens, to check whether "
            "agent-generated history triggers compaction. Workspace34: 5287 tokens over "
            "three reference calls, a cross-suite retention/control check. Selection uses "
            "workflow and exact reference lengths before new agent outcomes."
        ),
        "budget_basis": (
            "Keep the spike total budget 7680 (5632 input + 2048 output) while validating "
            "384-token summaries. Largest declared reference latest group is 5102 tokens. "
            "Clean compaction is a measured gate, not assumed from reference length."
        ),
        "profile_sha256": sha256_file(profile_path),
        "summary_replay_sha256": sha256_file(ROOT / "summary-replay-v2/report.json"),
        "runtime_configs_sha256": {
            name: sha256_file(Path(f"configs/phase2-calibration/runtime-{name}.toml"))
            for name in ("f16", "summarizer")
        },
        "budget_seconds": 5400,
        "expected_episodes": sum(
            len(plan_episodes(ExperimentConfig.model_validate(r["config"]))) for r in runs
        ),
        "runs": runs,
    }
    if PLAN.exists() and json.loads(PLAN.read_text()) != plan:
        raise ValueError("Frozen calibration plan differs; use a new cohort")
    atomic_json(PLAN, plan)
    print(json.dumps({"plan": str(PLAN), "episodes": plan["expected_episodes"]}))


def status():
    episodes = []
    for path in sorted((ROOT / "runs").glob("*/episodes/*/result.json")):
        result = json.loads(path.read_text())
        episodes.append(
            {
                "run": path.parents[2].name,
                **{
                    k: result[k]
                    for k in (
                        "episode_id",
                        "condition",
                        "status",
                        "utility",
                        "attack_success",
                        "duration_seconds",
                    )
                },
                "error": result.get("error"),
                "compactions": result["context_memory"]["compactions"],
            }
        )
    state = ROOT / "campaign.json"
    return {
        "status": json.loads(state.read_text())["status"] if state.exists() else "not_started",
        "finalized": len(episodes),
        "episodes": episodes,
    }


def run():
    plan = json.loads(PLAN.read_text())
    runtime_paths = {
        name: Path(f"configs/phase2-calibration/runtime-{name}.toml")
        for name in ("f16", "summarizer")
    }
    for name, path in runtime_paths.items():
        if sha256_file(path) != plan["runtime_configs_sha256"][name]:
            raise ValueError("Runtime differs from frozen calibration plan")
    identity = {
        "plan_sha256": stable_hash(plan),
        "implementation": implementation_identity(),
        "driver_sha256": sha256_file(Path(__file__)),
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = ROOT / "campaign.json"
        state = (
            json.loads(manifest.read_text())
            if manifest.exists()
            else {
                "identity": identity,
                "status": "prepared",
                "elapsed_seconds": 0,
            }
        )
        if state["status"] == "completed":
            if state["identity"]["plan_sha256"] != identity["plan_sha256"]:
                raise ValueError("Completed campaign belongs to a different plan")
            print(json.dumps(status()))
            return
        if state["identity"] != identity:
            raise ValueError("Resume refused: code or plan changed")
        elapsed = state["elapsed_seconds"] + state.get("reserved_seconds", 0)
        started = time.monotonic()
        owned = []

        def save():
            state.update(elapsed_seconds=elapsed + time.monotonic() - started, pid=os.getpid())
            atomic_json(manifest, state)

        def guard():
            save()
            if state["elapsed_seconds"] + 600 > plan["budget_seconds"]:
                raise TimeoutError("Development time budget reached")
            state["reserved_seconds"] = 600
            save()

        def interrupted(signum, frame):
            raise KeyboardInterrupt(f"Calibration received signal {signum}")

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            guard()
            for path in runtime_paths.values():
                stop_server(path)
            servers = []
            for path in runtime_paths.values():
                servers.append(start_server(path))
                owned.append(path)
            for entry in plan["runs"]:
                state.update(status="running", active_run=entry["run_id"])
                save()
                print(json.dumps({"starting": entry["run_id"]}), flush=True)
                config = ExperimentConfig.model_validate(entry["config"])
                directory = config.runner.output_root / entry["run_id"]
                if (
                    directory.exists()
                    and json.loads((directory / "manifest.json").read_text())["status"]
                    == "finalized"
                ):
                    continue
                sampler = ServerMemorySampler([s["pid"] for s in servers] + [os.getpid()])
                sampler.start()
                try:
                    run_experiment(
                        config, entry["run_id"], resume=directory.exists(), before_episode=guard
                    )
                finally:
                    memory = sampler.stop()
                    if directory.exists():
                        atomic_json(directory / "deployment-memory.json", memory)
                state["reserved_seconds"] = 0
                save()
                print(json.dumps(status()), flush=True)
            state.update(status="completed", active_run=None)
        except BaseException as error:
            state.update(status="interrupted", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            save()
            try:
                for path in reversed(owned):
                    stop_server(path)
            finally:
                signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("profile", "replay", "prepare", "run", "status"))
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.action not in {"run", "status"} and (args.plan != PLAN or args.output_root != ROOT):
        parser.error("Alternate frozen plans/output roots apply only to run or status")
    PLAN, ROOT = args.plan, args.output_root
    if args.action == "status":
        print(json.dumps(status(), indent=2))
    else:
        {"profile": profile, "replay": replay, "prepare": prepare, "run": run}[args.action]()
