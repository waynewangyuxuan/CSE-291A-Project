import csv
import gzip
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/profile_phase2_capability.py"
API = runpy.run_path(str(SCRIPT))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value).encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def sha(path):
    return API["digest"](path.read_bytes())


def trace_for(task):
    call = {"function": "lookup", "args": {"name": "Alice"}}
    messages = [
        {"role": "user", "content": "Find Alice"},
        {"role": "assistant", "tool_calls": [call]},
        {"role": "tool", "tool_call": call, "error": "temporary failure"},
        {"role": "assistant", "tool_calls": [call]},
        {"role": "tool", "tool_call": call, "error": None},
        {"role": "assistant", "content": "Alice found"},
    ]
    return {
        "episode_id": f"{task}__clean",
        "identity": "identity",
        "condition": "clean",
        "user_task_id": task,
        "user_prompt": "Find Alice",
        "messages": messages,
        "initial_environment": {"state": 1},
        "final_environment": {"state": 1},
        "tool_calls": 2,
        "tool_errors": 1,
        "requests": 3,
        "usage": {"prompt_tokens": 30, "completion_tokens": 6, "total_tokens": 36},
    }


def annotation(path, trace, seed, decision):
    return {
        "seed": seed,
        "trace_path": str(path),
        "decision": decision,
        "reason": "Reviewed",
        "evidence": {
            "objective_pointer": "/user_prompt",
            "message_indices": list(range(len(trace["messages"]))),
            "message_sha256": {
                str(i): API["digest"](API["canonical"](m).encode())
                for i, m in enumerate(trace["messages"])
            },
            "initial_environment_sha256": API["digest"](b'{"state":1}'),
            "final_environment_sha256": API["digest"](b'{"state":1}'),
            "state_changes": [],
            "additional_state_pointers": [],
        },
    }


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / "restored"
    protocol_path = "configs/phase2-followup-v1/preparation.json"
    baseline_path = "results/phase1-recovery/evaluation-episodes.csv"
    formal_path = "results/phase2-main-v1/analysis/episodes.json"
    doc_path = "docs/phase2-post-study-review.md"
    save_json(
        root / protocol_path,
        {
            "baseline_episodes": baseline_path,
            "formal_episodes": formal_path,
            "baseline_seeds": [17, 29],
        },
    )
    save_json(root / doc_path, "Scope")
    rows, tasks = [], []
    for number in (1, 2):
        task = f"user_task_{number}"
        trace = trace_for(task)
        reviewed = []
        for seed in (17, 29):
            relative = Path(f".cache/recovery/{task}/seed-{seed}/trace.json.gz")
            save_json(root / relative, trace)
            rows.append(
                {
                    "suite": "slack",
                    "user_task_id": task,
                    "condition": "clean",
                    "repeat_id": f"seed-{seed}",
                    "utility": "True",
                    "episode_id": trace["episode_id"],
                    "identity": "identity",
                    "trace_path": f"/nonexistent/historical/repository/{relative}",
                }
            )
            item = annotation(
                relative, trace, seed, "uncertain" if number == 2 and seed == 29 else "supported"
            )
            item["trace_sha256"] = sha(root / relative)
            reviewed.append(item)
        tasks.append({"suite": "slack", "task": task, "per_seed": reviewed})
    (root / baseline_path).parent.mkdir(parents=True)
    with (root / baseline_path).open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    episodes = [
        {
            "cohort": "main",
            "suite": "slack",
            "task": "user_task_2",
            "policy": "full",
            "condition": "clean",
        }
    ]
    profile_hashes = {}
    for precision, utility in (("f16", True), ("q8", False), ("q4", None)):
        source = Path(f"artifacts/{precision}/user_task_1")
        trace = trace_for("user_task_1")
        result = {
            **trace,
            "utility": utility,
            "status": "error" if utility is None else "completed",
            "duration_seconds": 2.0,
        }
        if utility is None:
            result["error"] = {"type": "BudgetError"}
        save_json(root / source / "trace.json.gz", trace)
        save_json(root / source / "result.json", result)
        events = []
        for index in range(3):
            events.extend(
                [
                    {"event": "request", "index": index},
                    {
                        "event": "response",
                        "index": index,
                        "latency_seconds": 0.5,
                        "response": {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            },
                            "timings": {"prompt_ms": 100, "predicted_ms": 200},
                        },
                    },
                ]
            )
        (root / source / "requests.jsonl.gz").write_bytes(
            gzip.compress("\n".join(json.dumps(e) for e in events).encode(), mtime=0)
        )
        for filename in ("trace.json.gz", "result.json", "requests.jsonl.gz"):
            profile_hashes[str(source / filename)] = sha(root / source / filename)
        episodes.append(
            {
                "cohort": "main",
                "suite": "slack",
                "task": "user_task_1",
                "precision": precision,
                "policy": "full",
                "condition": "clean",
                "source": str(source),
                "trace_sha256": sha(root / source / "trace.json.gz"),
                "result_sha256": sha(root / source / "result.json"),
                "episode_id": trace["episode_id"],
                "identity": "identity",
                "status": result["status"],
                "utility": utility,
                "seconds": 2.0,
                "requests": 3,
                "tool_calls": 2,
                "tool_errors": 1,
                "repeated_calls": 1,
                "review_status": "reviewed",
                "failure_category": "other",
                "secondary_labels": "scorer_false_positive",
                "evidence": "Source index",
                "notes": "Retain official outcome separately",
            }
        )
    save_json(root / formal_path, episodes)
    review = {
        "schema_version": 1,
        "reviewer": "test",
        "review_kind": "fixture",
        "lever_design": "Fixture",
        "source_hashes": {
            p: sha(root / p) for p in (protocol_path, doc_path, baseline_path, formal_path)
        },
        "profile_source_hashes": profile_hashes,
        "call_reviews": [],
        "tasks": tasks,
    }
    review_path = tmp_path / "manual-review.json"
    save_json(review_path, review)
    return root, tmp_path / "output", review_path


