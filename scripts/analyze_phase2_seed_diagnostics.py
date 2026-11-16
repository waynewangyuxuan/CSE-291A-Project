"""Reproduce source-bound, case-level seed comparisons without inference or pooling."""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import audit_phase2_spike as auditor
from phase2_seed_diagnostics import Selection

from agent_tradeoffs.analysis import FAILURE_CATEGORIES
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.runner import plan_episodes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


@dataclass
class Evidence:
    directory: Path
    original: Path
    hashes: dict[str, str] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)

    def local(self, path):
        path = Path(path)
        relative = path.relative_to(self.original) if path.is_absolute() else path
        require(".." not in relative.parts, "Evidence path escapes repository")
        local = self.directory / relative
        require(
            local.resolve().is_relative_to(self.directory.resolve()), "Evidence symlink escapes"
        )
        return local

    def read(self, path):
        local = self.local(path)
        data = local.read_bytes()
        key = str(local.relative_to(self.directory))
        previous = self.hashes.setdefault(key, digest(data))
        require(previous == digest(data), "Evidence changed during export")
        require(self.pins.get(key, digest(data)) == digest(data), "Frozen parent evidence changed")
        return data

    def json(self, path):
        return json.loads(self.read(path))


METRICS = (
    "status",
    "utility",
    "attack_success",
    "error_type",
    "seconds",
    "requests",
    "tool_calls",
    "tool_errors",
    "compactions",
    "agent_tokens",
    "summary_tokens",
    "review_status",
    "refusal",
    "failure_category",
    "secondary_labels",
    "evidence",
)


def verify_sampling(traffic, model, output_budget):
    expected = {
        "model": model.model,
        "temperature": model.temperature,
        "seed": model.seed,
        "max_tokens": output_budget,
        "stream": False,
        **model.extra_body,
    }
    for record in traffic:
        if record["event"] == "request":
            captured = {
                k: v
                for k, v in record["payload"].items()
                if k not in {"messages", "tools", "tool_choice"}
            }
            require(captured == expected, "Captured request sampling differs from frozen config")


