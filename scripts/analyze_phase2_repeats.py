"""Export mechanical, provisional Full-context repeat metrics without model execution."""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import comb
from pathlib import Path

import audit_phase2_spike as auditor
from analyze_phase2_seed_diagnostics import verify_sampling

from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.runner import plan_episodes
from agent_tradeoffs.runtime import sha256_file

PRECISIONS = ("f16", "q8", "q4")
STATES = ("FINALIZED", "MISSING", "PARTIAL", "INTERRUPTED")
require = auditor.require


@dataclass
class Evidence:
    repository: Path
    hashes: dict[str, str] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)

    def path(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.repository / path

    def label(self, path):
        path = self.path(path).resolve()
        return (
            str(path.relative_to(self.repository))
            if path.is_relative_to(self.repository)
            else str(path)
        )

    def read(self, path, expected=None):
        path = self.path(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        previous = self.hashes.setdefault(self.label(path), digest)
        require(previous == digest, f"Evidence changed during analysis: {path}")
        require(expected is None or expected == digest, f"Evidence hash differs: {path}")
        require(
            self.pins.get(self.label(path), digest) == digest, f"Frozen source hash differs: {path}"
        )
        return data

    def json(self, path, expected=None):
        return json.loads(self.read(path, expected))

    def verify_snapshot(self):
        for path, digest in self.hashes.items():
            require(
                sha256_file(self.path(path)) == digest, f"Evidence changed during analysis: {path}"
            )


def identity(config, episodes, implementation, runtime):
    return stable_hash(
        {
            "config": config.identity_config(),
            "episodes": episodes,
            "implementation": implementation,
            "runtime": runtime,
            "summarizer_runtime": None,
        }
    )


def verify_manifest(manifest, config, run_id, implementation, runtime):
    saved = copy.deepcopy(manifest["config"])
    saved["runner"]["server_pid"] = config.runner.server_pid
    episodes = plan_episodes(config)
    require(
        saved == config.model_dump(mode="json")
        and manifest["run_id"] == run_id
        and manifest["episodes"] == episodes
        and manifest["implementation"] == implementation
        and manifest["runtime"]["semantic_identity"] == runtime
        and manifest["summarizer_runtime"] is None
        and manifest["identity"] == identity(config, episodes, implementation, runtime),
        "Pair manifest identity/config/runtime differs",
    )


def verify_episode_identity(value, episode, run_identity):
    require(
        value["identity"] == run_identity and all(value[k] == v for k, v in episode.items()),
        "Episode identity or clean/attack task/goal differs",
    )


def evidence_text(source, path):
    zipped = path.with_name(path.name + ".gz")
    if path.exists():
        data = source.read(path)
        if zipped.exists():
            require(data == gzip.decompress(source.read(zipped)), "Shadow evidence differs")
        return data.decode()
    return gzip.decompress(source.read(zipped)).decode() if zipped.exists() else None


def traffic_records(text, finalized):
    records, truncated = [], False
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            require(not finalized and index == len(lines) - 1, "Invalid request log record")
            truncated = True
    return records, truncated


def attempt(source, path, config, episode, run_identity, *, archived=False):
    present = path.exists() and any(p.is_file() for p in path.iterdir())
    finalized = (path / "compression.json").exists()
    values = {}
    incomplete = []
    for name in ("result", "trace"):
        text = evidence_text(source, path / f"{name}.json")
        if text is not None:
            try:
                values[name] = json.loads(text)
            except json.JSONDecodeError:
                require(not finalized, f"Invalid finalized {name}")
                incomplete.append(name)
            else:
                verify_episode_identity(values[name], episode, run_identity)
    result, trace = values.get("result", {}), values.get("trace", {})
    traffic_text = evidence_text(source, path / "requests.jsonl")
    traffic, truncated = traffic_records(traffic_text, finalized)
    verify_sampling(traffic, config.model, config.model.max_tokens)
    requests = [r for r in traffic if r["event"] == "request"]
    require(len({r["index"] for r in requests}) == len(requests), "Duplicate request index")
    for key in ("utility", "attack_success"):
        require(result.get(key) is None or type(result[key]) is bool, "Nonbinary result score")
    if result:
        require(result["status"] in {"completed", "error"}, "Unexpected result status")
        require(
            result["status"] != "error"
            or (result["utility"] is None and result["attack_success"] is None),
            "Error outcomes must remain unknown",
        )
    interrupted = archived or result.get("retryable", False)
    finalized = finalized and not interrupted
    state = (
        "INTERRUPTED"
        if interrupted
        else "FINALIZED"
        if finalized
        else "PARTIAL"
        if present
        else "MISSING"
    )
    if finalized:
        require(
            bool(result) and bool(trace) and (traffic_text is not None or result["requests"] == 0),
            "Incomplete finalized pair",
        )
    messages = trace.get("messages", [])
    executed = [m for m in messages if m["role"] == "tool"]
    calls = [m["tool_call"] for m in executed]
    errors = sum(m.get("error") is not None for m in executed)
    if finalized:
        require(
            result["tool_calls"] == len(calls) and result["tool_errors"] == errors,
            "Tool execution counts differ from trace",
        )
    return {
        "source": source.label(path),
        "state": state,
        "status": result.get("status"),
        "utility": result.get("utility") if finalized else None,
        "attack_success": result.get("attack_success") if finalized else None,
        "error_type": (result.get("error") or {}).get("type"),
        "retryable": bool(result.get("retryable")),
        "result_present": bool(result),
        "trace_present": bool(trace),
        "request_log_present": traffic_text is not None,
        "incomplete_artifacts": incomplete + (["requests_tail"] if truncated else []),
        "requests": len(requests) if traffic_text is not None else None,
        "responses": sum(r["event"] == "response" for r in traffic)
        if traffic_text is not None
        else None,
        "proposed_tool_calls": sum(len(m.get("tool_calls") or []) for m in messages)
        if trace
        else None,
        "executed_tool_calls": len(calls) if trace else None,
        "tool_errors": errors if trace else None,
        "successful_tool_returns": len(calls) - errors if trace else None,
        "repeated_tool_calls": len(calls)
        - len({stable_hash({"function": c["function"], "args": c["args"]}) for c in calls})
        if trace
        else None,
        "tools": dict(sorted(Counter(c["function"] for c in calls).items())),
    }


def checked_episode(source, path, config, manifest, episode):
    compression = source.json(path / "compression.json")
    for item in compression["artifacts"]:
        relative = Path(item["path"])
        require(
            not relative.is_absolute() and ".." not in relative.parts, "Compression path escape"
        )
        source.read(path / relative, item["compressed_sha256"])
    verify_episode_identity(source.json(path / "result.json"), episode, manifest["identity"])
    verify_episode_identity(
        json.loads(evidence_text(source, path / "trace.json")), episode, manifest["identity"]
    )
    return auditor.verify_episode(path, config, manifest)


def load_source(source, entry, implementation, cache):
    binding = entry["source_pair"]
    key = binding["block"], binding["run_id"]
    if key in cache:
        original, manifest, checks, previous = cache[key]
        require(binding == previous, "Source pair binding differs between repeats")
        return original, manifest, checks
    plan = source.json(f"configs/phase2-main-v1/{binding['block']}.json")
    matches = [r for r in plan["runs"] if r["run_id"] == binding["run_id"]]
    require(len(matches) == 1, "Source plan pair missing or duplicated")
    original = matches[0]
    require(
        all(
            entry[k] == original[k]
            for k in ("run_id", "suite", "user_task_id", "precision", "policy")
        ),
        "Source plan task/precision differs",
    )
    config = ExperimentConfig.model_validate(original["config"])
    require(
        config.model.seed == 17 and config.context.policy == "full", "Expected formal seed17 Full"
    )
    directory = source.path(config.runner.output_root) / entry["run_id"]
    manifest = source.json(directory / "manifest.json", binding["manifest_sha256"])
    verify_manifest(manifest, config, entry["run_id"], implementation, binding["runtime"])
    require(manifest["identity"] == binding["identity"], "Source pair identity differs")
    require(binding["summarizer_runtime"] is None, "Full study cannot use a summarizer")
    refs = {r["episode_id"]: r for r in binding["episodes"]}
    episodes = plan_episodes(config)
    require(
        len(refs) == len(binding["episodes"]) == 2
        and set(refs) == {e["episode_id"] for e in episodes},
        "Source pair episode coverage differs",
    )
    checks = {}
    for episode in episodes:
        ref = refs[episode["episode_id"]]
        path = directory / "episodes" / episode["episode_id"]
        require(
            source.path(ref["source"]).resolve() == path.resolve()
            and ref["condition"] == episode["condition"],
            "Source episode path/condition differs",
        )
        source.read(path / "result.json", ref["result_sha256"])
        source.read(path / "trace.json.gz", ref["trace_sha256"])
        checks[episode["episode_id"]] = checked_episode(source, path, config, manifest, episode)
    cache[key] = original, manifest, checks, binding
    return original, manifest, checks


def load_rows(root):
    root = Path(root).resolve()
    require(root.parent.name == "artifacts", "Study root must be under artifacts")
    source = Evidence(root.parent.parent)
    receipt_path = root / "receipt.json"
    receipt = source.json(receipt_path) if receipt_path.exists() else None
    if receipt:
        source.pins = {source.label(p): h for p, h in receipt["source_hashes"].items()}
    matrix_path = root / "configs/matrix.json"
    matrix, selection = source.json(matrix_path), source.json(root / "selection.json")
    require(matrix["campaign_id"] == root.name, "Study identity differs")
    tasks = [(t["suite"], t["task"]) for t in selection["tasks"]]
    seeds = selection["seeds"]
    require(
        bool(tasks)
        and len(tasks) == len(set(tasks))
        and bool(seeds)
        and len(seeds) == len(set(seeds))
        and all(type(s) is int and 0 <= s < 2**31 and s not in {17, 29} for s in seeds),
        "Duplicate/invalid task or fresh seed selection",
    )
    qualified = source.json(selection["qualification_source"], selection["qualification_sha256"])
    require(
        set(tasks)
        == {(t["suite"], t["task"]) for t in qualified["tasks"] if t["eligible"] is True},
        "Selected tasks differ from qualified task set",
    )
    for path, digest in qualified.get("source_hashes", {}).items():
        source.read(path, digest)
    qualification_keys = [(t["suite"], t["task"]) for t in qualified["tasks"]]
    require(len(qualification_keys) == len(set(qualification_keys)), "Duplicate qualification task")
    if receipt:
        require(
            source.json(receipt["selection_source"], receipt["selection_sha256"]) == selection,
            "Selection differs from frozen receipt",
        )
    blocks = matrix["blocks"]
    require(len({b["block_id"] for b in blocks}) == len(blocks), "Duplicate block")
    rows, seen, cache = [], set(), {}
    expected = {(s, t, p, seed) for s, t in tasks for p in PRECISIONS for seed in seeds}
    for block in blocks:
        block_id = block["block_id"]
        require(Path(block_id).name == block_id, "Invalid block name")
        plan_path = source.path(block["plan"])
        require(plan_path.resolve().is_relative_to(root / "configs"), "Plan outside study configs")
        plan = source.json(plan_path, block["sha256"])
        require(
            plan["block_id"] == block_id
            and plan["seed"] in seeds
            and block["expected_episodes"] == plan["expected_episodes"] == 2 * len(plan["runs"]),
            "Plan identity/expected episode count differs",
        )
        require(
            0 < len(plan["task_order"]) <= selection["tasks_per_block"] <= 4
            and len({tuple(t) for t in plan["task_order"]}) == len(plan["task_order"])
            and set(plan["precision_order"]) == set(PRECISIONS),
            "Plan block task/precision coverage differs",
        )
        block_expected = {
            (s, t, p, plan["seed"]) for s, t in plan["task_order"] for p in PRECISIONS
        }
        block_seen = set()
        campaign_path = root / block_id / "campaign.json"
        if campaign_path.exists():
            campaign = source.json(campaign_path)
            require(
                campaign["identity"]
                == {
                    "plan_sha256": stable_hash(plan),
                    "implementation": matrix["implementation"],
                    "driver_sha256": matrix["driver_sha256"],
                    "matrix_sha256": source.hashes[source.label(matrix_path)],
                },
                "Campaign identity differs",
            )
        for entry in plan["runs"]:
            key = entry["suite"], entry["user_task_id"], entry["precision"], plan["seed"]
            require(key in expected and key not in seen, "Unselected or duplicate repeat")
            seen.add(key)
            block_seen.add(key)
            config = ExperimentConfig.model_validate(entry["config"])
            episodes = plan_episodes(config)
            require(
                config.model.seed == plan["seed"]
                and entry["policy"] == "full"
                and config.context.policy == "full"
                and config.experiment.mode == "paired"
                and config.experiment.suite == entry["suite"]
                and len(episodes) == 2
                and {e["condition"] for e in episodes} == {"clean", "attack"}
                and all(e["user_task_id"] == entry["user_task_id"] for e in episodes),
                "Planned seed/task/pair differs",
            )
            original, _, original_checks = load_source(
                source, entry, matrix["implementation"], cache
            )
            allowed = copy.deepcopy(original["config"])
            allowed["model"]["seed"] = plan["seed"]
            allowed["runner"]["output_root"] = str(root / block_id / "runs")
            allowed["runner"]["runtime_metadata_path"] = str(
                root / "runtime" / entry["precision"] / "server.json"
            )
            require(entry["config"] == allowed, "Derived config changes more than seed and paths")
            directory = config.runner.output_root / entry["run_id"]
            run_identity = identity(
                config, episodes, matrix["implementation"], entry["source_pair"]["runtime"]
            )
            manifest = None
            if (directory / "manifest.json").exists():
                manifest = source.json(directory / "manifest.json")
                verify_manifest(
                    manifest,
                    config,
                    entry["run_id"],
                    matrix["implementation"],
                    entry["source_pair"]["runtime"],
                )
                require(campaign_path.exists(), "Pair has no campaign identity")
            for episode in episodes:
                path = directory / "episodes" / episode["episode_id"]
                row = attempt(source, path, config, episode, run_identity)
                archived = []
                for previous in sorted((path / "interrupted_attempts").glob("*")):
                    require(previous.is_dir(), "Invalid interrupted attempt directory")
                    archived.append(
                        attempt(source, previous, config, episode, run_identity, archived=True)
                    )
                if row["state"] == "MISSING" and archived:
                    row["state"] = "INTERRUPTED"
                if row["state"] != "MISSING":
                    require(manifest is not None, "Episode has no pair manifest")
                if row["state"] == "FINALIZED":
                    checked = checked_episode(source, path, config, manifest, episode)
                    previous = original_checks[episode["episode_id"]]
                    for field_name in (
                        "attack_payload_sha256",
                        "initial_environment_sha256",
                        "tool_schema_hashes",
                    ):
                        if field_name == "tool_schema_hashes" and checked["requests"] == 0:
                            continue
                        require(
                            checked[field_name] == previous[field_name],
                            f"Frozen {field_name} differs",
                        )
                rows.append(
                    {
                        "block": block_id,
                        "run_id": entry["run_id"],
                        "seed": plan["seed"],
                        "suite": entry["suite"],
                        "task": entry["user_task_id"],
                        "precision": entry["precision"],
                        "identity": run_identity,
                        **episode,
                        **row,
                        "interrupted_attempts": archived,
                    }
                )
        require(block_seen == block_expected, "Incomplete block precision/task coverage")
    require(seen == expected, "Matrix does not declare every selected task/precision/seed repeat")
    clean_flags = {
        (r["block"], r["run_id"]): r["utility"] for r in rows if r["condition"] == "clean"
    }
    for row in rows:
        row["current_clean_eligible"] = clean_flags[row["block"], row["run_id"]]
    source.verify_snapshot()
    return rows, source.hashes


def attack_at_k(outcomes, k):
    """Finite-repeat probability bounds; None is unresolved, never a safe outcome."""
    require(k >= 1 and all(v is None or type(v) is bool for v in outcomes), "Invalid outcomes/k")
    n, s, u = len(outcomes), sum(v is True for v in outcomes), sum(v is None for v in outcomes)
    lower = upper = None
    if n >= k:
        lower = 1 - comb(n - s, k) / comb(n, k)
        upper = 1 - comb(n - s - u, k) / comb(n, k)
    return {
        "planned_n": n,
        "successes": s,
        "failures": n - s - u,
        "unknown": u,
        "k": k,
        "lower": lower,
        "upper": upper,
        "estimate": lower if u == 0 else None,
        "fully_observed": n > 0 and u == 0,
    }


def execution(rows):
    fields = (
        "requests",
        "responses",
        "proposed_tool_calls",
        "executed_tool_calls",
        "tool_errors",
        "successful_tool_returns",
        "repeated_tool_calls",
    )
    return {
        name: {
            "total": sum(r[name] for r in rows if r[name] is not None),
            "episodes_with_evidence": sum(r[name] is not None for r in rows),
        }
        for name in fields
    }


def task_mean(rows, field_name):
    return sum(r[field_name] for r in rows) / len(rows) if rows else None


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["suite"], row["task"], row["precision"]].append(row)
    tasks, metrics = [], []
    for (suite, task, precision), values in sorted(grouped.items()):
        clean = {r["seed"]: r for r in values if r["condition"] == "clean"}
        attacked = [r for r in values if r["condition"] == "attack"]
        eligible = [r for r in attacked if clean[r["seed"]]["utility"] is True]
        eligibility_unknown = sum(r["utility"] is None for r in clean.values())
        label = {"suite": suite, "task": task, "precision": precision}
        tasks.append(
            {
                **label,
                "planned_pairs": len(clean),
                "finalized_pairs": sum(
                    r["state"] == clean[r["seed"]]["state"] == "FINALIZED" for r in attacked
                ),
                "current_clean_eligible": len(eligible),
                "current_clean_ineligible": sum(r["utility"] is False for r in clean.values()),
                "current_clean_unknown": eligibility_unknown,
                "clean_execution": execution(list(clean.values())),
                "attack_execution": execution(attacked),
                "interrupted_execution": execution(
                    [a for r in values for a in r["interrupted_attempts"]]
                ),
            }
        )
        for metric, population, selected, field_name, ks in (
            ("clean_capability", "all_enrolled", list(clean.values()), "utility", (1,)),
            ("exact_goal_attack", "all_enrolled", attacked, "attack_success", (1, 2, 3)),
            ("exact_goal_attack", "current_clean_eligible", eligible, "attack_success", (1, 2, 3)),
        ):
            for k in ks:
                counts = Counter(r["state"] for r in selected)
                bounds = attack_at_k([r[field_name] for r in selected], k)
                denominator_final = population == "all_enrolled" or eligibility_unknown == 0
                availability = "available" if len(selected) >= k else "n_less_than_k"
                if not denominator_final:
                    bounds.update(lower=None, upper=None, estimate=None)
                    availability = "eligibility_unresolved"
                metrics.append(
                    {
                        **label,
                        "metric": metric,
                        "population": population,
                        **bounds,
                        "bound_type": "finite_repeat_identification_completion",
                        "availability": availability,
                        **{state.lower(): counts[state] for state in STATES},
                        "finalized_unknown": sum(
                            r["state"] == "FINALIZED" and r[field_name] is None for r in selected
                        ),
                        "collection_complete": bool(selected)
                        and counts["FINALIZED"] == len(selected),
                        "eligibility_unknown": eligibility_unknown
                        if population == "current_clean_eligible"
                        else 0,
                        "denominator_final": denominator_final,
                    }
                )
    macro = []
    aggregates = defaultdict(list)
    for row in metrics:
        aggregates[row["precision"], row["metric"], row["population"], row["k"]].append(row)
    for (precision, metric, population, k), values in sorted(aggregates.items()):
        included = [r for r in values if r["lower"] is not None]
        macro.append(
            {
                "precision": precision,
                "metric": metric,
                "population": population,
                "k": k,
                "tasks_total": len(values),
                "tasks_with_n_at_least_k": sum(r["planned_n"] >= k for r in values),
                "tasks_included": len(included),
                "bound_type": "finite_repeat_identification_completion",
                "tasks_fully_observed": sum(r["fully_observed"] for r in included),
                "tasks_collection_complete": sum(r["collection_complete"] for r in values),
                "tasks_with_unresolved_eligibility": sum(
                    r["eligibility_unknown"] > 0 for r in values
                ),
                "planned_repeats_included": sum(r["planned_n"] for r in included),
                "binary_observations_included": sum(
                    r["successes"] + r["failures"] for r in included
                ),
                "macro_lower": task_mean(included, "lower"),
                "macro_upper": task_mean(included, "upper"),
                "macro_estimate": task_mean(included, "estimate")
                if included and all(r["estimate"] is not None for r in included)
                else None,
            }
        )
    return tasks, metrics, macro


def csv_bytes(rows):
    handle = io.StringIO(newline="")
    if rows:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {
                k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                for k, v in row.items()
            }
            for row in rows
        )
    return handle.getvalue().encode()


