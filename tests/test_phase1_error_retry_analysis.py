"""Error recovery auditing must retain unknowns and reject corrupt evidence."""

import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "retry_analysis", SCRIPTS / "analyze_phase1_error_retries.py"
)
ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYSIS)


def test_unknown_outcomes_have_explicit_bounds():
    rate = ANALYSIS.score_rate(
        [{"utility": True}, {"utility": False}, {"utility": None}], "utility"
    )
    assert rate["successes"] == 1
    assert rate["denominator"] == 3
    assert rate["unknown"] == 1
    assert rate["known_rate"] == 0.5
    assert rate["unknown_outcome_bounds"] == [1 / 3, 2 / 3]


def fixture_episode(directory, mutation):
    config = {
        "model": {
            "model": "test-local",
            "seed": 17,
            "temperature": 0.7,
            "max_tokens": 2048,
            "extra_body": {"cache_prompt": False},
        },
        "runner": {"max_tool_steps": 15, "request_timeout_seconds": 120},
    }
    expected = {
        "episode_id": "task__clean",
        "condition": "clean",
        "user_task_id": "task",
        "injection_task_id": None,
    }
    result = {
        **expected,
        "identity": "identity",
        "status": "error",
        "utility": None,
        "attack_success": None,
        "requests": 1,
        "tool_calls": 0,
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "server_memory": {"peak_rss_bytes": 100},
        "observed_injection_vectors": [],
    }
    payload = {**config["model"], "stream": False, **config["model"]["extra_body"]}
    payload.pop("extra_body")
    events = [
        {"event": "request", "index": 0, "payload": payload, "timeout_seconds": 120},
        {
            "event": "response",
            "index": 0,
            "latency_seconds": 0.5,
            "response": {"usage": result["usage"]},
        },
    ]
    if mutation == "duplicate_terminal":
        events.append(events[1])
    elif mutation == "terminal_before_request":
        events.reverse()
    elif mutation == "wrong_seed":
        payload["seed"] = 29
    elif mutation == "usage_mismatch":
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    elif mutation == "error_scored":
        result["utility"] = False
    elif mutation in {"first_request_error", "first_request_error_zero_keys"}:
        events[1] = {
            "event": "request_error",
            "index": 0,
            "latency_seconds": 120.0,
            "error_type": "APITimeoutError",
            "message": "Request timed out.",
        }
        result["usage"] = (
            {}
            if mutation == "first_request_error"
            else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
    trace = {**expected, "identity": "identity", "messages": []}
    (directory / "result.json").write_text(json.dumps(result))
    compression = []
    for name, data in (
        ("trace.json", json.dumps(trace)),
        ("requests.jsonl", "\n".join(json.dumps(event) for event in events)),
    ):
        original = data.encode()
        compressed = gzip.compress(original, mtime=0)
        target = directory / (name + ".gz")
        target.write_bytes(compressed)
        compression.append(
            {
                "path": target.name,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
                "original_bytes": len(original),
                "compressed_bytes": len(compressed),
            }
        )
    (directory / "compression.json").write_text(json.dumps({"artifacts": compression}))
    if mutation == "corrupt_gzip":
        path = directory / "trace.json.gz"
        path.write_bytes(path.read_bytes()[:-3])
    return {"identity": "identity", "config": config}, expected


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_terminal",
        "terminal_before_request",
        "wrong_seed",
        "usage_mismatch",
        "error_scored",
        "corrupt_gzip",
    ],
)
def test_rejects_corrupt_or_inconsistent_episode(tmp_path, mutation):
    manifest, expected = fixture_episode(tmp_path, mutation)
    with pytest.raises((ValueError, EOFError)):
        ANALYSIS.verify_episode(tmp_path, manifest, expected)


def test_accepts_lossless_unknown_error(tmp_path, monkeypatch):
    manifest, expected = fixture_episode(tmp_path, None)
    monkeypatch.setattr(ANALYSIS, "ROOT", tmp_path.parent)
    row, _ = ANALYSIS.verify_episode(tmp_path, manifest, expected)
    assert row["status"] == "error"
    assert row["utility"] is None
    assert row["total_tokens"] == 12


@pytest.mark.parametrize("mutation", ["first_request_error", "first_request_error_zero_keys"])
def test_accepts_first_request_timeout_without_returned_tokens(tmp_path, monkeypatch, mutation):
    manifest, expected = fixture_episode(tmp_path, mutation)
    monkeypatch.setattr(ANALYSIS, "ROOT", tmp_path.parent)
    row, _ = ANALYSIS.verify_episode(tmp_path, manifest, expected)
    assert row["status"] == "error"
    assert row["utility"] is None
    assert row["response_count"] == 0
    assert row["request_error_count"] == 1