def compare(root, evidence_root):
    root = Path(root)
    require(
        not root.is_absolute()
        and root.parent == Path("artifacts")
        and re.fullmatch(r"phase2-seed-diagnostics-[a-z0-9][a-z0-9-]*", root.name),
        "Use a relative diagnostic artifact root",
    )
    plan_path = root / "configs/seed-29.json"
    plan = json.loads((evidence_root / plan_path).read_bytes())
    original = Path(plan["runs"][0]["config"]["runner"]["output_root"]).parents[3]
    source = Evidence(evidence_root, original)
    plan = source.json(plan_path)
    receipt = source.json(root / "receipt.json")
    require(
        Path(receipt["selection_source"]).is_relative_to(original),
        "This offline exporter requires a repository-local selection source",
    )
    source.pins = {
        str(source.local(path).relative_to(evidence_root)): sha
        for path, sha in receipt["source_hashes"].items()
    }
    selection_bytes = source.read(root / "selection.json")
    selection = Selection.model_validate_json(selection_bytes)
    require(
        selection_bytes == encoded(selection.model_dump(mode="json")), "Selection encoding changed"
    )
    require(
        digest(source.read(receipt["selection_source"])) == receipt["selection_sha256"],
        "Selection source changed",
    )
    require(
        json.loads(source.read(receipt["selection_source"])) == selection.model_dump(mode="json"),
        "Selection differs from supplied source",
    )
    parent_path = Path("results/phase2-main-v1/analysis/episodes.json")
    parent_rows = source.json(parent_path)
    require(
        len(parent_rows) == 1098
        and digest(source.read(parent_path)) == receipt["source_hashes"][str(parent_path)]
        and digest(source.read(parent_path.with_suffix(".csv"))) == selection.reviewed_table_sha256,
        "Reviewed parent tables changed",
    )
    campaign = source.json(root / "seed-29/campaign.json")
    require(campaign["status"] == "completed", "Diagnostic collection is incomplete")
    annotations_path = root / "review-draft.csv"
    reviews = list(csv.DictReader(io.StringIO(source.read(annotations_path).decode())))
    indexed = {(r["run_id"], r["episode_id"]): r for r in reviews}
    require(
        len(indexed) == len(reviews) == plan["expected_episodes"], "Incomplete/duplicate review"
    )
    source.read(root / "review-findings.md")
    gate = source.json(root / "seed-29/review-audit.json")
    require(
        gate["checks"] == "passed"
        and gate["partial"] is False
        and gate["episodes_checked"] == gate["reviewed_episodes"] == plan["expected_episodes"]
        and gate["plan_sha256"] == digest(source.read(plan_path))
        and gate["annotations_sha256"] == digest(source.read(annotations_path))
        and gate["auditor_sha256"]
        == digest(source.read("scripts/audit_phase2_spike.py"))
        == digest(Path(auditor.__file__).read_bytes()),
        "Full review gate is missing, stale or uses a different auditor",
    )
    expected_runs = {
        run: (h.id, task, side)
        for h in selection.hypotheses
        for task in h.tasks
        for side, run in zip(h.sides, task.source_runs, strict=True)
    }
    require(
        len(plan["runs"]) == len(expected_runs)
        and set(expected_runs) == {r["run_id"] for r in plan["runs"]},
        "Unselected/missing/duplicate run",
    )
    compared, reproduced = [], []
    for entry in plan["runs"]:
        hypothesis, task, side = expected_runs[entry["run_id"]]
        require(
            (entry["suite"], entry["user_task_id"], entry["precision"], entry["policy"])
            == (task.suite, task.task, side.precision, side.policy),
            "Selected side differs",
        )
        config = ExperimentConfig.model_validate(entry["config"])
        require(config.model.seed == 29, "Diagnostic seed differs")
        original_plan = source.json(f"configs/phase2-main-v1/{task.block}.json")
        original_runs = [r for r in original_plan["runs"] if r["run_id"] == entry["run_id"]]
        require(len(original_runs) == 1, "Original frozen run missing")
        original_entry = original_runs[0]
        expected_config = copy.deepcopy(original_entry["config"])
        require(expected_config["model"]["seed"] == 17, "Original seed differs")
        original_manifest_path = (
            Path(expected_config["runner"]["output_root"]) / entry["run_id"] / "manifest.json"
        )
        original_manifest = source.json(original_manifest_path)
        require(
            digest(source.read(original_manifest_path)) == entry["source_pair"]["manifest_sha256"]
            and original_manifest["identity"] == entry["source_pair"]["identity"],
            "Original manifest binding differs",
        )
        expected_config["model"]["seed"] = 29
        expected_config["runner"]["output_root"] = str(original / root / "seed-29/runs")
        expected_config["runner"]["runtime_metadata_path"] = str(
            original / root / "runtime" / side.precision / "server.json"
        )
        if side.policy == "summary":
            expected_config["context"]["runtime_metadata_path"] = str(
                original / root / "runtime/summarizer/server.json"
            )
        require(
            entry["config"] == expected_config, "Derived config changes more than seed and paths"
        )
        directory = Path(config.runner.output_root) / entry["run_id"]
        manifest = source.json(directory / "manifest.json")
        saved = dict(manifest["config"])
        saved["runner"] = dict(saved["runner"], server_pid=None)
        require(saved == entry["config"], "Manifest config differs")
        require(manifest["episodes"] == plan_episodes(config), "Planned episodes differ")
        require(
            manifest["implementation"]
            == campaign["identity"]["implementation"]
            == original_manifest["implementation"]
            and manifest["runtime"]["semantic_identity"] == entry["source_pair"]["runtime"],
            "Deployment identity differs",
        )
        summary_runtime = manifest["summarizer_runtime"]
        require(
            (summary_runtime["semantic_identity"] if summary_runtime else None)
            == entry["source_pair"]["summarizer_runtime"],
            "Summarizer deployment differs",
        )
        require(
            manifest["runtime"]["semantic_identity"]
            == original_manifest["runtime"]["semantic_identity"]
            and (summary_runtime["semantic_identity"] if summary_runtime else None)
            == (
                original_manifest["summarizer_runtime"]["semantic_identity"]
                if original_manifest["summarizer_runtime"]
                else None
            ),
            "Seed comparison runtime differs from original manifest",
        )
        expected_identity = stable_hash(
            {
                "config": config.identity_config(),
                "episodes": plan_episodes(config),
                "implementation": manifest["implementation"],
                "runtime": manifest["runtime"]["semantic_identity"],
                "summarizer_runtime": summary_runtime["semantic_identity"]
                if summary_runtime
                else None,
            }
        )
        require(manifest["identity"] == expected_identity, "Diagnostic run identity differs")
        original_gate = source.json(
            f"results/phase2-main-v1/reviews/{task.block}.verification.json"
        )
        for episode in manifest["episodes"]:
            path = directory / "episodes" / episode["episode_id"]
            result = source.json(path / "result.json")
            review = indexed.get((entry["run_id"], episode["episode_id"]))
            require(
                review is not None
                and review["identity"] == result["identity"]
                and review["review_status"] == "reviewed"
                and review["reviewer"].strip()
                and review["evidence"].strip()
                and review["refusal"] in {"yes", "no", "unclear"}
                and review["failure_category"] in FAILURE_CATEGORIES,
                "Invalid review binding",
            )
            require(
                f"source=runs/{entry['run_id']}/episodes/{episode['episode_id']}/"
                in review["evidence"],
                "Review source path differs",
            )
            for file in source.local(path).iterdir():
                if file.is_file():
                    source.read(file.relative_to(evidence_root))
            for name in (
                "trace.json.gz",
                "requests.jsonl.gz",
                "context-requests.jsonl.gz",
                "result.json",
                "compression.json",
                "memory-requests.jsonl.gz",
                "summary-context.jsonl.gz",
            ):
                optional = name in {"memory-requests.jsonl.gz", "summary-context.jsonl.gz"}
                if optional and not source.local(path / name).exists():
                    continue
                data = source.read(path / name)
                unpacked = gzip.decompress(data) if name.endswith(".gz") else None
                if optional and not unpacked.strip():
                    continue
                require(
                    f"{name} sha256={digest(data)}" in review["evidence"], "Raw review hash missing"
                )
                if unpacked is not None:
                    require(
                        f"{name} decompressed_sha256={digest(unpacked)}" in review["evidence"],
                        "Decompressed review hash missing",
                    )
            checked = auditor.verify_episode(source.local(path), config, manifest)
            traffic = [
                json.loads(line)
                for line in gzip.decompress(source.read(path / "requests.jsonl.gz")).splitlines()
            ]
            verify_sampling(traffic, config.model, config.model.max_tokens)
            if (
                side.policy == "summary"
                and source.local(path / "memory-requests.jsonl.gz").exists()
            ):
                traffic = [
                    json.loads(line)
                    for line in gzip.decompress(
                        source.read(path / "memory-requests.jsonl.gz")
                    ).splitlines()
                ]
                verify_sampling(
                    traffic, config.context.summarizer, config.context.summary_max_tokens
                )
            reproduced.append(
                {
                    "run": entry["run_id"],
                    "suite": entry["suite"],
                    "user_task_id": entry["user_task_id"],
                    **checked,
                    "trace_review": "reviewed",
                }
            )
            old_gate = [
                r
                for r in original_gate["episodes"]
                if (r["run"], r["episode_id"]) == (entry["run_id"], episode["episode_id"])
            ]
            require(len(old_gate) == 1, "Original audit pair missing")
            for key in (
                "attack_payload_sha256",
                "initial_environment_sha256",
                "tool_schema_hashes",
            ):
                require(checked[key] == old_gate[0][key], f"Seed comparison changes {key}")
            old = [
                r
                for r in parent_rows
                if (r["block"], r["run_id"], r["episode_id"])
                == (task.block, entry["run_id"], episode["episode_id"])
            ]
            require(
                len(old) == 1 and old[0]["review_status"] == "reviewed",
                "Original reviewed pair missing",
            )
            old = old[0]
            require(old["identity"] == entry["source_pair"]["identity"], "Parent identity differs")
            require(
                result["status"] != "error"
                or (result["utility"] is None and result["attack_success"] is None),
                "Error outcomes must remain unknown",
            )
            current = {
                **{
                    k: result[k]
                    for k in (
                        "status",
                        "utility",
                        "attack_success",
                        "requests",
                        "tool_calls",
                        "tool_errors",
                    )
                },
                "error_type": (result.get("error") or {}).get("type"),
                "seconds": result["duration_seconds"],
                "compactions": result["context_memory"]["compactions"],
                "agent_tokens": result["usage"]["total_tokens"],
                "summary_tokens": result["context_memory"]["summary_usage"].get("total_tokens", 0),
                **{
                    k: review[k]
                    for k in (
                        "review_status",
                        "refusal",
                        "failure_category",
                        "secondary_labels",
                        "evidence",
                    )
                },
            }
            compared.append(
                {
                    "hypothesis": hypothesis,
                    "suite": task.suite,
                    "task": task.task,
                    "precision": side.precision,
                    "policy": side.policy,
                    "condition": episode["condition"],
                    "episode_id": episode["episode_id"],
                    "seed17_source": old["source"],
                    "seed29_source": str(path),
                    "seed17": {k: old[k] for k in METRICS},
                    "seed29": current,
                }
            )
    require(reproduced == gate["episodes"], "Replayed episode audits differ from full gate")
    require(len(compared) == plan["expected_episodes"], "Comparison coverage differs")
    for path in Path("src/agent_tradeoffs").glob("*.py"):
        require(
            digest(path.read_bytes()) == digest(source.read(path)),
            "Analysis implementation differs",
        )
    for path in (
        "scripts/analyze_phase2_seed_diagnostics.py",
        "scripts/phase2_seed_diagnostics.py",
        "scripts/analyze_phase2_matrix.py",
        "scripts/phase2_collect.py",
        "scripts/phase2_matrix.py",
        "uv.lock",
        "pyproject.toml",
    ):
        source.read(path)
    require(
        digest(Path(__file__).read_bytes())
        == digest(source.read("scripts/analyze_phase2_seed_diagnostics.py")),
        "Comparison exporter differs",
    )
    for path, sha in list(source.hashes.items()):
        require(digest(source.read(path)) == sha, "Evidence changed during export")
    return compared, source.hashes


