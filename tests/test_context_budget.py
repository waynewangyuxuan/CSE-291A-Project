"""Budgeted request views preserve task boundaries and explicit failure outcomes."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.memory import ContextBudgetExceededError, EpisodeMemory


def transcript(rounds=6):
    messages = [
        {"role": "system", "content": "Original policy"},
        {"role": "user", "content": "Find the account and act."},
    ]
    for index in range(rounds):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{index}",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"c{index}",
                    "content": f"account-{index} " + "source data " * 120,
                },
            ]
        )
    return messages


def measured(messages):
    return sum(len(json.dumps(message).split()) for message in messages)


def settings(policy):
    context = {"policy": policy, "max_context_tokens": 700}
    if policy == "summary":
        context.update(
            summary_max_tokens=40,
            summarizer_context_tokens=2048,
            summarizer={"model": "fixed-f16"},
            runtime_metadata_path="unused.json",
        )
    return ExperimentConfig.model_validate(
        {"model": {"max_tokens": 100}, "context": context}
    ).context


def test_full_budget_counts_the_completion_reserve_without_silent_truncation():
    memory = EpisodeMemory(settings("full"))
    source = transcript(1)
    assert memory.prepare(source, measure=measured, output_tokens=100) == source
    with pytest.raises(ContextBudgetExceededError, match="Full history"):
        memory.prepare(source, measure=lambda _: 601, output_tokens=100)
    assert memory.events[-1]["status"] == "overflow"
    assert len(source) == 4


def test_budget_truncation_keeps_whole_groups_and_exact_source_mapping():
    source = transcript(4)
    original = deepcopy(source)
    memory = EpisodeMemory(settings("truncate"))
    view = memory.prepare(source, measure=measured, output_tokens=100)
    assert view == source[:2] + source[6:]
    assert source == original
    decision = memory.events[-1]
    assert decision["retained_source_message_ids"] == [0, 1, 6, 7, 8, 9]
    assert decision["removed_source_message_ids"] == [2, 3, 4, 5]
    assert decision["after_tokens"] <= 600


def test_budget_refuses_to_split_an_oversized_latest_parallel_group():
    source = transcript(2)
    source[4]["tool_calls"].append(
        {"id": "parallel", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    )
    source.append({"role": "tool", "tool_call_id": "parallel", "content": "large " * 700})
    memory = EpisodeMemory(settings("truncate"))
    with pytest.raises(ContextBudgetExceededError, match="recent tool groups"):
        memory.prepare(source, measure=measured, output_tokens=100)
    assert memory.events[-1]["reason"] == "indivisible_recent_history"
    assert source[-1]["content"] == "large " * 700


def test_rolling_summary_receives_previous_memory_and_only_newly_removed_sources():
    sent = []

    class SummaryService:
        def summarize(self, task, source, budget):
            sent.append(deepcopy(source))
            return f"Memory version {len(sent)}: tool source reports account-0."

    source = transcript(6)
    memory = EpisodeMemory(settings("summary"), SummaryService())
    first = memory.prepare(source[:8], measure=measured, output_tokens=100)
    assert "Memory version 1" in first[3]["content"]
    second = memory.prepare(source, measure=measured, output_tokens=100)
    assert "Memory version 2" in second[3]["content"]
    assert sent[1][0]["kind"] == "previous_summary"
    assert sent[1][0]["source_message_ids"] == [2, 3, 4, 5]
    assert [m["message_id"] for m in sent[1][1:]] == [6, 7, 8, 9, 10, 11]
    assert second[:2] == source[:2]
    assert second[-2:] == source[-2:]
    assert memory.events[-1]["after_tokens"] <= 600


def test_summary_overflow_is_an_explicit_error_instead_of_unbudgeted_input():
    class SummaryService:
        def summarize(self, task, source, budget):
            return "oversized " * 700

    memory = EpisodeMemory(settings("summary"), SummaryService())
    with pytest.raises(ContextBudgetExceededError, match="Generated summary"):
        memory.prepare(transcript(3), measure=measured, output_tokens=100)
    assert memory.events[-1]["reason"] == "summary_envelope_does_not_fit"


def test_configuration_rejects_mixed_budget_and_round_triggers():
    with pytest.raises(ValueError, match="exactly one"):
        ExperimentConfig.model_validate(
            {"context": {"policy": "truncate", "max_context_tokens": 8000, "trigger_rounds": 2}}
        )


def test_inference_token_mismatch_remains_an_unknown_execution_error(tmp_path, monkeypatch):
    from agent_tradeoffs import agent, runner

    class Counter:
        seconds = 0.0

        def __init__(self, *args):
            pass

        def count(self, payload):
            return 100

    monkeypatch.setattr(agent, "RequestTokenCounter", Counter)
    response = {
        "choices": [{"finish_reason": "stop", "message": {"content": "Done", "tool_calls": []}}],
        "usage": {"prompt_tokens": 101, "completion_tokens": 1, "total_tokens": 102},
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(model_dump=lambda **kwargs: response)
            )
        )
    )
    config = ExperimentConfig.model_validate(
        {
            "experiment": {"suite": "banking", "mode": "clean", "task_ids": ["user_task_1"]},
            "context": {"policy": "full", "max_context_tokens": 4096},
        }
    )
    result = runner.run_episode(
        config, runner.plan_episodes(config)[0], tmp_path, "development", client=client
    )
    assert result["status"] == "error"
    assert result["utility"] is None and result["attack_success"] is None
    assert "token count differs" in result["error"]["message"]