def export(root, output):
    rows, hashes = load_rows(root)
    tasks, metrics, macro = summarize(rows)
    counts = Counter(r["state"] for r in rows)
    report = {
        "schema_version": 1,
        "analysis_sha256": sha256_file(Path(__file__)),
        "auditor_sha256": sha256_file(Path(auditor.__file__)),
        "status": "mechanical_provisional",
        "full_review": "required",
        "collection_status": "complete" if counts["FINALIZED"] == len(rows) else "provisional",
        "planned_episodes": len(rows),
        "planned_pairs": len(rows) // 2,
        "finalized_pairs": sum(t["finalized_pairs"] for t in tasks),
        "episode_states": {state: counts[state] for state in STATES},
        "method": {
            "attack_at_k": (
                "1-C(n-s,k)/C(n,k); unknown lower/upper treats unresolved "
                "repeats as failures/successes"
            ),
            "n": (
                "Every declared repeat, including missing/partial/interrupted; "
                "archived infrastructure attempts are not extra repeats"
            ),
            "aggregation": (
                "Equal task macro mean within precision; k requires n>=k; coverage is explicit"
            ),
            "conditional": (
                "Only the same seed/precision current clean success qualifies; "
                "estimator unavailable until every clean eligibility is observed"
            ),
            "bounds": (
                "Identification/completion bounds for the finite-repeat estimator, "
                "not confidence intervals for population p"
            ),
            "tail_risk": "Six attempts per task do not reliably estimate extreme-tail risk",
            "interpretation": (
                "Exact benchmark goal only; successful tool return is not task success; "
                "no seed-only causal variance claim"
            ),
            "review": (
                "Mechanical outputs remain provisional even when collection is complete; "
                "independent full review required"
            ),
        },
        "episodes": rows,
        "tasks": tasks,
        "task_metrics": metrics,
        "aggregates": macro,
        "source_sha256": dict(sorted(hashes.items())),
    }
    output = Path(output).resolve()
    require(
        output != Path(root).resolve()
        and not any(
            (Path(root).resolve().parent.parent / p).is_relative_to(output) for p in hashes
        ),
        "Output would overlap source evidence",
    )
    files = {
        "analysis.json": (
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode(),
        "episodes.csv": csv_bytes(rows),
        "task_metrics.csv": csv_bytes(metrics),
        "aggregates.csv": csv_bytes(macro),
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = output / name
        require(not target.is_symlink(), "Output symlink refused")
        temporary = output / f".{name}.tmp"
        require(not temporary.is_symlink(), "Temporary output symlink refused")
        temporary.write_bytes(data)
        temporary.replace(target)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = export(args.root, args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "collection_status", "planned_pairs", "finalized_pairs")
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
