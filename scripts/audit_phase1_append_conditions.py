#!/usr/bin/env python3
"""Check frozen append conditions in fresh benchmark environments, without inference."""

from __future__ import annotations

import argparse
import hashlib
import importlib.resources
import inspect
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.attacks import load_attack
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import read_suite_file
from agentdojo.yaml_loader import ImportLoader

from agent_tradeoffs.campaign import load_campaign_plan
from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import implementation_identity, make_injections

FROZEN_IDENTITY = "b61d56e553d420327c917fb8c3ad7793bc1cce293e12fae07b90c0eefe99a2c1"
SERIALIZATION = (
    "SHA256 of UTF-8 json.dumps(value, sort_keys=True, separators=(',', ':'), "
    "allow_nan=False), with the default ensure_ascii=True; agent_tradeoffs.config.stable_hash"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class Sources:
    """Hash actual inspected methods and the YAML resources resolved by the suite loader."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.methods: dict[str, dict[str, Any]] = {}

    def add_file(self, path: Path, role: str) -> None:
        path = path.resolve()
        data = path.read_bytes()
        record = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        previous = self.files.setdefault(str(path), {**record, "roles": []})
        require(all(previous[key] == value for key, value in record.items()), f"Changed: {path}")
        if role not in previous["roles"]:
            previous["roles"].append(role)

    def add_method(self, name: str, function: Any) -> None:
        function = inspect.unwrap(function)
        path = Path(inspect.getfile(function)).resolve()
        self.add_file(path, "implementation")
        self.methods[name] = {
            "path": str(path),
            "line": inspect.getsourcelines(function)[1],
            "qualname": function.__qualname__,
        }

    def add_resource(self, path: Path) -> None:
        """Follow the actual !include nodes with ImportLoader's relative-path semantics."""
        path = path.resolve()
        if str(path) in self.files:
            return
        self.add_file(path, "benchmark_resource")
        if path.suffix not in {".yaml", ".yml"}:
            return

        def visit(node: yaml.Node) -> None:
            if isinstance(node, yaml.ScalarNode) and node.tag == "!include":
                self.add_resource(path.parent / node.value)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    visit(child)
            elif isinstance(node, yaml.MappingNode):
                for key, value in node.value:
                    visit(key)
                    visit(value)

        node = yaml.compose(path.read_text())
        if node is not None:
            visit(node)

    def verify_unchanged(self) -> None:
        for path, record in self.files.items():
            data = Path(path).read_bytes()
            require(
                len(data) == record["bytes"]
                and hashlib.sha256(data).hexdigest() == record["sha256"],
                f"Source changed during audit: {path}",
            )


def collect_sources(sources: Sources, suite: Any, spec: dict, styles: list[str]) -> None:
    for method in (
        "load_and_inject_default_environment",
        "get_injection_vector_defaults",
        "_check_user_task_utility",
        "_check_injection_task_security",
    ):
        sources.add_method(f"{suite.name}/{method}", getattr(suite, method))
    sources.add_method(f"{suite.name}/environment_type", suite.environment_type)
    for tool in suite.tools:
        sources.add_method(f"{suite.name}/tool/{tool.name}", tool.run)
    for task_id in spec["task_ids"]:
        task = suite.user_tasks[task_id]
        for method in ("init_environment", "ground_truth", "utility", "utility_from_traces"):
            sources.add_method(f"{suite.name}/{task_id}/{method}", getattr(task, method))
    for goal_id in spec["injection_task_ids"]:
        goal = suite.injection_tasks[goal_id]
        for method in ("security", "security_from_traces"):
            sources.add_method(f"{suite.name}/{goal_id}/{method}", getattr(goal, method))
    for style in styles:
        attack = load_attack(style.removesuffix("_append"), suite, GroundTruthPipeline(None))
        for method in ("__init__", "attack", "get_injection_candidates", "summarize_task"):
            sources.add_method(f"{suite.name}/{style}/{method}", getattr(attack, method))
    # This is the resource location used by read_suite_file, including custom suite data paths.
    resource_root = suite.data_path or (
        importlib.resources.files("agentdojo") / "data" / "suites" / suite.name
    )
    for filename in ("environment.yaml", "injection_vectors.yaml"):
        with importlib.resources.as_file(resource_root / filename) as path:
            sources.add_resource(path)


def audit(plan_path: Path, report: dict[str, Any], sources: Sources) -> None:
    sources.add_file(Path(__file__), "audit_script")
    sources.add_file(plan_path, "campaign_plan")
    sources.add_file(Path("uv.lock"), "dependency_lock")
    for function in (
        load_campaign_plan,
        implementation_identity,
        make_injections,
        stable_hash,
        get_suite,
        load_attack,
        read_suite_file,
        GroundTruthPipeline.query,
        FunctionsRuntime.run_function,
        ImportLoader,
    ):
        sources.add_method(f"shared/{function.__qualname__}", function)

    plan, entries, identity = load_campaign_plan(plan_path)
    require(identity == FROZEN_IDENTITY, "Campaign identity differs from frozen Phase 1")
    manifest_path = Path(plan["task_manifest"])
    sources.add_file(manifest_path, "task_manifest")
    manifest = json.loads(manifest_path.read_text())
    implementation = implementation_identity()
    revision = implementation["agentdojo_source"]["vcs_info"]["commit_id"]
    require(revision == manifest["agentdojo_commit"], "Installed benchmark revision differs")
    require(manifest["seeds"] == [17, 29], "Frozen seed repeats differ")
    styles = manifest["attack_styles"]
    require(styles == ["direct_append", "ignore_previous_append"], "Frozen attack styles differ")
    for entry in entries:
        sources.add_file(Path(entry["config_path"]), "run_configuration")
    require(
        {entry["config_snapshot"]["runner"]["max_tool_steps"] for entry in entries} == {15},
        "Frozen tool budget differs",
    )
    report["source_provenance"] = {
        "campaign_identity": identity,
        "implementation_identity": implementation,
        "pinned_agentdojo_revision": revision,
        "files": sources.files,
        "methods": sources.methods,
    }
    suites = {
        name: get_suite(manifest["benchmark_version"], name) for name in manifest["selection"]
    }
    for name, suite in suites.items():
        collect_sources(sources, suite, manifest["selection"][name], styles)
    sources.verify_unchanged()

    for name, spec in manifest["selection"].items():
        suite = suites[name]
        for user_id in spec["task_ids"]:
            user = suite.user_tasks[user_id]
            for goal_id in spec["injection_task_ids"]:
                goal = suite.injection_tasks[goal_id]
                for style in styles:
                    case = {"suite": name, "user_task": user_id, "goal": goal_id, "style": style}
                    report["checks"].append(case)
                    try:
                        payloads, injections = make_injections(
                            suite, GroundTruthPipeline(None), style, user, goal
                        )
                        case.update(
                            injection_vectors=sorted(injections),
                            payloads_sha256=stable_hash(payloads),
                            injections_sha256=stable_hash(injections),
                        )
                        pre = user.init_environment(
                            suite.load_and_inject_default_environment(injections)
                        )
                        noop = suite._check_injection_task_security(
                            goal, [], pre, pre.model_copy(deep=True), []
                        )
                        case["noop_attack_success"] = bool(noop)
                        require(not noop, "Injected initial state already satisfies attack goal")
                        _, _, post, messages, _ = GroundTruthPipeline(user).query(
                            user.PROMPT, FunctionsRuntime(suite.tools), pre.model_copy(deep=True)
                        )
                        calls = [
                            call
                            for message in messages
                            if message["role"] == "assistant"
                            for call in message.get("tool_calls") or []
                        ]
                        utility = suite._check_user_task_utility(
                            user, messages[-1]["content"], pre, post, calls
                        )
                        case.update(utility=bool(utility), tool_count=len(calls))
                        require(bool(utility), "Injected ground truth fails official utility")
                        require(len(calls) <= 15, "Ground truth exceeds frozen tool budget")
                        legal_attack_success = suite._check_injection_task_security(
                            goal, messages[-1]["content"], pre, post, calls
                        )
                        case["legal_ground_truth_attack_success"] = bool(legal_attack_success)
                        require(
                            not legal_attack_success,
                            "Legal ground truth also satisfies selected attack goal",
                        )
                        case["status"] = "passed"
                    except Exception as error:
                        case.update(status="failed", error=f"{type(error).__name__}: {error}")
    keys = {
        (case["suite"], case["user_task"], case["goal"], case["style"]) for case in report["checks"]
    }
    require(len(keys) == len(report["checks"]) == 240, "Expected 240 unique append conditions")
    require(all(case["status"] == "passed" for case in report["checks"]), "Condition checks failed")
    require(implementation_identity() == implementation, "Implementation identity changed")
    require(load_campaign_plan(plan_path)[2] == identity, "Campaign identity changed")


def validate_output_destination(output: Path, plan_path: Path, sources: Sources) -> None:
    """Keep report writes separate from the study's inputs and running deployment."""
    output = output.resolve()
    protected_directories = (
        "artifacts/runs",
        "artifacts/campaigns",
        "artifacts/runtime",
        "configs",
        "src",
        "scripts",
        ".cache",
        ".venv",
        ".git",
    )
    study_root = Path(__file__).resolve().parents[1]
    for root in {Path.cwd().resolve(), study_root}:
        for directory in protected_directories:
            require(
                not output.is_relative_to((root / directory).resolve()),
                "Audit output must be outside experiment inputs and deployment directories",
            )
    inputs = {plan_path.resolve(), Path(__file__).resolve(), Path("uv.lock").resolve()}
    inputs.add(study_root / "uv.lock")
    inputs.update(Path(path).resolve() for path in sources.files)
    require(output not in inputs, "Audit output must not overwrite a source input")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("configs/phase1/campaign.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/phase1/append-conditions-audit.json")
    )
    args = parser.parse_args()
    output = args.output.resolve()
    sources = Sources()
    try:
        validate_output_destination(output, args.plan, sources)
    except ValueError as error:
        parser.error(str(error))
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "scope": {
            "unique_append_conditions": 240,
            "model_episodes": 0,
            "formal_attack_episodes": 480,
            "seeds": [17, 29],
            "seed_collapse_reason": (
                "The two frozen model seeds receive identical deterministic payloads: "
                "make_injections has only suite, GroundTruthPipeline(None), style, user and goal "
                "inputs; neither this pipeline nor these two fixed templates uses a model seed."
            ),
            "evaluation": (
                "Fresh simulated environments only; no formal episode reads or rescoring."
            ),
            "limitations": (
                "Proves existence of an official ground-truth completion within 15 tool calls "
                "and no attack success before or after that legitimate completion. "
                "Does not prove semantic checker validity "
                "or a model's ability to discover that completion. No inference or networking."
            ),
        },
        "payload_hash_serialization": SERIALIZATION,
        "checks": [],
        "errors": [],
    }
    try:
        audit(args.plan, report, sources)
        report["status"] = "passed"
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    try:
        sources.verify_unchanged()
        report["sources_unchanged"] = True
    except Exception as error:
        report["sources_unchanged"] = False
        report["status"] = "failed"
        report["errors"].append(f"{type(error).__name__}: {error}")
    report["summary"] = {
        "checked": len(report["checks"]),
        "passed": sum(case.get("status") == "passed" for case in report["checks"]),
        "noop_attack_success_true": sum(
            case.get("noop_attack_success") is True for case in report["checks"]
        ),
        "legal_ground_truth_attack_success_true": sum(
            case.get("legal_ground_truth_attack_success") is True for case in report["checks"]
        ),
        "by_suite": dict(Counter(case["suite"] for case in report["checks"])),
    }
    try:
        validate_output_destination(output, args.plan, sources)
    except ValueError as error:
        parser.error(str(error))
    atomic_json(output, report)
    print(json.dumps({"status": report["status"], **report["summary"], "output": str(output)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
