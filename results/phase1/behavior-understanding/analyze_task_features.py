"""Descriptive frozen-task analysis; reads clean evidence, never executes tasks."""
from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
MANIFEST = ROOT / "configs/phase1/task-manifest.json"
EPISODES = ROOT / "results/phase1/evaluation-episodes.csv"
SITE = next((ROOT / ".venv/lib").glob("python*/site-packages"))

# Chosen before aggregating feature/outcome groups. These are post-experiment
# descriptive choices, not a preregistered difficulty taxonomy.
BIN_RULES = {
    "ground_truth_tool_count": "1–2 / 3–4 / 5+",
    "prompt_whitespace_words": "1–25 / 26–60 / 61+",
    "observed_first_returned_prompt_tokens": "<=3000 / 3001–6000 / >6000",
    "observed_max_returned_prompt_tokens": "<=4000 / 4001–8000 / >8000",
}
WRITE_TOOLS = {
    "send_money", "schedule_transaction", "update_scheduled_transaction",
    "update_user_info", "update_password", "send_direct_message",
    "invite_user_to_slack", "send_channel_message", "post_webpage",
    "add_user_to_channel", "create_calendar_event", "reschedule_calendar_event",
    "add_calendar_event_participants", "create_file", "send_email",
    "append_to_file", "delete_file", "reserve_hotel",
}
RETRIEVAL_SIDE_EFFECTS = {
    "get_unread_emails": "marks returned emails as read",
    "get_webpage": "appends visited URL to web.web_requests",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


source_bytes: dict[Path, bytes] = {}


def read(path: Path) -> bytes:
    if path not in source_bytes:
        source_bytes[path] = path.read_bytes()
    return source_bytes[path]


def family(suite: str, tool: str) -> str:
    if suite == "banking":
        return "local_file" if tool == "read_file" else "banking"
    if suite == "slack":
        return "web" if tool in {"get_webpage", "post_webpage"} else "slack"
    if suite == "workspace":
        if "calendar" in tool: return "calendar"
        if tool == "get_current_day": return "current_date"
        if "contact" in tool: return "contacts"
        if "email" in tool: return "email"
        if "file" in tool: return "cloud_drive"
    if suite == "travel":
        if "hotel" in tool: return "hotels"
        if "restaurant" in tool or tool == "check_restaurant_opening_hours": return "restaurants"
        if "car" in tool: return "car_rental"
        if "calendar" in tool: return "calendar"
        if tool == "send_email": return "email"
    raise AssertionError((suite, tool))


def group_bin(n: int, cuts: tuple[int, int], labels: tuple[str, str, str]) -> str:
    return labels[0] if n <= cuts[0] else labels[1] if n <= cuts[1] else labels[2]


manifest = json.loads(read(MANIFEST))
frozen_prompts = {(suite, t["id"]): t["prompt"] for suite, audit in manifest["offline_audit"].items() for t in audit["users"]}
read(ROOT / "artifacts/campaigns/phase1-f16-v1/manifest.json")
read(ROOT / "results/phase1/reviewed-traces.csv")
with EPISODES.open(newline="") as stream:
    rows = list(csv.DictReader(stream))
read(EPISODES)
assert len(rows) == 600
clean_rows = [r for r in rows if r["condition"] == "clean"]
assert len(clean_rows) == 120
by_task = defaultdict(list)
episodes = []

for row in clean_rows:
    directory = ROOT / "artifacts/runs" / row["run_id"] / "episodes" / row["episode_id"]
    result = json.loads(read(directory / "result.json"))
    compression = json.loads(read(directory / "compression.json"))
    unpacked = {}
    for name in ("trace.json", "requests.jsonl"):
        compressed = read(directory / (name + ".gz"))
        plain = gzip.decompress(compressed)
        record = next(x for x in compression["artifacts"] if x["path"] == name + ".gz")
        assert sha(compressed) == record["compressed_sha256"]
        assert sha(plain) == record["original_sha256"]
        assert len(compressed) == record["compressed_bytes"]
        assert len(plain) == record["original_bytes"]
        unpacked[name] = plain
    trace = json.loads(unpacked["trace.json"])
    journal = [json.loads(line) for line in unpacked["requests.jsonl"].splitlines()]
    assert result["identity"] == trace["identity"] == row["identity"]
    assert result["condition"] == trace["condition"] == "clean"
    assert result["user_task_id"] == row["user_task_id"]
    assert trace["user_prompt"] == frozen_prompts[(row["suite"], row["user_task_id"])]
    assert str(result["utility"]) == (row["utility"] or "None")
    requests = {x["index"]: x for x in journal if x["event"] == "request"}
    responses = {x["index"]: x for x in journal if x["event"] == "response"}
    request_errors = [x for x in journal if x["event"] == "request_error"]
    assert len(requests) == result["requests"]
    assert len(responses) + len(request_errors) == len(requests)
    assert 0 in responses
    prompt_tokens = [r["response"]["usage"]["prompt_tokens"] for r in responses.values()]
    accepted = [m["tool_call"] for m in trace["messages"] if m["role"] == "tool"]
    assert len(accepted) == result["tool_calls"]
    signatures = [canonical({"function": t["function"], "args": t["args"]}) for t in accepted]
    multiplicities = Counter(signatures)
    last = trace["messages"][-1]
    max_request_payload_bytes = max(len(canonical(x["payload"]).encode()) for x in requests.values())
    first_payload = requests[0]["payload"]
    assert all(x["payload"]["seed"] == int(row["repeat_id"].split("-")[-1]) for x in requests.values())
    item = {
        "suite": row["suite"], "user_task_id": row["user_task_id"],
        "seed": int(row["repeat_id"].split("-")[-1]), "run_id": row["run_id"],
        "episode_id": row["episode_id"], "identity": result["identity"],
        "status": result["status"], "utility": result["utility"],
        "error_type": row["error_type"] or None,
        "duration_seconds": result["duration_seconds"],
        "available_prompt_tokens_sum": result["usage"]["prompt_tokens"],
        "available_completion_tokens_sum": result["usage"]["completion_tokens"],
        "first_returned_prompt_tokens_including_schema": responses[0]["response"]["usage"]["prompt_tokens"],
        "max_returned_prompt_tokens_including_schema": max(prompt_tokens),
        "max_request_payload_json_utf8_bytes": max_request_payload_bytes,
        "failed_request_prompt_tokens_unknown_count": len(request_errors),
        "tool_schema_count": len(first_payload["tools"]),
        "tool_schema_canonical_json_chars": len(canonical(first_payload["tools"])),
        "actual_tool_calls": result["tool_calls"], "tool_errors": result["tool_errors"],
        "requests": result["requests"],
        "actual_tool_functions_in_order": [t["function"] for t in accepted],
        "exact_tool_call_repeats_after_first": sum(c - 1 for c in multiplicities.values()),
        "max_exact_tool_call_multiplicity": max(multiplicities.values(), default=0),
        "consecutive_exact_tool_call_repeats": sum(a == b for a, b in zip(signatures, signatures[1:])),
        "review_failure_category": row["failure_category"],
        "review_secondary_labels": row["secondary_labels"],
        "review_evidence": row["evidence"],
        "review_notes": row["notes"],
        "final_assistant_content": last.get("content") if last["role"] == "assistant" else None,
    }
    by_task[(row["suite"], row["user_task_id"])].append(item)
    episodes.append(item)

features = []
for suite, audit in manifest["offline_audit"].items():
    for task in audit["users"]:
        path = SITE / task["utility_source"]["path"]
        data = read(path)
        tree = ast.parse(data.decode())
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        assert sha(data) == task["utility_source"]["file_sha256"]
        cls = classes["UserTask" + task["id"].rsplit("_", 1)[1]]
        gt = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "ground_truth")
        literals = []
        for node in ast.walk(gt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FunctionCall":
                value = next(k.value for k in node.keywords if k.arg == "function")
                assert isinstance(value, ast.Constant) and isinstance(value.value, str)
                literals.append((node.lineno, value.value))
        tools = [name for _, name in sorted(literals)]
        assert len(tools) == task["ground_truth_tool_count"]
        paired = sorted(by_task[(suite, task["id"])], key=lambda x: x["seed"])
        assert [x["seed"] for x in paired] == [17, 29]
        outcomes = [x["utility"] for x in paired]
        pair_type = ("unknown_present" if None in outcomes else "both_success" if all(outcomes)
                     else "both_failure" if not any(outcomes) else "seed_disagreement")
        explicit = [name for name in tools if name in WRITE_TOOLS]
        for episode in paired:
            actual_actions = [name for name in episode["actual_tool_functions_in_order"] if name in WRITE_TOOLS]
            episode["actual_explicit_action_call_functions"] = actual_actions
            episode["explicit_action_requested_but_no_action_call"] = bool(explicit) and not actual_actions
        prompt = task["prompt"]
        prompt_words = len(re.findall(r"\S+", prompt))
        item = {
            "suite": suite, "user_task_id": task["id"], "legitimate_prompt": prompt,
            "source_difficulty_label": task["difficulty"],
            "task_intent": "explicit_action" if explicit else "information_answer",
            "task_intent_definition": "reference path includes a user-authorized write/send/update/delete action" if explicit else "asks for information; reference path has no explicit write/send/update/delete action",
            "reference_retrieval_side_effects": {name: RETRIEVAL_SIDE_EFFECTS[name] for name in tools if name in RETRIEVAL_SIDE_EFFECTS},
            "ground_truth_tool_count": len(tools), "reference_tool_functions_in_source_order": tools,
            "reference_explicit_action_tools": explicit,
            "reference_tool_families": sorted({family(suite, name) for name in tools}),
            "reference_distinct_tool_count": len(set(tools)),
            "reference_distinct_family_count": len({family(suite, name) for name in tools}),
            "prompt_chars": len(prompt), "prompt_whitespace_words": prompt_words,
            "ground_truth_tool_count_bin": group_bin(len(tools), (2, 4), ("1–2", "3–4", "5+")),
            "prompt_word_count_bin": group_bin(prompt_words, (25, 60), ("1–25", "26–60", "61+")),
            "outcome_pair_type": pair_type, "clean_success_count": sum(x is True for x in outcomes),
            "clean_failure_count": sum(x is False for x in outcomes), "clean_unknown_count": sum(x is None for x in outcomes),
            "clean_outcomes_seed17_seed29": outcomes,
            "ground_truth_source": {"path": str(path.relative_to(ROOT)), "line": gt.lineno,
                                    "file_sha256": sha(data), "method_source_sha256": sha(ast.get_source_segment(data.decode(), gt).encode())},
            "episodes": paired,
        }
        features.append(item)

assert len(features) == 60
assert sum(x["clean_success_count"] for x in features) == 58
assert sum(x["clean_failure_count"] for x in features) == 59
assert sum(x["clean_unknown_count"] for x in features) == 3


def summarize(tasks):
    eps = [e for task in tasks for e in task["episodes"]]
    t = sum(e["utility"] is True for e in eps)
    f = sum(e["utility"] is False for e in eps)
    u = sum(e["utility"] is None for e in eps)
    return {
        "task_clusters": len(tasks), "clean_episodes": len(eps),
        "utility_true": t, "utility_false": f, "utility_unknown": u,
        "all_attempt_official_utility_rate": t / len(eps) if eps else None,
        "known_official_utility_rate": t / (t + f) if t + f else None,
        "pair_types": dict(Counter(x["outcome_pair_type"] for x in tasks)),
        "by_suite_task_clusters": dict(Counter(x["suite"] for x in tasks)),
        "task_keys": [x["suite"] + "/" + x["user_task_id"] for x in tasks],
        "median_actual_tool_calls": median(e["actual_tool_calls"] for e in eps) if eps else None,
        "median_first_returned_prompt_tokens": median(e["first_returned_prompt_tokens_including_schema"] for e in eps) if eps else None,
        "median_max_returned_prompt_tokens": median(e["max_returned_prompt_tokens_including_schema"] for e in eps) if eps else None,
        "median_episode_seconds": median(e["duration_seconds"] for e in eps) if eps else None,
        "episodes_with_exact_tool_call_repeat": sum(e["exact_tool_call_repeats_after_first"] > 0 for e in eps),
        "review_failure_categories": dict(Counter(e["review_failure_category"] for e in eps)),
        "utility_false_review_failure_categories": dict(Counter(e["review_failure_category"] for e in eps if e["utility"] is False)),
        "utility_true_review_failure_categories": dict(Counter(e["review_failure_category"] for e in eps if e["utility"] is True)),
        "explicit_action_requested_but_no_action_call": sum(e["explicit_action_requested_but_no_action_call"] for e in eps),
        "utility_false_explicit_action_requested_but_no_action_call": sum(e["utility"] is False and e["explicit_action_requested_but_no_action_call"] for e in eps),
    }


groups = {}
for key in ("suite", "task_intent", "ground_truth_tool_count_bin", "prompt_word_count_bin", "source_difficulty_label", "reference_distinct_family_count", "outcome_pair_type"):
    partitions = defaultdict(list)
    for task in features:
        partitions[str(task[key])].append(task)
    groups[key] = {label: summarize(items) for label, items in sorted(partitions.items())}

tool_families = sorted({name for task in features for name in task["reference_tool_families"]})
groups["reference_tool_family_overlapping"] = {name: summarize([task for task in features if name in task["reference_tool_families"]]) for name in tool_families}

within_suite = {}
for suite in manifest["offline_audit"]:
    local = [x for x in features if x["suite"] == suite]
    within_suite[suite] = {}
    for key in ("task_intent", "ground_truth_tool_count_bin", "prompt_word_count_bin"):
        partitions = defaultdict(list)
        for task in local: partitions[str(task[key])].append(task)
        within_suite[suite][key] = {label: summarize(items) for label, items in sorted(partitions.items())}

observed = {}
for field, cuts, labels in (
    ("first_returned_prompt_tokens_including_schema", (3000, 6000), ("<=3000", "3001–6000", ">6000")),
    ("max_returned_prompt_tokens_including_schema", (4000, 8000), ("<=4000", "4001–8000", ">8000")),
):
    grouped = defaultdict(list)
    for ep in episodes: grouped[group_bin(ep[field], cuts, labels)].append(ep)
    observed[field] = {}
    for label, items in sorted(grouped.items()):
        observed[field][label] = {
            "episodes": len(items), "distinct_task_clusters": len({(e["suite"],e["user_task_id"]) for e in items}),
            "utility_true": sum(e["utility"] is True for e in items),
            "utility_false": sum(e["utility"] is False for e in items),
            "utility_unknown": sum(e["utility"] is None for e in items),
            "all_attempt_rate": sum(e["utility"] is True for e in items) / len(items),
            "by_suite_episodes": dict(Counter(e["suite"] for e in items)),
        }

for name in ("email_client.py", "slack.py", "web.py"):
    read(SITE / "agentdojo/default_suites/v1/tools" / name)
source_inventory = [{"path": str(path.relative_to(ROOT)), "bytes": len(data), "sha256": sha(data)} for path, data in sorted(source_bytes.items())]
assert all(path.read_bytes() == data for path, data in source_bytes.items())

common = {
    "schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
    "scope": "Original formal 600-cohort task manifest and its 120 clean episodes only; retry campaign excluded",
    "method": {
        "feature_extraction": "Static AST of pinned source ground_truth methods; every literal FunctionCall count agrees with frozen offline execution count. No task execution, model inference, Pydantic environment reconstruction, or score changes.",
        "task_unit": "60 distinct (suite,user_task_id) clusters; two correlated seed episodes per task",
        "task_intent": "information_answer versus explicit_action from reference write tools; information retrieval may have technical state effects, recorded separately",
        "reference_length": "Executed ground-truth call count from manifest, cross-checked with source; one benchmark reference path, not minimum required calls or inherent task difficulty",
        "tool_sequence": "FunctionCall literals in ground_truth source-line order, matching known executed count, not a freshly replayed trace",
        "prompt_length": "Whitespace-delimited words and Unicode characters in legitimate user prompt, not model tokens or full context",
        "context_length": "Observed returned-response usage.prompt_tokens, including system/user/history/tool schema/chat formatting; failed request token counts unknown. Maximum returned context is a lower bound where a request failed.",
        "context_causality": "Maximum history and repeated calls are outcomes of model behavior; a failure may cause longer context. First context includes suite-specific schema and is confounded with suite.",
        "repeat_measure": "Exact same accepted function+canonical args after first occurrence; repeats are mechanical observations, not automatically pathological loops",
        "action_omission_measure": "Reference includes an explicit action tool, while actual accepted tool results contain none of the manually enumerated write/send/update/delete functions. Tool calls that return errors still count as attempts; absence is descriptive and is not a model-internal cause.",
        "family_grouping": "reference_tool_family_overlapping groups overlap; a multi-family task appears in each applicable family and totals must not be added",
        "bin_selection": "Fixed coarse thresholds before computing group rates, after original experiment; exploratory and not preregistered",
        "bins": BIN_RULES,
        "statistics": "Descriptive counts/rates and medians; no significance tests, classifier fitting, bootstrap, or independent-episode claims",
        "review_labels": "Existing complete-clean agent trace annotations, qualitative rather than independent human/validated judge labels",
        "scores": "Official Boolean/None utility retained; checker limitations apply, especially Workspace task27 false-positive semantics and Travel task3 final-turn-only text checks",
    },
    "limitations": [
        "Task selection is fixed and non-random; 60 tasks and only two seeds limit generalization.",
        "Suite, task intent, source difficulty, reference length and schema/context length strongly overlap; pooled patterns are not isolated causal effects.",
        "Task family groups are hand-defined domains of the benchmark reference tools, not a validated universal taxonomy.",
        "No clean reference-path context token lengths exist in the frozen audit; do not use observed model histories as independent task complexity.",
        "Official success does not imply every semantic requirement is satisfied; official false can include extra side effects or final-turn answer omissions.",
        "Original 120 clean outcomes are not 120 independent tasks. Unknown outcomes are neither success nor a known failure.",
    ],
    "source_provenance": {"files": source_inventory, "files_unchanged": True, "inventory_sha256": sha(canonical(source_inventory).encode()), "analysis_script_sha256": sha(Path(__file__).read_bytes())},
}
summary = common | {"overall": summarize(features), "groups": groups, "within_suite": within_suite,
                    "observed_episode_context_groups": observed,
                    "observed_behavior_counts": {
                        "explicit_action_requested_but_no_action_call": [
                            {"suite": t["suite"], "user_task_id": t["user_task_id"], "seed": e["seed"], "utility": e["utility"], "actual_tool_functions": e["actual_tool_functions_in_order"]}
                            for t in features for e in t["episodes"] if e["explicit_action_requested_but_no_action_call"]
                        ],
                        "exact_repeated_tool_calls": [
                            {"suite": t["suite"], "user_task_id": t["user_task_id"], "seed": e["seed"], "utility": e["utility"], "repeats_after_first": e["exact_tool_call_repeats_after_first"]}
                            for t in features for e in t["episodes"] if e["exact_tool_call_repeats_after_first"]
                        ],
                    },
                    "task_outcome_lists": {key: [f"{x['suite']}/{x['user_task_id']}" for x in features if x["outcome_pair_type"] == key]
                                           for key in ("both_success", "both_failure", "seed_disagreement", "unknown_present")}}
(OUT / "task-features.json").write_text(json.dumps(common | {"tasks": features}, indent=2, ensure_ascii=False) + "\n")
(OUT / "pattern-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
flat = []
for task in features:
    row = {k: v for k, v in task.items() if k not in {"episodes", "task_intent_definition", "ground_truth_source"}}
    for key, value in list(row.items()):
        if isinstance(value, (list, dict)): row[key] = canonical(value)
    for ep in task["episodes"]:
        for key in ("utility", "status", "error_type", "actual_tool_calls", "tool_errors", "requests", "duration_seconds", "first_returned_prompt_tokens_including_schema", "max_returned_prompt_tokens_including_schema", "exact_tool_call_repeats_after_first", "review_failure_category"):
            row[f"seed{ep['seed']}_{key}"] = ep[key]
    flat.append(row)
with (OUT / "task-features.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
    writer.writeheader(); writer.writerows(flat)
print(json.dumps({"status": "passed", "tasks": len(features), "episodes": len(episodes), "source_files": len(source_inventory), "outcome_pairs": summary["overall"]["pair_types"], "source_inventory_sha256": common["source_provenance"]["inventory_sha256"]}, ensure_ascii=False))
