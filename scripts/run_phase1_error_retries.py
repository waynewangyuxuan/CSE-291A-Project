"""Run exactly two additional attempts of each original Phase 1 runtime error.

This supplemental diagnostic never replaces an original result. Its deployment
gate uses the original campaign identity, not merely the first retry deployment.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from agent_tradeoffs.campaign import compact_run_artifacts
from agent_tradeoffs.config import load_config, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    capture_runtime,
    implementation_identity,
    plan_episodes,
    run_experiment,
)
from agent_tradeoffs.runtime import load_runtime_config, probe, start_server

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/phase1-error-retries/plan.json"
OUTPUT = ROOT / "artifacts/phase1-error-retries"
PARENT = ROOT / "artifacts/campaigns/phase1-f16-v1/manifest.json"
EPISODES = ROOT / "results/phase1/evaluation-episodes.csv"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def now() -> str:
    return datetime.now(UTC).isoformat()


def check_attempt_mapping(selected: set[tuple[str, str]], runs: list[dict]) -> None:
    """Reject extras, omissions, duplicate attempts and invented retry rounds."""
    actual = Counter(
        (entry["retry_round"], entry["original_run_id"], episode_id)
        for entry in runs
        for episode_id in entry["episode_ids"]
    )
    expected = {(retry_round, *key) for retry_round in (1, 2) for key in selected}
    if set(actual) != expected or any(count != 1 for count in actual.values()):
        raise ValueError("Each selected case must occur exactly once in each of rounds 1 and 2")


def inventory() -> list[dict]:
    """Protect all original cohort evidence, public results and frozen inputs."""
    parent = json.loads(PARENT.read_text())
    paths = set()
    directories = [
        ROOT / "artifacts/campaigns/phase1-f16-v1",
        ROOT / "artifacts/phase1/final-evaluation",
        ROOT / "results/phase1",
        ROOT / "configs/phase1",
        ROOT / "src/agent_tradeoffs",
        ROOT / "artifacts/runtime",
    ]
    directories.extend(Path(entry["run_dir"]) for entry in parent["runs"])
    for directory in directories:
        paths.update(
            path
            for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".lock"
        )
    paths.update([ROOT / "uv.lock", ROOT / "configs/runtime-qwen3-8b-f16.toml"])
    return [
        {"path": relative(path), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(paths)
    ]


def verify_protected(plan: dict) -> None:
    source = ROOT / plan["protected_inventory"]["path"]
    if digest(source) != plan["protected_inventory"]["sha256"]:
        raise ValueError("Protected inventory changed")
    for item in json.loads(source.read_text())["files"]:
        path = ROOT / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item["bytes"]
            or digest(path) != item["sha256"]
        ):
            raise ValueError(f"Original evidence changed: {path}")


def validate_selection(plan: dict) -> None:
    parent = json.loads(PARENT.read_text())
    if (
        digest(PARENT) != plan["parent_manifest_sha256"]
        or parent["identity"] != plan["parent_identity"]
    ):
        raise ValueError("Original campaign changed")
    if implementation_identity() != plan["implementation"]:
        raise ValueError("Frozen evaluator or dependency lock changed")
    if digest(Path(__file__)) != plan["runner_sha256"]:
        raise ValueError("Supplemental runner changed after selection freeze")
    if digest(EPISODES) != plan["source_episodes_sha256"]:
        raise ValueError("Original exported episode table changed")
    selected = {(item["original_run_id"], item["episode_id"]): item for item in plan["selected"]}
    if len(selected) != 25 or len(plan["runs"]) != 30:
        raise ValueError("Expected 25 cases and 30 exact run shards")
    source_rows = list(csv.DictReader(EPISODES.open()))
    errors = {(row["run_id"], row["episode_id"]) for row in source_rows if row["status"] == "error"}
    if set(selected) != errors or len(source_rows) != 600:
        raise ValueError("Selection differs from the original 25 errors")
    for entry in plan["runs"]:
        path = ROOT / entry["config"]
        if digest(path) != entry["config_sha256"]:
            raise ValueError(f"Supplemental configuration changed: {path}")
        config = load_config(path)
        original = load_config(ROOT / entry["original_config"])
        expected = original.model_dump(mode="json")
        expected["experiment"]["task_ids"] = config.model_dump(mode="json")["experiment"][
            "task_ids"
        ]
        expected["experiment"]["injection_task_ids"] = config.model_dump(mode="json")["experiment"][
            "injection_task_ids"
        ]
        expected["runner"]["output_root"] = "artifacts/phase1-error-retries/runs"
        expected["runner"]["runtime_metadata_path"] = (
            "artifacts/phase1-error-retries/runtime/server.json"
        )
        if config.model_dump(mode="json") != expected:
            raise ValueError(
                "Supplemental config changes more than selected tasks and artifact paths"
            )
        episode_ids = [episode["episode_id"] for episode in plan_episodes(config)]
        if episode_ids != entry["episode_ids"] or len(episode_ids) != entry["expected_episodes"]:
            raise ValueError("Unexpected Cartesian task expansion")
        for episode_id in episode_ids:
            key = (entry["original_run_id"], episode_id)
            if key not in selected:
                raise ValueError("Supplemental run contains an unselected case")
    check_attempt_mapping(set(selected), plan["runs"])
    for item in selected.values():
        path = ROOT / item["result_path"]
        result = json.loads(path.read_text())
        if digest(path) != item["result_sha256"] or result["identity"] != item["identity"]:
            raise ValueError("Original selected result changed")
        if (
            result["status"] != "error"
            or result["utility"] is not None
            or result["attack_success"] is not None
        ):
            raise ValueError("Selected original was not an unknown error")


def prepare() -> dict:
    if PLAN.exists() or OUTPUT.exists():
        raise ValueError("Supplemental plan/output already exists; refuse to overwrite")
    parent = json.loads(PARENT.read_text())
    rows = [row for row in csv.DictReader(EPISODES.open()) if row["status"] == "error"]
    if len(rows) != 25 or parent["status"] != "finalized_with_errors":
        raise ValueError("Original cohort is not the expected finalized 25-error cohort")
    configs = PLAN.parent
    configs.mkdir(parents=True)
    OUTPUT.mkdir()
    records = inventory()
    protected = OUTPUT / "protected-original-inventory.json"
    atomic_json(protected, {"schema_version": 1, "captured_at": now(), "files": records})
    runtime_source = ROOT / "configs/runtime-qwen3-8b-f16.toml"
    runtime_target = configs / "runtime.toml"
    runtime_target.write_text(
        runtime_source.read_text().replace(
            'output_root = "artifacts/runtime"',
            'output_root = "artifacts/phase1-error-retries/runtime"',
        )
    )
    by_run = {entry["run_id"]: entry for entry in parent["runs"]}
    groups = defaultdict(list)
    selected = []
    for row in rows:
        entry = by_run[row["run_id"]]
        result_path = Path(entry["run_dir"]) / "episodes" / row["episode_id"] / "result.json"
        result = json.loads(result_path.read_text())
        if result["identity"] != row["identity"] or result["status"] != "error":
            raise ValueError("Exported error identity differs from raw result")
        selected.append(
            {
                "original_run_id": row["run_id"],
                "episode_id": row["episode_id"],
                "identity": result["identity"],
                "suite": row["suite"],
                "user_task_id": row["user_task_id"],
                "injection_task_id": row["injection_task_id"] or None,
                "condition": row["condition"],
                "seed": entry["seed"],
                "attack_variant": row["attack_variant"],
                "original_error_type": result["error"]["type"],
                "result_path": relative(result_path),
                "result_sha256": digest(result_path),
            }
        )
        groups[(row["run_id"], row["injection_task_id"])].append(row)
    runs = []
    for (original_run_id, goal), group in groups.items():
        entry = by_run[original_run_id]
        source = ROOT / entry["config"]
        original = load_config(source)
        task_ids = [
            task
            for task in original.experiment.task_ids
            if task in {row["user_task_id"] for row in group}
        ]
        stem = original_run_id.removeprefix("phase1-f16-") + (
            "-" + goal.replace("injection_task_", "g") if goal else ""
        )
        target = configs / (stem + ".toml")
        text = source.read_text()
        replacements = {
            "task_ids = ": json.dumps(task_ids),
            "injection_task_ids = ": json.dumps([goal] if goal else []),
            "output_root = ": '"artifacts/phase1-error-retries/runs"',
            "runtime_metadata_path = ": '"artifacts/phase1-error-retries/runtime/server.json"',
        }
        target.write_text(
            "\n".join(
                next(
                    (key + value for key, value in replacements.items() if line.startswith(key)),
                    line,
                )
                for line in text.splitlines()
            )
            + "\n"
        )
        episode_ids = [episode["episode_id"] for episode in plan_episodes(load_config(target))]
        if set(episode_ids) != {row["episode_id"] for row in group}:
            raise ValueError("Task sharding added an unselected episode")
        for retry_round in (1, 2):
            runs.append(
                {
                    "run_id": f"phase1-f16-retry-r{retry_round}-{stem}",
                    "retry_round": retry_round,
                    "original_run_id": original_run_id,
                    "original_config": entry["config"],
                    "config": relative(target),
                    "config_sha256": digest(target),
                    "expected_episodes": len(group),
                    "episode_ids": episode_ids,
                }
            )
    # Complete one full repeat of all 25 cases before the second repeat.
    runs.sort(key=lambda entry: entry["retry_round"])
    plan = {
        "schema_version": 1,
        "diagnostic_id": "phase1-f16-error-retries-v1",
        "created_at": now(),
        "scope": (
            "Two additional full episodes per original error, same original seed; "
            "outcome-selected recovery diagnostic, not independent new-seed replication."
        ),
        "expected_episodes": 50,
        "retry_rounds": [1, 2],
        "original_error_cases": 25,
        "parent_identity": parent["identity"],
        "parent_manifest_sha256": digest(PARENT),
        "source_episodes_sha256": digest(EPISODES),
        "implementation": implementation_identity(),
        "runner_sha256": digest(Path(__file__)),
        "runtime_config": relative(runtime_target),
        "runtime_config_sha256": digest(runtime_target),
        "original_runtime_semantic_identity_sha256": stable_hash(
            parent["runtime"]["semantic_identity"]
        ),
        "protected_inventory": {
            "path": relative(protected),
            "sha256": digest(protected),
            "files": len(records),
        },
        "selected": selected,
        "runs": runs,
    }
    atomic_json(PLAN, plan)
    validate_selection(plan)
    verify_protected(plan)
    return {
        "plan": relative(PLAN),
        "sha256": digest(PLAN),
        "selected": 25,
        "episodes": 50,
        "runs": 30,
    }


def execute(resume: bool) -> dict:
    plan = json.loads(PLAN.read_text())
    validate_selection(plan)
    verify_protected(plan)
    runtime_path = ROOT / plan["runtime_config"]
    if digest(runtime_path) != plan["runtime_config_sha256"]:
        raise ValueError("Supplemental runtime config changed")
    runtime_config = load_runtime_config(runtime_path)
    manifest_path = OUTPUT / "manifest.json"
    with (OUTPUT / ".diagnostic.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest_path.exists() != resume:
            raise ValueError("Use --resume only for an existing diagnostic manifest")
        if resume:
            manifest = json.loads(manifest_path.read_text())
            if manifest["plan_sha256"] != digest(PLAN):
                raise ValueError("Plan changed during resume")
        else:
            manifest = {
                "schema_version": 1,
                "created_at": now(),
                "plan_sha256": digest(PLAN),
                "runs": [],
            }
        manifest["status"] = "starting"
        atomic_json(manifest_path, manifest)
        try:
            if not resume:
                # Refuse occupied ports; never stop another process to acquire one.
                launch = start_server(runtime_config)
                atomic_json(OUTPUT / "runtime-launch.json", launch)
            expected = json.loads(PARENT.read_text())["runtime"]["semantic_identity"]
            live, _ = capture_runtime(load_config(ROOT / plan["runs"][0]["config"]))
            if live["semantic_identity"] != expected:
                raise ValueError("New deployment differs from original formal F16 deployment")
            # A resumed run requires a matching ready managed service. If startup failed,
            # restart through the CLI with this runtime config, then explicitly resume.
            probe_path = OUTPUT / ("runtime-probe-" + now().replace(":", "-") + ".json")
            atomic_json(probe_path, probe(runtime_config))
            manifest.setdefault("runtime_observations", []).append(live)
            manifest["runtime"] = live
            manifest["status"] = "running"
            atomic_json(manifest_path, manifest)
            print(
                "Original deployment identity matched; starting 50 same-seed diagnostic attempts.",
                flush=True,
            )
            finished = {entry["run_id"] for entry in manifest["runs"]}
            for entry in plan["runs"]:
                if entry["run_id"] in finished:
                    continue
                validate_selection(plan)
                manifest["active_run"] = entry["run_id"]
                manifest["updated_at"] = now()
                atomic_json(manifest_path, manifest)
                config = load_config(ROOT / entry["config"])
                run_dir = config.runner.output_root / entry["run_id"]
                print(
                    f"Starting {entry['run_id']}: {entry['expected_episodes']} episodes", flush=True
                )
                summary = run_experiment(
                    config,
                    run_id=entry["run_id"],
                    resume=(run_dir / "manifest.json").exists(),
                    expected_runtime_identity=expected,
                )
                compact_run_artifacts(run_dir)
                manifest["runs"].append({**entry, "summary": summary, "finalized_at": now()})
                atomic_json(manifest_path, manifest)
                print(
                    f"Finished {entry['run_id']}: {summary['completed_episodes']} completed, "
                    f"{summary['error_episodes']} errors",
                    flush=True,
                )
            verify_protected(plan)
            manifest["active_run"] = None
            manifest["status"] = "finalized"
        except BaseException:
            manifest["status"] = "interrupted"
            raise
        finally:
            manifest["updated_at"] = now()
            atomic_json(manifest_path, manifest)
    return {
        "status": manifest["status"],
        "runs": len(manifest["runs"]),
        "episodes": sum(entry["summary"]["finalized_episodes"] for entry in manifest["runs"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "check"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT:
        raise SystemExit("Run from the repository root")
    if args.action == "prepare":
        result = prepare()
    elif args.action == "check":
        frozen = json.loads(PLAN.read_text())
        validate_selection(frozen)
        verify_protected(frozen)
        result = {"plan_valid": True, "original_evidence_unchanged": True}
    else:
        result = execute(args.resume)
    print(json.dumps(result, indent=2))
