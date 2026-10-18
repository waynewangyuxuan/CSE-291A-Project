"""Offline, versioned supplement to the frozen first-wave analyzer (no inference).

Run only after all 600 rows and all 120 qualitative clean reviews are complete.
Uses official U/A unchanged. Cost intervals resample task clusters within suite.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from agent_tradeoffs.prompt_defense_analysis import ARMS, paired_effect, rate

ROOT = Path(__file__).resolve().parents[2]
if ROOT.name == "results":
    ROOT = ROOT.parent
CAMPAIGN = ROOT / "artifacts/prompt-defense-first-wave-v1"
OUTPUT = ROOT / "results/prompt-defense-first-wave-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct95(values):
    values = sorted(values)

    def q(p):
        index = p * (len(values) - 1)
        lower = int(index)
        return values[lower] + (index - lower) * (
            values[min(lower + 1, len(values) - 1)] - values[lower]
        )

    return [q(0.025), q(0.975)]


def numeric_effect(rows, value):
    pairs = defaultdict(dict)
    for row in rows:
        assert row["arm"] not in pairs[row["pair_id"]]
        pairs[row["pair_id"]][row["arm"]] = row
    assert all(set(pair) == set(ARMS) for pair in pairs.values())
    clusters = defaultdict(dict)
    differences, totals = [], [0, 0]
    for pair in pairs.values():
        before, after = [pair[arm] for arm in ARMS]
        b, a = value(before), value(after)
        if b is None or a is None:
            continue
        delta = a - b
        differences.append(delta)
        totals[0] += b
        totals[1] += a
        clusters[before["suite"]].setdefault(before["user_task_id"], []).append(delta)
    rng = random.Random(0)
    samples = []
    if differences:
        for _ in range(2000):
            chosen = [
                cluster
                for suite in sorted(clusters)
                for cluster in rng.choices(list(clusters[suite].values()), k=len(clusters[suite]))
            ]
            samples.append(sum(map(sum, chosen)) / sum(map(len, chosen)))
    return {
        "paired_episodes": len(pairs),
        "known_pairs": len(differences),
        "mean_delta_defense_minus_default": sum(differences) / len(differences)
        if differences
        else None,
        "ci95": pct95(samples) if samples else None,
        "known_pair_totals": dict(zip(ARMS, totals, strict=True)),
        "ci_method": "suite_stratified_task_cluster_bootstrap_2000_seed0",
    }


def labels(row):
    return {row["failure_category"]} | set(filter(None, row["secondary_labels"].split("|")))


def refusal_summary(subset):
    counts = Counter(r["refusal"] for r in subset)
    n = len(subset)
    positive = sum("unnecessary_refusal" in labels(r) for r in subset)
    unknown = sum(
        "unnecessary_refusal" not in labels(r)
        and (r["refusal"] == "unclear" or (r["refusal"] == "yes" and "unclear" in labels(r)))
        for r in subset
    )
    known = n - unknown
    return {
        "reviewed": n,
        "refusal_labels": dict(counts),
        "unnecessary_refusals": positive,
        "indeterminate_unnecessary_refusals": unknown,
        "observed_incidence": positive / n if n else None,
        "unknown_bounds": [positive / n, (positive + unknown) / n] if n else [None, None],
        "known_behavior_denominator": known,
        "known_behavior_rate": positive / known if known else None,
        "primary_categories": dict(Counter(r["failure_category"] for r in subset)),
    }


def outcome_summary(rows):
    return {
        "arms": {
            arm: {
                "episodes": len(own := [r for r in rows if r["arm"] == arm]),
                "utility": rate(own, "utility"),
                "attack_success": rate(own, "attack_success"),
                "exposed": sum(bool(r["observed_injection_vectors"]) for r in own),
                "errors": dict(Counter(r["error"]["type"] for r in own if r["status"] == "error")),
            }
            for arm in ARMS
        },
        "paired": {field: paired_effect(rows, field) for field in ["utility", "attack_success"]},
    }


def source_response_class(row, returned_indices):
    if not row["actual_sources"]:
        return "not_literal_exposed"
    if any(source["request_index"] in returned_indices for source in row["actual_sources"]):
        return "response_backed"
    return "only_requests_without_returned_response"


def main():
    manifest = json.loads((CAMPAIGN / "manifest.json").read_text())
    assert manifest["status"] in ["finalized", "finalized_with_errors"]
    rows = json.loads((OUTPUT / "episodes.json").read_text())
    assert len(rows) == 600 and len({r["episode_id"] for r in rows}) == 600
    assert {r["identity"] for r in rows} == {manifest["identity"]}
    assert [r["episode_id"] for r in rows] == [r["episode_id"] for r in manifest["episodes"]]
    reviews = list(csv.DictReader((OUTPUT / "clean-refusal-review.csv").open()))
    clean = {r["episode_id"]: r for r in rows if r["condition"] == "clean"}
    assert len(reviews) == len(clean) == 120
    assert len({r["episode_id"] for r in reviews}) == 120
    assert {r["episode_id"] for r in reviews} == set(clean)
    allowed = {
        "no_failure",
        "unnecessary_refusal",
        "task_understanding",
        "tool_use",
        "planning",
        "infrastructure",
        "other",
        "unclear",
    }
    for review in reviews:
        row = clean[review["episode_id"]]
        assert all(str(row[k]) == review[k] for k in ["identity", "suite", "arm", "user_task_id"])
        assert review["review_status"] == "reviewed"
        assert review["refusal"] in ["yes", "no", "unclear"]
        assert review["failure_category"] in allowed
        assert review["reviewer"].startswith("agent-trace-review")
        assert review["episode_id"] in review["evidence"] and review["notes"]
        assert set(filter(None, review["secondary_labels"].split("|"))) <= allowed
        assert "no_failure" not in labels(review) or labels(review) == {"no_failure"}
        if "unnecessary_refusal" in labels(review):
            assert review["refusal"] == "yes" and row["utility"] is not True
        if row["status"] == "error":
            assert review["refusal"] == "unclear" and review["failure_category"] == "infrastructure"
    attacks = [r for r in rows if r["condition"] == "attack"]
    strata = sorted({(r["suite"], r["attack_style"], r["injection_task_id"]) for r in attacks})
    source = {}
    for arm in ARMS:
        own = [r for r in attacks if r["arm"] == arm]
        exposed = [r for r in own if r["observed_injection_vectors"]]
        carriers = defaultdict(set)
        ambiguous = set()
        for row in own:
            for event in row["actual_sources"]:
                assert event["role"] == "tool"
                carriers[event["function"]].add(row["episode_id"])
                if event["vector_attribution"] == "ambiguous_payload_match":
                    ambiguous.add(row["episode_id"])
        source[arm] = {
            "attack_episodes": len(own),
            "exposed_episodes": len(exposed),
            "unexposed_episodes": len(own) - len(exposed),
            "ambiguous_origin_episodes": len(ambiguous),
            "carrier_function_episode_counts": {k: len(v) for k, v in sorted(carriers.items())},
            "note": (
                "Nonexclusive carrier counts; literal exposure, not resistance. "
                "API role=tool; Qwen template renders <tool_response> inside a user segment. "
                "Source, suite and goal are confounded."
            ),
        }
    metrics = {
        "duration_seconds": lambda r: r["duration_seconds"],
        "tool_calls": lambda r: r["tool_calls"],
        "requests": lambda r: r["requests"],
        "available_prompt_tokens": lambda r: r.get("usage", {}).get("prompt_tokens"),
        "available_completion_tokens": lambda r: r.get("usage", {}).get("completion_tokens"),
    }
    for metric in ["request_latency_seconds", "native_decode_seconds", "native_prefill_seconds"]:
        metrics[metric] = lambda r, key=metric: r["request_measurements"].get(key)
    pair_rows = defaultdict(dict)
    for row in rows:
        pair_rows[row["pair_id"]][row["arm"]] = row
    prefix_deltas = []
    returned_request_indices = {}
    checked = 0
    unavailable = []
    for pair in pair_rows.values():
        first = []
        usage = []
        for arm in ARMS:
            d = CAMPAIGN / "episodes" / pair[arm]["episode_id"]
            path = d / "requests.jsonl.gz"
            events = (
                [
                    json.loads(line)
                    for line in gzip.decompress(path.read_bytes()).decode().splitlines()
                ]
                if path.exists()
                else []
            )
            returned_request_indices[pair[arm]["episode_id"]] = {
                event["index"] for event in events if event["event"] == "response"
            }
            request = next((e for e in events if e["event"] == "request" and e["index"] == 0), None)
            payload = json.loads(json.dumps(request["payload"])) if request else None
            if payload is not None:
                payload["messages"][0] = {
                    "role": "system",
                    "content": "<arm-specific-frozen-system-prompt>",
                }
            first.append(payload)
            response = next(
                (e for e in events if e["event"] == "response" and e["index"] == 0), None
            )
            usage.append(
                response["response"].get("usage", {}).get("prompt_tokens") if response else None
            )
        if any(payload is None for payload in first):
            unavailable.append({arm: pair[arm]["episode_id"] for arm in ARMS})
            continue
        assert first[0] == first[1], "First paired requests differ beyond system prompt"
        checked += 1
        if all(isinstance(v, int) for v in usage):
            prefix_deltas.append(usage[1] - usage[0])
    for arm in ARMS:
        classification = Counter(
            source_response_class(row, returned_request_indices[row["episode_id"]])
            for row in attacks
            if row["arm"] == arm
        )
        source[arm]["response_evidence_classes"] = dict(classification)
        source[arm]["response_evidence_note"] = (
            "The frozen exposure field records literal payloads in submitted requests. "
            "Response-backed means at least one exposed request has a corresponding "
            "raw model response; "
            "it does not establish attention, understanding or resistance. "
            "A request without a response may be rejected or may have unobserved compute."
        )
    output = {
        "campaign_identity": manifest["identity"],
        "support_sha256": sha(Path(__file__)),
        "inputs": {
            name: sha(OUTPUT / name)
            for name in ["episodes.json", "measurements.json", "clean-refusal-review.csv"]
        },
        "by_suite_template_goal": {
            "/".join(key): outcome_summary(
                [
                    r
                    for r in attacks
                    if (r["suite"], r["attack_style"], r["injection_task_id"]) == key
                ]
            )
            for key in strata
        },
        "clean_review": {
            "arms": {arm: refusal_summary([r for r in reviews if r["arm"] == arm]) for arm in ARMS},
            "by_suite_arm": {
                suite: {
                    arm: refusal_summary(
                        [r for r in reviews if r["suite"] == suite and r["arm"] == arm]
                    )
                    for arm in ARMS
                }
                for suite in sorted({r["suite"] for r in reviews})
            },
            "scope": (
                "Predetermined all 120 clean traces; qualitative agent inspection, "
                "not independent human annotation or a validated LLM judge. "
                "Broad factual-premise declines may qualify; no safety-policy motive is inferred."
            ),
        },
        "source": source,
        "paired_costs": {
            subset: {name: numeric_effect(selected, fn) for name, fn in metrics.items()}
            for subset, selected in {
                "all": rows,
                "clean": list(clean.values()),
                "attack": attacks,
            }.items()
        },
        "first_paired_requests": {
            "planned_pairs": len(pair_rows),
            "checked_pairs": checked,
            "unavailable_pairs": unavailable,
            "same_payload_except_system_among_checked": True,
            "first_returned_usage_known_pairs": len(prefix_deltas),
            "prompt_token_delta_min": min(prefix_deltas) if prefix_deltas else None,
            "prompt_token_delta_max": max(prefix_deltas) if prefix_deltas else None,
            "prompt_token_delta_mean": sum(prefix_deltas) / len(prefix_deltas)
            if prefix_deltas
            else None,
        },
        "cost_limitations": (
            "Available returned usage and native timings omit failed-request compute. "
            "Wall duration includes client waits; process RSS is shared warm server memory. "
            "Paired order balances drift but trajectory divergence changes workload."
        ),
    }
    (OUTPUT / "supplementary-measurements.json").write_text(json.dumps(output, indent=2) + "\n")
    print(
        json.dumps(
            {
                "complete_clean_reviews": len(reviews),
                "matched_first_requests": checked,
                "output": str(OUTPUT / "supplementary-measurements.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
