"""Campaign reproducibility and lossless evidence retention contracts."""

import gzip
import json
from pathlib import Path

import pytest

from agent_tradeoffs import campaign


def test_compaction_preserves_exact_request_and_state_bytes(tmp_path):
    episode = tmp_path / "episodes" / "test"
    episode.mkdir(parents=True)
    (episode / "result.json").write_text(json.dumps({"status": "completed"}))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"status": "finalized", "episodes": [{"episode_id": "test"}]})
    )
    original = {}
    for name in ("requests.jsonl", "trace.json"):
        original[name] = ('{"text":"保留全部证据", "value":12.34}\n' * 20).encode()
        (episode / name).write_bytes(original[name])
    report = campaign.compact_run_artifacts(tmp_path)
    assert report["lossless"] and len(report["artifacts"]) == 2
    for name, value in original.items():
        assert not (episode / name).exists()
        with gzip.open(episode / (name + ".gz"), "rb") as handle:
            assert handle.read() == value
    assert campaign.compact_run_artifacts(tmp_path) == report


def test_compaction_refuses_active_run(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(ValueError, match="finalized"):
        campaign.compact_run_artifacts(tmp_path)


def test_frozen_phase1_plan_contains_60_tasks_and_600_attempts():
    plan, entries, _ = campaign.load_campaign_plan("configs/phase1/campaign.json")
    assert plan["unique_user_tasks"] == 60
    assert len(entries) == 24
    assert sum(entry["planned_episodes"] for entry in entries) == 600
    assert (
        sum(entry["planned_episodes"] for entry in entries if entry["condition"] == "clean") == 120
    )


def test_campaign_resume_keeps_completed_model_calls_and_rejects_changes(tmp_path, monkeypatch):
    # Keep this fixture tiny, but use the actual task-plan validation and persistence.
    base = Path.cwd()
    config = tmp_path / "fixture.toml"
    run_root = tmp_path / "runs"
    config.write_text(
        '[experiment]\nmode="clean"\ntask_ids=["user_task_0"]\n'
        f'[runner]\noutput_root="{run_root}"\n'
    )
    tasks = tmp_path / "tasks.json"
    tasks.write_text("{}")
    plan_path = tmp_path / "campaign.json"
    plan = {
        "schema_version": 1,
        "campaign_id": "fixture",
        "task_manifest": str(tasks),
        "unique_user_tasks": 1,
        "expected_episodes": 1,
        "runs": [
            {
                "run_id": "fixture-clean",
                "config": str(config),
                "repeat_id": "seed0",
                "seed": 0,
                "suite": "banking",
                "condition": "clean",
                "expected_episodes": 1,
            }
        ],
    }
    plan_path.write_text(json.dumps(plan))
    calls = []

    def fake_run(settings, run_id, resume, expected_runtime_identity):
        assert expected_runtime_identity == {"fixture": "F16"}
        directory = settings.runner.output_root / run_id
        if not resume:
            calls.append(run_id)
            episode = directory / "episodes" / "task"
            episode.mkdir(parents=True)
            (directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "finalized",
                        "identity": "test-only",
                        "episodes": [{"episode_id": "task"}],
                    }
                )
            )
            (episode / "result.json").write_text(
                json.dumps({"status": "completed", "identity": "test-only"})
            )
            (episode / "requests.jsonl").write_text('{"fixture":true}\n')
            (episode / "trace.json").write_text('{"fixture":true}')
        return {"completed_episodes": 1, "error_episodes": 0}

    monkeypatch.setattr(campaign, "run_experiment", fake_run)
    monkeypatch.setattr(
        campaign,
        "capture_runtime",
        lambda config: ({"semantic_identity": {"fixture": "F16"}}, None),
    )
    output = tmp_path / "campaigns"
    assert campaign.run_campaign(plan_path, output_root=output)["finalized_episodes"] == 1
    assert campaign.run_campaign(plan_path, output_root=output, resume=True)["error_episodes"] == 0
    assert len(calls) == 1
    config.write_text(
        config.read_text().replace('mode="clean"', 'mode="clean"\nattack="direct_append"')
    )
    with pytest.raises(ValueError, match="resume refused"):
        campaign.run_campaign(plan_path, output_root=output, resume=True)
    assert base == Path.cwd()
