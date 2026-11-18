import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

LANE = Path("results/phase2-followup-v1/capability")
SCRIPT = Path("scripts/profile_phase2_capability.py")
PRECISIONS = ("f16", "q8", "q4")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def source_path(root, value):
    path = Path(value)
    if path.is_absolute():
        parts = path.parts
        if ".cache" not in parts:
            raise ValueError(f"Unexpected absolute evidence path: {value}")
        path = Path(*parts[parts.index(".cache"):])
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Evidence escapes source root: {value}")
    return resolved


def candidates(root):
    protocol = read_json(root / "configs/phase2-followup-v1/preparation.json")
    episodes = read_json(root / protocol["formal_episodes"])
    main = {(e["suite"], e["task"]) for e in episodes if e["cohort"] == "main"}
    with (root / protocol["baseline_episodes"]).open() as stream:
        rows = list(csv.DictReader(stream))
    grouped = {}
    for row in rows:
        key = row["suite"], row["user_task_id"]
        if row["condition"] == "clean" and key in main:
            seed = int(row["repeat_id"].removeprefix("seed-"))
            if seed not in protocol["baseline_seeds"]:
                continue
            if seed in grouped.setdefault(key, {}):
                raise ValueError(f"Duplicate baseline: {key} seed {seed}")
            grouped[key][seed] = row
    for key, seeds in grouped.items():
        if set(seeds) != set(protocol["baseline_seeds"]):
            raise ValueError(f"Missing baseline seed: {key}")
    selected = {k: v for k, v in grouped.items() if all(
        row["utility"] == "True" for row in v.values()
    )}
    return protocol, episodes, dict(sorted(selected.items()))


def changes(before, after, path=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(before.keys() | after.keys())
                for change in changes(before.get(key), after.get(key), f"{path}/{key}")]
    if isinstance(before, list) and isinstance(after, list):
        return [change for i in range(max(len(before), len(after)))
                for change in changes(before[i] if i < len(before) else None,
                                      after[i] if i < len(after) else None, f"{path}/{i}")]
    return [{"path": path, "before": before, "after": after}]


def inspect(root, suite, task):
    _, _, selected = candidates(root)
    for (suite_name, task_name), seeds in selected.items():
        if suite and suite_name != suite or task and task_name != task:
            continue
        for seed, row in sorted(seeds.items()):
            path = source_path(root, row["trace_path"])
            trace = read_json(path)
            print(f"\n{suite_name}/{task_name} seed={seed} sha256={digest(path.read_bytes())}")
            print("OBJECTIVE:", trace["user_prompt"])
            for index, message in enumerate(trace["messages"]):
                if index < 2:
                    continue
                print(f"messages[{index}] {message['role']}: {canonical(message)}")
            print("STATE CHANGES:", canonical(changes(
                trace["initial_environment"], trace["final_environment"])))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_hash(path, expected):
    require(digest(path.read_bytes()) == expected, f"Source hash mismatch: {path}")


def pointer(value, path):
    for part in path.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def qualify(root, review, selected):
    by_key = {(t["suite"], t["task"]): t for t in review["tasks"]}
    require(len(by_key) == len(review["tasks"]), "Duplicate task review")
    require(set(by_key) == set(selected), "Review coverage differs from baseline candidates")
    tasks, hashes = [], dict(review["source_hashes"])
    for key, rows in selected.items():
        task = by_key[key]
        seeds = {s["seed"]: s for s in task["per_seed"]}
        require(len(seeds) == len(task["per_seed"]), f"Duplicate reviewed seed: {key}")
        require(set(seeds) == set(rows), f"Review seed coverage mismatch: {key}")
        for seed, row in rows.items():
            annotation = seeds[seed]
            require(annotation["decision"] in {"supported", "unsupported", "uncertain"},
                    f"Invalid decision: {key} seed {seed}")
            require(bool(annotation["reason"].strip()), f"Missing review reason: {key}")
            path = source_path(root, row["trace_path"])
            relative = str(path.relative_to(root.resolve()))
            require(annotation["trace_path"] == relative, f"Trace path mismatch: {key}")
            verify_hash(path, annotation["trace_sha256"])
            hashes[relative] = annotation["trace_sha256"]
            trace = read_json(path)
            for field in ("episode_id", "identity", "condition", "user_task_id"):
                require(trace[field] == row[field], f"Baseline identity mismatch: {key} {field}")
            evidence = annotation["evidence"]
            require(evidence["objective_pointer"] == "/user_prompt" and trace["user_prompt"],
                    f"Missing objective evidence: {key}")
            require(evidence["message_indices"] == list(range(len(trace["messages"]))),
                    f"Incomplete message review: {key}")
            expected = {str(i): digest(canonical(m).encode())
                        for i, m in enumerate(trace["messages"])}
            require(evidence["message_sha256"] == expected, f"Message anchor mismatch: {key}")
            for state in ("initial_environment", "final_environment"):
                require(evidence[f"{state}_sha256"] == digest(canonical(trace[state]).encode()),
                        f"State anchor mismatch: {key}")
            require(evidence["state_changes"] == changes(
                trace["initial_environment"], trace["final_environment"]),
                f"State changes mismatch: {key}")
            for anchor in evidence["additional_state_pointers"]:
                pointer(trace, anchor)
        eligible = all(s["decision"] == "supported" for s in seeds.values())
        tasks.append({**task, "eligible": eligible,
                      "reason": "; ".join(f"seed {s['seed']}: {s['decision']} — {s['reason']}"
                                          for s in task["per_seed"])})
    return tasks, hashes


