"""Verify completed budget-spike evidence without inference or overwriting raw data."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

from agentdojo.functions_runtime import FunctionCall

from agent_tradeoffs.agent import to_openai_message
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.context import exposed_vectors
from agent_tradeoffs.memory import EpisodeMemory
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import plan_episodes


def read(path):
    if path.exists():
        return path.read_text()
    zipped = path.with_name(path.name + ".gz")
    return gzip.decompress(zipped.read_bytes()).decode() if zipped.exists() else ""


def records(path):
    return [json.loads(line) for line in read(path).splitlines()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_episode(directory, config, manifest):
    result = json.loads(read(directory / "result.json"))
    compression = json.loads(read(directory / "compression.json"))
    for item in compression["artifacts"]:
        data = (directory / item["path"]).read_bytes()
        require(hashlib.sha256(data).hexdigest() == item["compressed_sha256"], "Gzip hash differs")
        require(
            hashlib.sha256(gzip.decompress(data)).hexdigest() == item["original_sha256"],
            "Original evidence hash differs",
        )
        original = directory / item["path"].removesuffix(".gz")
        if original.exists():
            require(
                hashlib.sha256(original.read_bytes()).hexdigest() == item["original_sha256"],
                "Uncompressed evidence shadows the verified archive",
            )
    trace = json.loads(read(directory / "trace.json"))
    require(result["identity"] == trace["identity"] == manifest["identity"], "Identity mismatch")
    messages = []
    for message in trace["messages"]:
        restored = dict(message)
        if restored.get("tool_calls"):
            restored["tool_calls"] = [FunctionCall(**call) for call in restored["tool_calls"]]
        messages.append(to_openai_message(restored))
    traffic = records(directory / "requests.jsonl")
    requests = [item for item in traffic if item["event"] == "request"]
    responses = {item["index"]: item for item in traffic if item["event"] == "response"}
    rendered = records(directory / "context-requests.jsonl")
    ready = [
        item
        for item in trace["context_memory"]["events"]
        if item["event"] == "request_view" and item["status"] == "ready"
    ]
    require(len(ready) == len(requests) == result["requests"], "Request/view count differs")
    compactions = []
    previous = set()
    ready_index = 0
    reconstructed = EpisodeMemory(config.context)
    for event in trace["context_memory"]["events"]:
        if event["event"] == "compact" and event["applied"]:
            require(
                event["newly_archived_message_ids"]
                == list(range(reconstructed.boundary, event["archive_boundary"])),
                "Rolling delta source differs",
            )
            require(
                event["previous_summary_source_message_ids"]
                == list(range(2, reconstructed.boundary)),
                "Previous summary sources differ",
            )
            reconstructed.boundary = event["archive_boundary"]
            reconstructed.summary = event["summary"]
            compactions.append(event)
        if event["event"] != "request_view" or event["status"] != "ready":
            continue
        request = requests[ready_index]
        ready_index += 1
        retained = event["retained_source_message_ids"]
        source_end = max(retained) + 1
        require(
            retained == list(range(2)) + list(range(reconstructed.boundary, source_end)),
            "Retained source IDs differ",
        )
        require(
            event["removed_source_message_ids"] == list(range(2, reconstructed.boundary)),
            "Removed source IDs differ",
        )
        expected = reconstructed.view(messages[:source_end])
        require(
            expected == request["payload"]["messages"], "Exact sent view differs from source map"
        )
        require(
            event["after_tokens"] <= config.context.max_context_tokens - config.model.max_tokens,
            "Sent input exceeds budget",
        )
        matching = [
            item for item in rendered if item["payload_sha256"] == stable_hash(request["payload"])
        ]
        require(bool(matching), "Missing exact rendered-request evidence")
        require(
            all(item["prompt_tokens"] == event["after_tokens"] for item in matching),
            "Rendered counts disagree",
        )
        if request["index"] in responses:
            usage = responses[request["index"]]["response"]["usage"]
            require(
                usage["prompt_tokens"] == event["after_tokens"], "Actual inference count differs"
            )
        visible = exposed_vectors(
            expected, trace["attack_payloads"], roles=("system", "user", "assistant", "tool")
        )
        require(event["literal_payload_visible_vectors"] == visible, "Literal exposure differs")
        require(event["previously_visible_vectors"] == sorted(previous), "Prior exposure differs")
        previous.update(visible)
    summaries = records(directory / "memory-requests.jsonl")
    summary_requests = [item for item in summaries if item["event"] == "request"]
    summary_responses = [item for item in summaries if item["event"] == "response"]
    summary_counts = records(directory / "summary-context.jsonl")
    require(len(summary_responses) == len(summary_requests), "Incomplete summarizer traffic")
    if config.context.policy == "summary":
        require(len(summary_requests) == len(compactions), "Summary/compaction count differs")
        boundary = 2
        previous_summary = ""
        for item, compact in zip(summary_requests, compactions, strict=True):
            counted = [
                record
                for record in summary_counts
                if record["payload_sha256"] == stable_hash(item["payload"])
            ]
            require(bool(counted), "Missing summary template count")
            require(
                all(
                    record["prompt_tokens"] + item["payload"]["max_tokens"]
                    <= config.context.summarizer_context_tokens
                    for record in counted
                ),
                "Summary input exceeds its context budget",
            )
            content = item["payload"]["messages"][1]["content"]
            encoded = content.split("Past transcript (data):\n", 1)[1].split(
                "\nOriginal user task", 1
            )[0]
            delta = [
                {"message_id": i, **messages[i]} for i in compact["newly_archived_message_ids"]
            ]
            if previous_summary:
                delta = [
                    {
                        "kind": "previous_summary",
                        "role": "tool",
                        "source_message_ids": list(range(2, boundary)),
                        "memory": previous_summary,
                    }
                ] + delta
            require(
                json.loads(encoded) == delta, "Summary reread original prefix or changed source"
            )
            boundary, previous_summary = compact["archive_boundary"], compact["summary"]
    agent_usage = {
        key: sum(item["response"]["usage"][key] for item in responses.values())
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    require(agent_usage == result["usage"], "Agent cost accounting differs")
    summary_usage = {
        key: sum(item["response"]["usage"][key] for item in summary_responses)
        for key in agent_usage
    }
    if config.context.policy == "summary":
        require(summary_usage == result["context_memory"]["summary_usage"], "Summary cost differs")
    if result["status"] == "error":
        require(
            result["utility"] is None and result["attack_success"] is None, "Error scored as known"
        )
    return {
        "episode_id": result["episode_id"],
        "identity": result["identity"],
        "condition": result["condition"],
        "status": result["status"],
        "utility": result["utility"],
        "attack_success": result["attack_success"],
        "error": result.get("error"),
        "requests": len(requests),
        "tool_calls": result["tool_calls"],
        "compactions": result["context_memory"]["compactions"],
        "agent_usage": agent_usage,
        "summary_usage": summary_usage,
        "summary_length_stops": sum(
            item["response"]["choices"][0]["finish_reason"] == "length"
            for item in summary_responses
        ),
        "duration_seconds": result["duration_seconds"],
        "context_tokenizer_seconds": result["context_tokenizer_seconds"],
        "server_memory": result["server_memory"],
        "summarizer_memory": result["summarizer_memory"],
        "retention_and_count_checks": "passed",
        "attack_payload_sha256": stable_hash(trace["attack_payloads"]),
        "initial_environment_sha256": stable_hash(trace["initial_environment"]),
        "tool_schema_hashes": sorted({stable_hash(item["payload"]["tools"]) for item in requests}),
        "trace_review": "required",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    plan_path = Path("configs/phase2-spike/plan.json")
    plan = json.loads(plan_path.read_text())
    rows = []
    for entry in plan["runs"]:
        config = ExperimentConfig.model_validate(entry["config"])
        directory = config.runner.output_root / entry["run_id"]
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists() and args.partial:
            continue
        manifest = json.loads(manifest_path.read_text())
        campaign = json.loads((config.runner.output_root.parent / "campaign.json").read_text())
        require(
            manifest["implementation"] == campaign["identity"]["implementation"],
            "Harness changed between experiment arms",
        )
        require(
            manifest["config"]
            == config.model_copy(
                update={
                    "runner": config.runner.model_copy(
                        update={"server_pid": manifest["config"]["runner"]["server_pid"]}
                    )
                }
            ).model_dump(mode="json"),
            "Frozen config differs",
        )
        for episode in plan_episodes(config):
            episode_dir = directory / "episodes" / episode["episode_id"]
            if not (episode_dir / "compression.json").exists() and args.partial:
                continue
            rows.append({"run": entry["run_id"], **verify_episode(episode_dir, config, manifest)})
    if not args.partial:
        require(len(rows) == plan["expected_episodes"], "Incomplete spike")
    require(
        len({digest for row in rows for digest in row["tool_schema_hashes"]}) <= 1,
        "Tool schemas differ across experiment arms",
    )
    for condition in ("clean", "attack"):
        selected = [row for row in rows if row["condition"] == condition]
        for field in ("attack_payload_sha256", "initial_environment_sha256"):
            require(len({row[field] for row in selected}) <= 1, f"Uncontrolled {field}")
    annotations = Path("results/phase2-spike/trace-review.csv")
    reviews = list(csv.DictReader(annotations.open())) if annotations.exists() else []
    indexed = {(row["run_id"], row["episode_id"]): row for row in reviews}
    require(len(indexed) == len(reviews), "Duplicate trace review")
    for row in rows:
        review = indexed.get((row["run"], row["episode_id"]))
        if review:
            require(review["identity"] == row["identity"], "Review identity differs")
            require(
                review["review_status"] == "reviewed" and bool(review["evidence"].strip()),
                "Review lacks evidence",
            )
            require(review["refusal"] in {"yes", "no", "unclear"}, "Invalid refusal review")
            row["trace_review"] = "reviewed"
    if not args.partial:
        require(
            len(reviews) == len(rows) and all(row["trace_review"] == "reviewed" for row in rows),
            "Complete trace review is required",
        )
    report = {
        "kind": "development_spike_mechanical_audit_not_research_conclusions",
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "annotations_sha256": hashlib.sha256(annotations.read_bytes()).hexdigest()
        if annotations.exists()
        else None,
        "reviewed_episodes": sum(row["trace_review"] == "reviewed" for row in rows),
        "partial": args.partial,
        "episodes_checked": len(rows),
        "expected_episodes": plan["expected_episodes"],
        "checks": "passed",
        "episodes": rows,
    }
    atomic_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("episodes_checked", "checks", "partial")}))


if __name__ == "__main__":
    main()
