"""One command surface for provisioning, running, and inspecting experiments."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

RUNTIME_CONFIG = "configs/runtime-qwen3-8b-q4.toml"


def _print(value: object) -> None:
    def encode(item: object) -> object:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return dataclasses.asdict(item)
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Cannot encode {type(item).__name__}")

    print(json.dumps(value, default=encode, indent=2, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="agent-tradeoffs", description="Local AgentDojo deployment experiments."
    )
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("doctor", "serve", "probe", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--config", default=RUNTIME_CONFIG, type=Path)
        if name == "serve":
            command.add_argument("--background", action="store_true")
    model = commands.add_parser("model")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    download = model_commands.add_parser("download")
    download.add_argument("--config", default=RUNTIME_CONFIG, type=Path)
    tasks = commands.add_parser("tasks")
    tasks.add_argument("--suite", default="banking")
    tasks.add_argument("--benchmark-version", default="v1.2.2")
    run = commands.add_parser("run")
    run.add_argument("--config", default="configs/smoke.toml", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--resume", action="store_true")
    summary = commands.add_parser("summarize")
    summary.add_argument("run", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command in {"doctor", "serve", "probe", "stop", "model"}:
            from agent_tradeoffs import runtime

            config = runtime.load_runtime_config(arguments.config)
            if arguments.command == "serve":
                result = runtime.serve(config, background=arguments.background)
            elif arguments.command == "stop":
                result = runtime.stop_server(config)
            elif arguments.command == "doctor":
                result = runtime.doctor(config)
            elif arguments.command == "probe":
                result = runtime.probe(config)
            else:
                result = runtime.download_model(config)
        elif arguments.command == "tasks":
            from agent_tradeoffs.runner import list_tasks

            result = list_tasks(arguments.suite, arguments.benchmark_version)
        elif arguments.command == "run":
            from agent_tradeoffs.config import load_config
            from agent_tradeoffs.runner import run_experiment

            result = run_experiment(
                load_config(arguments.config),
                run_id=arguments.run_id,
                resume=arguments.resume,
            )
        else:
            from agent_tradeoffs.reporting import summarize_run

            result = summarize_run(arguments.run)
        _print(result)
        if arguments.command == "doctor" and not result.get("ready", False):
            return 1
        if arguments.command == "run" and result.get("error_episodes", 0):
            return 1
        if arguments.command == "serve" and result.get("exit_code", 0) != 0:
            return 1
    except KeyboardInterrupt:
        print("Interrupted; completed episodes remain available for resume.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
