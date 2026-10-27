"""Context contracts at the request-view and retrieval boundaries."""

import json

import pytest

from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.memory import EpisodeMemory


def transcript():
    messages = [
        {"role": "system", "content": "Original policy"},
        {"role": "user", "content": "Find the account and act."},
    ]
    for i in range(4):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"c{i}",
                    "content": f"account-{i} " + "source data " * 120,
                },
            ]
        )
    return messages


class FakeSummaryTransport:
    usage = {"total_tokens": 10}
    summary_seconds = 0.1
    tokenizer_seconds = 0.0

    def count(self, text):
        return len(text) // 4 + 1

    def summarize(self, task, archive, budget):
        assert task == "Find the account and act."
        return "Tool message 3 reports account-0."


def memory(policy):
    context = {"policy": policy}
    if policy != "full":
        context.update(trigger_rounds=2, keep_rounds=1)
    if "summary" in policy:
        context.update(
            summary_max_tokens=128,
            summary_fraction=0.25,
            summarizer={"model": "fixed-f16"},
            runtime_metadata_path="server.json",
            search_max_tokens=100,
        )
    settings = ExperimentConfig.model_validate({"context": context}).context
    return EpisodeMemory(settings, FakeSummaryTransport())


def test_full_history_preserves_exact_messages_and_does_not_alias_them():
    source = transcript()
    result = memory("full").prepare(source)
    assert result == source
    result[0]["content"] = "changed"
    assert source[0]["content"] == "Original policy"


def test_truncation_preserves_task_and_whole_latest_parallel_tool_group():
    source = transcript()[:6]
    source[4]["tool_calls"].append(
        {"id": "parallel", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    )
    source.append({"role": "tool", "tool_call_id": "parallel", "content": "parallel data"})
    before = json.dumps(source)
    state = memory("truncate")
    view = state.prepare(source)
    assert view == source[:2] + source[4:]
    assert json.dumps(source) == before
    assert state.metrics()["compactions"] == 1


def test_summary_is_lower_trust_memory_and_archive_contains_only_removed_past():
    source = transcript()
    state = memory("short_summary_rag")
    view = state.prepare(source[:6])
    assert view[:2] == source[:2]
    assert [m["role"] for m in view] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert "account-0" in view[3]["content"]
    result, error = state.search({"query": "account"})
    assert error is None
    matches = json.loads(result)["matches"]
    assert matches and {m["message_id"] for m in matches} == {3}
    assert "account-1" not in result and "account-2" not in result
    assert state.services.count(result) <= 100
    second = memory("short_summary_rag")
    second.prepare(transcript()[:2])
    assert json.loads(second.search({"query": "account"})[0]) == {"matches": []}


def test_repeated_compaction_uses_original_prefix_and_preserves_auditable_transcript():
    source = transcript()
    state = memory("long_summary")
    state.prepare(source[:6])
    state.prepare(source[:8])
    view = state.prepare(source)
    assert state.metrics()["compactions"] == 2
    assert state.archive[-1]["message_id"] == 7
    assert view[-2:] == source[-2:]
    assert len(source) == 10
    assert all(m["role"] != "system" for m in state.archive)


def test_incomplete_tool_group_is_rejected_not_silently_cut():
    with pytest.raises(ValueError, match="Incomplete"):
        memory("truncate").prepare(transcript()[:3])


def test_search_budget_and_invalid_queries_are_visible_errors():
    state = memory("short_summary_rag")
    state.prepare(transcript()[:6])
    assert state.search({"query": 1})[1] is not None
    for _ in range(3):
        state.search({"query": "account"})
    assert state.search({"query": "account"}) == (None, "History search budget exhausted")


def test_real_harness_scores_original_transcript_with_search_as_a_bounded_tool(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from agent_tradeoffs import agent, runner

    class SummaryTransport(FakeSummaryTransport):
        def summarize(self, task, archive, budget):
            return "Earlier transaction data are archived in tool message 3."

    monkeypatch.setattr(agent, "MemoryServices", lambda *args: SummaryTransport())
    scripted = iter(
        [
            ("get_most_recent_transactions", {"n": 20}),
            ("get_balance", {}),
            ("search_history", {"query": "Pizza"}),
            (None, None),
        ]
    )
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        name, arguments = next(scripted)
        calls = (
            []
            if name is None
            else [
                {
                    "id": f"c{len(sent)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        )
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "Done" if name is None else None, "tool_calls": calls},
                }
            ],
            "usage": {},
        }
        return SimpleNamespace(model_dump=lambda **kwargs: response)

    settings = ExperimentConfig.model_validate(
        {
            "experiment": {"suite": "banking", "mode": "clean", "task_ids": ["user_task_1"]},
            "context": {
                "policy": "short_summary_rag",
                "trigger_rounds": 2,
                "summary_max_tokens": 128,
                "summary_fraction": 0.15,
                "summarizer": {},
                "runtime_metadata_path": str(tmp_path / "absent.json"),
            },
        }
    )
    result = runner.run_episode(
        settings,
        runner.plan_episodes(settings)[0],
        tmp_path,
        "dev",
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert result["status"] == "completed"
    assert result["tool_calls"] == 3
    assert result["context_memory"]["search_calls"] == 1
    assert result["context_memory"]["compactions"] == 1
    assert trace["initial_environment"] == trace["final_environment"]
    assert len(trace["messages"]) == 9
    assert "Pizza party" in trace["messages"][7]["content"][0]["content"]
    assert all(
        call["function"] != "context_memory"
        for message in trace["messages"]
        for call in (message.get("tool_calls") or [])
    )
    assert any(tool["function"]["name"] == "search_history" for tool in sent[0]["tools"])
    assert sent[2]["messages"][3]["role"] == "tool"
