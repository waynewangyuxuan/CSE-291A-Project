"""Exit status must distinguish a finished invocation from valid measurements."""

import json

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
