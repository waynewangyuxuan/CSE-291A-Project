"""Run a frozen, serial experiment campaign without mixing deployment identities."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_tradeoffs.artifact_io import compact_episode_artifacts
from agent_tradeoffs.config import load_config, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import (
    capture_runtime,
    implementation_identity,
    plan_episodes,
    run_experiment,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def compact_run_artifacts(directory: Path) -> dict[str, Any]:
    """Losslessly compact finalized evidence, checking hashes before removing originals."""
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["status"] != "finalized":
        raise ValueError("Only a finalized run can be compacted")
    records = []
    for episode in manifest["episodes"]:
        episode_dir = directory / "episodes" / episode["episode_id"]
        compressed = compact_episode_artifacts(episode_dir)
        for record in compressed["artifacts"]:
            records.append(
                {**record, "path": str(episode_dir.relative_to(directory) / record["path"])}
            )
    report = {"format": "gzip", "lossless": True, "artifacts": records}
    atomic_json(directory / "compression.json", report)
    return report


def load_campaign_plan(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Validate all workload configurations before starting any inference."""
    source = Path(path).resolve()
    plan = json.loads(source.read_text())
    if plan.get("schema_version") != 1 or not plan.get("runs"):
        raise ValueError("A schema_version=1 campaign with runs is required")
    run_ids: set[str] = set()
    unique_tasks = set()
    entries = []
    for entry in plan["runs"]:
        if entry["run_id"] in run_ids:
            raise ValueError(f"Duplicate campaign run ID: {entry['run_id']}")
        run_ids.add(entry["run_id"])
        config_path = Path(entry["config"]).resolve()
        config = load_config(config_path)
        episodes = plan_episodes(config)
        if entry["expected_episodes"] != len(episodes):
            raise ValueError(f"Episode count does not match {config_path}")
        if entry["suite"] != config.experiment.suite or entry["seed"] != config.model.seed:
            raise ValueError(f"Campaign metadata does not match {config_path}")
        unique_tasks.update((config.experiment.suite, task) for task in config.experiment.task_ids)
        entries.append(
            {
                **entry,
                "config_path": str(config_path),
                "config_snapshot": config.model_dump(mode="json"),
                "run_dir": str(config.runner.output_root.resolve() / entry["run_id"]),
                "planned_episodes": len(episodes),
                "status": "pending",
            }
        )
    if len(unique_tasks) != plan["unique_user_tasks"]:
        raise ValueError("Campaign unique task count does not match configurations")
    if sum(entry["planned_episodes"] for entry in entries) != plan["expected_episodes"]:
        raise ValueError("Campaign total episode count does not match configurations")
    task_manifest = Path(plan["task_manifest"])
    task_digest = _digest(task_manifest)
    identity = stable_hash(
        {
            "plan": plan,
            "configurations": [entry["config_snapshot"] for entry in entries],
            "task_manifest_sha256": task_digest,
            "implementation": implementation_identity(),
            "orchestrator_sha256": _digest(Path(__file__)),
        }
    )
    return plan, entries, identity


def campaign_status(path: str | Path) -> dict[str, Any]:
    directory = Path(path)
    manifest_path = directory / "manifest.json" if directory.is_dir() else directory
    manifest = json.loads(manifest_path.read_text())
    finalized = completed = errors = 0
    by_run = []
    for entry in manifest["runs"]:
        run_directory = Path(entry["run_dir"])
        run_manifest_path = run_directory / "manifest.json"
        results = []
        if run_manifest_path.exists():
            run_manifest = json.loads(run_manifest_path.read_text())
            for episode in run_manifest["episodes"]:
                result_file = run_directory / "episodes" / episode["episode_id"] / "result.json"
                if result_file.exists():
                    result = json.loads(result_file.read_text())
                    if result.get("identity") != run_manifest["identity"]:
                        raise ValueError(f"Result identity mismatch at {result_file}")
                    results.append(result)
        counts = {"finalized": len(results), "completed": 0, "errors": 0}
        for result in results:
            counts["completed" if result["status"] == "completed" else "errors"] += 1
        finalized += counts["finalized"]
        completed += counts["completed"]
        errors += counts["errors"]
        by_run.append({"run_id": entry["run_id"], "planned": entry["planned_episodes"], **counts})
    return {
        "campaign_id": manifest["campaign_id"],
        "status": manifest["status"],
        "planned_episodes": manifest["planned_episodes"],
        "finalized_episodes": finalized,
        "completed_episodes": completed,
        "error_episodes": errors,
        "planned_unique_tasks": manifest["planned_unique_tasks"],
        "active_run": manifest.get("active_run"),
        "runs": by_run,
    }


