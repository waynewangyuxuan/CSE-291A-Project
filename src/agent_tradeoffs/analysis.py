"""Audit-friendly campaign analysis over immutable local episode evidence.

This module never reruns a model, changes a score, or infers a refusal label.
The selected task is the resampling unit, retaining repeated episodes together.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.reporting import atomic_json

ANNOTATION_FIELDS = (
    "review_status",
    "refusal",
    "failure_category",
    "reviewer",
    "evidence",
    "notes",
    "secondary_labels",
)
FAILURE_CATEGORIES = {
    "no_failure",
    "unnecessary_refusal",
    "task_understanding",
    "tool_use",
    "planning",
    "infrastructure",
    "other",
    "unclear",
}
REFUSAL_CANDIDATE = re.compile(
    r"\b(?:i (?:cannot|can't|won't|am unable to)|unable to (?:assist|help|comply)|"
    r"cannot (?:assist|help|comply)|sorry,? but|not authorized|unauthori[sz]ed)\b",
    re.I,
)


def distribution(values: list[float | int | None]) -> dict[str, Any]:
    """Linear-interpolated empirical quantiles; absent measurements stay absent."""
    observed = sorted(float(value) for value in values if value is not None)
    if any(not math.isfinite(value) for value in observed):
        raise ValueError("Non-finite measurement")
    if not observed:
        return {
            key: None for key in ("mean", "sd", "min", "p25", "median", "p75", "p90", "p95", "max")
        } | {"n": 0, "total": 0.0}

    def quantile(p: float) -> float:
        index = (len(observed) - 1) * p
        lower = math.floor(index)
        upper = math.ceil(index)
        return observed[lower] + (observed[upper] - observed[lower]) * (index - lower)

    return {
        "n": len(observed),
        "mean": mean(observed),
        "sd": stdev(observed) if len(observed) > 1 else None,
        "min": observed[0],
        "p25": quantile(0.25),
        "median": quantile(0.5),
        "p75": quantile(0.75),
        "p90": quantile(0.9),
        "p95": quantile(0.95),
        "max": observed[-1],
        "total": sum(observed),
    }


def _cluster(row: dict[str, Any]) -> tuple[str, str]:
    return row["suite"], row["user_task_id"]


def measured_rate(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Observed positives / supplied denominator, with dependence-aware intervals.

    Caller selects the denominator. Unknowns in all-attempt rates count in the
    denominator but remain explicit; they are never called resistant outcomes.
    """
    positives = sum(row.get(field) is True for row in rows)
    n = len(rows)
    grouped: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for row in rows:
        grouped[_cluster(row)].append(row.get(field) is True)
    result = {
        "successes": positives,
        "denominator": n,
        "rate": positives / n if n else None,
        "unique_tasks": len(grouped),
        "ci95": None,
        "ci_method": None,
        "unknown_outcomes": sum(row.get(field) is None for row in rows),
    }
    unknown = result["unknown_outcomes"]
    result["outcome_bounds"] = {
        "lower": positives / n if n else None,
        "upper": (positives + unknown) / n if n else None,
        "note": "Bounds assign unknown outcomes all false or all true; not confidence intervals.",
    }
    if not n:
        return result
    if len(grouped) == n:
        # Wilson score interval applies to one observed episode per task only.
        z = 1.959963984540054
        p = positives / n
        center = (p + z * z / (2 * n)) / (1 + z * z / n)
        width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        result.update(
            ci95=[max(0.0, center - width), min(1.0, center + width)],
            ci_method="wilson_unique_tasks",
        )
    elif len(grouped) >= 2:
        rng = random.Random(0)
        strata: dict[str, list[list[bool]]] = defaultdict(list)
        for (suite, _), values in grouped.items():
            strata[suite].append(values)
        samples = []
        for _ in range(2000):
            selected = [
                cluster
                for suite in sorted(strata)
                for cluster in rng.choices(strata[suite], k=len(strata[suite]))
            ]
            samples.append(sum(sum(cluster) for cluster in selected) / sum(map(len, selected)))
        quantiles = distribution(samples)
        samples.sort()
        result.update(
            ci95=[
                samples[49] + 0.975 * (samples[50] - samples[49]),
                samples[1949] + 0.025 * (samples[1950] - samples[1949]),
            ],
            ci_method="task_cluster_bootstrap_suite_stratified_percentile_2000_seed0",
            bootstrap_degenerate=quantiles["min"] == quantiles["max"],
            stratum_unique_tasks={suite: len(clusters) for suite, clusters in strata.items()},
        )
    else:
        result["ci_method"] = "unavailable_one_task_cluster"
    return result


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(_read_text(path))


def _artifact_path(path: Path) -> Path:
    return path if path.is_file() else path.with_suffix(path.suffix + ".gz")


def _read_text(path: Path) -> str:
    actual = _artifact_path(path)
    if actual.suffix == ".gz":
        with gzip.open(actual, "rt") as handle:
            return handle.read()
    return actual.read_text()


def _content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("content", "")) for block in content)
    return ""


def _state_changes(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in _state_changes(before.get(key), after.get(key), f"{prefix}/{key}")
        ]
    return [prefix or "/"] if before != after else []


