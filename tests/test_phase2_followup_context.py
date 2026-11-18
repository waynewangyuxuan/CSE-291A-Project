import copy
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "phase2_context_profile", ROOT / "scripts/profile_phase2_context.py"
)
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
SPEC.loader.exec_module(profile)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def raw_json(value):
    return json.dumps(value).encode()


def payload_hash(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


@pytest.fixture(scope="module")
def runtime():
    return profile.load_runtime(profile.Evidence(ROOT))


class EpisodeFixture:
    def __init__(self, root, counts=(1000, 1200, 1700, 1900), sizes=(1, 1, 1), overflow=False):
        self.root = root
        self.source = "artifacts/phase2-main-v1/main-001/runs/f16-full-test/episodes/test__clean"
        self.directory = root / self.source
        self.directory.mkdir(parents=True)
        self.manifest_path = root / "artifacts/phase2-main-v1/archives/main-001.manifest.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.row = {
            "block": "main-001",
            "cohort": "main",
            "precision": "f16",
            "policy": "full",
            "suite": "test",
            "task": "user_task_1",
            "condition": "clean",
            "identity": "test-id",
            "episode_id": "test__clean",
            "status": "error" if overflow else "completed",
            "requests": len(counts) - int(overflow),
            "source": self.source,
        }
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "task"}]
        trace_messages = [
            {"role": "system", "content": [{"type": "text", "content": "system"}]},
            {"role": "user", "content": [{"type": "text", "content": "task"}]},
        ]
        prefixes = [copy.deepcopy(messages)]
        for group_index, size in enumerate(sizes):
            calls = [
                {
                    "id": f"g{group_index}c{j}",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
                for j in range(size)
            ]
            messages.append({"role": "assistant", "content": None, "tool_calls": calls})
            trace_messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": c["id"], "function": "lookup", "args": {}} for c in calls
                    ],
                }
            )
            for call in calls:
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": "data"})
                trace_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": [{"type": "text", "content": "data"}],
                    }
                )
            prefixes.append(copy.deepcopy(messages))
        if not overflow:
            trace_messages.append(
                {"role": "assistant", "content": [{"type": "text", "content": "done"}]}
            )
        self.events = []
        self.counts = []
        views = []
        for i, tokens in enumerate(counts):
            sent = i < self.row["requests"]
            payload = {"model": "f16-test", "max_tokens": 2048, "messages": prefixes[i]}
            rendered = f"synthetic rendered prompt {i}"
            self.counts.append(
                {
                    "event": "rendered_token_count",
                    "prompt_tokens": tokens,
                    "payload_sha256": payload_hash(payload),
                    "rendered_prompt": rendered,
                    "rendered_prompt_sha256": digest(rendered.encode()),
                }
            )
            view = {
                "event": "request_view",
                "request_index": i,
                "input_allowance": 4992,
                "output_reserve": 2048,
                "max_context_tokens": 7040,
                "before_tokens": tokens,
                "status": "ready" if sent else "overflow",
            }
            if sent:
                view.update(
                    after_tokens=tokens,
                    retained_source_message_ids=list(range(len(prefixes[i]))),
                    removed_source_message_ids=[],
                    summary_source_message_ids=[],
                    reported_prompt_tokens=tokens,
                    token_count_matches=True,
                    literal_payload_visible_vectors=[],
                )
                output = (
                    messages[len(prefixes[i])]
                    if i < len(sizes)
                    else {"role": "assistant", "content": "done"}
                )
                self.events.extend(
                    [
                        {"event": "request", "index": i, "payload": payload},
                        {
                            "event": "response",
                            "index": i,
                            "response": {
                                "usage": {"prompt_tokens": tokens},
                                "choices": [
                                    {
                                        "finish_reason": "tool_calls"
                                        if output.get("tool_calls")
                                        else "stop",
                                        "message": output,
                                    }
                                ],
                            },
                        },
                    ]
                )
            views.append(view)
        memory = {"policy": "full", "compactions": 0, "events": views}
        identity = {
            "episode_id": "test__clean",
            "condition": "clean",
            "identity": "test-id",
            "user_task_id": "user_task_1",
            "requests": self.row["requests"],
            "context_memory": memory,
        }
        self.result = {
            **identity,
            "status": self.row["status"],
            "utility": None if overflow else False,
            "attack_success": None,
            "error": {"type": "ContextBudgetExceededError"} if overflow else None,
        }
        self.trace = {
            **identity,
            "messages": trace_messages,
            "injections": {},
            "attack_payloads": {},
        }
        self.save()

    def save(self):
        files = {"result.json": raw_json(self.result)}
        receipts = []
        for name, value in (
            ("trace.json.gz", self.trace),
            ("requests.jsonl.gz", self.events),
            ("context-requests.jsonl.gz", self.counts),
        ):
            raw = b"\n".join(raw_json(row) for row in value) if "jsonl" in name else raw_json(value)
            compressed = gzip.compress(raw, mtime=0)
            files[name] = compressed
            receipts.append(
                {
                    "path": name,
                    "original_sha256": digest(raw),
                    "compressed_sha256": digest(compressed),
                    "original_bytes": len(raw),
                    "compressed_bytes": len(compressed),
                }
            )
        files["compression.json"] = raw_json({"artifacts": receipts})
        members = []
        for name, data in files.items():
            (self.directory / name).write_bytes(data)
            members.append(
                {"path": f"{self.source}/{name}", "sha256": digest(data), "bytes": len(data)}
            )
        self.manifest_path.write_bytes(raw_json({"block": "main-001", "members": members}))
        self.row.update(
            trace_sha256=digest(files["trace.json.gz"]), result_sha256=digest(files["result.json"])
        )

    def run(self, runtime):
        return profile.profile_trajectory(profile.Evidence(self.root), self.row, runtime, 4992)


