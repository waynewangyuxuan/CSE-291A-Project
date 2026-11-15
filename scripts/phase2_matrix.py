"""Freeze, launch and resume one balanced block of the formal Phase 2 matrix."""

from __future__ import annotations

import argparse
import copy
import fcntl
import itertools
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    RuntimeDeploymentChangedError,
    ServerMemorySampler,
    guard_runtime_process,
    implementation_identity,
    plan_episodes,
    run_experiment,
)
from agent_tradeoffs.runtime import load_runtime_config, sha256_file, start_server, stop_server

CONFIGS = Path("configs/phase2-main-v1")
OUTPUT = Path("artifacts/phase2-main-v1")
PROTOCOL = Path("results/phase2-calibration/selected-protocol.json")
TEMPLATES = Path("configs/phase2-calibration/plan-before-action.json")
FORMATS = ("f16", "q8", "q4")
POLICIES = ("full", "truncate", "summary")


def read(path):
    return json.loads(path.read_text())


def frozen_write(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError(f"Frozen content differs: {path}; use a new version")
        return
    atomic_json(path, value)


def interleave(tasks):
    return [
        (suite, task)
        for row in itertools.zip_longest(*tasks.values())
        for suite, task in zip(tasks, row, strict=True)
        if task is not None
    ]


def stop_leftover(path):
    config = load_runtime_config(path)
    metadata_path = config.resolve(config.output_root) / "server.json"
    if metadata_path.exists():
        metadata = read(metadata_path)
        if metadata["status"] in {"stopped", "not_running"} and not psutil.pid_exists(
            metadata["pid"]
        ):
            return
    stop_server(path)


def verify_resident(metadata, root, active_run):
    try:
        guard_runtime_process(metadata["pid"], metadata["process_identity"])
    except RuntimeDeploymentChangedError:
        atomic_json(
            root / "invalid-deployment.json",
            {
                "role": "summarizer",
                "affected_run": active_run,
                "reason": "Resident summarizer changed; retain evidence and review before resuming",
                "expected": metadata,
            },
        )
        raise


def prepare():
    protocol = read(PROTOCOL)
    if implementation_identity() != protocol["implementation"]:
        raise ValueError("The selected execution implementation changed")
    templates = {row["policy"]: row["config"] for row in read(TEMPLATES)["runs"]}
    runtimes = {
        name: load_runtime_config(CONFIGS / f"runtime-{name}.toml")
        for name in (*FORMATS, "summarizer")
    }
    permutations = list(itertools.permutations(FORMATS))
    policy_orders = list(itertools.permutations(POLICIES))
    blocks = []
    for cohort, tasks in (
        ("main", protocol["known_main_tasks"]),
        ("confirmation", protocol["confirmation_candidates_after_source_review"]),
    ):
        ordered = interleave(tasks)
        for index, offset in enumerate(range(0, len(ordered), 3)):
            block_id = f"{cohort}-{index + 1:03d}"
            selected = ordered[offset : offset + 3]
            runs = []
            precision_order = permutations[index % len(permutations)]
            for precision in precision_order:
                policy_order = policy_orders[
                    (index + FORMATS.index(precision)) % len(policy_orders)
                ]
                for policy_slot, policy in enumerate(policy_order):
                    shift = (index + policy_slot) % len(selected)
                    for suite, task in selected[shift:] + selected[:shift]:
                        config = copy.deepcopy(templates[policy])
                        config["experiment"].update(
                            suite=suite,
                            task_ids=[task],
                            injection_task_ids=[
                                protocol["attack"]["injection_goal_by_suite"][suite]
                            ],
                        )
                        config["model"].update(
                            model=runtimes[precision].model_alias,
                            base_url=runtimes[precision].base_url + "/v1",
                        )
                        config["runner"].update(
                            output_root=str(OUTPUT / block_id / "runs"),
                            runtime_metadata_path=str(
                                Path(runtimes[precision].output_root) / "server.json"
                            ),
                        )
                        if policy == "summary":
                            config["context"]["summarizer"]["base_url"] = (
                                runtimes["summarizer"].base_url + "/v1"
                            )
                            config["context"]["runtime_metadata_path"] = str(
                                Path(runtimes["summarizer"].output_root) / "server.json"
                            )
                        config = ExperimentConfig.model_validate(config)
                        runs.append(
                            {
                                "run_id": f"{precision}-{policy}-{suite}"
                                + task.removeprefix("user_task_"),
                                "precision": precision,
                                "policy": policy,
                                "suite": suite,
                                "user_task_id": task,
                                "config": config.model_dump(mode="json"),
                            }
                        )
            plan = {
                "block_id": block_id,
                "cohort": cohort,
                "task_order": selected,
                "precision_order": precision_order,
                "budget_seconds": 36000,
                "expected_episodes": sum(
                    len(plan_episodes(ExperimentConfig.model_validate(r["config"]))) for r in runs
                ),
                "runs": runs,
            }
            path = CONFIGS / f"{block_id}.json"
            frozen_write(path, json.loads(json.dumps(plan)))
            blocks.append(
                {
                    "block_id": block_id,
                    "plan": str(path),
                    "sha256": sha256_file(path),
                    "expected_episodes": plan["expected_episodes"],
                }
            )
    manifest = {
        "campaign_id": "phase2-main-v1",
        "protocol_sha256": sha256_file(PROTOCOL),
        "template_sha256": sha256_file(TEMPLATES),
        "implementation": implementation_identity(),
        "driver_sha256": sha256_file(Path(__file__)),
        "runtime_configs_sha256": {
            name: sha256_file(CONFIGS / f"runtime-{name}.toml") for name in runtimes
        },
        "ordering": (
            "Interleave suite task lists in their recorded order, then blocks of three. "
            "Cycle all six precision permutations; rotate policy permutations by block and "
            "precision identity, and task order by block and policy slot. Clean precedes attack."
        ),
        "warm_state": (
            "No synthetic warmup or prefix cache. Reload agent at each precision change; "
            "summarizer remains resident across all arms of a block. Record service launches, "
            "pair order and resume boundaries; first request after each reload is cold. "
            "This is not randomized independent replication."
        ),
        "retry_policy": (
            "Finalized errors are retained without automatic retries. Resume preserves "
            "interrupted attempts; later infrastructure retries use separate cohorts."
        ),
        "review_gate": (
            "Collection integrity is automatic; full evidence-bound trace review "
            "and analysis remain separate checkpoints."
        ),
        "blocks": blocks,
    }
    frozen_write(CONFIGS / "matrix.json", manifest)
    print(
        json.dumps({"blocks": len(blocks), "episodes": sum(b["expected_episodes"] for b in blocks)})
    )


def load_block(block_id):
    matrix = read(CONFIGS / "matrix.json")
    entry = next((b for b in matrix["blocks"] if b["block_id"] == block_id), None)
    if entry is None:
        raise ValueError(f"Unknown block: {block_id}")
    path = Path(entry["plan"])
    if sha256_file(path) != entry["sha256"]:
        raise ValueError("Frozen block changed")
    return matrix, read(path), path


def status(block_id):
    _, plan, _ = load_block(block_id)
    root = OUTPUT / block_id
    state = (
        read(root / "campaign.json")
        if (root / "campaign.json").exists()
        else {"status": "not_started"}
    )
    rows = []
    for entry in plan["runs"]:
        config = ExperimentConfig.model_validate(entry["config"])
        for episode in plan_episodes(config):
            directory = (
                config.runner.output_root / entry["run_id"] / "episodes" / episode["episode_id"]
            )
            if (
                not (directory / "result.json").exists()
                or not (directory / "compression.json").exists()
            ):
                continue
            result = read(directory / "result.json")
            if result.get("retryable"):
                continue
            rows.append(
                {
                    "run": entry["run_id"],
                    "episode": episode["episode_id"],
                    "status": result["status"],
                    "utility": result["utility"],
                    "attack_success": result["attack_success"],
                    "error": result.get("error"),
                    "seconds": result["duration_seconds"],
                }
            )
    return {
        "block": block_id,
        "status": state["status"],
        "active_run": state.get("active_run"),
        "pid": state.get("pid"),
        "finalized": len(rows),
        "expected": plan["expected_episodes"],
        "completed": sum(r["status"] == "completed" for r in rows),
        "errors": sum(r["status"] == "error" for r in rows),
        "review_gate": "pending",
        "episodes": rows,
    }


def run(block_id):
    matrix, plan, plan_path = load_block(block_id)
    if matrix["implementation"] != implementation_identity() or matrix[
        "driver_sha256"
    ] != sha256_file(Path(__file__)):
        raise ValueError("Execution code differs from frozen matrix")
    for path, digest in (
        (PROTOCOL, matrix["protocol_sha256"]),
        (TEMPLATES, matrix["template_sha256"]),
        *(
            (CONFIGS / f"runtime-{name}.toml", digest)
            for name, digest in matrix["runtime_configs_sha256"].items()
        ),
    ):
        if sha256_file(path) != digest:
            raise ValueError(f"Frozen dependency changed: {path}")
    root = OUTPUT / block_id
    if (root / "invalid-deployment.json").exists():
        raise ValueError("Deployment drift requires evidence review before resuming this block")
    root.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {
            "plan_sha256": stable_hash(plan),
            "implementation": implementation_identity(),
            "driver_sha256": matrix["driver_sha256"],
            "matrix_sha256": sha256_file(CONFIGS / "matrix.json"),
        }
        state_path = root / "campaign.json"
        state = (
            read(state_path)
            if state_path.exists()
            else {"identity": identity, "status": "prepared", "elapsed_seconds": 0, "launches": []}
        )
        if state["identity"] != identity:
            raise ValueError("Resume identity mismatch")
        if state["status"] == "completed":
            print(json.dumps(status(block_id)), flush=True)
            return
        elapsed = state["elapsed_seconds"] + state.get("reserved_seconds", 0)
        started = time.monotonic()
        agent_path = None
        summary_path = CONFIGS / "runtime-summarizer.toml"
        summary_started = False

        def save():
            state.update(elapsed_seconds=elapsed + time.monotonic() - started, pid=os.getpid())
            atomic_json(state_path, state)

        def guard():
            verify_resident(summary, root, state.get("active_run"))
            save()
            if state["elapsed_seconds"] + 600 > plan["budget_seconds"]:
                raise TimeoutError("Block collection budget reached")
            state["reserved_seconds"] = 600
            save()

        def interrupted(signum, frame):
            raise KeyboardInterrupt(f"Collection received signal {signum}")

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            state.update(status="running", active_run=None)
            save()
            for name in (*FORMATS, "summarizer"):
                stop_leftover(CONFIGS / f"runtime-{name}.toml")
            summary = start_server(summary_path)
            summary_started = True
            state["launches"].append({"role": "summarizer", "metadata": summary})
            precision = None
            agent = None
            for entry in plan["runs"]:
                config = ExperimentConfig.model_validate(entry["config"])
                directory = config.runner.output_root / entry["run_id"]
                if (directory / "manifest.json").exists() and read(directory / "manifest.json")[
                    "status"
                ] == "finalized":
                    continue
                guard()
                if precision != entry["precision"]:
                    if agent_path is not None:
                        stop_server(agent_path)
                        agent_path = None
                    path = CONFIGS / f"runtime-{entry['precision']}.toml"
                    agent = start_server(path)
                    agent_path = path
                    precision = entry["precision"]
                    state["launches"].append({"role": precision, "metadata": agent})
                state["active_run"] = entry["run_id"]
                save()
                print(json.dumps({"starting": entry["run_id"], "block": block_id}), flush=True)
                sampler = ServerMemorySampler([agent["pid"], summary["pid"], os.getpid()])
                sampler.start()
                try:
                    run_experiment(
                        config, entry["run_id"], resume=directory.exists(), before_episode=guard
                    )
                finally:
                    memory = sampler.stop()
                    if directory.exists():
                        attempt = len(list(directory.glob("deployment-memory-*.json"))) + 1
                        atomic_json(directory / f"deployment-memory-{attempt:03d}.json", memory)
                    verify_resident(summary, root, entry["run_id"])
                state["reserved_seconds"] = 0
                save()
                print(
                    json.dumps({k: v for k, v in status(block_id).items() if k != "episodes"}),
                    flush=True,
                )
            audit_path = root / "collection-audit.json"
            subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_phase2_spike.py",
                    "--plan",
                    str(plan_path),
                    "--annotations",
                    str(root / "trace-review.csv"),
                    "--partial",
                    "--output",
                    str(audit_path),
                ],
                check=True,
            )
            if read(audit_path)["episodes_checked"] != plan["expected_episodes"]:
                raise ValueError("Collection is incomplete")
            state.update(
                status="completed",
                active_run=None,
                collection_integrity="passed",
                review_gate="pending",
                reserved_seconds=0,
            )
            save()
        except BaseException as error:
            state.update(
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "error",
                error=f"{type(error).__name__}: {error}",
            )
            save()
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
            try:
                if agent_path is not None:
                    stop_server(agent_path)
            finally:
                if summary_started:
                    stop_server(summary_path)


def launch(block_id):
    load_block(block_id)
    root = OUTPUT / block_id
    root.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in OUTPUT.glob("*/launch.json"):
            prior = read(path)
            try:
                os.kill(prior["pid"], 0)
            except ProcessLookupError:
                continue
            raise ValueError(f"A collection launcher is still alive: {path}")
        if status(block_id)["status"] == "completed":
            print(json.dumps(status(block_id)))
            return
        with (root / "run.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-i",
                    sys.executable,
                    "-u",
                    str(Path(__file__).resolve()),
                    "run",
                    "--block",
                    block_id,
                ],
                cwd=Path.cwd(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        record = {
            "pid": process.pid,
            "block": block_id,
            "started_at": datetime.now(UTC).isoformat(),
            "log": str(root / "run.log"),
            "idle_sleep_prevention": True,
            "lid_close_sleep_prevented": False,
        }
        atomic_json(root / "launch.json", record)
        print(json.dumps(record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "launch", "run", "status"))
    parser.add_argument("--block", default="main-001")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "status":
        print(json.dumps(status(args.block)))
    elif args.command == "launch":
        launch(args.block)
    else:
        run(args.block)


if __name__ == "__main__":
    main()