def _request_measurements(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    available = _artifact_path(path).is_file()
    events = [json.loads(line) for line in _read_text(path).splitlines()] if available else []
    responses = [event for event in events if event.get("event") == "response"]
    timings = [event.get("response", {}).get("timings") or {} for event in responses]
    responses_with_usage = [
        event
        for event in responses
        if isinstance((event.get("response", {}).get("usage") or {}).get("completion_tokens"), int)
    ]
    valid_timed = [event for event in responses_with_usage if event.get("latency_seconds", 0) > 0]
    wall_seconds = sum(event["latency_seconds"] for event in valid_timed)
    completed_tokens = sum(event["response"]["usage"]["completion_tokens"] for event in valid_timed)
    native = [
        timing
        for timing in timings
        if timing.get("predicted_ms", 0) > 0 and isinstance(timing.get("predicted_n"), (int, float))
    ]
    prompt = [
        timing
        for timing in timings
        if timing.get("prompt_ms", 0) > 0 and isinstance(timing.get("prompt_n"), (int, float))
    ]
    predicted_seconds = sum(timing["predicted_ms"] for timing in native) / 1000
    # The pinned llama.cpp produces the first output token from prefill logits;
    # its native decoder throughput counts the remaining n-1 actual steps.
    decode_steps = sum(max(timing["predicted_n"] - 1, 0) for timing in native)
    prompt_seconds = sum(timing["prompt_ms"] for timing in prompt) / 1000
    measurements = {
        "request_log_available": available,
        "response_count": len(responses),
        "usage_response_count": len(responses_with_usage),
        "full_usage_response_count": sum(
            all(
                isinstance((event.get("response", {}).get("usage") or {}).get(key), int)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            )
            for event in responses
        ),
        "_request_latencies": [
            event["latency_seconds"]
            for event in events
            if event.get("event") in {"response", "request_error"}
            and event.get("latency_seconds") is not None
        ],
        "request_error_count": sum(event.get("event") == "request_error" for event in events),
        "request_latency_seconds": sum(
            event.get("latency_seconds", 0)
            for event in events
            if event.get("event") in {"response", "request_error"}
        ),
        "response_seconds": wall_seconds,
        "response_tokens": completed_tokens,
        "completion_tokens_per_response_second": completed_tokens / wall_seconds
        if wall_seconds
        else None,
        "native_decode_seconds": predicted_seconds if native else None,
        "native_decode_tokens": sum(timing["predicted_n"] for timing in native) if native else None,
        "native_decode_steps": decode_steps if native else None,
        "native_decode_tokens_per_second": decode_steps / predicted_seconds if native else None,
        "native_prefill_seconds": prompt_seconds if prompt else None,
        "native_prefill_tokens": sum(timing["prompt_n"] for timing in prompt) if prompt else None,
        "native_prefill_tokens_per_second": sum(timing["prompt_n"] for timing in prompt)
        / prompt_seconds
        if prompt
        else None,
        "native_timing_response_count": len(native),
        "cached_tokens": sum(timing.get("cache_n", 0) for timing in timings)
        if any("cache_n" in timing for timing in timings)
        else None,
    }
    return measurements, events


def _run_entries(campaign_path: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path]]]:
    path = campaign_path / "manifest.json" if campaign_path.is_dir() else campaign_path
    campaign = _read_json(path)
    if "episodes" in campaign and "config" in campaign:
        return campaign, [({}, path.parent)]
    entries = []
    for raw in campaign.get("runs", []):
        entry = {"run_dir": raw} if isinstance(raw, str) else raw
        run_dir = Path(entry.get("run_dir") or entry.get("path") or "")
        if not str(run_dir) or str(run_dir) == ".":
            raise ValueError("Campaign run entry needs run_dir or path")
        if not run_dir.is_absolute():
            run_dir = path.parent / run_dir
        entries.append((entry, run_dir.resolve()))
    if not entries:
        raise ValueError(f"No runs in campaign: {path}")
    return campaign, entries


