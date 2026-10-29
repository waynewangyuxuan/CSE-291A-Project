"""A frozen, adjacent-pair prompt comparison over the original Phase 1 tasks.

This driver reuses the original per-episode agent, tools, limits and checkers.
It captures the deployment once, serializes access with the original evaluator's
lock, and checks process plus frozen source identities before every episode.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import read_suite_file

from agent_tradeoffs.agent import LocalAgent
from agent_tradeoffs.artifact_io import compact_episode_artifacts
from agent_tradeoffs.config import ExperimentConfig, load_config, stable_hash
from agent_tradeoffs.prompt_policy import prompt_identity
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    RuntimeDeploymentChangedError,
    capture_runtime,
    guard_runtime_process,
    implementation_identity,
    make_injections,
    require_disk_reserve,
    run_episode,
)

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = Path("/Users/waynewang/CSE-291A-Project")
NAME = "prompt-defense-first-wave-v1"
ARMS = ("default", "source_boundary_v1")
STYLES = ("direct_append", "ignore_previous_append")
DEPLOYMENT_SHA256 = "4c3c5e1d45a1825df4628a6406d02ea459fb27f89415c3ef482ceefddbd72174"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def selected_config(plan: dict, entry: dict) -> ExperimentConfig:
    base = ExperimentConfig.model_validate(plan["configurations"][entry["suite"]][entry["arm"]])
    return base.model_copy(
        update={
            "experiment": base.experiment.model_copy(
                update={
                    "task_ids": (entry["user_task_id"],),
                    "mode": entry["condition"],
                    "attack": entry["attack_style"] or "direct_append",
                    "injection_task_ids": (
                        (entry["injection_task_id"],) if entry["injection_task_id"] else ()
                    ),
                }
            )
        }
    )


def make_schedule(selection: dict) -> list[dict]:
    result = []
    pair = 0
    for suite, selected in selection.items():
        for task in selected["task_ids"]:
            conditions = [("clean", None, None)] + [
                ("attack", style, goal)
                for style in STYLES
                for goal in selected["injection_task_ids"]
            ]
            for condition, style, goal in conditions:
                order = ARMS if pair % 2 == 0 else tuple(reversed(ARMS))
                for arm in order:
                    episode_id = f"{pair:03d}-{suite}-{task}-{arm}"
                    result.append(
                        {
                            "index": len(result),
                            "pair_id": pair,
                            "episode_id": episode_id,
                            "suite": suite,
                            "arm": arm,
                            "seed": 17,
                            "user_task_id": task,
                            "condition": condition,
                            "attack_style": style,
                            "injection_task_id": goal,
                        }
                    )
                pair += 1
    return result


def _marker_paths(value: Any, marker: str, path: tuple = ()) -> list[list]:
    found = []
    if isinstance(value, str) and marker in value:
        found.append(list(path))
    elif isinstance(value, dict):
        for key, item in value.items():
            if marker in str(key):
                found.append([*path, {"mapping_key": str(key)}])
            found.extend(_marker_paths(item, marker, (*path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_marker_paths(item, marker, (*path, index)))
    return found


def source_inventory(selection: dict) -> dict:
    """Map literal YAML vector fields without reconstructing saved mutable states."""
    inventory = {}
    for suite_name, selected in selection.items():
        suite = get_suite("v1.2.2", suite_name)
        environment = read_suite_file(suite.name, "environment.yaml", suite.data_path)
        defaults = suite.get_injection_vector_defaults()
        records = {}
        for vector in defaults:
            marker = "PROMPTDEFENSEVECTORBOUNDARYMARKER"
            values = {**defaults, vector: marker}
            parsed = yaml.safe_load(environment.format(**values))
            paths = _marker_paths(parsed, marker)
            if not paths:
                raise ValueError(f"No source mapping for {suite_name}/{vector}")
            records[vector] = {"yaml_field_paths": paths, "expected_transport_role": "tool"}
        planned = []
        # Both fixed templates use the same vectors; preserve each generated text.
        for style in STYLES:
            for task in selected["task_ids"]:
                for goal in selected["injection_task_ids"]:
                    config = ExperimentConfig.model_validate(
                        {
                            "experiment": {"suite": suite_name},
                            "model": {"model": "qwen3-8b-f16"},
                        }
                    )
                    agent = LocalAgent(config, Path("."), client=object())
                    raw, combined = make_injections(
                        suite,
                        agent,
                        style,
                        suite.get_user_task_by_id(task),
                        suite.get_injection_task_by_id(goal),
                    )
                    planned.append(
                        {
                            "user_task_id": task,
                            "injection_task_id": goal,
                            "attack_style": style,
                            "vectors": sorted(raw),
                            "attack_payloads": raw,
                            "combined_injection_sha256": {
                                v: hashlib.sha256(t.encode()).hexdigest()
                                for v, t in combined.items()
                            },
                        }
                    )
        inventory[suite_name] = {
            "environment_yaml_sha256": hashlib.sha256(environment.encode()).hexdigest(),
            "vectors": records,
            "planned_attacks": planned,
        }
    return {
        "schema_version": 1,
        "mapping_method": "literal_marker_in_raw_environment_yaml",
        "scope": "planned vectors; actual exposure and carrier functions require request evidence",
        "suites": inventory,
    }


def build_plan() -> dict:
    manifest_path = ROOT / "configs/phase1/task-manifest.json"
    task_manifest = json.loads(manifest_path.read_text())
    configs = {}
    for suite in task_manifest["selection"]:
        original = load_config(ROOT / f"configs/phase1/phase1-f16-s17-{suite}-clean.toml")
        configs[suite] = {}
        for arm in ARMS:
            config = original.model_copy(
                update={
                    "prompt": original.prompt.model_copy(update={"policy": arm}),
                    "runner": original.runner.model_copy(
                        update={
                            "output_root": ROOT / "artifacts" / NAME,
                            "runtime_metadata_path": ORIGINAL
                            / "artifacts/phase1-error-retries/runtime/server.json",
                        }
                    ),
                }
            )
            configs[suite][arm] = config.model_dump(mode="json")
    return {
        "schema_version": 1,
        "campaign_id": NAME,
        "exploratory": True,
        "seeds": [17],
        "unique_user_tasks": 60,
        "expected_episodes": 600,
        "expected_arm_episodes": 300,
        "task_manifest": str(manifest_path),
        "task_manifest_sha256": digest(manifest_path),
        "required_semantic_runtime_sha256": DEPLOYMENT_SHA256,
        "prompts": {arm: prompt_identity(arm) for arm in ARMS},
        "configurations": configs,
        "shared_endpoint_lock_root": str(ORIGINAL / "artifacts/runtime/evaluator-locks"),
        "schedule": make_schedule(task_manifest["selection"]),
        "retry_policy": "No automatic retries of finalized errors; interrupted evidence retained.",
    }


def validate_plan(plan: dict) -> None:
    if plan["campaign_id"] != NAME or plan["expected_episodes"] != 600:
        raise ValueError("Unexpected campaign size or name")
    manifest = Path(plan["task_manifest"])
    if digest(manifest) != plan["task_manifest_sha256"]:
        raise ValueError("Task manifest changed")
    expected = make_schedule(json.loads(manifest.read_text())["selection"])
    if plan["schedule"] != expected:
        raise ValueError("Schedule changed, duplicated or incomplete")
    if plan != build_plan():
        raise ValueError("Plan differs from the frozen first-wave design")
    if plan["prompts"] != {arm: prompt_identity(arm) for arm in ARMS}:
        raise ValueError("Frozen prompt mismatch")
    for entry in expected:
        config = selected_config(plan, entry)
        if config.prompt.policy != entry["arm"] or config.model.seed != 17:
            raise ValueError("Arm or seed mismatch")
        if config.context.policy != "full":
            raise ValueError("Context mismatch")


def frozen_identity(plan: dict, directory: Path) -> dict:
    inputs = [
        ROOT / "uv.lock",
        Path(plan["task_manifest"]),
        Path(__file__),
        Path(__file__).with_name("prompt_defense_analysis.py"),
        Path(__file__).with_name("analysis.py"),
        directory / "plan.json",
        directory / "source-inventory.json",
        directory / "protocol.json",
    ]
    return {
        "plan": plan,
        "implementation": implementation_identity(),
        "frozen_files": {str(path): digest(path) for path in inputs},
    }


def verify_frozen(identity_inputs: dict) -> None:
    if identity_inputs["plan"]["prompts"] != {arm: prompt_identity(arm) for arm in ARMS}:
        raise ValueError("Resolved system prompt changed since freeze")
    for path, sha in identity_inputs["frozen_files"].items():
        if digest(Path(path)) != sha:
            raise ValueError(f"Frozen input changed: {path}")
    if implementation_identity() != identity_inputs["implementation"]:
        raise ValueError("Frozen harness implementation changed")


@contextmanager
def shared_endpoint_lock(plan: dict):
    endpoint = urlparse(next(iter(plan["configurations"].values()))["default"]["model"]["base_url"])
    key = stable_hash({"host": "loopback", "port": endpoint.port or 80})
    # This is the same preexisting lock used by the original repository's runner.
    # No source/config/result file in that repository is modified.
    path = Path(plan["shared_endpoint_lock_root"]) / f"{key}.lock"
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Original evaluator still owns the inference endpoint") from error
        yield


def read_evidence(directory: Path, name: str) -> str:
    path = directory / name
    return (
        path.read_text()
        if path.is_file()
        else gzip.decompress((directory / (name + ".gz")).read_bytes()).decode()
    )


def existing_result(directory: Path, identity: str) -> dict | None:
    path = directory / "result.json"
    if not path.is_file():
        return None
    result = json.loads(path.read_text())
    if result.get("identity") != identity:
        raise ValueError(f"Result identity mismatch: {path}")
    # All terminal errors stay unknown. Runtime-change errors stop collection and
    # require a new campaign, rather than being silently overwritten on resume.
    if result.get("status") not in {"completed", "error"}:
        raise ValueError("Nonterminal result cannot be skipped")
    if result["status"] == "error" and (
        result.get("utility") is not None or result.get("attack_success") is not None
    ):
        raise ValueError("Error scores must remain unknown")
    if not any((directory / name).is_file() for name in ("trace.json", "trace.json.gz")):
        raise ValueError("Finalized result is missing its trace")
    if result.get("requests", 0) and not any(
        (directory / name).is_file() for name in ("requests.jsonl", "requests.jsonl.gz")
    ):
        raise ValueError("Finalized result is missing its recorded requests")
    if result.get("retryable"):
        raise ValueError("Runtime-change error retained; this campaign cannot silently resume it")
    return result


def run(plan_path: Path, *, resume: bool = False) -> dict:
    plan = json.loads(plan_path.read_text())
    validate_plan(plan)
    inputs = frozen_identity(plan, plan_path.parent)
    verify_frozen(inputs)
    config = selected_config(plan, plan["schedule"][0])
    root = config.runner.output_root
    with shared_endpoint_lock(plan):
        runtime, pid = capture_runtime(config)
        if stable_hash(runtime["semantic_identity"]) != plan["required_semantic_runtime_sha256"]:
            raise ValueError("Deployment differs from original formal F16 deployment")
        identity = stable_hash({"inputs": inputs, "runtime": runtime["semantic_identity"]})
        manifest_path = root / "manifest.json"
        if resume:
            manifest = json.loads(manifest_path.read_text())
            if manifest["identity"] != identity:
                raise ValueError("Resume identity mismatch")
            manifest.setdefault("resume_runtime_observations", []).append(runtime)
        else:
            root.mkdir(parents=True, exist_ok=False)
            manifest = {
                "schema_version": 1,
                "campaign_id": NAME,
                "identity": identity,
                "identity_inputs": inputs,
                "runtime": runtime,
                "created_at": now(),
                "planned_episodes": 600,
                "episodes": plan["schedule"],
            }
        process_identity = runtime["metadata_snapshot"]["process_identity"]
        manifest["status"] = "running"
        atomic_json(manifest_path, manifest)
        counts = Counter()
        try:
            for entry in plan["schedule"]:
                verify_frozen(inputs)
                guard_runtime_process(pid, process_identity)
                directory = root / "episodes" / entry["episode_id"]
                directory.mkdir(parents=True, exist_ok=True)
                result = existing_result(directory, identity)
                if result is None:
                    config = selected_config(plan, entry)
                    config = config.model_copy(
                        update={"runner": config.runner.model_copy(update={"server_pid": pid})}
                    )
                    require_disk_reserve(root, config.runner.min_free_disk_bytes)
                    remnants = [item for item in directory.iterdir() if item.is_file()]
                    if remnants:
                        archive = directory / "interrupted_attempts" / uuid.uuid4().hex
                        archive.mkdir(parents=True)
                        for item in remnants:
                            item.rename(archive / item.name)
                    manifest["active_episode"] = entry["episode_id"]
                    manifest["updated_at"] = now()
                    atomic_json(manifest_path, manifest)
                    result = run_episode(
                        config,
                        entry,
                        directory,
                        identity,
                        runtime_guard=lambda: guard_runtime_process(pid, process_identity),
                    )
                    print(
                        f"{entry['index'] + 1}/600 {entry['episode_id']} {result['status']} "
                        f"U={result['utility']} A={result['attack_success']}",
                        flush=True,
                    )
                verify_frozen(inputs)
                compact_episode_artifacts(directory)
                counts[result["status"]] += 1
                manifest["counts"] = dict(counts)
                manifest["finalized_episodes"] = sum(counts.values())
                atomic_json(manifest_path, manifest)
                if result.get("retryable"):
                    raise RuntimeDeploymentChangedError(result["error"]["message"])
            manifest["status"] = "finalized_with_errors" if counts["error"] else "finalized"
            manifest["active_episode"] = None
        except BaseException:
            manifest["status"] = "interrupted"
            raise
        finally:
            manifest["updated_at"] = now()
            atomic_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    directory = ROOT / "configs/prompt-defense-v1"
    if args.command == "prepare":
        directory.mkdir(parents=True, exist_ok=True)
        plan = build_plan()
        validate_plan(plan)
        atomic_json(directory / "plan.json", plan)
        selection = json.loads(Path(plan["task_manifest"]).read_text())["selection"]
        atomic_json(directory / "source-inventory.json", source_inventory(selection))
    else:
        run(directory / "plan.json", resume=args.resume)


if __name__ == "__main__":
    main()
