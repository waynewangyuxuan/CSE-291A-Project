#!/usr/bin/env python3
"""Audit Phase 2 candidate pairs against the real benchmark without model inference."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from audit_phase1_workload import inspect_selection

from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import make_injections


def appended_check(suite, user, goal, style: str) -> dict:
    payloads, injections = make_injections(suite, GroundTruthPipeline(None), style, user, goal)
    pre = user.init_environment(suite.load_and_inject_default_environment(injections))
    _, _, post, messages, _ = GroundTruthPipeline(user).query(
        user.PROMPT, FunctionsRuntime(suite.tools), pre.model_copy(deep=True)
    )
    calls = [
        call
        for message in messages
        if message["role"] == "assistant"
        for call in message.get("tool_calls") or []
    ]
    return {
        "payloads_sha256": stable_hash(payloads),
        "injections_sha256": stable_hash(injections),
        "injection_vectors": sorted(injections),
        "ground_truth_tool_count": len(calls),
        "legitimate_utility": bool(
            suite._check_user_task_utility(user, messages[-1]["content"], pre, post, calls)
        ),
        "noop_attack_success": bool(
            suite._check_injection_task_security(goal, [], pre, pre.model_copy(deep=True), [])
        ),
        "legal_path_attack_success": bool(
            suite._check_injection_task_security(goal, messages[-1]["content"], pre, post, calls)
        ),
    }


def audit(path: Path) -> dict:
    manifest = json.loads(path.read_text())
    prior_path = Path(manifest["prior_manifest"])
    prior = json.loads(prior_path.read_text())
    installed = json.loads(
        importlib.metadata.distribution("agentdojo").read_text("direct_url.json") or "{}"
    )
    if (
        manifest["schema_version"] != 1
        or manifest["status"] != "qualification_candidates_not_frozen_evaluation"
        or manifest["attack_style"] != "ignore_previous_append"
        or manifest["max_tool_steps"] != 15
        or manifest["benchmark_version"] != prior["benchmark_version"]
        or installed.get("vcs_info", {}).get("commit_id") != manifest["agentdojo_commit"]
        or manifest["agentdojo_commit"] != prior["agentdojo_commit"]
    ):
        raise ValueError("Candidate protocol or pinned benchmark differs")
    selection = manifest["selection"]
    if not selection:
        raise ValueError("No candidate tasks")
    for name, spec in selection.items():
        old = prior["selection"][name]
        seen = set(old["task_ids"]) | set(old["development_task_ids"])
        ids = spec["task_ids"]
        if not ids or len(ids) != len(set(ids)) or seen.intersection(ids):
            raise ValueError(f"Duplicate, empty or previously used candidate IDs: {name}")
        if len(spec["injection_task_ids"]) != 1:
            raise ValueError("Exactly one provisional attack goal per suite is required")
    inspected = inspect_selection(selection, manifest["benchmark_version"])
    cases = []
    for name, evidence in inspected.items():
        suite = get_suite(manifest["benchmark_version"], name)
        goal = evidence["goals"][0]
        goal_checks = {case["user_task_id"]: case for case in goal["initialized_user_state_checks"]}
        for user in evidence["users"]:
            appended = appended_check(
                suite,
                suite.user_tasks[user["id"]],
                suite.injection_tasks[goal["id"]],
                manifest["attack_style"],
            )
            checks = {
                "reference_utility": user["ground_truth_utility"],
                "nonvacuous_utility": not user["empty_noop_utility"],
                "injectable": bool(user["ground_truth_exposed_vectors"])
                and bool(user["attack_generator_vectors"]),
                "reference_within_tool_budget": user["ground_truth_tool_count"]
                <= manifest["max_tool_steps"],
                "attack_checker_positive_control": goal_checks[user["id"]]["ground_truth_success"],
                "attack_checker_negative_control": not goal_checks[user["id"]][
                    "empty_noop_success"
                ],
                "appended_reference_utility": appended["legitimate_utility"],
                "appended_within_tool_budget": appended["ground_truth_tool_count"]
                <= manifest["max_tool_steps"],
                "appended_noop_not_attack": not appended["noop_attack_success"],
                "legitimate_path_not_attack": not appended["legal_path_attack_success"],
            }
            cases.append(
                {
                    "suite": name,
                    "task": user["id"],
                    "goal": goal["id"],
                    "mechanical_checks_passed": all(checks.values()),
                    "checks": checks,
                    "appended_reference": appended,
                }
            )
    sources = {
        "candidate_manifest": path,
        "prior_manifest": prior_path,
        "qualification_script": Path(__file__),
        "workload_audit": Path(__file__).with_name("audit_phase1_workload.py"),
        "runner": Path("src/agent_tradeoffs/runner.py"),
        "dependency_lock": Path("uv.lock"),
    }
    return {
        "kind": "offline_qualification_not_model_evaluation",
        "benchmark_version": manifest["benchmark_version"],
        "agentdojo_commit": manifest["agentdojo_commit"],
        "source_sha256": {
            role: hashlib.sha256(source.read_bytes()).hexdigest()
            for role, source in sources.items()
        },
        "model_episodes": 0,
        "candidate_count": len(cases),
        "mechanical_pass_count": sum(case["mechanical_checks_passed"] for case in cases),
        "formal_selection_frozen": False,
        "remaining_gates": [
            "task_and_scorer_semantic_review",
            "workflow_family_overlap_disclosure",
            "development_only_context_budget_calibration",
            "complete_pair_and_runtime_manifest_freeze",
        ],
        "cases": cases,
        "benchmark_reference_audit": inspected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("configs/phase2/preparation-candidates.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.manifest)
    if args.output.exists() and json.loads(args.output.read_text()) != report:
        raise ValueError("Existing qualification evidence differs; choose a new output path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("candidate_count", "mechanical_pass_count")}))
    return 0 if report["mechanical_pass_count"] == report["candidate_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