def export(root, evidence_root, output):
    rows, hashes = compare(root, evidence_root)
    stream = io.StringIO(newline="")
    flattened = [
        {
            **{k: v for k, v in r.items() if k not in {"seed17", "seed29"}},
            **{
                f"{seed}_{k}": value
                for seed in ("seed17", "seed29")
                for k, value in r[seed].items()
            },
        }
        for r in rows
    ]
    writer = csv.DictWriter(stream, fieldnames=list(flattened[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(flattened)
    payloads = {"comparison.json": encoded(rows), "comparison.csv": stream.getvalue().encode()}
    payloads["provenance.json"] = encoded(
        {
            "kind": "outcome_selected_seed29_case_comparisons_not_pooled",
            "episodes_per_seed": len(rows),
            "episode_audits_reproduced": len(rows),
            "interpretation": "Equal scores do not prove action recurrence; read the review.",
            "input_sha256": hashes,
            "output_sha256": {k: digest(v) for k, v in payloads.items()},
        }
    )
    require(
        all(
            not (output / k).exists() or (output / k).read_bytes() == v for k, v in payloads.items()
        ),
        "Existing diagnostic export differs",
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        if not (output / name).exists():
            (output / name).write_bytes(data)
    return {
        "episodes_per_seed": len(rows),
        "episode_audits_reproduced": len(rows),
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.root, args.evidence_root.resolve(), args.output)))
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"Diagnostic export refused: {error}\n")
