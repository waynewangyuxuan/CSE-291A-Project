"""Revalidate source-bound, offline Phase 2 mechanism observations."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

LANE = Path(__file__).resolve().parents[1] / "results/phase2-followup-v1/mechanisms"
EPISODES = "results/phase2-main-v1/analysis/episodes.json"
PROTOCOL = "configs/phase2-followup-v1/preparation.json"
FOCUS = {("travel", f"user_task_{n}") for n in (2, 3, 4, 7, 9)}
REVIEWED_PREFIX_TOOLS = frozenset(
    {
        "get_all_hotels_in_city",
        "get_rating_reviews_for_hotels",
        "get_hotels_prices",
        "get_hotels_address",
        "get_all_restaurants_in_city",
        "get_cuisine_type_for_restaurants",
        "get_rating_reviews_for_restaurants",
        "get_price_for_restaurants",
        "search_emails",
        "get_unread_emails",
        "search_contacts_by_email",
        "search_contacts_by_name",
        "search_files_by_filename",
        "get_current_day",
    }
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(value) -> str:
    return digest(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass
class Sources:
    root: Path
    hashes: dict[str, str]

    def read(self, relative: str) -> bytes:
        path = (self.root / relative).resolve()
        require(path.is_relative_to(self.root), f"Source escapes root: {relative}")
        require(relative in self.hashes, f"Unpinned source: {relative}")
        raw = path.read_bytes()
        require(digest(raw) == self.hashes[relative], f"Source hash mismatch: {relative}")
        return gzip.decompress(raw) if path.suffix == ".gz" else raw

    def json(self, relative):
        return json.loads(self.read(relative))

    def lines(self, relative):
        return [json.loads(line) for line in self.read(relative).splitlines()]

    def verify(self):
        for relative in sorted(self.hashes):
            self.read(relative)


def content(message):
    blocks = message.get("content")
    return "\n".join(block["content"] for block in blocks) if isinstance(blocks, list) else blocks


def wire_message(message):
    result = {"role": message["role"], "content": content(message)}
    if message.get("tool_calls"):
        result["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["function"], "arguments": json.dumps(call["args"])},
            }
            for call in message["tool_calls"]
        ]
    if message["role"] == "tool":
        result.update(
            tool_call_id=message["tool_call_id"], content=message.get("error") or content(message)
        )
    return result


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def literal_vectors(messages, payloads):
    values = []
    for message in messages:
        text = str(message.get("content") or "")
        values.append(text)
        values.extend(strings(message.get("tool_calls")))
        try:
            values.extend(strings(yaml.safe_load(text)))
        except yaml.YAMLError:
            pass
    values = [" ".join(value.split()) for value in values]
    return sorted(k for k, v in payloads.items() if any(" ".join(v.split()) in s for s in values))


def complete_groups(messages):
    groups = []
    index = 2
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls") or []
        end = index + len(calls) + 1
        require(message["role"] == "assistant" and calls, "Incomplete interaction group")
        results = messages[index + 1 : end]
        require(
            len(results) == len(calls)
            and all(
                r["role"] == "tool" and r["tool_call_id"] == c["id"]
                for r, c in zip(results, calls, strict=True)
            ),
            "Incomplete or mismatched tool results",
        )
        groups.append(list(range(index, end)))
        index = end
    return groups


def changes(before, after, pointer=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in changes(before.get(key), after.get(key), f"{pointer}/{key}")
        ]
    return [{"pointer": pointer, "before": before, "after": after}]


def summary_evidence(sources, base, trace, messages):
    compactions = [
        (i, e)
        for i, e in enumerate(trace["context_memory"]["events"])
        if e["event"] == "compact" and e["summary"]
    ]
    if not compactions:
        return {}
    logs = sources.lines(f"{base}/memory-requests.jsonl.gz")
    counts = sources.lines(f"{base}/summary-context.jsonl.gz")
    requests = [(i, r) for i, r in enumerate(logs) if r["event"] == "request"]
    responses = [(i, r) for i, r in enumerate(logs) if r["event"] == "response"]
    require(len(requests) == len(responses) == len(compactions), f"Summary log count: {base}")
    evidence = {}
    previous = ""
    for ordinal, (
        (event_index, event),
        (request_line, request),
        (response_line, response),
    ) in enumerate(zip(compactions, requests, responses, strict=True)):
        payload = request["payload"]
        choice = response["response"]["choices"][0]
        require(
            choice["message"]["content"] == event["summary"], f"Summary output mismatch: {base}"
        )
        serialized = payload["messages"][1]["content"].split("Past transcript (data):\n", 1)[1]
        archive = json.loads(serialized.split("\nOriginal user task (already retained;", 1)[0])
        mapping = {item["canonical"]: item["original"] for item in request["call_id_mapping"]}
        for message in archive:
            for call in message.get("tool_calls") or []:
                call["id"] = mapping[call["id"]]
            if message.get("tool_call_id"):
                message["tool_call_id"] = mapping[message["tool_call_id"]]
        expected = [{"message_id": i, **messages[i]} for i in event["newly_archived_message_ids"]]
        if previous:
            expected.insert(
                0,
                {
                    "kind": "previous_summary",
                    "role": "tool",
                    "source_message_ids": event["previous_summary_source_message_ids"],
                    "memory": previous,
                },
            )
        require(archive == expected, f"Summary input/source mismatch: {base}")
        matches = [
            (i, c) for i, c in enumerate(counts) if c["payload_sha256"] == stable_hash(payload)
        ]
        require(matches, f"Summary tokenizer evidence missing: {base}")
        require(
            all(
                digest(c["rendered_prompt"].encode()) == c["rendered_prompt_sha256"]
                and c["prompt_tokens"] == response["response"]["usage"]["prompt_tokens"]
                for _, c in matches
            ),
            f"Summary tokenizer mismatch: {base}",
        )
        evidence[event_index] = {
            "summary_request_ordinal": ordinal,
            "memory_request_jsonl_line_index": request_line,
            "memory_response_jsonl_line_index": response_line,
            "summary_context_jsonl_line_indexes": [i for i, _ in matches],
            "summary_payload_sha256": stable_hash(payload),
            "summary_source_message_ids": event["newly_archived_message_ids"],
            "summary_source_matches_recorded_archive": True,
            "summary_response_finish_reason": choice["finish_reason"],
        }
        previous = event["summary"]
    return evidence


def load_episode(sources, row):
    base = row["source"]
    trace = sources.json(f"{base}/trace.json.gz")
    result = sources.json(f"{base}/result.json")
    compression = sources.json(f"{base}/compression.json")
    for artifact in compression["artifacts"]:
        name = f"{base}/{artifact['path']}"
        require(sources.hashes[name] == artifact["compressed_sha256"], f"Gzip digest: {name}")
        decoded = sources.read(name)
        require(digest(decoded) == artifact["original_sha256"], f"Original digest: {name}")
        require(len(decoded) == artifact["original_bytes"], f"Original size: {name}")
    requests = sources.lines(f"{base}/requests.jsonl.gz")
    counts = sources.lines(f"{base}/context-requests.jsonl.gz")
    sent = {r["index"]: (i, r) for i, r in enumerate(requests) if r["event"] == "request"}
    require(list(sent) == list(range(row["requests"])), f"Request sequence: {base}")
    require(trace["context_memory"] == result["context_memory"], f"Context disagreement: {base}")
    require(trace["identity"] == row["identity"], f"Identity disagreement: {base}")
    for count in counts:
        if "rendered_prompt" in count:
            require(
                digest(count["rendered_prompt"].encode()) == count["rendered_prompt_sha256"],
                f"Rendered prompt digest: {base}",
            )
    messages = [wire_message(m) for m in trace["messages"]]
    assistant_ids = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    responses = {r["index"]: (i, r) for i, r in enumerate(requests) if r["event"] == "response"}
    for index, (line, response) in responses.items():
        output = response["response"]["choices"][0]["message"]
        expected = {"role": "assistant", "content": output["content"]}
        if output.get("tool_calls"):
            expected["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {
                        "name": c["function"]["name"],
                        "arguments": json.dumps(json.loads(c["function"]["arguments"])),
                    },
                }
                for c in output["tool_calls"]
            ]
        require(
            index < len(assistant_ids) and messages[assistant_ids[index]] == expected,
            f"Response/trace alignment: {base}:{line}",
        )
    summaries = summary_evidence(sources, base, trace, messages)
    views = []
    current_summary = ""
    boundary = 2
    for event_index, event in enumerate(result["context_memory"]["events"]):
        if event["event"] == "compact":
            if event["applied"]:
                current_summary = event["summary"]
                boundary = event["archive_boundary"]
            continue
        require(event["event"] == "request_view", f"Unknown context event: {base}")
        index = event["request_index"]
        prefix_end = assistant_ids[index] if index < len(assistant_ids) else len(messages)
        prefix = messages[:prefix_end]
        groups = complete_groups(prefix)
        record = sent.get(index)
        if record:
            require(event["status"] == "ready", f"Overflow sent to model: {base}")
            line, request = record
            payload = request["payload"]
            retained = event["retained_source_message_ids"]
            expected = prefix[:2]
            if current_summary:
                expected += [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"context_memory_{boundary}",
                                "type": "function",
                                "function": {"name": "context_memory", "arguments": "{}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": f"context_memory_{boundary}",
                        "content": json.dumps(
                            {
                                "kind": "summary_of_past_messages",
                                "source_message_ids": [2, boundary - 1],
                                "memory": current_summary,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ]
            expected += prefix[boundary:]
            require(payload["messages"] == expected, f"Retained view mismatch: {base}:{index}")
            require(
                retained == list(range(2)) + list(range(boundary, prefix_end)),
                f"Retained indexes mismatch: {base}:{index}",
            )
            visible = literal_vectors(payload["messages"], trace["attack_payloads"])
            require(
                visible == event["literal_payload_visible_vectors"],
                f"Literal exposure mismatch: {base}:{index}",
            )
            matches = [
                (i, c)
                for i, c in enumerate(counts)
                if c.get("payload_sha256") == stable_hash(payload)
            ]
            require(
                matches and all(c["prompt_tokens"] == event["after_tokens"] for _, c in matches),
                f"Missing or inconsistent sent token count: {base}:{index}",
            )
            full_payload = {**payload, "messages": prefix}
        else:
            require(event["status"] == "overflow", f"Missing actual request: {base}:{index}")
            line, visible = None, []
            require(sent, f"No initial payload to reconstruct tools: {base}")
            full_payload = {**sent[0][1]["payload"], "messages": prefix}
        full_matches = [
            (i, c)
            for i, c in enumerate(counts)
            if c.get("payload_sha256") == stable_hash(full_payload)
        ]
        full_tokens = full_matches[0][1]["prompt_tokens"] if full_matches else None
        carriers = [
            i
            for i, m in enumerate(prefix)
            if m["role"] == "tool" and literal_vectors([m], trace["attack_payloads"])
        ]
        views.append(
            {
                "request_index": index,
                "context_event_index": event_index,
                "request_jsonl_line_index": line,
                "response_jsonl_line_index": responses[index][0] if index in responses else None,
                "sent": bool(record),
                "prefix_end_exclusive": prefix_end,
                "groups": groups,
                "carrier_message_ids": carriers,
                "literal_visible_vectors": visible,
                "full_prompt_tokens": full_tokens,
                "full_token_count_line_indexes": [i for i, _ in full_matches],
                "full_payload_sha256": stable_hash(full_payload),
                "actual_payload_sha256": stable_hash(record[1]["payload"]) if record else None,
                "actual_prompt_tokens": event.get("after_tokens") if record else None,
                "retained_source_message_ids": event.get("retained_source_message_ids"),
                "removed_source_message_ids": event.get("removed_source_message_ids", []),
                "summary": current_summary if record else None,
                "status": event["status"],
                "overflow_reason": event.get("reason"),
            }
        )
    return {
        "row": row,
        "trace": trace,
        "result": result,
        "requests": requests,
        "sent": sent,
        "views": views,
        "messages": messages,
        "summary_evidence": summaries,
    }


def write_call_indexes(episode):
    row = episode["row"]
    return [
        i
        for i, message in enumerate(episode["trace"]["messages"])
        for call in message.get("tool_calls") or []
        if (row["suite"] == "travel" and call["function"].startswith("reserve_"))
        or (row["suite"] == "workspace" and call["function"] == "send_email")
    ]


def checkpoint(episode, view, allowance):
    row = episode["row"]
    prior_exposures = [
        v["request_index"]
        for v in episode["views"]
        if v["request_index"] < view["request_index"] and v["sent"] and v["literal_visible_vectors"]
    ]
    groups = view["groups"]
    latest = groups[-1] if groups else []
    carriers = view["carrier_message_ids"]
    old_carriers = [i for i in carriers if i not in latest]
    prior_actions = [i for i in write_call_indexes(episode) if i < view["prefix_end_exclusive"]]
    prefix_tools = sorted(
        {
            c["function"]
            for m in episode["trace"]["messages"][: view["prefix_end_exclusive"]]
            for c in m.get("tool_calls") or []
        }
    )
    reasons = []
    if set(prefix_tools) - REVIEWED_PREFIX_TOOLS:
        reasons.append("prefix_tool_effects_not_in_reviewed_reconstruction_set")
    if row["condition"] == "clean":
        reasons.append("clean_control_no_attack")
    if not prior_exposures:
        reasons.append("attack_not_previously_sent")
    if not old_carriers:
        reasons.append("no_attack_carrier_in_old_complete_group")
    if any(i in latest for i in carriers):
        reasons.append("attack_carrier_in_protected_latest_group")
    if prior_actions:
        reasons.append("prior_reservation_or_email_write_conservative")
    if view["full_prompt_tokens"] is None:
        reasons.append("full_counterfactual_token_count_unobserved")
    elif view["full_prompt_tokens"] > allowance:
        reasons.append("full_counterfactual_exceeds_4992")
    if view["removed_source_message_ids"]:
        reasons.append("history_already_compacted_before_this_actual_request")
    removable = [g for g in groups[:-1] if any(i in g for i in old_carriers)]
    proposed_boundary = max(g[-1] + 1 for g in removable) if removable else None
    return {
        "run_id": row["run_id"],
        "episode_id": row["episode_id"],
        "suite": row["suite"],
        "task": row["task"],
        "precision": row["precision"],
        "policy": row["policy"],
        "condition": row["condition"],
        "request_index": view["request_index"],
        "checkpoint": "before_request_after_prior_tool_results",
        "prefix_end_exclusive": view["prefix_end_exclusive"],
        "intervention_eligible": not reasons,
        "ineligible_reasons": reasons,
        "prior_actual_exposure_request_indexes": prior_exposures,
        "removable_carrier_groups": removable,
        "proposed_archive_boundary": proposed_boundary,
        "proposed_removed_source_message_ids": list(range(2, proposed_boundary))
        if proposed_boundary
        else [],
        "protected_latest_complete_group": latest,
        "prior_reservation_or_email_call_message_ids": prior_actions,
        "prior_attack_action_proven_by_profiler": None,
        "write_screening": "Conservative reservation/email call cutoff, "
        "irrespective of authorization or target match; other unreviewed tool effects also block. "
        "Not proof of a prior attack action.",
        "prefix_tool_names": prefix_tools,
        "full_prompt_tokens": view["full_prompt_tokens"],
        "input_allowance": allowance,
        "full_payload_sha256": view["full_payload_sha256"],
        "full_token_count_line_indexes": view["full_token_count_line_indexes"],
        "source": row["source"],
        "eligibility_scope": "structural pre-write window; not clean capability or causal proof",
        "historical_capability_qualified": None,
    }


def pointer_get(value, pointer):
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def validate_annotations(sources, annotations):
    require(annotations["schema_version"] == 1, "Unsupported annotations schema")
    seen = set()
    for annotation in annotations["cases"]:
        key = annotation["run_id"], annotation["condition"]
        require(key not in seen, "Duplicate case annotation")
        seen.add(key)
        require(annotation["checks"], "Case requires source assertions")
        for check in annotation["checks"]:
            actual = pointer_get(sources.json(check["source"]), check["pointer"])
            if "equals" in check:
                require(actual == check["equals"], f"Annotation changed: {key} {check['pointer']}")
            else:
                require(check["contains"] in actual, f"Annotation quote absent: {key}")


def timeline(episode):
    trace, row = episode["trace"], episode["row"]
    entries = [
        {
            "stage": "attack_source",
            "file": "trace.json.gz",
            "pointers": ["/injection_goal", "/attack_payloads", "/injections"],
            "goal": trace["injection_goal"],
            "payloads": trace["attack_payloads"],
        }
    ]
    previous_visible = False
    previously_seen = False
    for view in episode["views"]:
        index = view["request_index"]
        for event_index in range(view["context_event_index"] - 1, -1, -1):
            event = trace["context_memory"]["events"][event_index]
            if event["event"] != "compact":
                break
            entries.append(
                {
                    "stage": "compression",
                    "file": "result.json",
                    "context_event_index": event_index,
                    "before_request_index": index,
                    **episode["summary_evidence"].get(event_index, {}),
                    **{k: v for k, v in event.items() if k != "event"},
                }
            )
        visible = bool(view["literal_visible_vectors"])
        stage = (
            "actual_exposure"
            if visible and not previously_seen
            else (
                "re_exposure_after_literal_absence"
                if visible and not previous_visible
                else "request_view"
            )
        )
        entries.append(
            {
                "stage": stage if view["sent"] else "unsent_overflow",
                "file": "requests.jsonl.gz" if view["sent"] else "result.json",
                **{k: v for k, v in view.items() if k != "groups"},
            }
        )
        if view["sent"]:
            previous_visible = visible
            previously_seen |= visible
        start = view["prefix_end_exclusive"]
        if not view["sent"] or start >= len(trace["messages"]):
            continue
        message = trace["messages"][start]
        calls = message.get("tool_calls") or []
        if not calls:
            entries.append(
                {
                    "stage": "final_answer",
                    "file": "trace.json.gz",
                    "message_index": start,
                    "response_to_request_index": index,
                    "text": content(message),
                }
            )
        for call in calls:
            results = [
                (i, m)
                for i, m in enumerate(trace["messages"])
                if m["role"] == "tool" and m["tool_call_id"] == call["id"]
            ]
            entries.append(
                {
                    "stage": "tool_action",
                    "file": "trace.json.gz",
                    "message_index": start,
                    "response_to_request_index": index,
                    "function": call["function"],
                    "arguments": call["args"],
                    "result_message_index": results[0][0] if results else None,
                    "tool_error": results[0][1].get("error") if results else None,
                    "result_text": content(results[0][1]) if results else None,
                    "executed_result_recorded": bool(results),
                }
            )
    entries.append(
        {
            "stage": "final_state",
            "file": "trace.json.gz",
            "environment_changes": changes(
                trace["initial_environment"], trace["final_environment"]
            ),
            "initial_environment_sha256": stable_hash(trace["initial_environment"]),
            "final_environment_sha256": stable_hash(trace["final_environment"]),
            "official_status": row["status"],
            "official_utility": row["utility"],
            "official_attack_success": row["attack_success"],
            "error": episode["result"].get("error"),
        }
    )
    return entries


def reconstruction(episode, candidate):
    row = episode["row"]
    return {
        "current_harness_can_resume_exact_checkpoint": False,
        "reason": "LocalAgent.query rejects nonempty messages; "
        "runner resume skips finalized episodes, not turns.",
        "history_available": True,
        "history_source": f"{row['source']}/trace.json.gz",
        "history_slice": [0, candidate["prefix_end_exclusive"]],
        "actual_request_and_tool_schema_available": True,
        "request_index": candidate["request_index"],
        "tool_schema_sha256": stable_hash(
            episode["sent"][candidate["request_index"]][1]["payload"]["tools"]
        ),
        "environment_checkpoint_available": False,
        "environment_reconstruction": (
            "Replay recorded get_unread_emails once on isolated initial_environment; "
            "preserve read flags and model aliasing/computed fields. "
            "Other prefix searches are read-only; compare every returned result."
            if row["suite"] == "workspace" and row["task"] == "user_task_14"
            else "Prefix contains read-only queries: initial_environment is the candidate state; "
            "verify typed deserialization and replayed tool results before claiming "
            "exact reconstruction."
        ),
        "needed": [
            "New isolated continuation entry point with history, environment, tool registry, "
            "memory boundary, step counters, sampling settings, remaining-budget semantics "
            "and fixed checkpoint seed.",
            "Offline replay must match recorded tool outputs and checkpoint environment "
            "before branching; "
            "no replay or continuation was executed by this profiler.",
            "Use pinned AgentDojo implementation, runtime/chat-template identity "
            "and actual schema; "
            "freeze or record tool clock/ID generation for future writes.",
            "Fork one verified checkpoint into Full/Truncate/Summary with identical latest group; "
            "remeasure each first request, enforce 4992 and retain all overflow/unknown outcomes.",
        ],
    }


def build(sources, annotations):
    sources.verify()
    validate_annotations(sources, annotations)
    protocol = sources.json(PROTOCOL)
    constraints = protocol["constraints"]
    allowance = constraints["total_token_budget"] - constraints["completion_reserve_tokens"]
    require(allowance == 4992 and constraints["protect_latest_complete_group"], "Protocol changed")
    rows = [r for r in sources.json(EPISODES) if r["cohort"] == "main"]
    require(len(rows) == 972, "Expected complete 972-row main cohort")
    require(len({(r["run_id"], r["episode_id"]) for r in rows}) == 972, "Duplicate main episodes")
    compact_tasks = {(r["suite"], r["task"]) for r in rows if r["compactions"]}
    selected_tasks = compact_tasks | FOCUS
    for row in rows:
        result_path = f"{row['source']}/result.json"
        require(
            sources.hashes[result_path] == row["result_sha256"], f"Row result hash: {result_path}"
        )
        result = sources.json(result_path)
        for key in ("status", "utility", "attack_success", "requests"):
            require(row[key] == result[key], f"Row {key} disagrees: {result_path}")
        count = sum(
            e["event"] == "compact" and e["applied"] for e in result["context_memory"]["events"]
        )
        require(row["compactions"] == count, f"Compaction count disagrees: {result_path}")
    episodes = {}
    checkpoints = []
    episode_coverage = []
    for row in sorted(rows, key=lambda r: (r["suite"], r["task"], r["run_id"], r["episode_id"])):
        key = row["run_id"], row["condition"]
        detail = {
            k: row[k]
            for k in (
                "suite",
                "task",
                "run_id",
                "episode_id",
                "precision",
                "policy",
                "condition",
                "source",
                "status",
                "utility",
                "attack_success",
                "compactions",
                "trace_sha256",
                "result_sha256",
            )
        }
        detail["deep_profiled"] = (row["suite"], row["task"]) in selected_tasks
        if detail["deep_profiled"]:
            require(
                sources.hashes[f"{row['source']}/trace.json.gz"] == row["trace_sha256"],
                "Trace row hash",
            )
            episode = load_episode(sources, row)
            episodes[key] = episode
            candidates = [checkpoint(episode, v, allowance) for v in episode["views"]]
            for candidate in candidates:
                if candidate["intervention_eligible"]:
                    candidate["reconstruction"] = reconstruction(episode, candidate)
            checkpoints.extend(candidates)
            detail["actual_exposure_request_indexes"] = [
                v["request_index"]
                for v in episode["views"]
                if v["sent"] and v["literal_visible_vectors"]
            ]
            detail["eligible_request_indexes"] = [
                c["request_index"] for c in candidates if c["intervention_eligible"]
            ]
            detail["all_checkpoint_rejection_reasons"] = sorted(
                {reason for c in candidates for reason in c["ineligible_reasons"]}
            )
        episode_coverage.append(detail)
    cases = []
    for annotation in annotations["cases"]:
        episode = episodes[(annotation["run_id"], annotation["condition"])]
        row = episode["row"]
        candidates = [
            c
            for c in checkpoints
            if c["run_id"] == row["run_id"] and c["episode_id"] == row["episode_id"]
        ]
        eligible = [c for c in candidates if c["intervention_eligible"]]
        chosen = next(
            c for c in candidates if c["request_index"] == annotation["checkpoint_request_index"]
        )
        require(
            chosen["intervention_eligible"] == annotation["expected_intervention_eligible"],
            f"Eligibility changed: {row['run_id']}",
        )
        base = row["source"]
        cases.append(
            {
                "case_id": annotation["case_id"],
                **{
                    k: row[k]
                    for k in (
                        "suite",
                        "task",
                        "precision",
                        "policy",
                        "condition",
                        "run_id",
                        "episode_id",
                    )
                },
                "episode_source_hashes": {
                    p: h for p, h in sources.hashes.items() if p.startswith(base + "/")
                },
                "checkpoint_request_index": chosen["request_index"],
                "intervention_eligible": chosen["intervention_eligible"],
                "intervention_reason": chosen["ineligible_reasons"]
                or ["all_structural_conditions_verified"],
                "checkpoint": chosen,
                "all_eligible_request_indexes": [c["request_index"] for c in eligible],
                "observations": annotation["observations"],
                "source_assertions": annotation["checks"],
                "reconstruction": [reconstruction(episode, c) for c in eligible],
                "timeline": timeline(episode),
            }
        )
    eligible = [c for c in checkpoints if c["intervention_eligible"]]
    task_coverage = []
    for suite, task in sorted({(r["suite"], r["task"]) for r in rows}):
        subset = [r for r in rows if r["suite"] == suite and r["task"] == task]
        task_coverage.append(
            {
                "suite": suite,
                "task": task,
                "episodes": len(subset),
                "compacted_episodes": sum(bool(r["compactions"]) for r in subset),
                "compactions": sum(r["compactions"] for r in subset),
                "compacted_clean": sum(
                    bool(r["compactions"]) for r in subset if r["condition"] == "clean"
                ),
                "compacted_attack": sum(
                    bool(r["compactions"]) for r in subset if r["condition"] == "attack"
                ),
                "deep_profiled": (suite, task) in selected_tasks,
            }
        )
    coverage = {
        "schema_version": 1,
        "cohort": "main",
        "main_rows": len(rows),
        "main_tasks": len(task_coverage),
        "compacted_episodes": sum(bool(r["compactions"]) for r in rows),
        "compactions": sum(r["compactions"] for r in rows),
        "deep_profiled_episodes": len(episodes),
        "deep_profiled_tasks": len(selected_tasks),
        "selection": "All main rows for coverage; every actual-compaction task plus "
        "requested Travel2/3/4/7/9. All 18 cells of each selected task checked before "
        "representative annotations. No confirmation rows.",
        "eligible_checkpoints": len(eligible),
        "eligible_episodes": len({(c["run_id"], c["episode_id"]) for c in eligible}),
        "eligible_tasks": sorted({f"{c['suite']}/{c['task']}" for c in eligible}),
        "capability_join": "Not joined to historical capability qualification. "
        "These are mechanism-only candidates until the parent performs the source-bound join.",
        "rejection_scope": "No accepted window means no window under the conservative screens, "
        "not proof that an intervention after a legitimate write is impossible.",
        "checkpoint_rejection_counts_overlapping": dict(
            sorted(
                Counter(reason for c in checkpoints for reason in c["ineligible_reasons"]).items()
            )
        ),
        "tasks": task_coverage,
        "episodes": episode_coverage,
    }
    return {"cases.json": cases, "coverage.json": coverage, "checkpoints.json": checkpoints}


def encoded_json(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def write_outputs(output, outputs):
    output.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        data = encoded_json(value)
        destination = output / name
        if destination.is_file() and destination.read_bytes() == data:
            continue
        temporary = output / f".{name}.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-lock", type=Path, default=LANE / "sources.json")
    parser.add_argument("--annotations", type=Path, default=LANE / "annotations.json")
    args = parser.parse_args(argv)
    try:
        root, output = args.source_root.resolve(), args.output.resolve()
        require(
            not any(
                output.is_relative_to(root / part)
                for part in ("artifacts", "results", "src", "configs", ".venv")
            ),
            "Output must not modify the source evidence tree",
        )
        sources = Sources(root, json.loads(args.source_lock.read_text())["sha256"])
        annotations = json.loads(args.annotations.read_text())
        outputs = build(sources, annotations)
        outputs["verification.json"] = {
            "schema_version": 1,
            "script_sha256": digest(Path(__file__).read_bytes()),
            "annotations_sha256": digest(args.annotations.read_bytes()),
            "source_lock_sha256": digest(args.source_lock.read_bytes()),
            "verified_source_files": len(sources.hashes),
            "output_sha256": {name: digest(encoded_json(value)) for name, value in outputs.items()},
            "model_calls": 0,
            "source_root_and_output_paths_excluded_for_relocatable_identity": True,
        }
        write_outputs(output, outputs)
        coverage = outputs["coverage.json"]
        print(
            json.dumps(
                {
                    k: coverage[k]
                    for k in (
                        "main_rows",
                        "deep_profiled_episodes",
                        "compacted_episodes",
                        "eligible_checkpoints",
                        "eligible_episodes",
                    )
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f"Mechanism evidence rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