def generate(evidence):
    root, output, review = evidence
    return API["generate"](root, output, review)


def edit_review(evidence, edit):
    review_path = evidence[2]
    review = json.loads(review_path.read_text())
    edit(review)
    save_json(review_path, review)


def test_qualification_uses_both_baselines_and_preserves_current_failures(evidence):
    qualification, summaries = generate(evidence)
    assert qualification["eligible_tasks"] == 1
    assert [(t["task"], t["eligible"]) for t in qualification["tasks"]] == [
        ("user_task_1", True),
        ("user_task_2", False),
    ]
    assert qualification["decision_counts"] == {"supported": 3, "uncertain": 1}
    assert [
        (s["official_success"], s["official_failure"], s["official_unknown"]) for s in summaries
    ] == [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    assert summaries[0]["metrics"]["exact_repeated_calls"]["sum"] == 1
    assert summaries[0]["metrics"]["total_tokens"]["sum"] == 36
    assert summaries[0]["metrics"]["response_seconds"]["sum"] == 1.5
    assert summaries[0]["unnecessary_calls_total"] is None
    assert all(not Path(path).is_absolute() for path in qualification["source_hashes"])


def test_cli_is_idempotent_and_rebases_historical_absolute_trace_paths(evidence):
    root, output, review = evidence
    command = [
        sys.executable,
        str(SCRIPT),
        "--source-root",
        str(root),
        "--output",
        str(output),
        "--review",
        str(review),
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout)["reviewed_traces"] == 4
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    subprocess.run(command, capture_output=True, text=True, check=True)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


def test_corrupt_trace_is_rejected_before_writing(evidence):
    path = evidence[0] / ".cache/recovery/user_task_1/seed-17/trace.json.gz"
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        generate(evidence)
    assert not evidence[1].exists()


def test_missing_trace_does_not_fall_back_to_historical_root(evidence):
    (evidence[0] / ".cache/recovery/user_task_1/seed-17/trace.json.gz").unlink()
    with pytest.raises(FileNotFoundError):
        generate(evidence)


def test_missing_candidate_review_is_rejected(evidence):
    edit_review(evidence, lambda review: review["tasks"].pop())
    with pytest.raises(ValueError, match="Review coverage"):
        generate(evidence)


def test_missing_seed_review_is_rejected(evidence):
    edit_review(evidence, lambda review: review["tasks"][0]["per_seed"].pop())
    with pytest.raises(ValueError, match="Review seed coverage"):
        generate(evidence)


def test_changed_message_anchor_is_rejected(evidence):
    edit_review(
        evidence,
        lambda review: review["tasks"][0]["per_seed"][0]["evidence"]["message_sha256"].update(
            {"0": "bad"}
        ),
    )
    with pytest.raises(ValueError, match="Message anchor mismatch"):
        generate(evidence)


def test_missing_raw_request_evidence_is_rejected(evidence):
    (evidence[0] / "artifacts/q4/user_task_1/requests.jsonl.gz").unlink()
    with pytest.raises(FileNotFoundError):
        generate(evidence)
    assert not evidence[1].exists()


def test_profile_request_count_mismatch_is_rejected_even_with_updated_hash(evidence):
    path = evidence[0] / "artifacts/q4/user_task_1/requests.jsonl.gz"
    records = gzip.decompress(path.read_bytes()).splitlines()
    path.write_bytes(gzip.compress(b"\n".join(records[:2]), mtime=0))
    edit_review(
        evidence,
        lambda review: review["profile_source_hashes"].update(
            {"artifacts/q4/user_task_1/requests.jsonl.gz": sha(path)}
        ),
    )
    with pytest.raises(ValueError, match="Request count mismatch"):
        generate(evidence)


def test_source_root_escape_is_rejected(evidence):
    with pytest.raises(ValueError, match="escapes source root"):
        API["source_path"](evidence[0], "../secret.json")