def test_sent_decision_has_removable_old_group_and_observed_steps(tmp_path, runtime):
    result = EpisodeFixture(tmp_path).run(runtime)
    assert result["initial_input_tokens_including_fixed_template_and_tools"] == 1000
    assert result["observed_history_peak_proxy_including_unsent"] == 900
    assert result["structurally_eligible_sent_candidates"] == [2, 3]
    candidate = result["candidates"][2]
    assert candidate["removable_message_ids"] == [2, 3]
    assert candidate["protected_newest_message_ids"] == [4, 5]
    assert candidate["remaining_sent_decisions_including_current"] == 2
    assert candidate["remaining_tool_decisions_observed"] == 1
    hit = profile.intervention(result, 512, "history_prompt_delta_proxy")
    assert hit["sent_request_index"] == 2
    assert hit["final_answer_only"] is False
    assert profile.intervention(result, 1024, "history_prompt_delta_proxy") is None
    assert (
        profile.intervention(result, 800, "history_prompt_delta_proxy")["final_answer_only"] is True
    )


def test_parallel_tool_calls_are_one_protected_exchange(tmp_path, runtime):
    result = EpisodeFixture(tmp_path, counts=(1000, 2000), sizes=(2,)).run(runtime)
    assert result["structurally_eligible_sent_candidates"] == []
    assert result["candidates"][1]["completed_tool_calls"] == 2
    assert result["candidates"][1]["completed_tool_exchanges"] == 1
    assert result["candidates"][1]["protected_newest_message_ids"] == [2, 3, 4]
    assert profile.intervention(result, 512, "history_prompt_delta_proxy") is None


def test_overflow_is_unsent_censored_prefix_even_with_removable_history(tmp_path, runtime):
    result = EpisodeFixture(tmp_path, counts=(1000, 1100, 9000), sizes=(1, 1), overflow=True).run(
        runtime
    )
    assert result["status"] == "censored_error_prefix"
    assert result["observed_history_peak_proxy_including_unsent"] == 8000
    assert result["observed_sent_history_peak_proxy"] == 100
    assert result["single_oversized_newest_result_candidates_proxy"] == [2]
    assert result["candidates"][2]["removable_old_group_count"] == 1
    assert result["candidates"][2]["sent_request_index"] is None
    assert result["candidates"][2]["remaining_sent_decisions_including_current"] == 0
    assert profile.intervention(result, 512, "history_prompt_delta_proxy") is None