def collect_records(
    campaign_path: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    campaign, entries = _run_entries(Path(campaign_path))
    records = []
    manifests = []
    seen: set[tuple[str, str]] = set()
    for entry, run_dir in entries:
        if not (run_dir / "manifest.json").is_file():
            continue
        manifest = _read_json(run_dir / "manifest.json")
        manifest = {**manifest, "_run_dir": str(run_dir)}
        manifests.append(manifest)
        experiment = manifest["config"]["experiment"]
        for episode in manifest["episodes"]:
            episode_dir = run_dir / "episodes" / episode["episode_id"]
            result_path = episode_dir / "result.json"
            if not result_path.is_file():
                continue
            result = _read_json(result_path)
            if result.get("identity") != manifest["identity"]:
                raise ValueError(f"Episode identity mismatch: {result_path}")
            for key in ("episode_id", "user_task_id", "condition", "injection_task_id"):
                if result.get(key) != episode.get(key):
                    raise ValueError(f"Episode {key} mismatch: {result_path}")
            key = manifest["run_id"], episode["episode_id"]
            if key in seen:
                raise ValueError(f"Duplicate campaign episode: {key}")
            if result.get("status") != "completed" and (
                result.get("utility") is not None or result.get("attack_success") is not None
            ):
                raise ValueError(f"Errored episode has non-unknown scores: {result_path}")
            seen.add(key)
            trace_path = _artifact_path(episode_dir / "trace.json")
            trace = _read_json(trace_path) if trace_path.is_file() else {}
            if trace and trace.get("identity") != manifest["identity"]:
                raise ValueError(f"Trace identity mismatch: {trace_path}")
            measurements, _ = _request_measurements(episode_dir / "requests.jsonl")
            final_answers = [
                _content(message)
                for message in trace.get("messages", [])
                if message.get("role") == "assistant"
            ]
            final_answer = final_answers[-1] if final_answers else ""
            error = result.get("error") or {}
            error_type = error.get("type")
            changes = (
                _state_changes(trace["initial_environment"], trace["final_environment"])
                if trace.get("initial_environment") is not None
                and trace.get("final_environment") is not None
                else None
            )
            usage = result.get("usage") or {}
            record = {
                **result,
                **measurements,
                "run_id": manifest["run_id"],
                "suite": experiment["suite"],
                "repeat_id": str(
                    entry.get(
                        "repeat_id",
                        entry.get(
                            "repeat",
                            manifest.get(
                                "repeat_id",
                                manifest["config"]["model"].get("seed", manifest["run_id"]),
                            ),
                        ),
                    )
                ),
                "model": manifest["config"]["model"]["model"],
                "attack_variant": experiment.get("attack"),
                "episode_dir": str(episode_dir.resolve()),
                "trace_path": str(trace_path.resolve()),
                "error_type": error_type,
                "error_message": error.get("message"),
                "timeout": bool(error_type and "timeout" in error_type.lower()),
                "invalid_model_output": error_type == "InvalidModelOutputError",
                "invalid_structured_call": error_type == "InvalidModelOutputError"
                and "structured tool call" in error.get("message", "").lower(),
                "tool_limit": error_type == "ToolStepLimitError",
                "exposed": bool(result["observed_injection_vectors"])
                if "observed_injection_vectors" in result
                else None,
                "refusal_candidate": bool(REFUSAL_CANDIDATE.search(final_answer)),
                "final_answer": final_answer,
                "state_changed_fields": changes,
                "state_change_count": len(changes) if changes is not None else None,
                "peak_server_rss_bytes": (result.get("server_memory") or {}).get("peak_rss_bytes"),
                **{
                    key: usage.get(key)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                },
                **{key: "" for key in ANNOTATION_FIELDS},
            }
            records.append(record)
    return campaign, records, manifests


def merge_annotations(records: list[dict[str, Any]], path: str | Path | None) -> None:
    if path is None:
        return
    index = {(row["run_id"], row["episode_id"]): row for row in records}
    seen = set()
    with Path(path).open(newline="") as handle:
        for annotation in csv.DictReader(handle):
            key = annotation["run_id"], annotation["episode_id"]
            if key in seen or key not in index:
                raise ValueError(f"Duplicate or unknown annotation: {key}")
            seen.add(key)
            row = index[key]
            if annotation.get("identity") != row["identity"]:
                raise ValueError(f"Stale annotation identity: {key}")
            if annotation.get("review_status") == "reviewed":
                if annotation.get("failure_category") not in FAILURE_CATEGORIES:
                    raise ValueError(f"Invalid failure category: {key}")
                if annotation.get("refusal") not in {"yes", "no", "unclear"}:
                    raise ValueError(f"Reviewed refusal label is missing: {key}")
                if (
                    not annotation.get("reviewer", "").strip()
                    or not annotation.get("evidence", "").strip()
                ):
                    raise ValueError(f"Reviewed annotation needs reviewer and evidence: {key}")
                if (
                    annotation.get("failure_category") == "unnecessary_refusal"
                    and annotation.get("refusal") != "yes"
                ):
                    raise ValueError(f"Unnecessary refusal must be labeled refusal=yes: {key}")
                if (
                    annotation.get("failure_category") == "unnecessary_refusal"
                    and row.get("utility") is True
                ):
                    raise ValueError(f"Unnecessary refusal contradicts utility success: {key}")
            elif annotation.get("review_status") not in {"", "unreviewed"}:
                raise ValueError(f"Invalid review status: {key}")
            secondary = annotation.get("secondary_labels", "")
            if secondary:
                labels = secondary.split("|")
                if any(label not in FAILURE_CATEGORIES for label in labels):
                    raise ValueError(f"Invalid secondary failure label: {key}")
                if "unnecessary_refusal" in labels and (
                    annotation.get("refusal") != "yes" or row.get("utility") is True
                ):
                    raise ValueError(f"Secondary unnecessary-refusal label contradicts row: {key}")
            row.update({field: annotation.get(field, "") for field in ANNOTATION_FIELDS})


def _costs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "duration_seconds",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "requests",
        "tool_calls",
        "tool_errors",
        "peak_server_rss_bytes",
        "completion_tokens_per_response_second",
        "native_decode_tokens_per_second",
        "native_prefill_tokens_per_second",
        "state_change_count",
    )
    return {
        **{field: distribution([row.get(field) for row in rows]) for field in fields},
        "request_latency_seconds": distribution(
            [latency for row in rows for latency in row.get("_request_latencies", [])]
        ),
    }


