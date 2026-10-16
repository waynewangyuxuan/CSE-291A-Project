"""Exit status must distinguish a finished invocation from valid measurements."""

import csv
import json

import pytest

from agent_tradeoffs import cli, runtime


def test_doctor_missing_dependencies_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "load_runtime_config", lambda path: object())
    monkeypatch.setattr(runtime, "doctor", lambda config: {"ready": False})
    assert cli.main(["doctor"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ready": False}


def test_server_crash_is_reported_as_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "load_runtime_config", lambda path: object())
    monkeypatch.setattr(
        runtime, "serve", lambda config, background=False: {"status": "exited", "exit_code": 1}
    )
    assert cli.main(["serve"]) == 1
    assert json.loads(capsys.readouterr().out)["exit_code"] == 1


def test_run_reports_infrastructure_errors_as_nonzero(monkeypatch, capsys):
    from agent_tradeoffs import runner

    monkeypatch.setattr(runner, "run_experiment", lambda *args, **kwargs: {"error_episodes": 1})
    assert cli.main(["run", "--config", "configs/smoke.toml"]) == 1
    assert json.loads(capsys.readouterr().out)["error_episodes"] == 1


def test_cli_can_inspect_real_pinned_suite_without_a_server(capsys):
    assert cli.main(["tasks", "--suite", "banking"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    assert tasks["benchmark_version"] == "v1.2.2"
    assert any(item["id"] == "user_task_0" for item in tasks["user_tasks"])
    assert any(item["id"] == "injection_task_7" for item in tasks["injection_tasks"])


@pytest.mark.parametrize("compact", [False, True])
def test_analyze_stdout_mode_preserves_full_native_artifacts(tmp_path, capsys, compact):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    episodes = []
    for episode_id, task, condition, status, utility, attack_success in (
        ("clean-a", "a", "clean", "completed", True, None),
        ("clean-b", "b", "clean", "error", None, None),
        ("attack-a", "a", "attack", "completed", False, True),
        ("attack-b", "b", "attack", "error", None, None),
        ("pending", "c", "clean", None, None, None),
    ):
        episode = {
            "episode_id": episode_id,
            "user_task_id": task,
            "condition": condition,
            "injection_task_id": "goal" if condition == "attack" else None,
        }
        episodes.append(episode)
        if status is None:
            continue
        directory = run_dir / "episodes" / episode_id
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(
            json.dumps(
                {
                    **episode,
                    "identity": "fixture-identity",
                    "status": status,
                    "utility": utility,
                    "attack_success": attack_success,
                    "duration_seconds": 1,
                    "requests": 1,
                    "tool_calls": 0,
                    "tool_errors": 0,
                    "observed_injection_vectors": ["tool"] if status == "completed" else [],
                    "error": {"type": "APITimeoutError"} if status == "error" else None,
                }
            )
        )
    template = "Full chat template retained only in native artifacts. " * 100
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "fixture-run",
                "identity": "fixture-identity",
                "episodes": episodes,
                "config": {
                    "experiment": {"suite": "banking", "attack": "direct_append"},
                    "model": {"model": "fixture-f16", "seed": 17},
                },
                "runtime": {"semantic_identity": {"chat_template": template}},
            }
        )
    )
    annotations = tmp_path / "annotations.csv"
    with annotations.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "run_id",
                "episode_id",
                "identity",
                "review_status",
                "refusal",
                "failure_category",
                "reviewer",
                "evidence",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "fixture-run",
                "episode_id": "clean-a",
                "identity": "fixture-identity",
                "review_status": "reviewed",
                "refusal": "no",
                "failure_category": "no_failure",
                "reviewer": "fixture-reviewer",
                "evidence": "fixture-evidence",
            }
        )
    output = tmp_path / "analysis"
    arguments = [
        "analyze",
        str(run_dir),
        "--output",
        str(output),
        "--annotations",
        str(annotations),
        "--require-review",
    ]
    if compact:
        arguments.append("--compact")
    assert cli.main(arguments) == 0
    stdout = capsys.readouterr().out
    printed = json.loads(stdout)
    saved = json.loads((output / "summary.json").read_text())
    assert (
        saved["measurement_provenance"]["runs"][0]["runtime_identity"]["chat_template"] == template
    )
    for filename in (
        "summary.md",
        "episodes.csv",
        "requests.csv",
        "review.csv",
        "review-sample.csv",
        "latency.svg",
    ):
        assert (output / filename).stat().st_size > 0
    if not compact:
        assert printed == saved
        return
    assert "Full chat template" not in stdout
    assert "measurement_provenance" not in printed
    assert printed["campaign_id"] == "fixture-run"
    assert printed["output_dir"] == str(output)
    assert printed["planned_episodes"] == 5
    assert printed["finalized_episodes"] == 4
    assert printed["missing_episodes"] == 1
    assert printed["preliminary_evaluation_ready"] is False
    assert printed["overall"]["unique_tasks"] == 2
    assert printed["overall"]["rates"]["clean_utility_all_attempts"] == {
        "successes": 1,
        "denominator": 2,
        "unknown_outcomes": 1,
        "rate": 0.5,
    }
    assert printed["overall"]["rates"]["attack_success_all_attempts"] == {
        "successes": 1,
        "denominator": 2,
        "unknown_outcomes": 1,
        "rate": 0.5,
    }
    assert printed["overall"]["review"]["reviewed_clean_episodes"] == 1
    assert printed["overall"]["review"]["unreviewed_finalized_clean_episodes"] == 1
    assert printed["review_sample"]["reviewed_selected_traces"] == 1
    assert printed["review_sample"]["provisional_selection"] is True
    assert saved["overall"]["rates"]["clean_utility_all_attempts"]["outcome_bounds"] == {
        "lower": 0.5,
        "upper": 1,
        "note": "Bounds assign unknown outcomes all false or all true; not confidence intervals.",
    }
