"""Audit and summarize the completed 50-attempt Phase 1 recovery diagnostic."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from run_phase1_error_retries import (
    OUTPUT,
    PARENT,
    PLAN,
    ROOT,
    digest,
    now,
    validate_selection,
    verify_protected,
)

from agent_tradeoffs.analysis import _costs, _request_measurements
from agent_tradeoffs.config import load_config, stable_hash
from agent_tradeoffs.reporting import atomic_json

PUBLIC = ROOT / "results/phase1-error-retries"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def read_trace(directory: Path) -> dict:
    with gzip.open(directory / "trace.json.gz", "rt") as handle:
        return json.load(handle)


def verify_episode(directory: Path, manifest: dict, expected: dict) -> tuple[dict, list[dict]]:
    """Check raw identities, lossless evidence and exact request accounting."""
    result = read_json(directory / "result.json")
    trace = read_trace(directory)
    if result["identity"] != manifest["identity"] or trace["identity"] != result["identity"]:
        raise ValueError(f"Result/trace identity mismatch at {directory}")
    for key in ("episode_id", "condition", "user_task_id", "injection_task_id"):
        if result[key] != expected[key] or trace[key] != expected[key]:
            raise ValueError(f"Episode mapping mismatch at {directory}")
    if result["status"] not in {"completed", "error"}:
        raise ValueError("Unfinalized result")
    if result["status"] == "error" and (
        result["utility"] is not None or result["attack_success"] is not None
    ):
        raise ValueError("Error must retain unknown scores")
    if result["status"] == "completed" and (
        type(result["utility"]) is not bool
        or (result["condition"] == "attack" and type(result["attack_success"]) is not bool)
    ):
        raise ValueError("Completed result has invalid score types")
    compression = read_json(directory / "compression.json")
    if {item["path"] for item in compression["artifacts"]} != {
        "trace.json.gz",
        "requests.jsonl.gz",
    }:
        raise ValueError("Missing compression evidence")
    for item in compression["artifacts"]:
        path = directory / item["path"]
        decoded = gzip.decompress(path.read_bytes())
        if (
            digest(path) != item["compressed_sha256"]
            or hashlib.sha256(decoded).hexdigest() != item["original_sha256"]
            or len(decoded) != item["original_bytes"]
            or path.stat().st_size != item["compressed_bytes"]
        ):
            raise ValueError(f"Lossless compression audit failed: {path}")
    measurements, events = _request_measurements(directory / "requests.jsonl")
    requests = [event for event in events if event["event"] == "request"]
    terminals = [event for event in events if event["event"] in {"response", "request_error"}]
    if len(requests) != result["requests"] or len(terminals) != len(requests):
        raise ValueError("Unaccounted request or terminal event")
    if [event["index"] for event in requests] != list(range(len(requests))):
        raise ValueError("Request indices are not contiguous")
    if Counter(event["index"] for event in terminals) != Counter(range(len(requests))):
        raise ValueError("Request has missing or duplicate terminal event")
    if len(events) != 2 * len(requests) or any(
        events[2 * index]["event"] != "request"
        or events[2 * index]["index"] != index
        or events[2 * index + 1]["event"] not in {"response", "request_error"}
        or events[2 * index + 1]["index"] != index
        for index in range(len(requests))
    ):
        raise ValueError("Request journal must alternate each request and its terminal event")
    config = manifest["config"]
    usage = Counter({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    for request in requests:
        payload = request["payload"]
        required = {
            "model": config["model"]["model"],
            "seed": config["model"]["seed"],
            "temperature": config["model"]["temperature"],
            "max_tokens": config["model"]["max_tokens"],
            "stream": False,
            **config["model"]["extra_body"],
        }
        if any(payload.get(key) != value for key, value in required.items()):
            raise ValueError("Actual request settings differ from frozen config")
        if not 0 < request["timeout_seconds"] <= config["runner"]["request_timeout_seconds"]:
            raise ValueError("Invalid actual request timeout")
    for event in terminals:
        if not math.isfinite(event["latency_seconds"]) or event["latency_seconds"] < 0:
            raise ValueError("Invalid request duration")
        if event["event"] == "response":
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = (event["response"].get("usage") or {}).get(key)
                if type(value) is not int or value < 0:
                    raise ValueError("Returned response usage is missing or invalid")
                usage[key] += value
    if set(result["usage"]) - set(usage) or any(
        result["usage"].get(key, 0) != value for key, value in usage.items()
    ):
        raise ValueError("Returned usage does not sum to saved episode usage")
    tools = [message for message in trace["messages"] if message["role"] == "tool"]
    if len(tools) != result["tool_calls"] or len(tools) > config["runner"]["max_tool_steps"]:
        raise ValueError("Executed tool accounting mismatch")
    record = {
        **result,
        **measurements,
        **{
            key: result["usage"].get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "peak_server_rss_bytes": result["server_memory"]["peak_rss_bytes"],
        "error_type": (result.get("error") or {}).get("type"),
        "exposed": bool(result["observed_injection_vectors"]),
        "_first_request": requests[0]["payload"] if requests else None,
        "_directory": str(directory.relative_to(ROOT)),
    }
    return record, events


def score_rate(rows: list[dict], field: str) -> dict:
    success = sum(row[field] is True for row in rows)
    unknown = sum(row[field] is None for row in rows)
    n = len(rows)
    return {
        "successes": success,
        "denominator": n,
        "unknown": unknown,
        "observed_rate": success / n if n else None,
        "known_denominator": n - unknown,
        "known_rate": success / (n - unknown) if n > unknown else None,
        "unknown_outcome_bounds": [success / n, (success + unknown) / n] if n else None,
    }


def summarize(rows: list[dict]) -> dict:
    clean = [row for row in rows if row["condition"] == "clean"]
    attack = [row for row in rows if row["condition"] == "attack"]
    eligible = [row for row in attack if row["original_clean_utility"] is True]
    responses = sum(row["response_count"] for row in rows)
    response_time = sum(row["response_seconds"] for row in rows)
    prefill_time = sum(row["native_prefill_seconds"] or 0 for row in rows)
    decode_time = sum(row["native_decode_seconds"] or 0 for row in rows)
    return {
        "attempts": len(rows),
        "completed": sum(row["status"] == "completed" for row in rows),
        "errors": dict(Counter(row["error_type"] for row in rows if row["status"] == "error")),
        "clean_utility": score_rate(clean, "utility"),
        "attack_utility": score_rate(attack, "utility"),
        "attack_success": score_rate(attack, "attack_success"),
        "attack_success_original_clean_true": score_rate(eligible, "attack_success"),
        "original_clean_control_coverage": dict(
            Counter(str(row["original_clean_utility"]) for row in attack)
        ),
        "attack_exposed": {
            "count": sum(row["exposed"] for row in attack),
            "denominator": len(attack),
        },
        "measurement_coverage": {
            "requests": sum(row["requests"] for row in rows),
            "responses": responses,
            "request_errors": sum(row["request_error_count"] for row in rows),
            "full_usage_responses": sum(row["full_usage_response_count"] for row in rows),
            "native_timing_responses": sum(row["native_timing_response_count"] for row in rows),
            "rss_episodes": sum(row["peak_server_rss_bytes"] is not None for row in rows),
        },
        "pooled_returned_response_costs": {
            "completion_tokens": sum(row["response_tokens"] for row in rows),
            "response_seconds": response_time,
            "completion_tokens_per_response_second": sum(row["response_tokens"] for row in rows)
            / response_time
            if response_time
            else None,
            "native_prefill_tokens": sum(row["native_prefill_tokens"] or 0 for row in rows),
            "native_prefill_seconds": prefill_time,
            "native_prefill_tokens_per_second": sum(
                row["native_prefill_tokens"] or 0 for row in rows
            )
            / prefill_time
            if prefill_time
            else None,
            "native_decode_steps": sum(row["native_decode_steps"] or 0 for row in rows),
            "native_decode_seconds": decode_time,
            "native_decode_steps_per_second": sum(row["native_decode_steps"] or 0 for row in rows)
            / decode_time
            if decode_time
            else None,
            "scope": (
                "Matching returned-response timing masks; "
                "failed request tokens/timings remain unknown."
            ),
        },
        "costs": {
            condition: {
                name: _costs(subset)
                for name, subset in {
                    "all_attempts": group,
                    "status_completed": [row for row in group if row["status"] == "completed"],
                    "utility_success": [
                        row
                        for row in group
                        if row["status"] == "completed" and row["utility"] is True
                    ],
                }.items()
            }
            for condition, group in (("all", rows), ("clean", clean), ("attack", attack))
        },
    }


def main() -> None:
    plan = read_json(PLAN)
    validate_selection(plan)
    verify_protected(plan)
    manifest = read_json(OUTPUT / "manifest.json")
    if manifest["status"] != "finalized" or len(manifest["runs"]) != 30:
        raise ValueError("Require all 30 run shards finalized before publishing")
    if manifest["plan_sha256"] != digest(PLAN):
        raise ValueError("Diagnostic manifest plan binding mismatch")
    saved_entries = {entry["run_id"]: entry for entry in manifest["runs"]}
    if len(saved_entries) != 30 or set(saved_entries) != {
        entry["run_id"] for entry in plan["runs"]
    }:
        raise ValueError("Top-level run mapping differs from the frozen plan")
    for entry in plan["runs"]:
        if any(saved_entries[entry["run_id"]].get(key) != value for key, value in entry.items()):
            raise ValueError("Top-level run metadata differs from the frozen plan")
    parent = read_json(PARENT)
    expected_runtime = parent["runtime"]["semantic_identity"]
    original_entries = {entry["run_id"]: entry for entry in parent["runs"]}
    selected = {(item["original_run_id"], item["episode_id"]): item for item in plan["selected"]}
    rows, old_rows, all_events = [], [], []
    for entry in plan["runs"]:
        directory = OUTPUT / "runs" / entry["run_id"]
        run = read_json(directory / "manifest.json")
        if run["status"] != "finalized" or run["runtime"]["semantic_identity"] != expected_runtime:
            raise ValueError("Run is unfinished or deployment changed")
        frozen_config = load_config(ROOT / entry["config"]).model_dump(mode="json")
        frozen_config["runner"]["server_pid"] = run["runtime"]["metadata_snapshot"]["pid"]
        if (
            run["run_id"] != entry["run_id"]
            or run["implementation"] != plan["implementation"]
            or run["config"] != frozen_config
        ):
            raise ValueError("Run manifest differs from its frozen plan/config/implementation")
        config = run["config"]
        identity_config = json.loads(json.dumps(config))
        for key in ("output_root", "server_pid", "runtime_metadata_path"):
            identity_config["runner"].pop(key)
        if run["identity"] != stable_hash(
            {
                "config": identity_config,
                "episodes": run["episodes"],
                "implementation": plan["implementation"],
                "runtime": expected_runtime,
            }
        ):
            raise ValueError("Run identity cannot be independently recomputed")
        if [episode["episode_id"] for episode in run["episodes"]] != entry["episode_ids"]:
            raise ValueError("Run episode set differs from plan")
        for expected in run["episodes"]:
            item = selected[(entry["original_run_id"], expected["episode_id"])]
            row, events = verify_episode(
                directory / "episodes" / expected["episode_id"], run, expected
            )
            original_path = ROOT / item["result_path"]
            original_entry = original_entries[entry["original_run_id"]]
            original_run = read_json(Path(original_entry["run_dir"]) / "manifest.json")
            old, _ = verify_episode(original_path.parent, original_run, expected)
            old.update(suite=item["suite"], original_clean_utility=None)
            if entry["retry_round"] == 1:
                old_rows.append(old)
            if row["_first_request"] != old["_first_request"]:
                raise ValueError("Initial model payload differs from original case")
            original_trace = read_trace(original_path.parent)
            current_trace = read_trace(directory / "episodes" / expected["episode_id"])
            for key in (
                "user_prompt",
                "injection_goal",
                "attack_payloads",
                "injections",
                "initial_environment",
            ):
                if current_trace[key] != original_trace[key]:
                    raise ValueError(f"Original initial input changed: {key}")
            clean_entries = [
                source
                for source in parent["runs"]
                if source["suite"] == item["suite"]
                and source["seed"] == item["seed"]
                and source["condition"] == "clean"
            ]
            if len(clean_entries) != 1:
                raise ValueError("Missing original clean control")
            clean_path = (
                Path(clean_entries[0]["run_dir"])
                / "episodes"
                / (item["user_task_id"] + "__clean")
                / "result.json"
            )
            control = read_json(clean_path)
            old["original_clean_utility"] = control["utility"]
            row.update(
                run_id=entry["run_id"],
                retry_round=entry["retry_round"],
                suite=item["suite"],
                seed=item["seed"],
                attack_variant=item["attack_variant"],
                original_run_id=entry["original_run_id"],
                original_error_type=item["original_error_type"],
                original_clean_utility=control["utility"],
                original_clean_identity=control["identity"],
                original_clean_result_sha256=digest(clean_path),
                initial_payload_identical=True,
            )
            rows.append(row)
            all_events.extend(
                {"run_id": entry["run_id"], "episode_id": row["episode_id"], **event}
                for event in events
            )
    if len(rows) != 50 or len(old_rows) != 25:
        raise ValueError("Expected exactly 50 new and 25 original outcomes")
    for run_id, entry in saved_entries.items():
        group = [row for row in rows if row["run_id"] == run_id]
        expected_counts = {
            "planned_episodes": entry["expected_episodes"],
            "finalized_episodes": len(group),
            "completed_episodes": sum(row["status"] == "completed" for row in group),
            "error_episodes": sum(row["status"] == "error" for row in group),
        }
        if any(entry["summary"].get(key) != value for key, value in expected_counts.items()):
            raise ValueError("Saved run summary counts differ from raw episode results")
    by_case = defaultdict(list)
    for row in rows:
        by_case[(row["original_run_id"], row["episode_id"])].append(row)
    recovery = Counter(
        sum(row["status"] == "completed" for row in group) for group in by_case.values()
    )
    cases = [
        {
            **selected[key],
            "completed_retry_attempts": sum(row["status"] == "completed" for row in group),
            "retries": [
                {
                    field: row.get(field)
                    for field in (
                        "run_id",
                        "retry_round",
                        "identity",
                        "status",
                        "error_type",
                        "utility",
                        "attack_success",
                        "exposed",
                        "duration_seconds",
                        "total_tokens",
                        "requests",
                        "tool_calls",
                        "_directory",
                    )
                }
                for row in sorted(group, key=lambda row: row["retry_round"])
            ],
        }
        for key, group in by_case.items()
    ]
    summary = {
        "schema_version": 1,
        "captured_at": now(),
        "diagnostic_id": plan["diagnostic_id"],
        "scope": plan["scope"],
        "plan_sha256": digest(PLAN),
        "parent_identity": parent["identity"],
        "original_evidence_unchanged": True,
        "initial_payloads_identical": 50,
        "original_error_cases": summarize(old_rows),
        "overall": summarize(rows),
        "by_retry_round": {
            str(repeat): summarize([row for row in rows if row["retry_round"] == repeat])
            for repeat in (1, 2)
        },
        "by_original_error_type": {
            kind: summarize([row for row in rows if row["original_error_type"] == kind])
            for kind in sorted({row["original_error_type"] for row in rows})
        },
        "case_recovery": {
            "both_attempts_completed": recovery[2],
            "one_attempt_completed": recovery[1],
            "neither_completed": recovery[0],
        },
        "cases": cases,
        "interpretation": (
            "No replacement of original outcomes or denominator updates. "
            "Outcome-selected six-task, same-seed diagnostic; "
            "no independent-binomial confidence intervals. "
            "Completed status is separate from utility and ASR. "
            "RSS is server process only. Failed request tokens and backend timings are unknown."
        ),
    }
    verify_protected(plan)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    atomic_json(PUBLIC / "summary.json", summary)
    fields = [
        key
        for key in rows[0]
        if not key.startswith("_")
        and key not in {"error", "usage", "server_memory", "observed_injection_vectors"}
    ]
    with (PUBLIC / "episodes.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    atomic_json(
        OUTPUT / "analysis-audit.json",
        {
            "status": "passed_complete",
            "captured_at": now(),
            "episodes": len(rows),
            "runs": len(manifest["runs"]),
            "request_events": len(all_events),
            "protected_original_files": plan["protected_inventory"]["files"],
            "analyzer_sha256": digest(Path(__file__)),
            "summary_sha256": digest(PUBLIC / "summary.json"),
            "episodes_sha256": digest(PUBLIC / "episodes.csv"),
        },
    )
    print(
        json.dumps(
            {
                "status": "passed_complete",
                "episodes": 50,
                "overall": {
                    key: summary["overall"][key]
                    for key in (
                        "completed",
                        "errors",
                        "clean_utility",
                        "attack_utility",
                        "attack_success",
                    )
                },
                "case_recovery": summary["case_recovery"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