def _labels(row: dict[str, Any]) -> set[str]:
    return {row["failure_category"], *filter(None, row["secondary_labels"].split("|"))}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [row for row in rows if row["condition"] == "clean"]
    attack = [row for row in rows if row["condition"] == "attack"]

    def pair_key(row):
        return row["suite"], row["user_task_id"], row["repeat_id"]

    clean_lookup = {pair_key(row): row for row in clean}
    if len(clean_lookup) != len(clean):
        raise ValueError(
            "Ambiguous clean pairing: more than one clean episode per suite/task/repeat"
        )
    control_counts = Counter()
    for row in attack:
        control = clean_lookup.get(pair_key(row))
        if control is None:
            control_counts["missing_clean_result"] += 1
        elif control["status"] == "completed" and control.get("utility") is True:
            control_counts["verified_clean_true"] += 1
        elif control["status"] == "completed" and control.get("utility") is False:
            control_counts["verified_clean_false"] += 1
        else:
            control_counts["unknown_clean_outcome"] += 1
    eligible = [
        row
        for row in attack
        if clean_lookup.get(pair_key(row), {}).get("utility") is True
        and clean_lookup[pair_key(row)]["status"] == "completed"
    ]

    def known(items, field):
        return [
            row
            for row in items
            if row["status"] == "completed" and isinstance(row.get(field), bool)
        ]

    exposed = [row for row in attack if row["exposed"]]
    reviewed_clean = [row for row in clean if row["review_status"] == "reviewed"]
    refusal_positives = sum("unnecessary_refusal" in _labels(row) for row in reviewed_clean)
    refusal_indeterminate = sum(
        "unnecessary_refusal" not in _labels(row)
        and (row["refusal"] == "unclear" or (row["refusal"] == "yes" and "unclear" in _labels(row)))
        for row in reviewed_clean
    )
    known_refusal_behavior = len(reviewed_clean) - refusal_indeterminate
    rates = {
        "clean_utility_all_attempts": measured_rate(clean, "utility"),
        "clean_utility_known": measured_rate(known(clean, "utility"), "utility"),
        "attack_utility_all_attempts": measured_rate(attack, "utility"),
        "attack_utility_known": measured_rate(known(attack, "utility"), "utility"),
        "attack_success_all_attempts": measured_rate(attack, "attack_success"),
        "attack_success_known": measured_rate(known(attack, "attack_success"), "attack_success"),
        "attack_success_clean_completable_all_attempts": measured_rate(eligible, "attack_success"),
        "attack_success_clean_completable_known": measured_rate(
            known(eligible, "attack_success"), "attack_success"
        ),
        "attack_exposure": measured_rate(attack, "exposed"),
        "attack_success_exposed_all_attempts": measured_rate(exposed, "attack_success"),
        "attack_success_exposed_known": measured_rate(
            known(exposed, "attack_success"), "attack_success"
        ),
        "attack_success_exposed_clean_completable_known": measured_rate(
            known([row for row in eligible if row["exposed"]], "attack_success"), "attack_success"
        ),
    }
    paired = [(row, clean_lookup.get(pair_key(row))) for row in attack]
    paired = [
        (attack_row, clean_row)
        for attack_row, clean_row in paired
        if clean_row
        and isinstance(attack_row.get("utility"), bool)
        and isinstance(clean_row.get("utility"), bool)
    ]
    return {
        "episodes": len(rows),
        "unique_tasks": len({_cluster(row) for row in rows}),
        "completed": sum(row["status"] == "completed" for row in rows),
        "unknown_clean_episodes": sum(
            row["status"] != "completed" or not isinstance(row.get("utility"), bool)
            for row in clean
        ),
        "clean_control_coverage": {
            "attack_episodes": len(attack),
            **{
                category: control_counts[category]
                for category in (
                    "verified_clean_true",
                    "verified_clean_false",
                    "unknown_clean_outcome",
                    "missing_clean_result",
                )
            },
            "note": (
                "The four counts partition attack episodes. A suite/task/repeat clean control "
                "is reused across goals and styles (four fixed attack variants in Phase 1); "
                "unknown clean outcomes do not establish inability."
            ),
        },
        "errors_by_type": dict(
            Counter(row["error_type"] or "unknown" for row in rows if row["status"] != "completed")
        ),
        "timeouts": sum(row["timeout"] for row in rows),
        "invalid_model_output_episodes": sum(row["invalid_model_output"] for row in rows),
        "invalid_structured_call_episodes": sum(row["invalid_structured_call"] for row in rows),
        "tool_limit_episodes": sum(row["tool_limit"] for row in rows),
        "episodes_with_tool_errors": sum(row.get("tool_errors", 0) > 0 for row in rows),
        "measurement_coverage": {
            "episodes_with_request_log": sum(row["request_log_available"] for row in rows),
            "observed_responses": sum(row["response_count"] for row in rows),
            "responses_with_completion_usage": sum(row["usage_response_count"] for row in rows),
            "responses_with_full_usage": sum(row["full_usage_response_count"] for row in rows),
            "responses_with_native_decode_timing": sum(
                row["native_timing_response_count"] for row in rows
            ),
            "request_errors": sum(row["request_error_count"] for row in rows),
            "episodes_with_server_rss": sum(
                row["peak_server_rss_bytes"] is not None for row in rows
            ),
            "note": (
                "Usage coverage refers to observed completed responses; "
                "token use on failed requests is unknown."
            ),
        },
        "rates": rates,
        "paired_utility_change": {
            "pairs": len(paired),
            "mean_attack_minus_clean": mean(
                int(attack_row["utility"]) - int(clean_row["utility"])
                for attack_row, clean_row in paired
            )
            if paired
            else None,
            "clean_success_attack_failure": sum(
                clean_row["utility"] and not attack_row["utility"]
                for attack_row, clean_row in paired
            ),
            "clean_failure_attack_success": sum(
                not clean_row["utility"] and attack_row["utility"]
                for attack_row, clean_row in paired
            ),
            "attacks_without_paired_clean_result": sum(
                pair_key(row) not in clean_lookup for row in attack
            ),
            "weighting": (
                "one observation per attack episode paired by suite/task/repeat_id; "
                "attack styles and goals reuse the corresponding clean episode"
            ),
        },
        "costs_by_condition": {
            condition: _costs([row for row in rows if row["condition"] == condition])
            for condition in ("clean", "attack")
        },
        "costs_by_condition_completed": {
            condition: _costs(
                [
                    row
                    for row in rows
                    if row["condition"] == condition and row["status"] == "completed"
                ]
            )
            for condition in ("clean", "attack")
        },
        "review": {
            "refusal_keyword_candidates": sum(row["refusal_candidate"] for row in rows),
            "reviewed_episodes": sum(row["review_status"] == "reviewed" for row in rows),
            "reviewed_clean_episodes": len(reviewed_clean),
            "total_clean_episodes": len(clean),
            "unreviewed_finalized_clean_episodes": len(clean) - len(reviewed_clean),
            "reviewed_clean_unnecessary_refusals": refusal_positives,
            "reviewed_clean_indeterminate_refusals": refusal_indeterminate,
            "known_behavior_denominator": known_refusal_behavior,
            "unnecessary_refusal_rate_known": refusal_positives / known_refusal_behavior
            if known_refusal_behavior
            else None,
            "possible_bounds_among_reviewed_clean": [
                refusal_positives / len(reviewed_clean),
                (refusal_positives + refusal_indeterminate) / len(reviewed_clean),
            ]
            if reviewed_clean
            else None,
            "unnecessary_refusal_rate_among_reviewed_clean": refusal_positives / len(reviewed_clean)
            if reviewed_clean
            else None,
            "unnecessary_refusal_rate_among_reviewed_clean_note": (
                "Observed incidence among reviewed finalized clean episodes; "
                "a lower bound when reviewed refusal classifications are indeterminate. "
                "Unreviewed and pending episodes are not assigned refusal labels."
            ),
            "failure_categories_reviewed": dict(
                Counter(
                    row["failure_category"] for row in rows if row["review_status"] == "reviewed"
                )
            ),
            "failure_labels_reviewed": dict(
                Counter(
                    label
                    for row in rows
                    if row["review_status"] == "reviewed"
                    for label in _labels(row)
                )
            ),
            "note": (
                "Keyword candidates are triage only. Reviewed-clean rate may have selection bias; "
                "no unreviewed refusal labels are inferred."
            ),
        },
    }