def run_campaign(
    path: str | Path,
    *,
    resume: bool = False,
    output_root: str | Path = "artifacts/campaigns",
) -> dict[str, Any]:
    plan, entries, identity = load_campaign_plan(path)
    runtime_snapshot, _ = capture_runtime(load_config(entries[0]["config_path"]))
    expected_deployment = runtime_snapshot["semantic_identity"]
    if plan.get("required_weight_format"):
        actual = runtime_snapshot["live_server_props"].get("model_ftype")
        if actual != plan["required_weight_format"]:
            raise ValueError(f"Campaign requires {plan['required_weight_format']}, got {actual}")
    name = plan["campaign_id"]
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("Campaign ID must be a single directory name")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / name
    manifest_path = directory / "manifest.json"
    with (root / ".campaign.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another campaign currently owns the serial evaluator") from error
        if resume:
            manifest = json.loads(manifest_path.read_text())
            if manifest["identity"] != identity:
                raise ValueError(
                    "Campaign resume refused: workload, code, or dependency lock changed"
                )
            if manifest["runtime"]["semantic_identity"] != expected_deployment:
                raise ValueError("Campaign resume refused: baseline deployment changed")
        else:
            directory.mkdir()
            manifest = {
                "schema_version": 1,
                "campaign_id": name,
                "identity": identity,
                "plan_source": str(Path(path).resolve()),
                "plan_snapshot": plan,
                "planned_episodes": plan["expected_episodes"],
                "planned_unique_tasks": plan["unique_user_tasks"],
                "created_at": _now(),
                "runtime": runtime_snapshot,
                "runs": entries,
            }
        manifest["status"] = "running"
        manifest["updated_at"] = _now()
        atomic_json(manifest_path, manifest)
        try:
            for entry in manifest["runs"]:
                run_directory = Path(entry["run_dir"])
                config = load_config(entry["config_path"])
                if config.model_dump(mode="json") != entry["config_snapshot"]:
                    raise ValueError(
                        f"Configuration changed during campaign: {entry['config_path']}"
                    )
                free = shutil.disk_usage(directory).free
                if free < 256 * 1024**2:
                    raise RuntimeError(
                        "Less than 256 MiB disk reserve remains; campaign interrupted"
                    )
                manifest["active_run"] = entry["run_id"]
                entry["status"] = "running"
                entry.setdefault("started_at", _now())
                atomic_json(manifest_path, manifest)
                print(
                    f"Starting {entry['run_id']} ({entry['planned_episodes']} episodes)", flush=True
                )
                summary = run_experiment(
                    config,
                    run_id=entry["run_id"],
                    resume=(run_directory / "manifest.json").exists(),
                    expected_runtime_identity=expected_deployment,
                )
                entry["summary"] = summary
                entry["status"] = (
                    "finalized_with_errors" if summary["error_episodes"] else "finalized"
                )
                entry["finalized_at"] = _now()
                entry["compression"] = compact_run_artifacts(run_directory)
                manifest["updated_at"] = _now()
                atomic_json(manifest_path, manifest)
                print(
                    f"Finished {entry['run_id']}: {summary['completed_episodes']} completed, "
                    f"{summary['error_episodes']} errors",
                    flush=True,
                )
            manifest["status"] = (
                "finalized_with_errors"
                if any(entry["summary"]["error_episodes"] for entry in manifest["runs"])
                else "finalized"
            )
            manifest["active_run"] = None
        except BaseException:
            manifest["status"] = "interrupted"
            raise
        finally:
            manifest["updated_at"] = _now()
            atomic_json(manifest_path, manifest)
    return campaign_status(directory)
