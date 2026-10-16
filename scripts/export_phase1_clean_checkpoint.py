#!/usr/bin/env python3
"""Export the frozen 120-clean checkpoint offline, only after collection and review.

Run from the repository root with its evaluation Python environment:
  .venv/bin/python scripts/export_phase1_clean_checkpoint.py --check-only
  .venv/bin/python scripts/export_phase1_clean_checkpoint.py

The default destination is ignored artifacts/phase1/clean120-checkpoint. Native
readiness applies to this clean subset, never to the 600-episode parent campaign.
No model, endpoint, service, or runtime-lock operation is performed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_tradeoffs import analysis
from agent_tradeoffs.campaign import load_campaign_plan
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import implementation_identity, plan_episodes

FROZEN_IDENTITY = "b61d56e553d420327c917fb8c3ad7793bc1cce293e12fae07b90c0eefe99a2c1"
SUITE_TASKS = {"banking": 8, "slack": 16, "workspace": 24, "travel": 12}
EPISODE_FIELDS = ("episode_id", "condition", "user_task_id", "injection_task_id")
ANNOTATION_FIELDS = ["run_id", "episode_id", "identity", *analysis.ANNOTATION_FIELDS]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def source_info(path: Path, content: bytes, *, tracked: bool = False) -> dict[str, Any]:
    info = {"path": str(path.resolve()), "sha256": digest(content), "bytes": len(content)}
    if tracked:
        info["git_revision"] = git("log", "-1", "--format=%H", "--", str(path)) or None
        info["working_tree_status"] = git("status", "--porcelain", "--", str(path))
    return info


def preflight(campaign_path: Path, reviews_dir: Path) -> dict[str, Any]:
    """Validate all inputs before creating a checkpoint or curated output."""
    parent_bytes = campaign_path.read_bytes()
    parent = json.loads(parent_bytes)
    plan, planned_runs, identity = load_campaign_plan(parent["plan_source"])
    require(identity == parent["identity"] == FROZEN_IDENTITY, "Frozen parent identity differs")
    require(parent["plan_snapshot"] == plan, "Parent plan snapshot differs from its source")
    require(plan["expected_episodes"] == 600, "Expected the frozen 600-episode parent")
    planned = [r for r in planned_runs if r["condition"] == "clean"]
    expected_cells = {(suite, seed) for suite in SUITE_TASKS for seed in (17, 29)}
    require(
        len(planned) == 8 and {(r["suite"], r["seed"]) for r in planned} == expected_cells,
        "Expected exactly eight clean suite/seed runs",
    )
    parents = {r["run_id"]: r for r in parent["runs"]}
    require(len(parents) == len(parent["runs"]), "Duplicate parent run IDs")
    implementation = implementation_identity()
    entries, records, sources, snapshots = [], [], [], {}

    def capture(path: Path, snapshot: str | None = None, tracked: bool = False) -> bytes:
        content = path.read_bytes()
        sources.append(source_info(path, content, tracked=tracked))
        if snapshot:
            snapshots[snapshot] = content
        return content

    capture(Path(parent["plan_source"]), "sources/campaign-plan.json", True)
    capture(Path(plan["task_manifest"]), "sources/task-manifest.json", True)
    for planned_run in planned:
        run_id = planned_run["run_id"]
        entry = parents[run_id]
        require(
            all(entry[k] == planned_run[k] for k in ("suite", "seed", "repeat_id")),
            f"Parent run metadata differs: {run_id}",
        )
        require(entry["run_dir"] == planned_run["run_dir"], f"Run directory differs: {run_id}")
        require(
            entry["status"] in {"finalized", "finalized_with_errors"},
            f"Clean run not finalized: {run_id} ({entry['status']}); no export written",
        )
        directory = Path(entry["run_dir"])
        manifest = json.loads(
            capture(directory / "manifest.json", f"sources/{run_id}.manifest.json")
        )
        config = ExperimentConfig.model_validate(planned_run["config_snapshot"])
        require(
            ExperimentConfig.model_validate(manifest["config"]).identity_config()
            == config.identity_config(),
            f"Run configuration differs: {run_id}",
        )
        episodes = plan_episodes(config)
        require(
            manifest["status"] == "finalized"
            and manifest["run_id"] == run_id
            and manifest["episodes"] == episodes
            and len(episodes) == SUITE_TASKS[planned_run["suite"]],
            f"Run plan/count/state differs: {run_id}",
        )
        runtime = manifest["runtime"]["semantic_identity"]
        expected_identity = stable_hash(
            {
                "config": config.identity_config(),
                "episodes": episodes,
                "implementation": implementation,
                "runtime": runtime,
            }
        )
        require(
            manifest["implementation"] == implementation
            and runtime == parent["runtime"]["semantic_identity"]
            and manifest["identity"] == expected_identity,
            f"Run code/deployment/identity differs: {run_id}",
        )
        capture(Path(planned_run["config_path"]), tracked=True)
        require(
            {p.parent.name for p in (directory / "episodes").glob("*/result.json")}
            == {e["episode_id"] for e in episodes},
            f"Missing or extra result files: {run_id}",
        )
        for episode in episodes:
            episode_dir = directory / "episodes" / episode["episode_id"]
            result = json.loads(capture(episode_dir / "result.json"))
            require(
                result["identity"] == manifest["identity"]
                and all(result.get(k) == episode[k] for k in EPISODE_FIELDS)
                and result["attack_success"] is None
                and result["status"] in {"completed", "error"}
                and (
                    isinstance(result["utility"], bool)
                    if result["status"] == "completed"
                    else result["utility"] is None
                ),
                f"Result identity/score/plan differs: {episode_dir}",
            )
            compression_path = episode_dir / "compression.json"
            compression = (
                json.loads(capture(compression_path)) if compression_path.is_file() else None
            )
            for artifact in ("trace.json", "requests.jsonl"):
                artifact_path = analysis._artifact_path(episode_dir / artifact)
                require(artifact_path.is_file(), f"Missing raw evidence: {artifact_path}")
                content = capture(artifact_path)
                if artifact_path.suffix == ".gz":
                    require(compression is not None, f"Missing compression record: {artifact_path}")
                    matches = [
                        a for a in compression["artifacts"] if a["path"] == artifact_path.name
                    ]
                    require(len(matches) == 1, f"Ambiguous compression record: {artifact_path}")
                    recorded = matches[0]
                    raw = gzip.decompress(content)
                    require(
                        compression["lossless"]
                        and len(content) == recorded["compressed_bytes"]
                        and digest(content) == recorded["compressed_sha256"]
                        and len(raw) == recorded["original_bytes"]
                        and digest(raw) == recorded["original_sha256"],
                        f"Compression digest/length differs: {artifact_path}",
                    )
                else:
                    raw = content
                if artifact == "trace.json":
                    require(
                        json.loads(raw)["identity"] == result["identity"],
                        f"Trace identity differs: {artifact_path}",
                    )
            records.append(
                {
                    **result,
                    "run_id": run_id,
                    "suite": planned_run["suite"],
                    "seed": config.model.seed,
                }
            )
        entries.append(
            {k: entry[k] for k in ("run_id", "run_dir", "suite", "seed", "repeat_id", "status")}
        )
    pairs = Counter((r["suite"], r["user_task_id"]) for r in records)
    require(len(records) == 120 and len(pairs) == 60, "Expected 120 results /60 unique tasks")
    require(set(pairs.values()) == {2}, "Each task must have exactly two results")
    require(
        all(
            {r["seed"] for r in records if (r["suite"], r["user_task_id"]) == key} == {17, 29}
            for key in pairs
        ),
        "Task pairing does not cover both frozen seeds",
    )
    annotations = []
    for suite in SUITE_TASKS:
        path = reviews_dir / f"review-{suite}.csv"
        content = capture(path, f"sources/review-{suite}.csv", True)
        annotations.extend(
            row
            for row in csv.DictReader(io.StringIO(content.decode()))
            if row["run_id"] in {e["run_id"] for e in entries if e["suite"] == suite}
        )
    annotations.sort(key=lambda row: (row["run_id"], row["episode_id"]))
    require(
        len(annotations) == 120 and all(r["review_status"] == "reviewed" for r in annotations),
        "All 120 canonical clean annotations must be reviewed before export",
    )
    annotation_bytes = csv_bytes(annotations, ANNOTATION_FIELDS)
    with tempfile.TemporaryDirectory(prefix="phase1-review-preflight-") as temporary:
        path = Path(temporary) / "annotations.csv"
        path.write_bytes(annotation_bytes)
        analysis.merge_annotations(records, path)  # Native duplicate/identity/semantic validation.
    for name, path in (
        ("analysis-source.py", Path(analysis.__file__)),
        ("exporter-source.py", Path(__file__)),
    ):
        capture(path, name, True)
    fingerprint = stable_hash(
        {"parent_identity": identity, "sources": [(s["path"], s["sha256"]) for s in sources]}
    )
    return {
        "parent": parent,
        "parent_path": str(campaign_path),
        "parent_bytes": parent_bytes,
        "entries": entries,
        "sources": sources,
        "snapshots": snapshots,
        "annotations": annotation_bytes,
        "identity": fingerprint,
    }


def immutable_write(path: Path, content: bytes) -> None:
    if path.exists():
        require(path.read_bytes() == content, f"Existing snapshot content differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def pooled_metrics(request_rows: list[dict[str, str]]) -> dict[str, Any]:
    """Reuse the native measurement function, with its exact token/time masks."""
    events = []
    for row in request_rows:
        event = {"event": row["event"]}
        if row["latency_seconds"]:
            event["latency_seconds"] = float(row["latency_seconds"])
        if row["event"] == "response":
            event["response"] = {
                "usage": {
                    k: int(row[k])
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if row[k]
                },
                "timings": {
                    k: float(row[k])
                    for k in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n")
                    if row[k]
                },
            }
        events.append(event)
    with tempfile.TemporaryDirectory(prefix="phase1-pooled-metrics-") as temporary:
        path = Path(temporary) / "requests.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        measurements, _ = analysis._request_measurements(path)
    return {
        k: v
        for k, v in measurements.items()
        if not k.startswith("_") and k != "request_log_available"
    }


def export(inputs: dict[str, Any], output: Path, results: Path) -> dict[str, Any]:
    identity = inputs["identity"]
    owner = output / "manifest.json"
    curated_path = results / "clean-baseline-measurements.json"
    if curated_path.exists():
        require(
            json.loads(curated_path.read_text())["scope"]["checkpoint_identity"] == identity,
            f"Curated output belongs to another snapshot: {curated_path}",
        )
    if owner.exists():
        cohort = json.loads(owner.read_text())
        require(cohort["identity"] == identity, f"Checkpoint identity differs: {owner}")
    else:
        require(
            not output.exists() or not any(output.iterdir()), f"Unowned partial output: {output}"
        )
        cohort = {
            "schema_version": 1,
            "campaign_id": "phase1-f16-v1-clean120-checkpoint",
            "identity": identity,
            "parent_campaign_identity": FROZEN_IDENTITY,
            "parent_manifest_source": inputs["parent_path"],
            "parent_manifest_sha256": digest(inputs["parent_bytes"]),
            "planned_episodes": 120,
            "planned_unique_tasks": 60,
            "runs": inputs["entries"],
        }
        immutable_write(output / "source-campaign-manifest.json", inputs["parent_bytes"])
        immutable_write(owner, json_bytes(cohort))
    parent_snapshot = (output / "source-campaign-manifest.json").read_bytes()
    require(
        digest(parent_snapshot) == cohort["parent_manifest_sha256"], "Parent snapshot hash differs"
    )
    for name, content in inputs["snapshots"].items():
        immutable_write(output / name, content)
    immutable_write(output / "annotations.csv", inputs["annotations"])
    provenance_path = output / "source-provenance.json"
    if not provenance_path.exists():
        immutable_write(
            provenance_path,
            json_bytes(
                {
                    "checkpoint_identity": identity,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "git_head": git("rev-parse", "HEAD"),
                    "sources": inputs["sources"],
                    "parent_manifest_source": cohort["parent_manifest_source"],
                    "parent_manifest_sha256": digest(parent_snapshot),
                }
            ),
        )
    provenance = json.loads(provenance_path.read_text())
    require(provenance["checkpoint_identity"] == identity, "Provenance identity differs")
    native = analysis.analyze_campaign(owner, output / "analysis", output / "annotations.csv", True)
    require(
        native["finalized_episodes"]
        == native["overall"]["review"]["reviewed_clean_episodes"]
        == 120
        and native["overall"]["unique_tasks"] == 60
        and native["measurement_provenance"]["analysis_sha256"]
        == digest(inputs["snapshots"]["analysis-source.py"])
        and native["preliminary_evaluation_ready"],
        "Native clean checkpoint coverage/readiness failed",
    )
    _, records, _ = analysis.collect_records(owner)
    analysis.merge_annotations(records, output / "annotations.csv")
    with (output / "analysis/requests.csv").open(newline="") as handle:
        requests = list(csv.DictReader(handle))
    repeat_ids = ["seed-17", "seed-29"]
    by_suite_repeat = {
        suite: {
            repeat: analysis._summarize(
                [r for r in records if r["suite"] == suite and r["repeat_id"] == repeat]
            )
            for repeat in repeat_ids
        }
        for suite in SUITE_TASKS
    }
    run_repeats = {r["run_id"]: r["repeat_id"] for r in inputs["entries"]}
    pooled = {
        "source": "Native requests.csv rows passed through unchanged _request_measurements",
        "scope": "Only returned responses supply tokens/native timing; failed compute is unknown. "
        "Native masks pair numerator/time; rates are not means of episode rates. "
        "request_latency_seconds includes observed failed-request latency.",
        "overall": pooled_metrics(requests),
        "by_suite": {
            suite: pooled_metrics([r for r in requests if r["suite"] == suite])
            for suite in SUITE_TASKS
        },
        "by_repeat": {
            repeat: pooled_metrics([r for r in requests if run_repeats[r["run_id"]] == repeat])
            for repeat in repeat_ids
        },
        "by_suite_repeat": {
            suite: {
                repeat: pooled_metrics(
                    [
                        r
                        for r in requests
                        if r["suite"] == suite and run_repeats[r["run_id"]] == repeat
                    ]
                )
                for repeat in repeat_ids
            }
            for suite in SUITE_TASKS
        },
    }
    with (output / "analysis/episodes.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields, episodes = list(reader.fieldnames or []), list(reader)
    run_seeds = {r["run_id"]: r["seed"] for r in inputs["entries"]}
    for row in episodes:
        row["seed"] = run_seeds[row["run_id"]]
    episode_bytes = csv_bytes(episodes, [*fields, "seed"])
    csv_path = results / "clean-baseline-episodes.csv"
    require(
        not csv_path.exists() or csv_path.read_bytes() == episode_bytes, f"CSV differs: {csv_path}"
    )
    for source in inputs["sources"]:
        require(
            digest(Path(source["path"]).read_bytes()) == source["sha256"],
            "Input changed during export",
        )
    curated = {
        "schema_version": 1,
        "scope": {
            "checkpoint_identity": identity,
            "parent_campaign_id": "phase1-f16-v1",
            "parent_campaign_identity": FROZEN_IDENTITY,
            "condition": "clean",
            "seeds": [17, 29],
            "finalized_episodes": 120,
            "unique_tasks": 60,
            "planned_parent_episodes": 600,
            "excluded_parent_attack_episodes": 480,
            "full_phase1_complete": False,
            "attack_success_rate": None,
            "readiness_note": "Native readiness covers only these 120 clean episodes and review. "
            "The 600-episode parent evaluation is not complete by this checkpoint; attacks and "
            "their required audits are excluded. No precision/context causal effect is estimated.",
        },
        "source_provenance": provenance,
        "source_files": [
            source_info(path, path.read_bytes())
            for path in (
                owner,
                output / "source-campaign-manifest.json",
                output / "annotations.csv",
                provenance_path,
                output / "analysis/summary.json",
                output / "analysis/episodes.csv",
                output / "analysis/requests.csv",
            )
        ],
        "native_summary": native,
        "by_suite_repeat": by_suite_repeat,
        "pooled_request_metrics": pooled,
        "unknown_episodes": [
            {
                k: row.get(k)
                for k in (
                    "run_id",
                    "episode_id",
                    "identity",
                    "suite",
                    "repeat_id",
                    "status",
                    "utility",
                    "error_type",
                    "duration_seconds",
                    "requests",
                    "response_count",
                    "request_error_count",
                )
            }
            for row in records
            if row["status"] != "completed" or not isinstance(row.get("utility"), bool)
        ],
        "curated_episodes": source_info(csv_path, episode_bytes),
        "limitations": [
            "60 fixed task clusters, two stochastic seeds; episodes are not 120 independent tasks.",
            "Official programmatic scores are retained despite documented checker limitations.",
            "Agent trace review is not independent human annotation or a validated LLM judge.",
            "Unknown timeout scores/failed-request tokens remain unknown, never zero compute.",
            "Fixed run order and shared host load/thermal drift confound performance comparisons.",
            "RSS measures the server process, not total GPU/unified-device memory.",
        ],
    }
    results.mkdir(parents=True, exist_ok=True)
    immutable_write(csv_path, episode_bytes)
    atomic_json(curated_path, curated)
    return {"measurements": str(curated_path), "episodes": str(csv_path), "checkpoint": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign", type=Path, default=Path("artifacts/campaigns/phase1-f16-v1/manifest.json")
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("artifacts/phase1/clean120-checkpoint")
    )
    parser.add_argument("--results", type=Path, default=Path("results/phase1"))
    parser.add_argument(
        "--check-only", action="store_true", help="Validate; write no checkpoint or curated output"
    )
    args = parser.parse_args()
    try:
        inputs = preflight(args.campaign.resolve(), args.results.resolve())
        result = (
            {"ready": True, "episodes": 120, "tasks": 60, "checkpoint_identity": inputs["identity"]}
            if args.check_only
            else export(inputs, args.checkpoint.resolve(), args.results.resolve())
        )
    except (ValueError, KeyError, OSError, EOFError) as error:
        parser.exit(1, f"Clean checkpoint export refused: {error}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