def exact_repeats(trace):
    calls = [m["tool_call"] for m in trace["messages"] if m["role"] == "tool"]
    signatures = [canonical([c["function"], c["args"]]) for c in calls]
    return len(signatures) - len(set(signatures))


def finite_number(value, label):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f"Invalid numeric evidence: {label}")
    return value


def profile_episode(root, episode, source_hashes, call_annotations):
    base = source_path(root, episode["source"])
    for filename in ("trace.json.gz", "result.json", "requests.jsonl.gz"):
        path = base / filename
        relative = str(path.relative_to(root.resolve()))
        require(relative in source_hashes, f"Missing pinned profile source: {relative}")
        verify_hash(path, source_hashes[relative])
    trace = read_json(base / "trace.json.gz")
    result = read_json(base / "result.json")
    for filename, field in (("trace.json.gz", "trace_sha256"), ("result.json", "result_sha256")):
        verify_hash(base / filename, episode[field])
    for raw in (trace, result):
        for field in ("identity", "episode_id", "condition"):
            require(raw[field] == episode[field], f"Profile identity mismatch: {base} {field}")
        require(raw["user_task_id"] == episode["task"], f"Profile task mismatch: {base}")
    require(result["utility"] is episode["utility"] and result["status"] == episode["status"],
            f"Official outcome mismatch: {base}")
    require(result["utility"] is None or type(result["utility"]) is bool,
            f"Invalid utility: {base}")
    tools = [(i, m) for i, m in enumerate(trace["messages"]) if m["role"] == "tool"]
    errors = sum(m["error"] is not None for _, m in tools)
    for field, observed in (("tool_calls", len(tools)), ("tool_errors", errors)):
        require(trace[field] == result[field] == episode[field] == observed,
                f"Tool count mismatch: {base} {field}")
    events = [json.loads(line) for line in gzip.decompress(
        (base / "requests.jsonl.gz").read_bytes()).splitlines()]
    requests = [e for e in events if e["event"] == "request"]
    responses = [e for e in events if e["event"] == "response"]
    request_errors = [e for e in events if e["event"] == "error"]
    require(len(events) == len(requests) + len(responses) + len(request_errors),
            f"Unknown request event: {base}")
    require([e["index"] for e in requests] == list(range(len(requests))),
            f"Request sequence mismatch: {base}")
    terminal = [e["index"] for e in responses + request_errors]
    require(sorted(terminal) == list(range(len(requests))), f"Incomplete request log: {base}")
    require(len(requests) == trace["requests"] == result["requests"] == episode["requests"],
            f"Request count mismatch: {base}")
    metrics = {"tool_calls": len(tools), "tool_errors": errors, "requests": len(requests),
               "exact_repeated_calls": exact_repeats(trace), "responses": len(responses),
               "request_errors": len(request_errors)}
    require(metrics["exact_repeated_calls"] == episode["repeated_calls"],
            f"Exact-repeat mismatch: {base}")
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [(e["response"].get("usage") or {}).get(name) for e in responses]
        observed = sum(finite_number(v, name) for v in values if v is not None)
        metrics[f"observed_{name}"] = observed
        metrics[f"missing_{name}_responses"] = sum(v is None for v in values)
        metrics[name] = observed if all(v is not None for v in values) else None
        require(result["usage"].get(name) == trace["usage"].get(name),
                f"Usage mismatch: {base} {name}")
        if metrics[name] is not None:
            require(result["usage"][name] == observed, f"Request usage mismatch: {base} {name}")
    metrics["seconds"] = finite_number(result["duration_seconds"], "duration_seconds")
    require(metrics["seconds"] == episode["seconds"], f"Duration mismatch: {base}")
    metrics["response_seconds"] = sum(finite_number(e["latency_seconds"], "latency_seconds")
                                      for e in responses)
    for name in ("prompt", "predicted"):
        times = [(e["response"].get("timings") or {}).get(f"{name}_ms") for e in responses]
        metrics[f"native_{name}_seconds"] = (
            sum(finite_number(t, f"{name}_ms") for t in times) / 1000
            if all(t is not None for t in times) else None)
        metrics[f"missing_native_{name}_responses"] = sum(t is None for t in times)
    for annotation in call_annotations:
        require(annotation["trace_sha256"] == episode["trace_sha256"],
                f"Call-review trace mismatch: {base}")
        for index in annotation["tool_message_indices"]:
            require(index in {i for i, _ in tools}, f"Invalid tool anchor: {base} {index}")
        require(annotation["category"] in {"unnecessary_scope_action", "avoidable_failed_call",
                                            "legitimate_recovery"}, "Invalid call category")
    unnecessary = {i for a in call_annotations if a["category"] != "legitimate_recovery"
                   for i in a["tool_message_indices"]}
    return {
        "suite": episode["suite"], "task": episode["task"], "precision": episode["precision"],
        "source": episode["source"], "official_utility": result["utility"],
        "status": result["status"], "error_type": (result.get("error") or {}).get("type"),
        **metrics, "confirmed_avoidable_calls_lower_bound": len(unnecessary),
        "unnecessary_calls_total": None, "call_review": call_annotations,
        "existing_semantic_review": {
            "status": episode["review_status"], "failure_category": episode["failure_category"],
            "secondary_labels": episode["secondary_labels"], "evidence": episode["evidence"],
            "notes": episode["notes"], "new_semantic_success_score": None},
    }