def test_missing_source_does_not_become_short_history(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    (case.directory / "context-requests.jsonl.gz").unlink()
    with pytest.raises(profile.EvidenceError, match="Missing source evidence"):
        case.run(runtime)


def test_corrupt_source_is_rejected_against_archive(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    (case.directory / "requests.jsonl.gz").write_bytes(b"corrupt")
    with pytest.raises(profile.EvidenceError, match="Source hash mismatch"):
        case.run(runtime)


def test_missing_response_is_rejected_even_with_updated_receipts(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.events.pop()
    case.save()
    with pytest.raises(profile.EvidenceError, match="Missing response evidence"):
        case.run(runtime)


def test_payload_must_match_actual_trace_and_tokenization(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.counts[2]["payload_sha256"] = "0" * 64
    case.save()
    with pytest.raises(profile.EvidenceError, match="Candidate payload hash mismatch"):
        case.run(runtime)


def test_mismatched_tool_result_cannot_count_as_complete_exchange(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.trace["messages"][3]["tool_call_id"] = "missing-call"
    for event in case.events:
        if event["event"] == "request" and event["index"] > 0:
            event["payload"]["messages"][3]["tool_call_id"] = "missing-call"
            case.counts[event["index"]]["payload_sha256"] = payload_hash(event["payload"])
    case.save()
    with pytest.raises(ValueError, match="Incomplete or mismatched tool results"):
        case.run(runtime)


def test_request_error_does_not_claim_meaningful_intervention(tmp_path, runtime):
    case = EpisodeFixture(tmp_path, counts=(1000, 1200, 1800), sizes=(1, 1))
    case.row["status"] = case.result["status"] = "error"
    case.result["utility"] = None
    case.result["error"] = {"type": "TimeoutError"}
    case.events[-1] = {"event": "request_error", "index": 2, "error_type": "TimeoutError"}
    case.save()
    result = case.run(runtime)
    assert result["structurally_eligible_sent_candidates"] == []
    assert result["status"] == "censored_error_prefix"
    assert result["candidates"][2]["decision_kind"] == "request_error"


def test_error_outcome_cannot_be_rewritten_as_safe(tmp_path, runtime):
    case = EpisodeFixture(tmp_path, counts=(1000, 9000), sizes=(1,), overflow=True)
    case.result["attack_success"] = False
    case.save()
    with pytest.raises(profile.EvidenceError, match="Error must be unknown"):
        case.run(runtime)


def test_duplicate_and_incomplete_reference_sets_are_rejected():
    row = {
        "cohort": "main",
        "precision": "f16",
        "policy": "full",
        "suite": "test",
        "task": "user_task_1",
        "condition": "clean",
    }
    with pytest.raises(profile.EvidenceError, match="Duplicate reference"):
        profile.select_pairs([row, row])
    with pytest.raises(profile.EvidenceError, match="Expected all 54 main tasks, found 1"):
        profile.select_pairs([row])


def test_cli_failure_preserves_existing_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "summary.json"
    existing.write_bytes(b"existing verified result")
    command = [
        sys.executable,
        str(ROOT / "scripts/profile_phase2_context.py"),
        "--source-root",
        str(tmp_path),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Missing source evidence: configs/phase2-followup-v1/preparation.json" in result.stderr
    assert existing.read_bytes() == b"existing verified result"


def test_source_path_cannot_escape_root(tmp_path):
    with pytest.raises(profile.EvidenceError, match="Source escapes root"):
        profile.Evidence(tmp_path).read("../outside.json")


def test_profile_threshold_is_clean_bound_even_when_attack_peak_changes(tmp_path, runtime):
    clean = EpisodeFixture(tmp_path / "clean").run(runtime)
    attack = EpisodeFixture(
        tmp_path / "attack", counts=(1000, 1200, 8000), sizes=(1, 1), overflow=True
    ).run(runtime)
    candidates, _ = profile.pair_triggers(clean, attack, [0.3, 0.5], [512, 1024])
    assert candidates["profile_a0.3_g512"]["h"] == 512
    assert candidates["profile_a0.5_g1024"]["h"] == 1024
    assert candidates["profile_a0.3_g512"]["clean"]["sent_request_index"] == 2
    assert candidates["profile_a0.3_g512"]["attack"] is None
    attack["observed_history_peak_proxy_including_unsent"] = 20000
    rerun, _ = profile.pair_triggers(clean, attack, [0.3, 0.5], [512, 1024])
    assert rerun == candidates


def test_literal_text_in_unsent_candidate_is_not_exposure(tmp_path, runtime):
    case = EpisodeFixture(tmp_path, counts=(1000, 1100, 9000), sizes=(1, 1), overflow=True)
    case.trace["attack_payloads"] = {"v1": "target instruction", "v2": "target instruction"}
    case.trace["messages"][-1]["content"][0]["content"] = "target instruction"
    case.counts[-1]["payload_sha256"] = payload_hash(
        {
            "model": "f16-test",
            "max_tokens": 2048,
            "messages": [runtime.normalize(m) for m in case.trace["messages"]],
        }
    )
    case.save()
    result = case.run(runtime)
    assert result["first_literal_exposure_sent_request"] is None
    assert result["literal_exposure_sent_requests"] == []
    assert result["candidates"][2]["literal_vectors_in_candidate"] == ["v1", "v2"]
    assert result["candidates"][2]["literal_vectors_sent"] == []


def test_literal_payload_aliases_are_checked_without_original_carrier_prefix(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.trace["attack_payloads"] = {"v1": "data", "v2": "data"}
    case.trace["injections"] = {
        "v1": "unseen original carrier\n\ndata",
        "v2": "another carrier\n\ndata",
    }
    for view in case.result["context_memory"]["events"][1:]:
        view["literal_payload_visible_vectors"] = ["v1", "v2"]
    case.save()
    result = case.run(runtime)
    assert result["first_literal_exposure_sent_request"] == 1
    assert result["candidates"][2]["removable_old_literal_vectors"] == ["v1", "v2"]
    assert result["candidates"][2]["protected_newest_literal_vectors"] == ["v1", "v2"]
    assert (
        profile.intervention(result, 512, "history_prompt_delta_proxy")["exposure_timing"]
        == "after_prior_exposure"
    )


def test_response_contradicting_trace_is_rejected(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.events[-1]["response"]["choices"][0]["message"]["content"] = "different answer"
    case.save()
    with pytest.raises(profile.EvidenceError, match="Response/trace mismatch"):
        case.run(runtime)


def test_rendered_prompt_corruption_is_rejected_even_with_updated_receipts(tmp_path, runtime):
    case = EpisodeFixture(tmp_path)
    case.counts[1]["rendered_prompt"] = "wrong rendering"
    case.save()
    with pytest.raises(profile.EvidenceError, match="Rendered hash mismatch"):
        case.run(runtime)


def test_fixed_input_growth_guard_does_not_confuse_static_schema_with_history(tmp_path, runtime):
    clean = EpisodeFixture(tmp_path, counts=(4000, 4100, 4500, 4600)).run(runtime)
    candidates, _ = profile.pair_triggers(clean, clean, [0.3], [512])
    assert candidates["fixed_input_4096"]["clean"]["sent_request_index"] == 2
    assert candidates["fixed_input_4096_g512"]["clean"]["sent_request_index"] == 3
    assert candidates["fixed_input_4096_g512"]["clean"]["final_answer_only"] is True
    assert candidates["fixed_input_4096_g512"]["minimum_history_growth"] == 512


def test_equal_coverage_does_not_require_identical_floor_bound_thresholds(tmp_path, runtime):
    clean = EpisodeFixture(tmp_path, counts=(1000, 1200, 1700, 2200)).run(runtime)
    candidates, _ = profile.pair_triggers(clean, clean, [0.2, 0.3, 0.4, 0.5], [512])
    assert candidates["profile_a0.3_g512"]["h"] == 512
    assert candidates["profile_a0.5_g512"]["h"] == 600
    assert candidates["profile_a0.3_g512"]["clean"]["sent_request_index"] == 2
    assert candidates["profile_a0.5_g512"]["clean"]["sent_request_index"] == 2
    result = profile.floor_sensitivity(
        [{"suite": "test", "task": "user_task_1", "trigger_candidates": candidates}]
    )
    assert result["profile_a0.5_g512"]["floor_bound_tasks"] == 0
    assert result["profile_a0.5_g512"]["different_first_sent_request_from_a03"] == []
