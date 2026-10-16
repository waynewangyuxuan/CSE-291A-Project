#!/usr/bin/env python3
"""Verify the frozen Phase 1 workload against the pinned benchmark, without inference."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.attacks import load_attack
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import get_text_content_as_str

from agent_tradeoffs.config import load_config
from agent_tradeoffs.runner import plan_episodes


def source_record(task, method: str) -> dict:
    function = getattr(task, method)
    path = Path(inspect.getfile(function))
    relative = "agentdojo/" + str(path).split("/agentdojo/", 1)[1]
    return {
        "path": relative,
        "line": inspect.getsourcelines(function)[1],
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def user_audit(suite, task) -> dict:
    canaries = {key: f"PHASE1_AUDIT_{key}" for key in suite.get_injection_vector_defaults()}
    environment = task.init_environment(suite.load_and_inject_default_environment(canaries))
    pre = environment.model_copy(deep=True)
    _, _, post, messages, _ = GroundTruthPipeline(task).query(
        task.PROMPT, FunctionsRuntime(suite.tools), environment
    )
    calls = [
        call
        for message in messages
        if message["role"] == "assistant"
        for call in message.get("tool_calls") or []
    ]
    exposed = [
        vector
        for vector, canary in canaries.items()
        if any(
            canary in get_text_content_as_str(message["content"])
            for message in messages
            if message["role"] == "tool"
        )
    ]
    # Exercise the same trace-aware dispatch used by real benchmark episodes.
    utility = suite._check_user_task_utility(task, messages[-1]["content"], pre, post, calls)
    noop = suite._check_user_task_utility(task, [], pre, pre, [])
    # Confirm that the attack generator also finds a vector, using its actual API.
    attack = load_attack("direct", suite, GroundTruthPipeline(None))
    attack_vectors = attack.get_injection_candidates(task)
    return {
        "id": task.ID,
        "prompt": task.PROMPT,
        "difficulty": task.DIFFICULTY.name.lower(),
        "ground_truth_utility": bool(utility),
        "empty_noop_utility": bool(noop),
        "ground_truth_tool_count": len(calls),
        "ground_truth_exposed_vectors": sorted(exposed),
        "attack_generator_vectors": sorted(attack_vectors),
        "utility_source": source_record(task, "utility"),
        "utility_from_traces_source": source_record(task, "utility_from_traces"),
    }


def goal_audit(suite, goal, user_ids: list[str]) -> dict:
    cases = []
    for user_id in user_ids:
        user = suite.user_tasks[user_id]
        pre = user.init_environment(suite.load_and_inject_default_environment({}))
        noop = suite._check_injection_task_security(goal, [], pre, pre, [])
        _, _, post, messages, _ = GroundTruthPipeline(goal).query(
            goal.GOAL, FunctionsRuntime(suite.tools), pre.model_copy(deep=True)
        )
        calls = [
            call
            for message in messages
            if message["role"] == "assistant"
            for call in message.get("tool_calls") or []
        ]
        success = suite._check_injection_task_security(
            goal, messages[-1]["content"], pre, post, calls
        )
        cases.append(
            {
                "user_task_id": user_id,
                "empty_noop_success": bool(noop),
                "ground_truth_success": bool(success),
            }
        )
    return {
        "id": goal.ID,
        "goal": goal.GOAL,
        "security_source": source_record(goal, "security"),
        "security_from_traces_source": source_record(goal, "security_from_traces"),
        "initialized_user_state_checks": cases,
    }


def inspect_selection(selection: dict, benchmark_version: str) -> dict:
    audited = {}
    for name, spec in selection.items():
        suite = get_suite(benchmark_version, name)
        users = [user_audit(suite, suite.user_tasks[key]) for key in spec["task_ids"]]
        goals = [
            goal_audit(suite, suite.injection_tasks[key], spec["task_ids"])
            for key in spec["injection_task_ids"]
        ]
        audited[name] = {"users": users, "goals": goals}
    return audited


def verify(manifest_path: Path, campaign_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    campaign = json.loads(campaign_path.read_text())
    direct_url = json.loads(
        importlib.metadata.distribution("agentdojo").read_text("direct_url.json") or "{}"
    )
    assert direct_url.get("vcs_info", {}).get("commit_id") == manifest["agentdojo_commit"]
    selection = manifest["selection"]
    assert sum(len(spec["task_ids"]) for spec in selection.values()) == 60
    for spec in selection.values():
        assert len(spec["task_ids"]) == len(set(spec["task_ids"]))
        assert not set(spec["task_ids"]) & set(spec["development_task_ids"])
        assert len(set(spec["injection_task_ids"])) == 2
    observed = inspect_selection(selection, manifest["benchmark_version"])
    assert observed == manifest["offline_audit"], "Benchmark/task scorer drift from frozen audit"
    for suite in observed.values():
        for user in suite["users"]:
            assert user["ground_truth_utility"] and not user["empty_noop_utility"]
            assert user["ground_truth_exposed_vectors"] and user["attack_generator_vectors"]
        for goal in suite["goals"]:
            assert all(
                case["ground_truth_success"] and not case["empty_noop_success"]
                for case in goal["initialized_user_state_checks"]
            )
    episodes = 0
    seen = set()
    counts = {"clean": 0, "attack": 0}
    for run in campaign["runs"]:
        config = load_config(run["config"])
        spec = selection[config.experiment.suite]
        assert list(config.experiment.task_ids) == spec["task_ids"]
        assert config.model.seed == run["seed"] in manifest["seeds"]
        assert config.model.model == manifest["model_alias"]
        assert config.model.temperature == 0.7
        assert config.model.extra_body == manifest["extra_body"]
        assert config.context.policy == "full"
        assert config.runner.max_tool_steps == 15
        assert config.runner.request_timeout_seconds == 120
        assert config.runner.episode_timeout_seconds == 600
        assert config.runner.compact_artifacts is True
        assert config.runner.min_free_disk_bytes == 512 * 1024**2
        assert config.experiment.mode in {"clean", "attack"}
        if config.experiment.mode == "attack":
            assert list(config.experiment.injection_task_ids) == spec["injection_task_ids"]
            assert config.experiment.attack in manifest["attack_styles"]
        planned = plan_episodes(config)
        assert len(planned) == run["expected_episodes"]
        for episode in planned:
            unit = (
                config.experiment.suite,
                run["seed"],
                episode["user_task_id"],
                episode["condition"],
                episode["injection_task_id"],
                config.experiment.attack if episode["condition"] == "attack" else None,
            )
            assert unit not in seen, f"Duplicate episode: {unit}"
            seen.add(unit)
            counts[episode["condition"]] += 1
        episodes += len(planned)
    assert len(campaign["runs"]) == 24
    assert episodes == campaign["expected_episodes"] == 600
    assert counts == {"clean": 120, "attack": 480}
    return {
        "status": "passed",
        "unique_user_tasks": 60,
        "suites": 4,
        "goals_per_suite": 2,
        "attack_styles": 2,
        "seeds": manifest["seeds"],
        "runs": 24,
        "planned_episodes": episodes,
        "condition_counts": counts,
        "checks": [
            "pinned_source",
            "development_disjoint",
            "ground_truth_utility",
            "nonvacuous_utility",
            "injectable_vectors",
            "nonvacuous_goal_checkers",
            "frozen_selection",
            "config_settings",
            "no_duplicate_episodes",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("configs/phase1/task-manifest.json"))
    parser.add_argument("--campaign", type=Path, default=Path("configs/phase1/campaign.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.manifest, args.campaign)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