def summarize(profiles):
    summaries = []
    metrics = ("tool_calls", "tool_errors", "exact_repeated_calls", "requests", "responses",
               "request_errors", "prompt_tokens", "completion_tokens", "total_tokens", "seconds",
               "response_seconds", "native_prompt_seconds", "native_predicted_seconds",
               "confirmed_avoidable_calls_lower_bound")
    for precision in PRECISIONS:
        rows = [r for r in profiles if r["precision"] == precision]
        summary = {"precision": precision, "episodes": len(rows),
                   "official_success": sum(r["official_utility"] is True for r in rows),
                   "official_failure": sum(r["official_utility"] is False for r in rows),
                   "official_unknown": sum(r["official_utility"] is None for r in rows),
                   "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
                   "existing_scorer_false_positive_flags": sum(
                       "scorer_false_positive" in r["existing_semantic_review"]["secondary_labels"]
                       for r in rows), "unnecessary_calls_total": None, "metrics": {}}
        for metric in metrics:
            values = [r[metric] for r in rows if r[metric] is not None]
            summary["metrics"][metric] = {
                "observed_n": len(values), "missing_n": len(rows) - len(values),
                "sum": sum(values), "median": statistics.median(values) if values else None,
                "min": min(values) if values else None, "max": max(values) if values else None}
        summaries.append(summary)
    return summaries


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_write(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def write_csv(path, rows):
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, stream.getvalue())


