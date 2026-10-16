"""Small synthetic measurement fixtures; never benchmark observations."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("export_phase1_evaluation")


def response(latency, prompt, completion, prompt_ms, predicted_ms):
    return {
        "event": "response",
        "latency_seconds": latency,
        "response": {
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
            "timings": {
                "prompt_n": prompt,
                "prompt_ms": prompt_ms,
                "predicted_n": completion,
                "predicted_ms": predicted_ms,
                "cache_n": 0,
            },
        },
    }


def request_csv(row, event):
    """Use the native requests.csv representation, including absent cells."""
    result = {key: row[key] for key in ("run_id", "episode_id", "suite", "condition")} | {
        "event": event["event"],
        "latency_seconds": str(event.get("latency_seconds", "")),
    }
    usage = (event.get("response") or {}).get("usage") or {}
    timings = (event.get("response") or {}).get("timings") or {}
    return result | {
        key: str(source[key]) if key in source else ""
        for source, fields in (
            (usage, ("prompt_tokens", "completion_tokens", "total_tokens")),
            (timings, ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n")),
        )
        for key in fields
    }


@pytest.fixture
def measured_fixture(driver, tmp_path):
    records, requests = [], []

    def add(run, condition, suite, task, status, utility, duration, events):
        path = tmp_path / f"{run}.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
        measured, _ = driver.analysis._request_measurements(path)
        row = {
            **measured,
            "run_id": run,
            # Reused by every run, as task/goal IDs are reused in real campaigns.
            "episode_id": "shared-episode-id",
            "suite": suite,
            "user_task_id": task,
            "condition": condition,
            "status": status,
            "utility": utility,
            "duration_seconds": duration,
            "requests": len(events),
            "prompt_tokens": sum(
                (event.get("response") or {}).get("usage", {}).get("prompt_tokens", 0)
                for event in events
            ),
            "completion_tokens": sum(
                (event.get("response") or {}).get("usage", {}).get("completion_tokens", 0)
                for event in events
            ),
            "tool_calls": 1,
            "tool_errors": 0,
            "peak_server_rss_bytes": 20_000_000_000,
        }
        row["total_tokens"] = row["prompt_tokens"] + row["completion_tokens"]
        records.append(row)
        requests.extend(request_csv(row, event) for event in events)

    partial_usage = {
        "event": "response",
        "latency_seconds": 3.0,
        "response": {
            "usage": {"completion_tokens": 7},
            "timings": {"prompt_n": 400, "prompt_ms": 0, "predicted_n": 7, "predicted_ms": 0},
        },
    }
    add(
        "clean-seed17",
        "clean",
        "banking",
        "task-0",
        "completed",
        True,
        7.0,
        [response(2.0, 20, 5, 1000, 500), partial_usage, response(0, 10, 1, 500, 250)],
    )
    add(
        "clean-seed29",
        "clean",
        "banking",
        "task-0",
        "completed",
        True,
        6.0,
        [response(4.0, 50, 9, 2000, 1000)],
    )
    add(
        "attack-success",
        "attack",
        "workspace",
        "task-1",
        "completed",
        True,
        12.0,
        [response(10.0, 100, 11, 5000, 2000)],
    )
    for run, condition, status, utility in (
        ("completed-failure", "attack", "completed", False),
        ("completed-unknown", "clean", "completed", None),
        ("errored-unknown", "attack", "error", None),
    ):
        add(
            run,
            condition,
            "empty-suite",
            "task-0",
            status,
            utility,
            900.0,
            [response(800.0, 9000, 7000, 100_000, 100_000)],
        )
    return records, requests


def test_success_selection_excludes_completed_failures_and_unknown_errors(driver, measured_fixture):
    records, requests = measured_fixture
    original_records, original_requests = copy.deepcopy(records), copy.deepcopy(requests)
    result = driver.utility_success_costs(records, requests)

    assert result["overall"]["selected_episodes"] == 3
    assert result["overall"]["selected_unique_tasks"] == 2
    assert result["by_condition"]["clean"]["selected_episodes"] == 2
    assert result["by_condition"]["clean"]["selected_unique_tasks"] == 1
    assert result["by_condition"]["attack"]["selected_episodes"] == 1
    assert result["overall"]["costs"]["duration_seconds"]["total"] == 25.0
    assert result["overall"]["pooled_request_metrics"]["response_count"] == 5
    assert result["by_condition"]["attack"]["pooled_request_metrics"]["response_count"] == 1
    assert result["by_suite_condition"]["banking"]["clean"]["selected_episodes"] == 2
    assert result["by_suite_condition"]["workspace"]["attack"]["selected_episodes"] == 1
    assert result["by_suite_condition"]["banking"]["clean"] == result["by_condition"]["clean"]
    assert result["by_suite_condition"]["workspace"]["attack"] == result["by_condition"]["attack"]
    assert records == original_records
    assert requests == original_requests
    assert (
        "neither a causal performance comparison nor a security metric" in result["interpretation"]
    )


def test_selection_requires_native_boolean_true_and_completed_status(driver):
    common = {
        "run_id": "synthetic",
        "episode_id": "synthetic",
        "suite": "synthetic",
        "user_task_id": "synthetic",
        "condition": "clean",
        "status": "completed",
    }
    records = [
        {**common, "utility": "True"},
        {**common, "utility": "False"},
        {**common, "utility": 1},
        {**common, "utility": True, "status": "error"},
    ]
    result = driver.utility_success_costs(records, [])
    assert result["overall"]["selected_episodes"] == 0


def test_request_join_uses_run_id_and_preserves_native_pooled_masks(driver, measured_fixture):
    records, requests = measured_fixture
    result = driver.utility_success_costs(records, requests)
    clean = result["by_condition"]["clean"]
    pooled = clean["pooled_request_metrics"]

    # All fixture episodes share an episode_id; unrelated runs must not leak in.
    assert len({row["episode_id"] for row in records}) == 1
    assert pooled["response_count"] == 4
    assert pooled["usage_response_count"] == 4
    assert pooled["full_usage_response_count"] == 3
    assert pooled["response_tokens"] == 21
    assert pooled["response_seconds"] == 9
    assert pooled["completion_tokens_per_response_second"] == pytest.approx(21 / 9)
    assert pooled["native_decode_steps"] == 12
    assert pooled["native_decode_seconds"] == 1.75
    assert pooled["native_decode_tokens_per_second"] == pytest.approx(12 / 1.75)
    assert pooled["native_prefill_tokens"] == 80
    assert pooled["native_prefill_seconds"] == 3.5
    assert pooled["native_prefill_tokens_per_second"] == pytest.approx(80 / 3.5)
    selected = [row for row in records if row["run_id"].startswith("clean-seed")]
    assert clean["costs"] == driver.analysis._costs(selected)
    assert clean["costs"]["duration_seconds"]["n"] == 2
    assert clean["costs"]["duration_seconds"]["mean"] == 6.5
    assert clean["costs"]["request_latency_seconds"]["n"] == 4
    assert clean["costs"]["request_latency_seconds"]["total"] == pooled["request_latency_seconds"]
    # Pooled token/time ratios differ from the mean of episode token/time ratios.
    assert clean["costs"]["completion_tokens_per_response_second"]["mean"] == 2.325
    assert pooled["completion_tokens_per_response_second"] != 2.325


def test_zero_success_cells_report_no_observations_and_undefined_rates(driver, measured_fixture):
    records, requests = measured_fixture
    result = driver.utility_success_costs(records, requests)
    for cell in result["by_suite_condition"]["empty-suite"].values():
        assert cell["selected_episodes"] == cell["selected_unique_tasks"] == 0
        assert cell["costs"]["duration_seconds"]["n"] == 0
        assert cell["costs"]["duration_seconds"]["mean"] is None
        assert cell["costs"]["request_latency_seconds"]["n"] == 0
        assert cell["pooled_request_metrics"]["response_count"] == 0
        for metric in (
            "completion_tokens_per_response_second",
            "native_decode_tokens_per_second",
            "native_prefill_tokens_per_second",
        ):
            assert cell["pooled_request_metrics"][metric] is None
