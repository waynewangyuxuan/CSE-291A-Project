"""Freeze and run the development-only, three-precision token-budget spike."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from pathlib import Path

from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    ServerMemorySampler,
    implementation_identity,
    plan_episodes,
    run_experiment,
)
from agent_tradeoffs.runtime import load_runtime_config, sha256_file, start_server, stop_server

PLAN = Path("configs/phase2-spike/plan.json")
OUTPUT = Path("artifacts/phase2-budget-spike-v1")
PRECISIONS = ("f16", "q8", "q4")
POLICIES = ("full", "truncate", "summary")


def runtime(precision):
    return load_runtime_config(f"configs/phase2-spike/runtime-{precision}.toml")


def experiment(precision, policy):
    context = {"policy": policy, "max_context_tokens": 7680}
    if policy != "full":
        context["keep_rounds"] = 1
    if policy == "summary":
        context.update(
            summary_max_tokens=256,
            summarizer_context_tokens=32768,
            summarizer={
                "model": "qwen3-8b-f16",
                "base_url": runtime("summarizer").base_url + "/v1",
                "temperature": 0,
                "seed": 0,
                "max_tokens": 256,
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "cache_prompt": False,
                    "top_k": 1,
                },
            },
            runtime_metadata_path="artifacts/phase2-spike/runtime/summarizer/server.json",
        )
    return ExperimentConfig.model_validate(
        {
            "experiment": {
                "suite": "travel",
                "task_ids": ["user_task_17"],
                "injection_task_ids": ["injection_task_0"],
                "mode": "paired",
                "attack": "ignore_previous_append",
            },
            "model": {
                "model": runtime(precision).model_alias,
                "base_url": runtime(precision).base_url + "/v1",
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
                "output_root": str(OUTPUT / "runs"),
                "runtime_metadata_path": "artifacts/phase2-spike/runtime/agent/server.json",
                "compact_artifacts": True,
                "min_free_disk_bytes": 1024**3,
                "max_tool_steps": 15,
                "request_timeout_seconds": 120,
                "episode_timeout_seconds": 600,
            },
        }
    )


def prepare():
    runs = [
        {
            "run_id": f"{precision}-{policy}-travel17",
            "precision": precision,
            "policy": policy,
            "config": experiment(precision, policy).model_dump(mode="json"),
        }
        for precision in PRECISIONS
        for policy in POLICIES
    ]
    profile = Path("artifacts/phase2-spike/reference-profile/profile.json")
    plan = {
        "schema_version": 1,
        "campaign_id": "phase2-budget-spike-v1",
        "purpose": "Development smoke only; no held-out evaluation or formal conclusions",
        "task_selection": (
            "Travel 17 is previously evaluated and reserved for development; "
            "nine reference calls exercise growing context. "
            "Reference answers are never supplied to the agent."
        ),
        "budget_selection": (
            "7680 total tokens, including 2048 output reserve: 5632 input. "
            "Development reference prefixes grow from 4309 to 5750 clean / 6167 attacked. "
            "Largest protected latest group is 5102 tokens before summary envelope. "
            "Summary cap 256. Formal budget remains uncalibrated."
        ),
        "reference_profile_sha256": sha256_file(profile),
        "reference_profile": json.loads(profile.read_text()),
        "expected_episodes": sum(
            len(plan_episodes(ExperimentConfig.model_validate(r["config"]))) for r in runs
        ),
        "budget_seconds": 7200,
        "runtime_configs_sha256": {
            precision: sha256_file(Path(f"configs/phase2-spike/runtime-{precision}.toml"))
            for precision in (*PRECISIONS, "summarizer")
        },
        "weights": {
            precision: {
                "sha256": runtime(precision).model_artifact.sha256,
                "revision": runtime(precision).model_artifact.revision,
            }
            for precision in PRECISIONS
        },
        "runs": runs,
    }
    if PLAN.exists() and json.loads(PLAN.read_text()) != plan:
        raise ValueError("Frozen spike plan differs; use a new version, never overwrite")
    atomic_json(PLAN, plan)
    print(json.dumps({"plan": str(PLAN), "episodes": plan["expected_episodes"]}))


def report():
    rows = []
    if not PLAN.exists():
        return {"status": "not_prepared"}
    for entry in json.loads(PLAN.read_text())["runs"]:
        directory = OUTPUT / "runs" / entry["run_id"]
        for path in sorted(directory.glob("episodes/*/result.json")):
            result = json.loads(path.read_text())
            rows.append(
                {
                    "run": entry["run_id"],
                    "episode": result["episode_id"],
                    "condition": result["condition"],
                    "status": result["status"],
                    "utility": result["utility"],
                    "attack_success": result["attack_success"],
                    "error": result.get("error"),
                    "seconds": round(result["duration_seconds"], 1),
                    "tool_calls": result["tool_calls"],
                    "compactions": result["context_memory"]["compactions"],
                }
            )
    state = OUTPUT / "campaign.json"
    value = {
        "status": json.loads(state.read_text())["status"] if state.exists() else "not_started",
        "finalized": len(rows),
        "expected": 18,
        "episodes": rows,
    }
    if OUTPUT.exists():
        atomic_json(OUTPUT / "progress.json", value)
    return value


def run():
    plan = json.loads(PLAN.read_text())
    for precision, digest in plan["runtime_configs_sha256"].items():
        if sha256_file(Path(f"configs/phase2-spike/runtime-{precision}.toml")) != digest:
            raise ValueError("Runtime config differs from frozen plan")
    if (
        sum(len(plan_episodes(ExperimentConfig.model_validate(e["config"]))) for e in plan["runs"])
        != plan["expected_episodes"]
    ):
        raise ValueError("Episode count differs from frozen plan")
    identity = {
        "plan_sha256": stable_hash(plan),
        "implementation": implementation_identity(),
        "driver_sha256": sha256_file(Path(__file__)),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = OUTPUT / "campaign.json"
        state = (
            json.loads(manifest.read_text())
            if manifest.exists()
            else {
                "identity": identity,
                "status": "prepared",
                "completed_runs": [],
                "elapsed_seconds": 0,
            }
        )
        if state["identity"] != identity:
            raise ValueError("Resume refused: implementation or plan differs")
        if state["status"] == "completed":
            print(json.dumps(report()))
            return
        elapsed = state["elapsed_seconds"] + state.get("reserved_seconds", 0)
        started = time.monotonic()
        active = None
        summary_owned = False

        def save():
            state.update(elapsed_seconds=elapsed + time.monotonic() - started, pid=os.getpid())
            atomic_json(manifest, state)

        def guard():
            save()
            if state["elapsed_seconds"] + 600 > plan["budget_seconds"]:
                raise TimeoutError("Spike time budget reached; inspect before expanding")
            state["reserved_seconds"] = 600
            save()

        def interrupt(signum, frame):
            raise KeyboardInterrupt(f"Spike received signal {signum}")

        previous = signal.signal(signal.SIGTERM, interrupt)
        try:
            if state.get("active_precision"):
                stop_server(runtime(state["active_precision"]))
                stop_server(runtime("summarizer"))
            guard()
            summary_server = start_server(runtime("summarizer"))
            summary_owned = True
            for entry in plan["runs"]:
                if entry["run_id"] in state["completed_runs"]:
                    continue
                guard()
                precision = entry["precision"]
                if precision != active:
                    if active is not None:
                        stop_server(runtime(active))
                        active = None
                    state["active_precision"] = precision
                    save()
                    agent_server = start_server(runtime(precision))
                    active = precision
                state.update(status="running", active_run=entry["run_id"])
                save()
                print(json.dumps({"starting": entry["run_id"]}), flush=True)
                config = ExperimentConfig.model_validate(entry["config"])
                directory = config.runner.output_root / entry["run_id"]
                sampler = ServerMemorySampler(
                    [agent_server["pid"], summary_server["pid"], os.getpid()]
                )
                sampler.start()
                try:
                    run_experiment(
                        config, entry["run_id"], resume=directory.exists(), before_episode=guard
                    )
                finally:
                    memory = sampler.stop()
                    if directory.exists():
                        atomic_json(directory / "deployment-memory.json", memory)
                state["completed_runs"].append(entry["run_id"])
                state["reserved_seconds"] = 0
                save()
                print(json.dumps(report()), flush=True)
            state.update(status="completed", active_run=None)
        except BaseException as error:
            state.update(status="interrupted", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            save()
            try:
                if active is not None:
                    stop_server(runtime(active))
            finally:
                if summary_owned:
                    stop_server(runtime("summarizer"))
                signal.signal(signal.SIGTERM, previous)
            print(json.dumps(report()), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "status"))
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(report(), indent=2))
    else:
        {"prepare": prepare, "run": run}[args.action]()
