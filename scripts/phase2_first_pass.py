"""Freeze, execute and report the single-seed quantization/context pilot."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import signal
import time
from dataclasses import replace
from pathlib import Path

from agent_tradeoffs.agent import load_system_message
from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    ServerMemorySampler,
    implementation_identity,
    plan_episodes,
    run_experiment,
)
from agent_tradeoffs.runtime import (
    ModelArtifactConfig,
    download_model,
    load_runtime_config,
    sha256_file,
    start_server,
    stop_server,
)

PLAN = Path("configs/phase2/first-pass.json")
OUTPUT = Path("artifacts/phase2-first-pass-v1")
POLICIES = ["full", "truncate", "long_summary", "short_summary", "short_summary_rag"]
TASKS = {"banking": [1, 3, 12, 15], "slack": [2, 4, 5, 9, 10, 13, 14, 16]}
REASONS = {
    "banking": (
        "Information lookup, refund, file-driven scheduled payment, multi-step account changes."
    ),
    "slack": "Web/inbox tasks plus member enumeration and multi-channel retrieval/action chains.",
}


def runtime(precision):
    source = load_runtime_config("configs/runtime-qwen3-8b-f16.toml")
    if precision == "summarizer":
        return replace(source, port=8082, output_root="artifacts/phase2-runtime/summarizer")
    source = replace(source, port=8083, output_root="artifacts/phase2-runtime/agent")
    if precision == "f16":
        return source
    path = Path(".cache/models/qwen3-8b-derived-q4/Qwen3-8B-Q4_K_M.provenance.json")
    provenance = json.loads(path.read_text())
    return replace(
        source,
        model_alias="qwen3-8b-derived-q4",
        model_artifact=ModelArtifactConfig(
            acquisition="quantized",
            repo_id=provenance["repo_id"],
            revision=provenance["revision"],
            filename=provenance["filename"],
            sha256=provenance["sha256"],
            local_dir=str(path.parent),
        ),
    )


def prepare():
    features = list(csv.DictReader(open("results/phase1/behavior-understanding/task-features.csv")))
    structural = [
        "suite",
        "user_task_id",
        "legitimate_prompt",
        "task_intent",
        "ground_truth_tool_count",
        "reference_tool_functions_in_source_order",
    ]
    selected = [
        {key: row[key] for key in structural}
        for row in features
        if row["suite"] in TASKS and int(row["user_task_id"].split("_")[-1]) in TASKS[row["suite"]]
    ]
    if len(selected) != 12:
        raise ValueError("Selected structural workload does not contain 12 tasks")
    original = json.loads(Path("configs/phase1/task-manifest.json").read_text())
    runs = []
    for precision in ["f16", "q4"]:
        cfg = runtime(precision)
        for policy in POLICIES:
            for suite, numbers in TASKS.items():
                base = ExperimentConfig.model_validate(
                    {
                        "experiment": {
                            "suite": suite,
                            "task_ids": [f"user_task_{n}" for n in numbers],
                            "injection_task_ids": goals_for(suite),
                            "mode": "paired",
                            "attack": "ignore_previous_append",
                        },
                        "model": {
                            "model": cfg.model_alias,
                            "base_url": "http://127.0.0.1:8083/v1",
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
                        "runner": {
                            "output_root": str(OUTPUT / "runs"),
                            "compact_artifacts": True,
                            "runtime_metadata_path": "artifacts/phase2-runtime/agent/server.json",
                            "max_tool_steps": 15,
                            "request_timeout_seconds": 120,
                            "episode_timeout_seconds": 600,
                            "min_free_disk_bytes": 1024**3,
                        },
                        "context": context(policy),
                    }
                )
                runs.append(
                    {
                        "run_id": f"{precision}-{policy}-{suite}",
                        "precision": precision,
                        "config": base.model_dump(mode="json"),
                    }
                )
    plan = {
        "schema_version": 1,
        "campaign_id": "phase2-first-pass-v1",
        "seed": 17,
        "expected_episodes": 360,
        "budget_seconds": 43200,
        "task_selection": {
            "basis": "Structural reference paths only; deliberate mechanism pilot.",
            "reasons": REASONS,
            "tasks": selected,
        },
        "system_prompt_sha256": hashlib.sha256(load_system_message().encode()).hexdigest(),
        "source_task_manifest_sha256": sha256_file(Path("configs/phase1/task-manifest.json")),
        "baseline_benchmark": original.get("benchmark_version", "v1.2.2"),
        "weights": {
            p: {
                "sha256": runtime(p).model_artifact.sha256,
                "revision": runtime(p).model_artifact.revision,
            }
            for p in ["f16", "q4"]
        },
        "runs": runs,
    }
    text = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
    if PLAN.exists() and PLAN.read_text() != text:
        raise ValueError("Frozen plan already exists with different content")
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(text)
    print(json.dumps({"plan": str(PLAN), "episodes": 360, "tasks": 12}))


def goals_for(suite):
    import tomllib

    with open(f"configs/phase1/phase1-f16-s17-{suite}-ignore-previous.toml", "rb") as handle:
        return tomllib.load(handle)["experiment"]["injection_task_ids"]


def context(policy):
    value = {"policy": policy}
    if policy == "full":
        return value
    value.update(trigger_rounds=2, keep_rounds=1)
    if policy == "truncate":
        return value
    value.update(
        summary_max_tokens=512 if policy == "long_summary" else 128,
        summary_fraction=0.5 if policy == "long_summary" else 0.15,
        summarizer={
            "model": "qwen3-8b-f16",
            "base_url": "http://127.0.0.1:8082/v1",
            "temperature": 0,
            "seed": 17,
            "max_tokens": 512,
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False},
                "cache_prompt": False,
                "top_k": 1,
            },
        },
        runtime_metadata_path="artifacts/phase2-runtime/summarizer/server.json",
        search_max_calls=4,
        search_max_tokens=512,
        search_top_k=3,
    )
    return value


class CampaignBudgetSpent(RuntimeError):
    pass


def run():
    plan = json.loads(PLAN.read_text())
    if plan["system_prompt_sha256"] != hashlib.sha256(load_system_message().encode()).hexdigest():
        raise ValueError("Original system prompt differs from frozen plan")
    if (
        sum(len(plan_episodes(ExperimentConfig.model_validate(e["config"]))) for e in plan["runs"])
        != plan["expected_episodes"]
    ):
        raise ValueError("Frozen workload episode count differs")
    for precision, pin in plan["weights"].items():
        artifact = runtime(precision).model_artifact
        if artifact.sha256 != pin["sha256"] or artifact.revision != pin["revision"]:
            raise ValueError("Runtime model differs from frozen weight identity")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_file = OUTPUT / "campaign.json"
        identity = {
            "plan_sha256": sha256_file(PLAN),
            "implementation": implementation_identity(),
            "orchestrator_sha256": sha256_file(Path(__file__)),
        }
        resuming = manifest_file.exists()
        state = (
            json.loads(manifest_file.read_text())
            if manifest_file.exists()
            else {
                "identity": identity,
                "elapsed_seconds": 0.0,
                "completed_runs": [],
                "status": "open",
                "pid": os.getpid(),
            }
        )
        if state["identity"] != identity:
            raise ValueError("Resume refused: code or frozen plan changed")
        if state["status"] == "completed":
            report()
            return
        started = time.monotonic()
        spent = state["elapsed_seconds"] + state.get("reserved_seconds", 0)
        active = None
        summary_owned = False

        def save():
            state.update(elapsed_seconds=spent + time.monotonic() - started, pid=os.getpid())
            atomic_json(manifest_file, state)

        def guard_budget():
            save()
            if state["elapsed_seconds"] + 600 > plan["budget_seconds"]:
                raise CampaignBudgetSpent("Insufficient campaign time for another bounded episode")
            state["reserved_seconds"] = 600
            save()

        def interrupt(signum, frame):
            raise KeyboardInterrupt(f"Campaign received signal {signum}")

        previous_signal = signal.signal(signal.SIGTERM, interrupt)
        try:
            guard_budget()
            if resuming:
                if state.get("agent_precision"):
                    stop_server(runtime(state["agent_precision"]))
                stop_server(runtime("summarizer"))
            summary_server = start_server(runtime("summarizer"))
            summary_owned = True
            for entry in plan["runs"]:
                if entry["run_id"] in state["completed_runs"]:
                    continue
                guard_budget()
                precision = entry["precision"]
                if active != precision:
                    if active is not None:
                        stop_server(runtime(active))
                        active = None
                    download_model(runtime(precision))
                    state["agent_precision"] = precision
                    save()
                    agent_server = start_server(runtime(precision))
                    active = precision
                state.update(status="running", active_run=entry["run_id"])
                save()
                config = ExperimentConfig.model_validate(entry["config"])
                directory = config.runner.output_root / entry["run_id"]
                print(json.dumps({"starting": entry["run_id"]}), flush=True)
                deployment_memory = ServerMemorySampler(
                    [
                        summary_server["pid"],
                        agent_server["pid"],
                        os.getpid(),
                    ]
                )
                deployment_memory.start()
                try:
                    run_experiment(
                        config,
                        entry["run_id"],
                        resume=directory.exists(),
                        before_episode=guard_budget,
                    )
                finally:
                    if directory.exists():
                        atomic_json(directory / "deployment-memory.json", deployment_memory.stop())
                    else:
                        deployment_memory.stop()
                state["completed_runs"].append(entry["run_id"])
                state["reserved_seconds"] = 0
                save()
                report()
            state.update(status="completed", active_run=None)
        except CampaignBudgetSpent as error:
            state.update(status="budget_spent", error=str(error))
        except BaseException as error:
            state.update(status="interrupted", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            state["reserved_seconds"] = 0
            save()
            if active is not None:
                stop_server(runtime(active))
            if summary_owned:
                stop_server(runtime("summarizer"))
            signal.signal(signal.SIGTERM, previous_signal)
            report()


def report():
    plan = json.loads(PLAN.read_text())
    rows = []
    for entry in plan["runs"]:
        config = entry["config"]
        directory = Path(config["runner"]["output_root"]) / entry["run_id"] / "episodes"
        for path in sorted(directory.glob("*/result.json")):
            result = json.loads(path.read_text())
            memory = result.get("context_memory", {})
            rows.append(
                {
                    "precision": entry["precision"],
                    "policy": config["context"]["policy"],
                    "suite": config["experiment"]["suite"],
                    "task": result["user_task_id"],
                    "condition": result["condition"],
                    "goal": result["injection_task_id"],
                    "U": result["utility"],
                    "A": result["attack_success"],
                    "status": result["status"],
                    "seconds": result["duration_seconds"],
                    "tools": result["tool_calls"],
                    "agent_tokens": result["usage"].get("total_tokens", 0),
                    "summary_tokens": memory.get("summary_usage", {}).get("total_tokens", 0),
                    "summary_seconds": memory.get("summary_seconds", 0),
                    "search_seconds": memory.get("search_seconds", 0),
                    "search_calls": memory.get("search_calls", 0),
                    "compactions": memory.get("compactions", 0),
                    "exposed_exact_payload": bool(result.get("observed_injection_vectors")),
                    "agent_peak_rss_bytes": result["server_memory"]["peak_rss_bytes"],
                    "summarizer_peak_rss_bytes": result.get("summarizer_memory", {}).get(
                        "peak_rss_bytes"
                    ),
                    "evidence": str(path),
                }
            )
    destination = Path("results/phase2-first-pass-v1")
    destination.mkdir(parents=True, exist_ok=True)
    if rows:
        with (destination / "episodes.csv").open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    paired = []

    def key(row):
        return row["suite"], row["task"], row["condition"], row["goal"]

    control = {
        key(row): row for row in rows if row["precision"] == "f16" and row["policy"] == "full"
    }
    for row in rows:
        baseline = control.get(key(row))
        if baseline is None:
            continue
        paired.append(
            {
                "precision": row["precision"],
                "policy": row["policy"],
                "suite": row["suite"],
                "task": row["task"],
                "condition": row["condition"],
                "goal": row["goal"],
                "baseline_U": baseline["U"],
                "variant_U": row["U"],
                "baseline_A": baseline["A"],
                "variant_A": row["A"],
                "delta_seconds": row["seconds"] - baseline["seconds"],
                "baseline_evidence": baseline["evidence"],
                "variant_evidence": row["evidence"],
            }
        )
    if paired:
        with (destination / "paired.csv").open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
            writer.writeheader()
            writer.writerows(paired)
    groups = []
    for precision in ["f16", "q4"]:
        for policy in POLICIES:
            subset = [r for r in rows if r["precision"] == precision and r["policy"] == policy]
            clean = [r for r in subset if r["condition"] == "clean"]
            attacked = [r for r in subset if r["condition"] == "attack"]
            eligible = {(r["suite"], r["task"]) for r in clean if r["U"] is True}
            own_clean = [r for r in attacked if (r["suite"], r["task"]) in eligible]

            def score(items, key):
                return {
                    "true": sum(r[key] is True for r in items),
                    "n": len(items),
                    "unknown": sum(r[key] is None for r in items),
                }

            groups.append(
                {
                    "precision": precision,
                    "policy": policy,
                    "n": len(subset),
                    "clean_U": score(clean, "U"),
                    "attack_U": score(attacked, "U"),
                    "A": score(attacked, "A"),
                    "own_clean_A": score(own_clean, "A"),
                    "compacted": sum(r["compactions"] > 0 for r in subset),
                    "searched": sum(r["search_calls"] > 0 for r in subset),
                }
            )
    atomic_json(
        destination / "summary.json", {"finalized": len(rows), "planned": 360, "groups": groups}
    )
    lines = [
        "# Quantization/context exploratory first pass",
        "",
        f"Finalized {len(rows)}/360 episodes. One seed (17); not a cross-seed robustness claim.",
        "Original U/A retained; unknown is not resistance. Own-clean denominators differ.",
        "Early-round compaction is a mechanism intervention, not a natural 8K/16K budget result.",
        "",
        "| Precision | Memory | N | Clean U | Attack U | A | Own-clean A | Compact | Search |",
        "|---|---|---:|---|---|---|---|---:|---:|",
    ]

    def fmt(value):
        return f"{value['true']}/{value['n']} (?={value['unknown']})"

    for group in groups:
        lines.append(
            f"| {group['precision']} | {group['policy']} | {group['n']} | "
            + " | ".join(fmt(group[k]) for k in ["clean_U", "attack_U", "A", "own_clean_A"])
            + f" | {group['compacted']} | {group['searched']} |"
        )
    lines.extend(
        [
            "",
            "Episode time includes summaries; token fields are split by role.",
            "RSS is per process, not physical unified-memory use; see the protocol.",
            "Raw requests, summaries, searches and errors remain in ignored artifacts.",
            "",
        ]
    )
    (destination / "README.md").write_text("\n".join(lines))
    print(json.dumps({"finalized": len(rows), "planned": 360}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "run", "report"])
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "report": report}[args.command]()


if __name__ == "__main__":
    main()
