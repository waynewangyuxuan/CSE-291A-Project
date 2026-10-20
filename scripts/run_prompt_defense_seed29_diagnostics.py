"""Frozen20 selected-case seed29 diagnostics; old first-wave files are read-only."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import subprocess
import textwrap
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

import agentdojo
from agentdojo.task_suite.load_suites import get_suite

from agent_tradeoffs.artifact_io import compact_episode_artifacts
from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.prompt_defense import (
    ARMS,
    DEPLOYMENT_SHA256,
    ORIGINAL,
    ROOT,
    digest,
    existing_result,
    now,
    selected_config,
    shared_endpoint_lock,
    verify_frozen,
)
from agent_tradeoffs.prompt_policy import prompt_identity
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    RuntimeDeploymentChangedError,
    capture_runtime,
    guard_runtime_process,
    implementation_identity,
    require_disk_reserve,
    run_episode,
)

NAME = "prompt-defense-seed29-diagnostics-v1"
DIRECTORY = ROOT / "configs" / NAME
CAMPAIGN = ROOT / "artifacts" / NAME
FIRST = ROOT / "artifacts/prompt-defense-first-wave-v1"
BASE_COMMIT = "c6fb5b19a4246b4799ea44eddfd4fbb229b38965"
FIRST_IDENTITY = "4424e31e36c5175611e672ed4d2f582c844df9855d977ba4749b417de33b4ec2"
SELECTED = (31, 49, 27, 149)
ORIGINAL_INVENTORY = ORIGINAL / "artifacts/phase1-error-retries/protected-original-inventory.json"
ORIGINAL_INVENTORY_SHA = "ed799722eb111cf16d3e2074b1f56475ef5a6840ccf06644fff24db62eed4eda"


def load(path):
    return json.loads(Path(path).read_text())


def protected_inventory():
    """Anchor old tracked bytes to pushed commit and old raw bytes to its audited hashes."""
    records = {}

    def add(path, expected, size=None):
        path = Path(path)
        if digest(path) != expected or (size is not None and path.stat().st_size != size):
            raise ValueError(f"Protected file changed: {path}")
        records[str(path)] = {"sha256": expected, "bytes": path.stat().st_size}

    names = (
        subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE_COMMIT], cwd=ROOT)
        .decode()
        .splitlines()
    )
    import hashlib

    for name in names:
        blob = subprocess.check_output(["git", "show", BASE_COMMIT + ":" + name], cwd=ROOT)
        add(ROOT / name, hashlib.sha256(blob).hexdigest(), len(blob))
    raw_audit = load(ROOT / "results/prompt-defense-first-wave-v1/raw-evidence-audit.json")
    if not raw_audit["passed"] or raw_audit["finalized_episodes"] != 600:
        raise ValueError("First-wave audit incomplete")
    add(FIRST / "manifest.json", raw_audit["campaign_manifest_sha256"])
    for item in raw_audit["inventory"]:
        folder = FIRST / "episodes" / item["episode_id"]
        add(folder / "result.json", item["result_sha256"])
        add(folder / "compression.json", item["compression_journal_sha256"])
        for artifact in load(folder / "compression.json")["artifacts"]:
            add(
                folder / artifact["path"],
                artifact["compressed_sha256"],
                artifact["compressed_bytes"],
            )
    add(ORIGINAL_INVENTORY, ORIGINAL_INVENTORY_SHA)
    return {
        "baseline_commit": BASE_COMMIT,
        "files": records,
        "original_inventory_path": str(ORIGINAL_INVENTORY),
        "original_inventory_sha256": ORIGINAL_INVENTORY_SHA,
    }


def verify_protected(inventory):
    for name, record in inventory["files"].items():
        path = Path(name)
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Protected first-wave file changed: {name}")
    original = Path(inventory["original_inventory_path"])
    if digest(original) != inventory["original_inventory_sha256"]:
        raise ValueError("Original inventory changed")
    records = load(original)["files"]
    if len(records) != 2631:
        raise ValueError("Original inventory population changed")
    for record in records:
        path = ORIGINAL / record["path"]
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Protected original file changed: {path}")
    return {
        "first_wave_and_tracked_files": len(inventory["files"]),
        "original_files": len(records),
        "passed": True,
    }


def reference(entry):
    path = FIRST / "episodes" / entry["episode_id"] / "result.json"
    value = load(path)
    if value["identity"] != FIRST_IDENTITY:
        raise ValueError("Wrong source campaign identity")
    return {
        "episode_id": entry["episode_id"],
        "result_path": str(path),
        "result_sha256": digest(path),
        "identity": value["identity"],
    }


def ground_truth_metadata(suite, task):
    value = get_suite("v1.2.2", suite).get_user_task_by_id(task)
    source = textwrap.dedent(inspect.getsource(value.ground_truth))
    tree = ast.parse(source)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "FunctionCall"
    ]
    tools = [next(k.value.value for k in n.keywords if k.arg == "function") for n in calls]
    path = Path(inspect.getsourcefile(value.ground_truth))
    return {
        "reference_call_count": len(calls),
        "reference_tools": tools,
        "source_path": str(path),
        "source_sha256": digest(path),
        "method_source": source,
        "note": (
            "Benchmark reference sequence, not minimum necessary calls or observed model steps."
        ),
    }


def benchmark_source_files():
    package = Path(agentdojo.__file__).parent
    return {
        str(p): digest(p)
        for p in sorted(package.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".json", ".j2", ".txt"}
    }


def build_plan():
    first = load(FIRST / "manifest.json")
    if first["identity"] != FIRST_IDENTITY or first["finalized_episodes"] != 600:
        raise ValueError("Wrong or unfinished first wave")
    groups = []
    for original_pair in SELECTED:
        attack = next(
            e for e in first["episodes"] if e["pair_id"] == original_pair and e["arm"] == "default"
        )
        clean = next(
            e
            for e in first["episodes"]
            if e["suite"] == attack["suite"]
            and e["user_task_id"] == attack["user_task_id"]
            and e["condition"] == "clean"
            and e["arm"] == "default"
        )
        groups.extend([(clean, "selected_clean"), (attack, "selected_attack")])
    for task in ["user_task_27", "user_task_35"]:
        groups.append(
            (
                next(
                    e
                    for e in first["episodes"]
                    if e["suite"] == "workspace"
                    and e["user_task_id"] == task
                    and e["condition"] == "clean"
                    and e["arm"] == "default"
                ),
                "budget_trajectory_clean",
            )
        )
    schedule = []
    for pair, (source, reason) in enumerate(groups):
        for arm in ARMS if pair % 2 == 0 else tuple(reversed(ARMS)):
            old = next(
                e
                for e in first["episodes"]
                if e["pair_id"] == source["pair_id"] and e["arm"] == arm
            )
            schedule.append(
                {
                    **{
                        k: old[k]
                        for k in [
                            "suite",
                            "arm",
                            "user_task_id",
                            "condition",
                            "attack_style",
                            "injection_task_id",
                        ]
                    },
                    "index": len(schedule),
                    "pair_id": pair,
                    "episode_id": f"{pair:03d}-{old['suite']}-{old['user_task_id']}-{arm}",
                    "seed": 29,
                    "selection_reason": reason,
                    "reference": reference(old),
                }
            )
    configs = json.loads(json.dumps(first["identity_inputs"]["plan"]["configurations"]))
    for arms in configs.values():
        for config in arms.values():
            config["model"]["seed"] = 29
            config["runner"]["output_root"] = str(CAMPAIGN)
            config["runner"]["runtime_metadata_path"] = str(CAMPAIGN / "runtime/server.json")
    tasks = sorted({(e["suite"], e["user_task_id"]) for e in schedule})
    return {
        "schema_version": 1,
        "campaign_id": NAME,
        "exploratory": True,
        "seed": 29,
        "expected_episodes": 20,
        "expected_pairs": 10,
        "selected_first_wave_attack_pair_ids": list(SELECTED),
        "first_wave_identity": FIRST_IDENTITY,
        "first_wave_manifest_sha256": digest(FIRST / "manifest.json"),
        "required_semantic_runtime_sha256": DEPLOYMENT_SHA256,
        "prompts": {a: prompt_identity(a) for a in ARMS},
        "configurations": configs,
        "shared_endpoint_lock_root": str(ORIGINAL / "artifacts/runtime/evaluator-locks"),
        "runtime_source_metadata": str(
            ORIGINAL / "artifacts/phase1-error-retries/runtime/server.json"
        ),
        "schedule": schedule,
        "benchmark_source_files": benchmark_source_files(),
        "ground_truth": {
            f"{suite}/{task}": ground_truth_metadata(suite, task) for suite, task in tasks
        },
        "retry_policy": (
            "Every finalized error stays terminal unknown; "
            "no automatic retry or replacement of old600."
        ),
    }


def validate_plan(plan):
    if plan != build_plan():
        raise ValueError("Plan differs from exact authorized selection/configurations")
    entries = plan["schedule"]
    if len(entries) != 20 or len({e["episode_id"] for e in entries}) != 20:
        raise ValueError("Expected20 unique episodes")
    if Counter(e["condition"] for e in entries) != {"clean": 12, "attack": 8}:
        raise ValueError("Wrong clean/attack mapping")
    for index in range(0, 20, 2):
        a, b = entries[index : index + 2]
        if a["pair_id"] != b["pair_id"] or {a["arm"], b["arm"]} != set(ARMS):
            raise ValueError("Invalid adjacent pair")


def identity_inputs(plan):
    paths = [
        Path(__file__),
        ROOT / "tests/test_prompt_defense_seed29_diagnostics.py",
        DIRECTORY / "plan.json",
        DIRECTORY / "protocol.json",
        DIRECTORY / "protected-inventory.json",
    ]
    paths.extend(Path(record["source_path"]) for record in plan["ground_truth"].values())
    old = load(FIRST / "manifest.json")["identity_inputs"]
    return {
        "plan": plan,
        "implementation": implementation_identity(),
        "frozen_files": {
            **old["frozen_files"],
            **plan["benchmark_source_files"],
            **{str(p): digest(p) for p in paths},
        },
    }


def checked_existing_result(folder, identity, entry):
    result = existing_result(folder, identity)
    if result is not None and any(result.get(k) != value for k, value in entry.items()):
        raise ValueError("Result schedule metadata mismatch")
    return result


def ensure_idle():
    with urlopen("http://127.0.0.1:8080/slots", timeout=10) as response:
        slots = json.load(response)
    if not slots or any(s["is_processing"] is not False for s in slots):
        raise ValueError("Service is not idle; no inference started")
    return slots


def wait_for_idle():
    deadline = time.monotonic() + 120
    while True:
        try:
            return ensure_idle()
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def run(*, resume=False):
    plan = load(DIRECTORY / "plan.json")
    validate_plan(plan)
    inputs = load(DIRECTORY / "identity.json")
    if inputs != identity_inputs(plan):
        raise ValueError("Prepared identity changed before launch/resume")
    verify_frozen(inputs)
    inventory = load(DIRECTORY / "protected-inventory.json")
    pre_guard = verify_protected(inventory)
    with shared_endpoint_lock(plan):
        slots = ensure_idle()
        config = selected_config(plan, plan["schedule"][0])
        metadata_path = config.runner.runtime_metadata_path
        if not metadata_path.is_file():
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(metadata_path, load(plan["runtime_source_metadata"]))
        runtime, pid = capture_runtime(config)
        if stable_hash(runtime["semantic_identity"]) != DEPLOYMENT_SHA256:
            raise ValueError("Deployment differs from original formal F16")
        identity = stable_hash({"inputs": inputs, "runtime": runtime["semantic_identity"]})
        path = CAMPAIGN / "manifest.json"
        if resume:
            manifest = load(path)
            if manifest["identity"] != identity:
                raise ValueError("Resume identity mismatch")
            manifest.setdefault("resume_runtime_observations", []).append(runtime)
        else:
            if path.exists() or (CAMPAIGN / "episodes").exists():
                raise ValueError("Campaign already exists; explicit resume required")
            manifest = {
                "schema_version": 1,
                "campaign_id": NAME,
                "identity": identity,
                "identity_inputs": inputs,
                "runtime": runtime,
                "created_at": now(),
                "planned_episodes": 20,
                "episodes": plan["schedule"],
                "pre_guard": pre_guard,
                "initial_idle_slots": slots,
            }
        process = runtime["metadata_snapshot"]["process_identity"]
        manifest["status"] = "running"
        atomic_json(path, manifest)
        counts = Counter()
        try:
            for entry in plan["schedule"]:
                verify_frozen(inputs)
                guard_runtime_process(pid, process)
                if (
                    digest(Path(entry["reference"]["result_path"]))
                    != entry["reference"]["result_sha256"]
                ):
                    raise ValueError("Source result changed")
                folder = CAMPAIGN / "episodes" / entry["episode_id"]
                folder.mkdir(parents=True, exist_ok=True)
                result = checked_existing_result(folder, identity, entry)
                if result is None:
                    config = selected_config(plan, entry)
                    config = config.model_copy(
                        update={"runner": config.runner.model_copy(update={"server_pid": pid})}
                    )
                    require_disk_reserve(CAMPAIGN, config.runner.min_free_disk_bytes)
                    remnants = [p for p in folder.iterdir() if p.is_file()]
                    if remnants:
                        archive = folder / "interrupted_attempts" / uuid.uuid4().hex
                        archive.mkdir(parents=True)
                        for item in remnants:
                            item.rename(archive / item.name)
                    manifest["active_episode"] = entry["episode_id"]
                    manifest["updated_at"] = now()
                    atomic_json(path, manifest)
                    result = run_episode(
                        config,
                        entry,
                        folder,
                        identity,
                        runtime_guard=lambda: guard_runtime_process(pid, process),
                    )
                    print(
                        f"{entry['index'] + 1}/20 {entry['episode_id']} {result['status']} "
                        f"U={result['utility']} A={result['attack_success']}",
                        flush=True,
                    )
                verify_frozen(inputs)
                compact_episode_artifacts(folder)
                counts[result["status"]] += 1
                manifest["counts"] = dict(counts)
                manifest["finalized_episodes"] = sum(counts.values())
                atomic_json(path, manifest)
                if result.get("retryable"):
                    raise RuntimeDeploymentChangedError(result["error"]["message"])
            manifest["status"] = "finalized_with_errors" if counts["error"] else "finalized"
            manifest["active_episode"] = None
        except BaseException:
            manifest["status"] = "interrupted"
            raise
        finally:
            manifest["updated_at"] = now()
            manifest["post_guard"] = verify_protected(inventory)
            atomic_json(path, manifest)
        manifest["final_idle_slots"] = wait_for_idle()
        atomic_json(path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        if (DIRECTORY / "plan.json").exists():
            raise ValueError("Already prepared; do not overwrite a freeze")
        inventory = protected_inventory()
        verify_protected(inventory)
        atomic_json(DIRECTORY / "protected-inventory.json", inventory)
        plan = build_plan()
        validate_plan(plan)
        atomic_json(DIRECTORY / "plan.json", plan)
        atomic_json(DIRECTORY / "identity.json", identity_inputs(plan))
    else:
        run(resume=args.resume)


if __name__ == "__main__":
    main()
