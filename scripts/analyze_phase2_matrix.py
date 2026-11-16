"""Export reviewed or explicitly provisional measurements of the frozen formal matrix."""

from __future__ import annotations

import argparse
import csv
import gzip
import itertools
import json
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from phase2_collect import MATRIX, verify_archive
from phase2_matrix import OUTPUT, load_block, read

from agent_tradeoffs.analysis import ANNOTATION_FIELDS, FAILURE_CATEGORIES, distribution
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runtime import sha256_file

PUBLIC = Path("results/phase2-main-v1")
CONFIGURATIONS = tuple(itertools.product(("f16", "q8", "q4"), ("full", "truncate", "summary")))


def score(values):
    values = list(values)
    n = len(values)
    success = sum(v is True for v in values)
    unknown = sum(v is None for v in values)
    if any(v is not None and type(v) is not bool for v in values):
        raise ValueError("Binary outcomes must be Boolean or unknown")
    return {
        "n": n,
        "success": success,
        "failure": n - success - unknown,
        "unknown": unknown,
        "lower_bound": success / n if n else None,
        "upper_bound": (success + unknown) / n if n else None,
        "known_only_rate": success / (n - unknown) if n > unknown else None,
    }


def task_key(row):
    return row["suite"], row["task"]


def configuration(row):
    return row["precision"], row["policy"]


def labels(row):
    return {row["failure_category"], *re.split(r"[|;]", row["secondary_labels"])} - {""}


def repeated_calls(calls):
    signatures = [json.dumps((call["function"], call["args"]), sort_keys=True) for call in calls]
    return len(signatures) - len(set(signatures))


def paired_uncertainty(values):
    strata = {}
    for (suite, _), value in sorted(values.items()):
        strata.setdefault(suite, []).append(value)
    result = {
        "method": "task_paired_suite_stratified_percentile_bootstrap_2000_seed0",
        "scope": "Known task pairs; shared task families and seed variation are not modeled",
        "tasks_by_suite": {suite: len(v) for suite, v in strata.items()},
        "interval_95": None,
        "degenerate": None,
    }
    if not strata or any(len(v) < 2 for v in strata.values()):
        return result | {"status": "insufficient_tasks_within_suite"}
    rng = random.Random(0)
    n = len(values)
    samples = sorted(
        sum(sum(rng.choices(v, k=len(v))) for v in strata.values()) / n for _ in range(2000)
    )

    def quantile(p):
        position = (len(samples) - 1) * p
        lower = int(position)
        fraction = position - lower
        return samples[lower] * (1 - fraction) + samples[lower + 1] * fraction

    return result | {
        "status": "estimated",
        "interval_95": [quantile(0.025), quantile(0.975)],
        "degenerate": samples[0] == samples[-1],
    }