def generate(root, output, review_path):
    root = root.resolve()
    protocol, episodes, selected = candidates(root)
    review = read_json(review_path)
    expected_sources = {"configs/phase2-followup-v1/preparation.json",
                        "docs/phase2-post-study-review.md", protocol["baseline_episodes"],
                        protocol["formal_episodes"]}
    require(set(review["source_hashes"]) == expected_sources, "Incomplete source-table provenance")
    for relative, sha in review["source_hashes"].items():
        verify_hash(source_path(root, relative), sha)
    tasks, hashes = qualify(root, review, selected)
    eligible = {(t["suite"], t["task"]) for t in tasks if t["eligible"]}
    require(eligible, "No qualified tasks; no capability profile denominator")
    arms = [e for e in episodes if e["cohort"] == "main" and e["policy"] == "full"
            and e["condition"] == "clean" and (e["suite"], e["task"]) in eligible]
    keys = [(e["suite"], e["task"], e["precision"]) for e in arms]
    require(len(set(keys)) == len(keys), "Duplicate Full clean arm")
    require(set(keys) == {(s, t, p) for s, t in eligible for p in PRECISIONS},
            "Missing Full clean arm")
    expected_raw = {str(Path(e["source"]) / f) for e in arms
                    for f in ("trace.json.gz", "result.json", "requests.jsonl.gz")}
    require(set(review["profile_source_hashes"]) == expected_raw,
            "Profile source coverage mismatch")
    hashes.update(review["profile_source_hashes"])
    annotations = review["call_reviews"]
    require(all(a["source"] in {e["source"] for e in arms} for a in annotations),
            "Call annotation outside qualified cohort")
    profiles = [profile_episode(root, e, hashes,
                               [a for a in annotations if a["source"] == e["source"]])
                for e in sorted(arms, key=lambda e: (e["suite"], e["task"],
                                                    PRECISIONS.index(e["precision"])))]
    hashes[str(LANE / "manual-review.json")] = digest(review_path.read_bytes())
    hashes[str(SCRIPT)] = digest(Path(__file__).read_bytes())
    qualification = {
        "schema_version": 1, "reviewer": review["reviewer"], "review_kind": review["review_kind"],
        "selection": "Phase1 clean official utility=true at both protocol seeds, in Phase2 main",
        "eligibility_rule": "both Phase1 seed decisions supported; no Phase2 outcome selection",
        "baseline_seeds": protocol["baseline_seeds"], "candidate_tasks": len(tasks),
        "reviewed_traces": sum(len(t["per_seed"]) for t in tasks), "eligible_tasks": len(eligible),
        "decision_counts": dict(sorted(Counter(s["decision"] for t in tasks
                                               for s in t["per_seed"]).items())),
        "source_hashes": dict(sorted(hashes.items())), "tasks": tasks}
    summary = summarize(profiles)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "qualification.json", qualification)
    write_json(output / "profile.json", {"schema_version": 1, "fixed_task_count": len(eligible),
                                       "source_hashes": qualification["source_hashes"],
                                       "summary": summary, "episodes": profiles})
    qualification_rows = [{"suite": t["suite"], "task": t["task"], "eligible": t["eligible"],
                           "seed": s["seed"], "decision": s["decision"], "reason": s["reason"],
                           "trace_path": s["trace_path"], "trace_sha256": s["trace_sha256"]}
                          for t in tasks for s in t["per_seed"]]
    write_csv(output / "qualification.csv", qualification_rows)
    write_csv(output / "profile.csv", [{k: v for k, v in r.items()
                                        if k not in ("call_review", "existing_semantic_review")}
                                       for r in profiles])
    paired = []
    for suite, task in sorted(eligible):
        rows = {r["precision"]: r for r in profiles if (r["suite"], r["task"]) == (suite, task)}
        for precision in ("q8", "q4"):
            row = {"suite": suite, "task": task, "contrast": f"{precision}-f16",
                   "f16_official_utility": rows["f16"]["official_utility"],
                   "comparison_official_utility": rows[precision]["official_utility"]}
            for metric in ("tool_calls", "requests", "prompt_tokens",
                           "completion_tokens", "seconds"):
                a, b = rows[precision][metric], rows["f16"][metric]
                row[f"delta_{metric}"] = a - b if a is not None and b is not None else None
            paired.append(row)
    write_csv(output / "paired-differences.csv", paired)
    lines = ["# Phase1 双 seed 语义资格审阅", "", review["lever_design"], "",
             "零基 messages 索引；每个 seed 独立原始 trace 哈希见 qualification.csv/json。", "",
             "| 任务 | seed | 判断 | 原始证据与理由 |", "|---|---:|---|---|"]
    lines.extend(f"| {r['suite']}/{r['task']} | {r['seed']} | {r['decision']} | {r['reason']} |"
                 for r in qualification_rows)
    atomic_write(output / "qualification.md", "\n".join(lines) + "\n")
    return qualification, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--suite")
    parser.add_argument("--task")
    parser.add_argument("--review", type=Path,
                        default=Path(__file__).resolve().parents[1] / LANE / "manual-review.json")
    args = parser.parse_args()
    if args.inspect:
        inspect(args.source_root, args.suite, args.task)
    else:
        if args.output is None:
            parser.error("--output is required unless --inspect")
        try:
            qualification, summary = generate(args.source_root, args.output, args.review)
        except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError) as error:
            parser.exit(2, f"Evidence validation failed: {error}\n")
        print(json.dumps({"candidates": qualification["candidate_tasks"],
                          "reviewed_traces": qualification["reviewed_traces"],
                          "eligible": qualification["eligible_tasks"],
                          "arms": [{k: s[k] for k in ("precision", "episodes", "official_success",
                                                     "official_failure", "official_unknown")}
                                   for s in summary]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