def _repeat_consistency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                row["suite"],
                row["user_task_id"],
                row["condition"],
                row.get("injection_task_id"),
                row.get("attack_variant") if row["condition"] == "attack" else None,
            )
        ].append(row)
    repeated = [group for group in groups.values() if len(group) > 1]
    return {
        "repeated_task_condition_cells": len(repeated),
        "utility_disagreement_cells": sum(
            len({row["utility"] for row in group}) > 1 for group in repeated
        ),
        "attack_success_disagreement_cells": sum(
            len({row["attack_success"] for row in group}) > 1
            for group in repeated
            if group[0]["condition"] == "attack"
        ),
        "status_disagreement_cells": sum(
            len({row["status"] for row in group}) > 1 for group in repeated
        ),
        "note": (
            "Repeated seeds estimate stochastic variability within a task/style/goal cell; "
            "repeats do not increase independent task sample size."
        ),
    }


def _startup(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    launches = {}
    for manifest in manifests:
        snapshots = [manifest.get("runtime", {})] + manifest.get("resume_runtime_observations", [])
        for snapshot in snapshots:
            metadata = snapshot.get("metadata_snapshot") or {}
            started, ready = metadata.get("started_at"), metadata.get("ready_at")
            if not started or not ready:
                continue
            duration = (
                datetime.fromisoformat(ready) - datetime.fromisoformat(started)
            ).total_seconds()
            if duration < 0:
                raise ValueError("Runtime ready_at precedes started_at")
            launches[(metadata.get("pid"), started)] = {
                "started_at": started,
                "ready_at": ready,
                "seconds_until_ready": duration,
                "model": metadata.get("model_alias"),
                "pid": metadata.get("pid"),
                "scope": "managed process launch until health-ready; OS disk caches uncontrolled",
            }
    return list(launches.values())


def _deployment_fingerprints(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """Do not pool measurements from changed model files or server settings."""
    fingerprints: dict[str, list[str]] = defaultdict(list)
    unavailable = []
    for manifest in manifests:
        semantic = manifest.get("runtime", {}).get("semantic_identity")
        if semantic:
            fingerprints[stable_hash(semantic)].append(manifest["run_id"])
        else:
            unavailable.append(manifest["run_id"])
    if len(fingerprints) > 1:
        raise ValueError(
            "Campaign contains mixed deployment identities; analyze each deployment separately: "
            + json.dumps(fingerprints, sort_keys=True)
        )
    return {
        "fingerprints": dict(fingerprints),
        "runs_without_semantic_identity": unavailable,
        "consistent": len(fingerprints) == 1 and not unavailable,
    }


def select_review_sample(
    rows: list[dict[str, Any]], planned: int, target: int = 30
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select reproducible, diverse traces without pretending review is random sampling."""

    def strata(row):
        categories = []
        if row["condition"] == "clean" and row.get("utility") is not True:
            categories.append("clean_failure")
        if row["invalid_model_output"] or row.get("tool_errors", 0) > 0:
            categories.append("invalid_output_or_tool_error")
        if row["condition"] == "attack" and row.get("attack_success") is True:
            categories.append("scored_attack_success")
        if row.get("utility") is True:
            categories.append("utility_preserving")
        return categories or ["other"]

    required = min(target, planned)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for category in strata(row):
            groups[(row["suite"], category)].append(row)

    def rank(row):
        return hashlib.sha256(f"{row['run_id']}/{row['episode_id']}".encode()).hexdigest()

    groups = {key: sorted(values, key=rank) for key, values in groups.items()}
    selected = []
    chosen = set()
    chosen_tasks = set()
    while len(selected) < min(required, len(rows)):
        advanced = False
        for key in sorted(groups, key=lambda item: (item[1], item[0])):
            candidates = [
                row for row in groups[key] if (row["run_id"], row["episode_id"]) not in chosen
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda row: (_cluster(row) in chosen_tasks, rank(row)))
            row = candidates[0]
            selected.append(row)
            chosen.add((row["run_id"], row["episode_id"]))
            chosen_tasks.add(_cluster(row))
            advanced = True
            if len(selected) == min(required, len(rows)):
                break
        if not advanced:
            break
    for row in rows:
        row["review_selected"] = (row["run_id"], row["episode_id"]) in chosen
        row["review_strata"] = "|".join(strata(row))
    reviewed = sum(row["review_status"] == "reviewed" for row in selected)
    return selected, {
        "required_traces": required,
        "selected_traces": len(selected),
        "selected_unique_tasks": len(chosen_tasks),
        "reviewed_selected_traces": reviewed,
        "complete": len(selected) == required and reviewed == required,
        "campaign_finalized": len(rows) == planned,
        "provisional_selection": len(rows) != planned,
        "selection_sha256": stable_hash(sorted(chosen)),
        "selection_method": (
            "stable SHA256 ranking, round-robin suite/outcome strata, "
            "prefer task diversity; deterministic purposive review, not a prevalence sample"
        ),
        "selected_by_suite": dict(Counter(row["suite"] for row in selected)),
        "selected_by_stratum": dict(
            Counter(category for row in selected for category in strata(row))
        ),
    }


def _write_csv(path: Path, records: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(
                {
                    key: json.dumps(value) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                }
            )
    temporary.replace(path)


def _svg_costs(path: Path, rows: list[dict[str, Any]]) -> None:
    """Empirical CDF exposes the complete latency distribution without bins."""
    width, height, left, bottom = 720, 400, 72, 342
    maximum = max((row["duration_seconds"] for row in rows), default=1) or 1
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="sans-serif" font-size="13" fill="#333">',
        '<text x="72" y="27" font-size="18">'
        "Episode latency: empirical cumulative distribution</text>",
    ]
    for value in (0, 0.25, 0.5, 0.75, 1):
        y = bottom - value * 280
        elements.extend(
            [
                f'<line x1="{left}" y1="{y}" x2="685" y2="{y}" stroke="#ddd"/>',
                f'<text x="30" y="{y + 4}">{value:.0%}</text>',
            ]
        )
    for tick in range(6):
        x = left + tick / 5 * 610
        elements.append(
            f'<text x="{x}" y="363" text-anchor="middle">{maximum * tick / 5:.1f}</text>'
        )
    for condition, color, legend_y in (("clean", "#2468ac", 48), ("attack", "#c44f32", 67)):
        durations = sorted(row["duration_seconds"] for row in rows if row["condition"] == condition)
        points = [(left, bottom)]
        for i, duration in enumerate(durations):
            x = left + duration / maximum * 610
            points.extend(
                [
                    (x, bottom - i / len(durations) * 280),
                    (x, bottom - (i + 1) / len(durations) * 280),
                ]
            )
        points_text = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        elements.extend(
            [
                f'<polyline points="{points_text}" fill="none" stroke="{color}" stroke-width="2"/>',
                f'<text x="490" y="{legend_y}" fill="{color}">'
                f"{escape(condition)} (n={len(durations)})</text>",
            ]
        )
    elements.extend(
        [
            '<text x="377" y="390" text-anchor="middle">'
            "Wall-clock episode latency (seconds; includes failed attempts)</text>",
            "</g></svg>",
        ]
    )
    path.write_text("\n".join(elements) + "\n")


def _markdown(summary: dict[str, Any]) -> str:
    overall = summary["overall"]
    controls = overall["clean_control_coverage"]
    refusal = overall["review"]
    refusal_bounds = refusal["possible_bounds_among_reviewed_clean"]
    refusal_bounds_text = (
        " – ".join(f"{value:.1%}" for value in refusal_bounds)
        if refusal_bounds is not None
        else "unavailable"
    )
    lines = [
        "# Preliminary campaign measurements",
        "",
        f"Finalized {summary['finalized_episodes']} / {summary['planned_episodes']} "
        "planned episodes; "
        f"{overall['unique_tasks']} distinct suite/task pairs.",
        "",
        "Rates retain raw counts. Error outcomes are unknown, "
        "and unexposed attacks do not demonstrate resistance.",
        "",
        f"Unknown clean episodes: {overall['unknown_clean_episodes']}. "
        "Attack episodes paired to clean controls: "
        f"verified true {controls['verified_clean_true']}, "
        f"verified false {controls['verified_clean_false']}, "
        f"unknown outcome {controls['unknown_clean_outcome']}, "
        f"missing result {controls['missing_clean_result']}. "
        "These counts partition attacks; attack variants reuse clean controls. "
        "An unknown clean outcome does not establish inability.",
        "",
        "| Outcome | Successes / denominator | Rate | 95% interval |",
        "|---|---:|---:|---:|",
    ]
    for label, rate in overall["rates"].items():
        value = f"{rate['rate']:.1%}" if rate["rate"] is not None else "unavailable"
        interval = (
            " – ".join(f"{value:.1%}" for value in rate["ci95"])
            if rate["ci95"] is not None
            else "unavailable"
        )
        lines.append(
            f"| {label} | {rate['successes']} / {rate['denominator']} | {value} | {interval} |"
        )
    lines.extend(
        [
            "",
            "Intervals reflect variation over the selected tasks. Repeated episodes remain in "
            "task clusters; fixed cohort selection and a single host limit generalization.",
            "",
            "| Condition | Latency median / p95 (s) | Prompt / completion tokens (mean) | "
            "Native decode tokens/s (median) |",
            "|---|---:|---:|---:|",
        ]
    )

    def number(value):
        return f"{value:.2f}" if value is not None else "unavailable"

    for condition, costs in overall["costs_by_condition"].items():
        latency = costs["duration_seconds"]
        lines.append(
            f"| {condition} | {number(latency['median'])} / {number(latency['p95'])} | "
            f"{number(costs['prompt_tokens']['mean'])} / "
            f"{number(costs['completion_tokens']['mean'])} | "
            f"{number(costs['native_decode_tokens_per_second']['median'])} |"
        )
    lines.extend(
        [
            "",
            f"Errors by type: `{json.dumps(overall['errors_by_type'], sort_keys=True)}`.",
            f"Reviewed episodes: {overall['review']['reviewed_episodes']}; "
            f"refusal keyword candidates: {overall['review']['refusal_keyword_candidates']}. "
            "Candidates have no semantic label until reviewed.",
            f"Clean refusal review: {refusal['reviewed_clean_episodes']} / "
            f"{refusal['total_clean_episodes']} finalized clean episodes reviewed; "
            f"unnecessary refusals {refusal['reviewed_clean_unnecessary_refusals']}, "
            f"indeterminate {refusal['reviewed_clean_indeterminate_refusals']}. "
            f"Possible incidence among reviewed clean episodes: {refusal_bounds_text} "
            "(classification bounds, not a confidence interval). "
            "Unreviewed finalized clean episodes: "
            f"{refusal['unreviewed_finalized_clean_episodes']}; "
            "pending episodes have no inferred refusal label.",
            f"Selected-trace review gate: {summary['review_sample']['reviewed_selected_traces']} / "
            f"{summary['review_sample']['required_traces']} reviewed; "
            f"complete={summary['review_sample']['complete']}. "
            "Selection remains provisional until collection finishes.",
            "",
            "Server RSS is sampled process resident memory, not total GPU allocation or machine "
            "unified memory. Native decode throughput excludes prefill; completion tokens / "
            "response seconds includes request overhead and prefill. Startup observations "
            "are separate from warmed episode latency.",
            "",
            "Artifacts: `summary.json`, `episodes.csv`, `review.csv`, `review-sample.csv`, "
            "`requests.csv`, "
            "`latency.svg`. See `docs/metrics.md` for definitions and review instructions.",
            "",
        ]
    )
    unknown_rates = {
        key: value
        for key, value in overall["rates"].items()
        if value["unknown_outcomes"] and key.startswith("attack_success")
    }
    if unknown_rates:
        lines.extend(
            [
                "Possible ASR bounds under unknown outcomes (these are not confidence intervals):",
                "",
                "| Metric | Unknown outcomes | Lower / upper possible rate |",
                "|---|---:|---:|",
            ]
        )
        for label, metric in unknown_rates.items():
            bounds = metric["outcome_bounds"]
            lines.append(
                f"| {label} | {metric['unknown_outcomes']} | "
                f"{bounds['lower']:.1%} / {bounds['upper']:.1%} |"
            )
        lines.append("")
    return "\n".join(lines)


def analyze_campaign(
    campaign_path: str | Path,
    output_dir: str | Path | None = None,
    annotations_path: str | Path | None = None,
    require_review: bool = False,
) -> dict[str, Any]:
    """Analyze a run or campaign manifest; inputs remain immutable."""
    campaign_path = Path(campaign_path).resolve()
    campaign, records, manifests = collect_records(campaign_path)
    deployment = _deployment_fingerprints(manifests)
    merge_annotations(records, annotations_path)
    root = campaign_path if campaign_path.is_dir() else campaign_path.parent
    output = Path(output_dir).resolve() if output_dir else root / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    grouped_suite = {
        suite: _summarize([row for row in records if row["suite"] == suite])
        for suite in sorted({row["suite"] for row in records})
    }
    grouped_repeat = {
        str(repeat): _summarize([row for row in records if str(row["repeat_id"]) == str(repeat)])
        for repeat in sorted({str(row["repeat_id"]) for row in records})
    }
    # Each style subgroup includes its paired clean controls for conditional ASR.
    grouped_attack = {
        str(style): _summarize(
            [
                row
                for row in records
                if row["condition"] == "clean" or row.get("attack_variant") == style
            ]
        )
        for style in sorted(
            {row.get("attack_variant") for row in records if row["condition"] == "attack"}
        )
    }
    grouped_goal = {
        suite: {
            goal: _summarize(
                [
                    row
                    for row in records
                    if row["suite"] == suite
                    and (row["condition"] == "clean" or row.get("injection_task_id") == goal)
                ]
            )
            for goal in sorted(
                {
                    row["injection_task_id"]
                    for row in records
                    if row["suite"] == suite and row["condition"] == "attack"
                }
            )
        }
        for suite in sorted({row["suite"] for row in records})
    }
    known_planned = sum(len(manifest["episodes"]) for manifest in manifests)
    planned = campaign.get("planned_episodes", known_planned)
    if planned < known_planned:
        raise ValueError("Campaign planned count is smaller than known run manifests")
    review_rows, review_audit = select_review_sample(records, planned)
    summary = {
        "schema_version": 1,
        "campaign_id": campaign.get("campaign_id", campaign.get("run_id")),
        "campaign_path": str(campaign_path),
        "output_dir": str(output),
        "planned_episodes": planned,
        "planned_count_scope": "campaign"
        if "planned_episodes" in campaign or "episodes" in campaign
        else "available_run_manifests",
        "available_run_manifests": len(manifests),
        "planned_runs": len(campaign.get("runs", manifests)),
        "finalized_episodes": len(records),
        "missing_episodes": planned - len(records),
        "overall": _summarize(records),
        "by_suite": grouped_suite,
        "by_repeat": grouped_repeat,
        "by_attack": grouped_attack,
        "by_suite_goal": grouped_goal,
        "review_sample": review_audit,
        "preliminary_evaluation_ready": len(records) == planned
        and review_audit["complete"]
        and deployment["consistent"],
        "repeat_consistency": _repeat_consistency(records),
        "startup_observations": _startup(manifests),
        "measurement_provenance": {
            "deployment_consistency": deployment,
            "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "score_source": (
                "pinned AgentDojo programmatic task utility and injection state checkers; "
                "no LLM judge"
            ),
            "latency_source": (
                "result.duration_seconds (monotonic wall-clock), "
                "includes errors, tools and client overhead"
            ),
            "token_source": (
                "native response usage, summed across actual requests; "
                "repeated prompt context counts again"
            ),
            "throughput_source": (
                "native decode sums max(predicted_n-1,0) / sum(predicted_ms/1000), "
                "matching the pinned llama.cpp first-token-in-prefill convention; "
                "prefill sums prompt_n / sum(prompt_ms/1000); "
                "response throughput separately uses usage.completion_tokens / "
                "client response latency"
            ),
            "exposure_source": (
                "result.observed_injection_vectors; "
                "exact payload normalized/raw-or-YAML matcher before each sent request"
            ),
            "memory_source": (
                "server_memory.peak_rss_bytes sampled every 0.1s; "
                "unified/GPU total and harness memory unmeasured"
            ),
            "startup_source": (
                "captured runtime metadata ready_at minus started_at, "
                "deduplicated by pid/start timestamp; OS caches uncontrolled"
            ),
            "intervals": (
                "95% Wilson if one episode per unique task; otherwise deterministic task-cluster "
                "bootstrap stratified by suite, 2000 percentile resamples seed0; "
                "one cluster has no bootstrap interval; "
                "all-equal clusters can produce degenerate intervals"
            ),
            "annotations_path": str(Path(annotations_path).resolve()) if annotations_path else None,
            "annotation_source": (
                "manual trace review only; refusal candidates are regex triage, "
                "never automatically scored"
            ),
            "runs": [
                {
                    "run_id": manifest["run_id"],
                    "identity": manifest["identity"],
                    "run_dir": manifest["_run_dir"],
                    "model": manifest["config"]["model"]["model"],
                    "runtime_identity": manifest.get("runtime", {}).get("semantic_identity"),
                    "implementation": manifest.get("implementation"),
                }
                for manifest in manifests
            ],
        },
    }
    atomic_json(output / "summary.json", summary)
    fields = [
        "run_id",
        "repeat_id",
        "suite",
        "model",
        "episode_id",
        "identity",
        "condition",
        "user_task_id",
        "injection_task_id",
        "attack_variant",
        "status",
        "utility",
        "attack_success",
        "exposed",
        "duration_seconds",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "requests",
        "tool_calls",
        "tool_errors",
        "error_type",
        "error_message",
        "timeout",
        "invalid_structured_call",
        "peak_server_rss_bytes",
        "completion_tokens_per_response_second",
        "native_decode_tokens_per_second",
        "native_prefill_tokens_per_second",
        "native_timing_response_count",
        "response_count",
        "usage_response_count",
        "full_usage_response_count",
        "request_error_count",
        "request_log_available",
        "cached_tokens",
        "state_change_count",
        "state_changed_fields",
        "refusal_candidate",
        "review_selected",
        "review_strata",
        "trace_path",
        *ANNOTATION_FIELDS,
    ]
    _write_csv(output / "episodes.csv", records, fields)
    _write_csv(
        output / "review.csv",
        records,
        [
            "run_id",
            "episode_id",
            "identity",
            "suite",
            "condition",
            "user_task_id",
            "injection_task_id",
            "status",
            "utility",
            "attack_success",
            "refusal_candidate",
            "review_selected",
            "review_strata",
            "error_type",
            "trace_path",
            "final_answer",
            *ANNOTATION_FIELDS,
        ],
    )
    _write_csv(
        output / "review-sample.csv",
        review_rows,
        [
            "run_id",
            "episode_id",
            "identity",
            "suite",
            "condition",
            "user_task_id",
            "injection_task_id",
            "status",
            "utility",
            "attack_success",
            "refusal_candidate",
            "review_selected",
            "review_strata",
            "error_type",
            "trace_path",
            "final_answer",
            *ANNOTATION_FIELDS,
        ],
    )
    requests = []
    for row in records:
        _, events = _request_measurements(Path(row["episode_dir"]) / "requests.jsonl")
        for event in events:
            if event.get("event") not in {"response", "request_error"}:
                continue
            response = event.get("response") or {}
            usage, timings = response.get("usage") or {}, response.get("timings") or {}
            requests.append(
                {
                    "run_id": row["run_id"],
                    "episode_id": row["episode_id"],
                    "suite": row["suite"],
                    "condition": row["condition"],
                    "request_index": event.get("index"),
                    "event": event["event"],
                    "latency_seconds": event.get("latency_seconds"),
                    "error_type": event.get("error_type"),
                    **{
                        key: usage.get(key)
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    },
                    **{
                        key: timings.get(key)
                        for key in (
                            "prompt_n",
                            "prompt_ms",
                            "predicted_n",
                            "predicted_ms",
                            "predicted_per_second",
                            "cache_n",
                        )
                    },
                }
            )
    _write_csv(
        output / "requests.csv",
        requests,
        [
            "run_id",
            "episode_id",
            "suite",
            "condition",
            "request_index",
            "event",
            "latency_seconds",
            "error_type",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_n",
            "prompt_ms",
            "predicted_n",
            "predicted_ms",
            "predicted_per_second",
            "cache_n",
        ],
    )
    _svg_costs(output / "latency.svg", records)
    (output / "summary.md").write_text(_markdown(summary))
    if require_review and len(records) == planned and not review_audit["complete"]:
        raise ValueError(
            "Final review gate incomplete: "
            f"{review_audit['reviewed_selected_traces']}/{review_audit['required_traces']} "
            "selected traces reviewed; report files were written with coverage details"
        )
    return summary