def annotation_index(block_id):
    path = PUBLIC / "reviews" / f"{block_id}.csv"
    if not path.exists():
        return {}, None
    rows = list(csv.DictReader(path.open()))
    indexed = {(r["run_id"], r["episode_id"]): r for r in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate review")
    for row in rows:
        if (
            row["review_status"] != "reviewed"
            or not row["evidence"].strip()
            or not row["reviewer"].strip()
        ):
            raise ValueError("Review needs evidence and reviewer")
        if row["refusal"] not in {"yes", "no", "unclear"}:
            raise ValueError("Invalid refusal annotation")
        if row["failure_category"] not in FAILURE_CATEGORIES | {"none"}:
            raise ValueError("Invalid failure classification")
    return indexed, sha256_file(path)


def load_rows():
    matrix = read(MATRIX)
    rows, blocks = [], []
    for block in matrix["blocks"]:
        block_id = block["block_id"]
        archive_dir = OUTPUT / "archives"
        inventory = archive_dir / f"{block_id}.manifest.json"
        if not inventory.exists():
            continue
        record = read(inventory)
        verify_archive(archive_dir / f"{block_id}.tar.gz", record)
        frozen = {r["path"]: r["sha256"] for r in record["members"]}

        def verified(path, compressed=False, frozen=frozen):
            if frozen.get(str(path)) != sha256_file(path):
                raise ValueError(f"Evidence differs from archived bytes: {path}")
            data = path.read_bytes()
            return json.loads(gzip.decompress(data) if compressed else data)

        _, plan, _ = load_block(block_id)
        campaign = verified(OUTPUT / block_id / "campaign.json")
        annotations, review_sha = annotation_index(block_id)
        seen = set()
        for run_index, entry in enumerate(plan["runs"]):
            directory = Path(entry["config"]["runner"]["output_root"]) / entry["run_id"]
            manifest = verified(directory / "manifest.json")
            samples = [verified(p) for p in sorted(directory.glob("deployment-memory-*.json"))]
            valid_peaks = [
                s["peak_rss_bytes"]
                for s in samples
                if s["error"] is None and s["peak_rss_bytes"] is not None
            ]
            for episode_index, episode in enumerate(manifest["episodes"]):
                path = directory / "episodes" / episode["episode_id"]
                result = verified(path / "result.json")
                trace = verified(path / "trace.json.gz", True)
                if (
                    result["identity"] != manifest["identity"]
                    or trace["identity"] != result["identity"]
                ):
                    raise ValueError("Episode identity mismatch")
                if result["status"] == "error" and (
                    result["utility"] is not None or result["attack_success"] is not None
                ):
                    raise ValueError("Error outcomes must remain unknown")
                key = entry["run_id"], episode["episode_id"]
                seen.add(key)
                review = annotations.get(key)
                if review and review["identity"] != result["identity"]:
                    raise ValueError("Stale review identity")
                memory = result["context_memory"]
                proposed = [v for v in memory["events"] if v["event"] == "request_view"]
                views = [
                    v
                    for v in memory["events"]
                    if v["event"] == "request_view" and v["status"] == "ready"
                ]
                visible = [
                    v["request_index"] for v in views if v["literal_payload_visible_vectors"]
                ]
                removed_after = [
                    v["request_index"]
                    for v in views
                    if not v["literal_payload_visible_vectors"] and v["previously_visible_vectors"]
                ]
                calls = [m["tool_call"] for m in trace["messages"] if m["role"] == "tool"]
                launches = [
                    launch["metadata"]
                    for launch in campaign["launches"]
                    if launch["role"] == entry["precision"]
                    and launch["metadata"]["pid"] == result["server_memory"]["pid"]
                    and launch["metadata"]["started_at"] <= result["started_at"]
                ]
                if not launches:
                    raise ValueError("Episode has no matching agent process launch")
                launch = max(launches, key=lambda x: x["started_at"])
                row = {
                    "block": block_id,
                    "cohort": plan["cohort"],
                    "run_id": entry["run_id"],
                    "episode_id": result["episode_id"],
                    "identity": result["identity"],
                    "precision": entry["precision"],
                    "policy": entry["policy"],
                    "suite": entry["suite"],
                    "task": result["user_task_id"],
                    "condition": result["condition"],
                    "goal": result["injection_task_id"],
                    "utility": result["utility"],
                    "attack_success": result["attack_success"],
                    "status": result["status"],
                    "started_at": result["started_at"],
                    "planned_run_position": run_index + 1,
                    "precision_position": plan["precision_order"].index(entry["precision"]) + 1,
                    "episode_position_in_pair": episode_index + 1,
                    "agent_process_started_at": launch["started_at"],
                    "agent_startup_seconds": launch["startup_seconds"],
                    "resume_observations": len(manifest.get("resume_runtime_observations", [])),
                    "interrupted_attempts": len(list((path / "interrupted_attempts").glob("*"))),
                    "error_type": (result.get("error") or {}).get("type"),
                    "seconds": result["duration_seconds"],
                    "requests": result["requests"],
                    "tool_calls": result["tool_calls"],
                    "tool_errors": result["tool_errors"],
                    "repeated_calls": repeated_calls(calls),
                    "compactions": memory["compactions"],
                    "context_overflow_reasons": [
                        v["reason"] for v in proposed if v["status"] == "overflow"
                    ],
                    "max_proposed_prompt_tokens": max(
                        (v["before_tokens"] for v in proposed), default=None
                    ),
                    "summary_seconds": memory["summary_seconds"],
                    "tokenizer_seconds": result["context_tokenizer_seconds"]
                    + memory["tokenizer_seconds"],
                    "agent_prompt_tokens": result["usage"]["prompt_tokens"],
                    "agent_completion_tokens": result["usage"]["completion_tokens"],
                    "agent_tokens": result["usage"]["total_tokens"],
                    "summary_prompt_tokens": memory["summary_usage"].get("prompt_tokens", 0),
                    "summary_completion_tokens": memory["summary_usage"].get(
                        "completion_tokens", 0
                    ),
                    "summary_tokens": memory["summary_usage"].get("total_tokens", 0),
                    "all_model_tokens": result["usage"]["total_tokens"]
                    + memory["summary_usage"].get("total_tokens", 0),
                    "agent_peak_rss_bytes": result["server_memory"]["peak_rss_bytes"],
                    "summary_peak_rss_bytes": result["summarizer_memory"]["peak_rss_bytes"],
                    "run_aggregate_peak_rss_bytes": max(valid_peaks) if valid_peaks else None,
                    "memory_sampling_errors": [s["error"] for s in samples if s["error"]],
                    "first_literal_exposure_request": min(visible) if visible else None,
                    "literal_exposure_requests": len(visible),
                    "literal_removal_after_exposure": bool(removed_after),
                    "max_sent_prompt_tokens": max((v["after_tokens"] for v in views), default=None),
                    "source": str(path),
                    "trace_sha256": frozen[str(path / "trace.json.gz")],
                    "result_sha256": frozen[str(path / "result.json")],
                    **{
                        field: review[field]
                        if review
                        else ("unreviewed" if field == "review_status" else "")
                        for field in ANNOTATION_FIELDS
                    },
                }
                rows.append(row)
        if set(annotations) - seen:
            raise ValueError("Review refers to an unplanned episode")
        blocks.append(
            {
                "block": block_id,
                "archive_sha256": record["archive_sha256"],
                "review_sha256": review_sha,
                "reviewed": len(annotations),
                "episodes": len(seen),
                "collection_elapsed_seconds": campaign["elapsed_seconds"],
                "runtime_launches": [
                    {
                        "role": launch["role"],
                        **{
                            k: launch["metadata"][k]
                            for k in ("started_at", "startup_seconds", "model_sha256")
                        },
                    }
                    for launch in campaign["launches"]
                ],
            }
        )
    keys = [(r["cohort"], configuration(r), task_key(r), r["condition"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate experimental cell")
    seen_launches = set()
    for row in sorted(rows, key=lambda r: r["started_at"]):
        key = row["block"], row["agent_process_started_at"]
        row["first_finalized_episode_on_process"] = key not in seen_launches
        seen_launches.add(key)
    return rows, blocks


def aggregates(rows):
    clean_tasks = {
        config: {
            task_key(r)
            for r in rows
            if configuration(r) == config and r["condition"] == "clean" and r["utility"] is True
        }
        for config in CONFIGURATIONS
    }
    common = set.intersection(*clean_tasks.values())
    reviewed_clean_tasks = {
        config: {
            task_key(r)
            for r in rows
            if configuration(r) == config
            and r["condition"] == "clean"
            and r["utility"] is True
            and r["review_status"] == "reviewed"
            and "scorer_false_positive" not in labels(r)
        }
        for config in CONFIGURATIONS
    }
    reviewed_common = set.intersection(*reviewed_clean_tasks.values())
    result = []
    for config in CONFIGURATIONS:
        subset = [r for r in rows if configuration(r) == config]
        clean = [r for r in subset if r["condition"] == "clean"]
        attacked = [r for r in subset if r["condition"] == "attack"]
        own = [r for r in attacked if task_key(r) in clean_tasks[config]]
        common_attack = [r for r in attacked if task_key(r) in common]
        matched_clean = [r for r in clean if task_key(r) in common]
        reviewed = [r for r in clean if r["review_status"] == "reviewed"]
        refusal_values = []
        for row in clean:
            refusal_values.append(
                None
                if row["review_status"] != "reviewed"
                or (
                    "unnecessary_refusal" not in labels(row)
                    and (
                        row["refusal"] == "unclear"
                        or (row["refusal"] == "yes" and "unclear" in labels(row))
                    )
                )
                else "unnecessary_refusal" in labels(row)
            )
        result.append(
            {
                "precision": config[0],
                "policy": config[1],
                "n": len(subset),
                "clean_utility": score(r["utility"] for r in clean),
                "attacked_utility": score(r["utility"] for r in attacked),
                "attack_success_all": score(r["attack_success"] for r in attacked),
                "attack_success_own_clean": score(r["attack_success"] for r in own),
                "attack_success_common_clean": score(r["attack_success"] for r in common_attack),
                "own_clean_tasks": sorted(clean_tasks[config]),
                "common_clean_tasks": sorted(common),
                "reviewed_clean_eligibility_sensitivity": {
                    "eligible_tasks": sorted(reviewed_clean_tasks[config]),
                    "common_tasks": sorted(reviewed_common),
                    "own_clean_ASR": score(
                        r["attack_success"]
                        for r in attacked
                        if task_key(r) in reviewed_clean_tasks[config]
                    ),
                    "common_clean_ASR": score(
                        r["attack_success"] for r in attacked if task_key(r) in reviewed_common
                    ),
                    "note": "Requires reviewed clean success and excludes labeled scorer false "
                    "positives; partial review limits coverage. Official scores are unchanged.",
                },
                "reviewed_scorer_false_positives": {
                    condition: sum(
                        r["review_status"] == "reviewed" and "scorer_false_positive" in labels(r)
                        for r in subset
                        if r["condition"] == condition
                    )
                    for condition in ("clean", "attack")
                },
                "unnecessary_refusal": score(refusal_values),
                "reviewed_clean": len(reviewed),
                "errors": dict(Counter(r["error_type"] for r in subset if r["error_type"])),
                "reviewed_failure_categories": {
                    condition: dict(
                        Counter(
                            r["failure_category"]
                            for r in subset
                            if r["condition"] == condition and r["review_status"] == "reviewed"
                        )
                    )
                    for condition in ("clean", "attack")
                },
                "reviewed_secondary_labels": {
                    condition: dict(
                        Counter(
                            label
                            for r in subset
                            if r["condition"] == condition and r["review_status"] == "reviewed"
                            for label in re.split(r"[|;]", r["secondary_labels"])
                            if label
                        )
                    )
                    for condition in ("clean", "attack")
                },
                "context": {
                    "clean_compacted": sum(r["compactions"] > 0 for r in clean),
                    "attack_compacted": sum(r["compactions"] > 0 for r in attacked),
                    "attacked_literal_exposed": sum(
                        r["literal_exposure_requests"] > 0 for r in attacked
                    ),
                    "attacked_no_literal_exposure": sum(
                        r["literal_exposure_requests"] == 0 for r in attacked
                    ),
                    "attacked_removal_after_exposure": sum(
                        r["literal_removal_after_exposure"] for r in attacked
                    ),
                    "attack_success_exposed": score(
                        r["attack_success"] for r in attacked if r["literal_exposure_requests"] > 0
                    ),
                    "repeated_calls": sum(r["repeated_calls"] for r in subset),
                    "overflow_reasons": {
                        condition: dict(
                            Counter(
                                reason
                                for r in subset
                                if r["condition"] == condition
                                for reason in r["context_overflow_reasons"]
                            )
                        )
                        for condition in ("clean", "attack")
                    },
                },
                "all_outcome_costs": {
                    k: distribution(r[k] for r in subset)
                    for k in (
                        "seconds",
                        "agent_tokens",
                        "agent_prompt_tokens",
                        "agent_completion_tokens",
                        "summary_tokens",
                        "summary_prompt_tokens",
                        "summary_completion_tokens",
                        "all_model_tokens",
                        "summary_seconds",
                        "tokenizer_seconds",
                        "agent_peak_rss_bytes",
                        "summary_peak_rss_bytes",
                        "run_aggregate_peak_rss_bytes",
                    )
                },
                "common_clean_costs": {
                    k: distribution(r[k] for r in matched_clean)
                    for k in ("seconds", "all_model_tokens", "run_aggregate_peak_rss_bytes")
                },
                "common_clean_cost_coverage": {
                    "expected": len(matched_clean),
                    "complete": sum(
                        all(
                            r[k] is not None
                            for k in (
                                "seconds",
                                "all_model_tokens",
                                "run_aggregate_peak_rss_bytes",
                            )
                        )
                        and not r["memory_sampling_errors"]
                        for r in matched_clean
                    ),
                },
                "latency_by_status": {
                    status: distribution(r["seconds"] for r in subset if r["status"] == status)
                    for status in ("completed", "error")
                },
                "latency_by_process_position": {
                    position: distribution(
                        r["seconds"]
                        for r in subset
                        if r["first_finalized_episode_on_process"] == first
                    )
                    for position, first in (("first_finalized", True), ("later_finalized", False))
                },
            }
        )
    return result


def paired(values_a, values_b):
    if set(values_a) != set(values_b):
        raise ValueError("Paired comparison needs the same task set")
    known = [key for key in values_a if values_a[key] is not None and values_b[key] is not None]
    gain = sum(values_b[k] is True and values_a[k] is False for k in known)
    loss = sum(values_b[k] is False and values_a[k] is True for k in known)
    lower = sum(int(values_b[k] is True) - int(values_a[k] is not False) for k in values_a)
    upper = sum(int(values_b[k] is not False) - int(values_a[k] is True) for k in values_a)
    return {
        "n": len(values_a),
        "known_pairs": len(known),
        "unknown_pairs": len(values_a) - len(known),
        "false_to_true": gain,
        "true_to_false": loss,
        "delta_known": (gain - loss) / len(known) if known else None,
        "delta_unknown_bounds": [lower / len(values_a), upper / len(values_a)]
        if values_a
        else None,
        "uncertainty": paired_uncertainty({k: int(values_b[k]) - int(values_a[k]) for k in known}),
    }


def comparisons(rows):
    clean = {
        config: {
            task_key(r): r["utility"]
            for r in rows
            if configuration(r) == config and r["condition"] == "clean"
        }
        for config in CONFIGURATIONS
    }
    attacked = {
        config: {
            task_key(r): r["attack_success"]
            for r in rows
            if configuration(r) == config and r["condition"] == "attack"
        }
        for config in CONFIGURATIONS
    }
    effects = []
    for a, b in itertools.combinations(CONFIGURATIONS, 2):
        common = {k for k in clean[a] if clean[a][k] is True and clean[b][k] is True}
        effects.append(
            {
                "a": a,
                "b": b,
                "clean_utility": paired(clean[a], clean[b]),
                "attack_success_all": paired(attacked[a], attacked[b]),
                "attack_success_common_clean": paired(
                    {k: attacked[a][k] for k in common}, {k: attacked[b][k] for k in common}
                ),
                "common_clean_tasks": sorted(common),
            }
        )
    interactions = []
    all_common = {
        k for k in clean[CONFIGURATIONS[0]] if all(clean[c][k] is True for c in CONFIGURATIONS)
    }
    for precision, policy in itertools.product(("q8", "q4"), ("truncate", "summary")):
        cells = ((precision, policy), (precision, "full"), ("f16", policy), ("f16", "full"))
        for name, values, selected in (
            ("clean_utility", clean, set(clean[cells[0]])),
            ("attack_success_common_clean", attacked, all_common),
        ):
            known = [k for k in selected if all(values[c][k] is not None for c in cells)]
            differences = {
                k: int(values[cells[0]][k])
                - int(values[cells[1]][k])
                - int(values[cells[2]][k])
                + int(values[cells[3]][k])
                for k in known
            }
            interactions.append(
                {
                    "precision": precision,
                    "policy": policy,
                    "outcome": name,
                    "n": len(selected),
                    "known_tasks": len(known),
                    "difference_in_differences": sum(differences.values()) / len(known)
                    if known
                    else None,
                    "uncertainty": paired_uncertainty(differences),
                }
            )
    return effects, interactions


def dominance(configs):
    vectors = {}
    for c in configs:
        capability = c["clean_utility"]
        security = c["attack_success_common_clean"]
        costs = c["common_clean_costs"]
        if not capability["n"] or capability["unknown"] or not security["n"] or security["unknown"]:
            continue
        coverage = c["common_clean_cost_coverage"]
        if coverage["expected"] != security["n"] or coverage["complete"] != security["n"]:
            continue
        vector = [
            -capability["lower_bound"],
            security["lower_bound"],
            *(
                costs[k]["median"]
                for k in ("seconds", "all_model_tokens", "run_aggregate_peak_rss_bytes")
            ),
        ]
        if any(v is None for v in vector):
            continue
        vectors[c["precision"], c["policy"]] = vector
    edges = [
        {"dominates": a, "dominated": b}
        for a, x in vectors.items()
        for b, y in vectors.items()
        if a != b
        and all(u <= v for u, v in zip(x, y, strict=True))
        and any(u < v for u, v in zip(x, y, strict=True))
    ]
    return {
        "kind": "observed_descriptive_dominance_not_statistical_certainty",
        "dimensions": [
            "clean_success_all_higher",
            "ASR_on_all_nine_common_clean_lower",
            "common_clean_median_seconds_lower",
            "common_clean_median_tokens_lower",
            "common_clean_median_run_aggregate_RSS_lower",
        ],
        "eligible_configurations": list(vectors),
        "edges": edges,
    }


def export(final=False):
    rows, blocks = load_rows()
    expected = sum(b["expected_episodes"] for b in read(MATRIX)["blocks"])
    if final and (len(rows) != expected or any(r["review_status"] != "reviewed" for r in rows)):
        raise ValueError("Final export requires every episode and complete evidence-bound review")
    if final:
        for block in blocks:
            block_id = block["block"]
            _, _, plan_path = load_block(block_id)
            subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_phase2_spike.py",
                    "--plan",
                    str(plan_path),
                    "--annotations",
                    str(PUBLIC / "reviews" / f"{block_id}.csv"),
                    "--output",
                    str(PUBLIC / "reviews" / f"{block_id}.verification.json"),
                ],
                check=True,
            )
    OUT = PUBLIC / "analysis"
    OUT.mkdir(parents=True, exist_ok=True)
    cohorts = {}
    for cohort in ("main", "confirmation"):
        selected = [r for r in rows if r["cohort"] == cohort]
        if selected:
            configs = aggregates(selected)
            effects, interactions = comparisons(selected)
            task_groups = {
                key: [r for r in selected if task_key(r) == key]
                for key in {task_key(r) for r in selected}
            }
            reviewed_tasks = {
                key
                for key, group in task_groups.items()
                if all(r["review_status"] == "reviewed" for r in group)
            }
            flagged_tasks = {
                task_key(r)
                for r in selected
                if r["review_status"] == "reviewed" and "scorer_false_positive" in labels(r)
            }
            eligible = reviewed_tasks - flagged_tasks
            sensitivity_rows = [r for r in selected if task_key(r) in eligible]
            sensitivity_configs = aggregates(sensitivity_rows) if sensitivity_rows else []
            cohorts[cohort] = {
                "episodes": len(selected),
                "tasks": len({task_key(r) for r in selected}),
                "configurations": configs,
                "paired_comparisons": effects,
                "interactions": interactions,
                "dominance": dominance(configs),
                "scorer_sensitivity": {
                    "kind": "exclude_whole_flagged_tasks_from_fully_reviewed_subset",
                    "included_tasks": sorted(eligible),
                    "excluded_flagged_tasks": sorted(flagged_tasks),
                    "fully_reviewed_tasks": sorted(reviewed_tasks),
                    "configurations": sensitivity_configs,
                    "dominance": dominance(sensitivity_configs),
                    "note": "Descriptive sensitivity to known permissive checkers; exclusion "
                    "is applied to every arm of a task and never alters the official collection.",
                },
            }
    report = {
        "status": "final_reviewed" if final else "provisional",
        "expected_episodes": expected,
        "included_episodes": len(rows),
        "reviewed_episodes": sum(r["review_status"] == "reviewed" for r in rows),
        "matrix_sha256": sha256_file(MATRIX),
        "analysis_sha256": sha256_file(Path(__file__)),
        "blocks": blocks,
        "limits": [
            "Main and confirmation stay separate; costs include errors unless common-clean.",
            "Unknown bounds describe incomplete outcomes, not statistical confidence intervals.",
            "Literal exposure/removal is not semantic retention or causal evidence of resistance.",
            "Run aggregate RSS spans a clean/attack pair and both resident services; "
            "it may double-count shared pages.",
            "Costs describe finalized attempts; interrupted attempts are separately counted. "
            "First-finalized process position records ordering, not a verified cold-cache state.",
            "Tasks share environments/families; paired differences and single-seed patterns "
            "do not establish broad population effects.",
            "Paired bootstrap intervals resample known tasks within suites; singleton suites "
            "have no interval. A degenerate interval does not establish equivalence.",
        ],
        "cohorts": cohorts,
    }
    atomic_json(OUT / "measurements.json", report)
    atomic_json(OUT / "episodes.json", rows)
    if rows:
        with (OUT / "episodes.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
                )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "included_episodes", "expected_episodes", "reviewed_episodes")
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true")
    export(parser.parse_args().final)
