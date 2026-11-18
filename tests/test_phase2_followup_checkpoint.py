import copy
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "checkpoint_restore", ROOT / "scripts/phase2_followup_checkpoint.py"
)
restore = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restore
SPEC.loader.exec_module(restore)

EMAIL_RESULT = """- attachments: []
  bcc: []
  body: hello
  cc: []
  id_: '0'
  read: true
  recipients:
  - alice@example.com
  sender: bob@example.com
  status: received
  subject: greeting
  timestamp: 2024-01-02 03:04:05"""
NO_EMAILS = "ValueError: No emails found. Try with a different query."


@pytest.fixture
def runtime():
    return restore.runtime_for("v1.2.2", "workspace")


@pytest.fixture
def initial(runtime):
    return runtime.suite.environment_type.model_validate(
        {
            "inbox": {
                "account_email": "alice@example.com",
                "initial_emails": [
                    {
                        "id_": "0",
                        "sender": "bob@example.com",
                        "recipients": ["alice@example.com"],
                        "subject": "greeting",
                        "body": "hello",
                        "status": "received",
                        "read": False,
                        "timestamp": "2024-01-02T03:04:05",
                        "attachments": [],
                        "cc": [],
                        "bcc": [],
                    }
                ],
            },
            "calendar": {
                "account_email": "alice@example.com",
                "current_day": "2024-05-20",
                "initial_events": [],
            },
            "cloud_drive": {"account_email": "alice@example.com", "initial_files": []},
        }
    ).model_dump(mode="json")


def prefix(parallel=False):
    first = {"function": "get_unread_emails", "args": {}, "id": "call0", "placeholder_args": None}
    second = {
        "function": "search_emails",
        "args": {"query": "absent"},
        "id": "call2",
        "placeholder_args": None,
    }
    calls = [first]
    if parallel:
        calls.append(
            {
                "function": "search_emails",
                "args": {"query": "hello"},
                "id": "call1",
                "placeholder_args": None,
            }
        )

    def tool(call, text, error=None):
        return {
            "role": "tool",
            "content": [{"type": "text", "content": text}],
            "tool_call": copy.deepcopy(call),
            "tool_call_id": call["id"],
            "error": error,
        }

    return [
        {"role": "system", "content": [{"type": "text", "content": "system"}]},
        {"role": "user", "content": [{"type": "text", "content": "task"}]},
        {"role": "assistant", "content": None, "tool_calls": calls},
        *[tool(c, EMAIL_RESULT) for c in calls],
        {"role": "assistant", "content": None, "tool_calls": [second]},
        tool(second, "", NO_EMAILS),
    ]


def test_replay_preserves_read_mutation_and_original_aliases(runtime, initial):
    result = restore.replay_prefix(runtime, initial, prefix())
    assert result["completed_tool_steps"] == 2
    assert result["tool_errors"] == 1
    assert result["complete_group_ranges"] == [[2, 3], [4, 5]]
    assert result["environment"]["inbox"]["emails"]["0"]["read"] is True
    assert result["environment"]["inbox"]["initial_emails"][0]["read"] is True
    assert result["environment"]["inbox"]["received"][0]["read"] is True
    assert initial["inbox"]["emails"]["0"]["read"] is False
    assert result["steps"][0]["read_flag_changes"] == [{"id": "0", "before": False, "after": True}]
    assert result["steps"][1]["actual_error"] == NO_EMAILS
    environment = restore.restore_environment(runtime, initial)
    assert environment.inbox.emails["0"] is environment.inbox.initial_emails[0]
    checkpoint = restore.restore_environment(runtime, result["environment"])
    assert checkpoint.inbox.emails["0"].read is True
    assert checkpoint.inbox.emails["0"] is checkpoint.inbox.initial_emails[0]


def test_multiple_tool_calls_replay_in_order_with_complete_groups(runtime, initial):
    result = restore.replay_prefix(runtime, initial, prefix(parallel=True))
    assert result["completed_tool_steps"] == 3
    assert result["complete_group_ranges"] == [[2, 3, 4], [5, 6]]
    assert [s["call"]["id"] for s in result["steps"]] == ["call0", "call1", "call2"]
    assert result["steps"][1]["read_flag_changes"] == []
    assert result["messages"][4]["content"] == [{"type": "text", "content": EMAIL_RESULT}]


def test_incorrect_recorded_tool_result_is_rejected(runtime, initial):
    history = prefix()
    history[3]["content"][0]["content"] = EMAIL_RESULT.replace("read: true", "read: false")
    with pytest.raises(restore.ReplayRejected, match="Tool result mismatch at message 3"):
        restore.replay_prefix(runtime, initial, history)


def test_incorrect_recorded_tool_error_is_rejected(runtime, initial):
    history = prefix()
    history[-1]["error"] = None
    with pytest.raises(restore.ReplayRejected, match="Tool result mismatch at message 5"):
        restore.replay_prefix(runtime, initial, history)


def test_typed_restore_rejects_validator_rewriting_recorded_state(runtime, initial):
    initial["inbox"]["emails"]["0"]["read"] = True
    with pytest.raises(restore.ReplayRejected, match="Typed initial environment changed"):
        restore.restore_environment(runtime, initial)


def test_incomplete_tool_group_is_rejected_before_replay(runtime, initial):
    with pytest.raises(ValueError, match="Incomplete or mismatched tool results"):
        restore.replay_prefix(runtime, initial, prefix()[:-1])


def test_unsupported_tool_effects_are_rejected_before_execution(runtime, initial):
    history = prefix()
    history[2]["tool_calls"][0]["function"] = "send_email"
    history[3]["tool_call"]["function"] = "send_email"
    with pytest.raises(restore.ReplayRejected, match="Unsupported prefix tool effect"):
        restore.replay_prefix(runtime, initial, history)
    assert len(initial["inbox"]["sent"]) == 0


def test_selection_uses_earliest_full_checkpoint_and_f16_tie_break():
    candidates = [
        {
            "suite": "workspace",
            "task": task,
            "condition": "attack",
            "policy": policy,
            "intervention_eligible": True,
            "request_index": index,
            "precision": precision,
            "run_id": f"{precision}-{policy}-{task}",
        }
        for task in ("user_task_28", "user_task_14")
        for precision, policy, index in (
            ("f16", "summary", 1),
            ("q8", "full", 2),
            ("f16", "full", 2),
            ("f16", "full", 3),
        )
    ]
    selected = restore.select_checkpoints(candidates)
    assert [(c["task"], c["precision"], c["request_index"]) for c in selected] == [
        ("user_task_28", "f16", 2),
        ("user_task_14", "f16", 2),
    ]


def test_missing_evidence_preserves_existing_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    old = output / "summary.json"
    old.write_bytes(b"verified old output")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/phase2_followup_checkpoint.py"),
            "--source-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Checkpoint evidence rejected:" in result.stderr
    assert old.read_bytes() == b"verified old output"
