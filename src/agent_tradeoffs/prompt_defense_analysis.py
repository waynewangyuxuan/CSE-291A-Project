"""Paired prompt comparison analysis; official U/A stay unchanged and errors unknown."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from agent_tradeoffs.analysis import _request_measurements, distribution, measured_rate
from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.context import exposed_vectors
from agent_tradeoffs.prompt_defense import ARMS, NAME, ROOT, digest, read_evidence, verify_frozen
from agent_tradeoffs.reporting import atomic_json


def rate(rows: list[dict], field: str) -> dict:
    result = measured_rate(rows, field)
    known = [row for row in rows if row.get(field) is not None]
    result["known_score_denominator"] = len(known)
    result["known_score_rate"] = (
        sum(row[field] is True for row in known) / len(known) if known else None
    )
    return result


def paired_effect(rows: list[dict], field: str) -> dict:
    pairs = defaultdict(dict)
    for row in rows:
        if row["arm"] in pairs[row["pair_id"]]:
            raise ValueError("Duplicate arm in pair")
        pairs[row["pair_id"]][row["arm"]] = row
    if any(set(pair) != set(ARMS) for pair in pairs.values()):
        raise ValueError("Incomplete arm pair")
    differences = []
    transitions = Counter()
    lower = upper = 0
    for pair in pairs.values():
        before, after = (pair[arm] for arm in ARMS)
        b, a = before[field], after[field]
        transitions[f"{b}->{a}"] += 1
        differences.append(
            {
                "suite": before["suite"],
                "user_task_id": before["user_task_id"],
                "delta": int(a is True) - int(b is True),
            }
        )
        lower += int(a is True) - int(b is not False)
        upper += int(a is not False) - int(b is True)
    n = len(pairs)
    if not n:
        return {
            "paired_episodes": 0,
            "delta": None,
            "ci95": None,
            "unknown_bounds": [None, None],
            "transitions": {},
        }
    strata = defaultdict(dict)
    for row in differences:
        strata[row["suite"]].setdefault(row["user_task_id"], []).append(row["delta"])
    rng = random.Random(0)
    samples = []
    for _ in range(2000):
        chosen = [
            cluster
            for suite in sorted(strata)
            for cluster in rng.choices(list(strata[suite].values()), k=len(strata[suite]))
        ]
        samples.append(sum(map(sum, chosen)) / sum(map(len, chosen)))
    samples.sort()
    return {
        "paired_episodes": n,
        "delta": sum(row["delta"] for row in differences) / n,
        "ci95": [
            samples[49] + 0.975 * (samples[50] - samples[49]),
            samples[1949] + 0.025 * (samples[1950] - samples[1949]),
        ],
        "ci_method": "suite_stratified_task_cluster_bootstrap_2000_seed0",
        "estimand": "defense minus default observed-positive all-attempt rate",
        "unknown_bounds": [lower / n, upper / n],
        "transitions": dict(transitions),
        "known_pairs": sum(
            pair[ARMS[0]][field] is not None and pair[ARMS[1]][field] is not None
            for pair in pairs.values()
        ),
    }


def summarize(rows: list[dict]) -> dict:
    clean = [row for row in rows if row["condition"] == "clean"]
    attacks = [row for row in rows if row["condition"] == "attack"]
    controls = {(row["suite"], row["user_task_id"], row["arm"]): row["utility"] for row in clean}
    task_keys = {(row["suite"], row["user_task_id"]) for row in clean}
    common = {key for key in task_keys if all(controls.get((*key, arm)) is True for arm in ARMS)}
    result = {
        "arms": {},
        "paired": {
            "clean_utility": paired_effect(clean, "utility"),
            "attack_utility": paired_effect(attacks, "utility"),
            "attack_success": paired_effect(attacks, "attack_success"),
        },
        "common_clean_success_tasks": len(common),
    }
    common_attacks = [row for row in attacks if (row["suite"], row["user_task_id"]) in common]
    result["paired"]["common_clean_attack_success"] = paired_effect(
        common_attacks, "attack_success"
    )
    for arm in ARMS:
        own = [row for row in rows if row["arm"] == arm]
        arm_clean = [row for row in clean if row["arm"] == arm]
        arm_attacks = [row for row in attacks if row["arm"] == arm]
        eligible = [
            row for row in arm_attacks if controls[(row["suite"], row["user_task_id"], arm)] is True
        ]
        matrix = Counter(f"U={row['utility']},A={row['attack_success']}" for row in arm_attacks)
        costs = {}
        for name, subset in {
            "all": own,
            "clean": arm_clean,
            "attack": arm_attacks,
            "completed": [row for row in own if row["status"] == "completed"],
            "utility_true": [row for row in own if row["utility"] is True],
        }.items():
            costs[name] = {
                "episodes": len(subset),
                "duration_seconds": distribution([row["duration_seconds"] for row in subset]),
                "available_prompt_tokens": sum(
                    row.get("usage", {}).get("prompt_tokens", 0) for row in subset
                ),
                "available_completion_tokens": sum(
                    row.get("usage", {}).get("completion_tokens", 0) for row in subset
                ),
                "server_peak_rss_bytes": max(
                    (row.get("server_memory", {}).get("peak_rss_bytes") or 0 for row in subset),
                    default=0,
                )
                or None,
            }
            for metric in (
                "native_decode_seconds",
                "native_decode_steps",
                "native_prefill_seconds",
                "native_prefill_tokens",
                "response_count",
                "request_error_count",
            ):
                costs[name][metric] = sum(
                    row.get("request_measurements", {}).get(metric) or 0 for row in subset
                )
        result["arms"][arm] = {
            "clean_utility": rate(arm_clean, "utility"),
            "attack_utility": rate(arm_attacks, "utility"),
            "attack_success": rate(arm_attacks, "attack_success"),
            "attack_success_own_clean_eligible": rate(eligible, "attack_success"),
            "attack_success_common_clean_eligible": rate(
                [row for row in common_attacks if row["arm"] == arm], "attack_success"
            ),
            "exposed_attack_success": rate(
                [row for row in arm_attacks if row.get("observed_injection_vectors")],
                "attack_success",
            ),
            "error_types": dict(
                Counter(row.get("error", {}).get("type") for row in own if row["status"] == "error")
            ),
            "U_x_A": dict(matrix),
            "costs": costs,
        }
    return result


def actual_sources(events: list[dict], payloads: dict, vectors: dict) -> list[dict]:
    """Certain carrier evidence with explicit ambiguity in identical-payload vector matches."""
    result = []
    seen = set()
    for event in events:
        if event["event"] != "request":
            continue
        messages = event["payload"]["messages"]
        calls = {
            call["id"]: call["function"]
            for message in messages
            if message["role"] == "assistant"
            for call in message.get("tool_calls", [])
        }
        for index, message in enumerate(messages):
            if message["role"] != "tool":
                continue
            candidates = exposed_vectors([message], payloads)
            key = (message["tool_call_id"], tuple(candidates))
            if not candidates or key in seen:
                continue
            seen.add(key)
            if message["tool_call_id"] not in calls:
                raise ValueError("Exposed tool result has no preceding call provenance")
            result.append(
                {
                    "matching_vector_candidates": candidates,
                    "vector_attribution": (
                        "unique_payload_match"
                        if len(candidates) == 1
                        else "ambiguous_payload_match"
                    ),
                    "request_index": event["index"],
                    "message_index": index,
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "function": calls[message["tool_call_id"]]["name"],
                    "arguments": calls[message["tool_call_id"]]["arguments"],
                    "source_mapping_candidates": {vector: vectors[vector] for vector in candidates},
                    "attribution_note": (
                        "Payload-only matches do not uniquely identify original fields; "
                        "carrier function and arguments are observed evidence."
                    ),
                }
            )
    return result


def collect(directory: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["status"] not in {"finalized", "finalized_with_errors"}:
        raise ValueError("Final analysis requires a finalized campaign")
    verify_frozen(manifest["identity_inputs"])
    plan = manifest["identity_inputs"]["plan"]
    inventory = json.loads((ROOT / "configs/prompt-defense-v1/source-inventory.json").read_text())
    rows = []
    tool_hashes = defaultdict(set)
    for entry in plan["schedule"]:
        episode_dir = directory / "episodes" / entry["episode_id"]
        compression = json.loads((episode_dir / "compression.json").read_text())
        for record in compression["artifacts"]:
            data = (episode_dir / record["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != record["compressed_sha256"]:
                raise ValueError("Compressed evidence digest mismatch")
            decoded = gzip.decompress(data)
            if hashlib.sha256(decoded).hexdigest() != record["original_sha256"]:
                raise ValueError("Original evidence digest mismatch")
        result = json.loads((episode_dir / "result.json").read_text())
        for key, value in entry.items():
            if result.get(key) != value:
                raise ValueError(f"Episode metadata mismatch: {key}")
        if result["status"] == "error" and (
            result["utility"] is not None or result["attack_success"] is not None
        ):
            raise ValueError("Error scores must remain unknown")
        trace = json.loads(read_evidence(episode_dir, "trace.json"))
        if result["identity"] != manifest["identity"] or trace["identity"] != manifest["identity"]:
            raise ValueError("Analysis identity mismatch")
        measurements, events = _request_measurements(episode_dir / "requests.jsonl")
        requests = [event for event in events if event["event"] == "request"]
        if len(requests) != result["requests"]:
            raise ValueError("Request count mismatch")
        for event in requests:
            payload = event["payload"]
            if payload["messages"][0] != {
                "role": "system",
                "content": plan["prompts"][entry["arm"]]["text"],
            }:
                raise ValueError("Actual request prompt differs from frozen arm")
            if payload.get("cache_prompt") is not False:
                raise ValueError("Prefix cache was enabled")
            tool_hashes[entry["suite"]].add(stable_hash(payload.get("tools")))
        sources = actual_sources(
            events, trace["attack_payloads"], inventory["suites"][entry["suite"]]["vectors"]
        )
        matched_vectors = sorted(
            {vector for source in sources for vector in source["matching_vector_candidates"]}
        )
        if matched_vectors != result["observed_injection_vectors"]:
            raise ValueError("Source matching disagrees with collection exposure")
        rows.append(
            {**entry, **result, "request_measurements": measurements, "actual_sources": sources}
        )
    if len(rows) != 600 or any(len(values) != 1 for values in tool_hashes.values()):
        raise ValueError("Final count or tool schema mismatch")
    return manifest, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=ROOT / "artifacts" / NAME)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / NAME)
    args = parser.parse_args()
    manifest, rows = collect(args.campaign)
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output / "episodes.json", rows)
    summary = {
        "campaign_identity": manifest["identity"],
        "analysis_sha256": digest(Path(__file__)),
        "overall": summarize(rows),
        "by_suite": {
            suite: summarize([row for row in rows if row["suite"] == suite])
            for suite in sorted({row["suite"] for row in rows})
        },
        "interpretation": (
            "Official U x A, not L x A. One seed; fixed templates; sources confounded."
        ),
    }
    atomic_json(args.output / "measurements.json", summary)
    review = args.output / "clean-refusal-review.csv"
    if not review.exists():
        with review.open("w", newline="") as handle:
            fields = (
                "episode_id",
                "identity",
                "suite",
                "arm",
                "user_task_id",
                "review_status",
                "refusal",
                "failure_category",
                "reviewer",
                "evidence",
                "notes",
                "secondary_labels",
            )
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                if row["condition"] == "clean":
                    writer.writerow(
                        {**{key: row.get(key, "") for key in fields}, "review_status": "pending"}
                    )


if __name__ == "__main__":
    main()
