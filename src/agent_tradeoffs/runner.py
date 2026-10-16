"""Execute paired benchmark episodes into isolated, resumable run directories."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import platform
import re
import signal
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import psutil
from agentdojo.attacks import load_attack
from agentdojo.attacks.base_attacks import FixedJailbreakAttack
from agentdojo.attacks.important_instructions_attacks import ImportantInstructionsAttack
from agentdojo.task_suite.load_suites import get_suite

from agent_tradeoffs.agent import EpisodeTimeoutError, LocalAgent
from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.reporting import atomic_json, summarize_run
from agent_tradeoffs.runtime import _process_identity, sha256_file


class RuntimeDeploymentChangedError(RuntimeError):
    """The live process stopped matching the captured deployment during a run."""


def guard_runtime_process(pid: int, expected_identity: dict[str, str]) -> None:
    if _process_identity(pid) != expected_identity:
        raise RuntimeDeploymentChangedError(
            "The inference server changed during evaluation; resume explicitly after checking it"
        )


def list_tasks(suite: str = "banking", benchmark_version: str = "v1.2.2") -> dict[str, Any]:
    selected = get_suite(benchmark_version, suite)
    return {
        "suite": suite,
        "benchmark_version": benchmark_version,
        "user_tasks": [
            {"id": task.ID, "prompt": task.PROMPT} for task in selected.user_tasks.values()
        ],
        "injection_tasks": [
            {"id": task.ID, "goal": task.GOAL} for task in selected.injection_tasks.values()
        ],
    }


def plan_episodes(config: ExperimentConfig) -> list[dict[str, Any]]:
    experiment = config.experiment
    suite = get_suite(experiment.benchmark_version, experiment.suite)
    unknown_users = set(experiment.task_ids) - suite.user_tasks.keys()
    unknown_injections = set(experiment.injection_task_ids) - suite.injection_tasks.keys()
    if unknown_users or (experiment.mode != "clean" and unknown_injections):
        raise ValueError(
            f"Unknown selected tasks: users={sorted(unknown_users)}, "
            f"injections={sorted(unknown_injections)}"
        )
    episodes = []
    for task_id in experiment.task_ids:
        if experiment.mode in {"clean", "paired"}:
            episodes.append(
                {
                    "episode_id": f"{task_id}__clean",
                    "condition": "clean",
                    "user_task_id": task_id,
                    "injection_task_id": None,
                }
            )
        if experiment.mode in {"attack", "paired"}:
            for injection_id in experiment.injection_task_ids:
                episodes.append(
                    {
                        "episode_id": f"{task_id}__{injection_id}__attack",
                        "condition": "attack",
                        "user_task_id": task_id,
                        "injection_task_id": injection_id,
                    }
                )
    return episodes


def implementation_identity() -> dict[str, Any]:
    directory = Path(__file__).parent
    digest = hashlib.sha256()
    for filename in ("agent.py", "config.py", "context.py", "reporting.py", "runner.py"):
        digest.update(filename.encode())
        digest.update((directory / filename).read_bytes())
    distribution = importlib.metadata.distribution("agentdojo")
    direct_url = distribution.read_text("direct_url.json")
    lockfile = Path.cwd() / "uv.lock"
    return {
        "harness_sha256": digest.hexdigest(),
        "agentdojo_version": distribution.version,
        "agentdojo_source": json.loads(direct_url) if direct_url else None,
        "uv_lock_sha256": hashlib.sha256(lockfile.read_bytes()).hexdigest()
        if lockfile.is_file()
        else None,
    }


def capture_runtime(config: ExperimentConfig) -> tuple[dict[str, Any], int]:
    """Validate and snapshot the managed deployment before collecting results.

    An endpoint alias alone cannot establish which quantization or server flags
    were used. Both live properties and the model file digest must match the
    managed runtime metadata. PIDs and wall-clock timestamps stay outside the
    semantic identity so a compatible server restart can resume an interrupted run.
    """
    path = config.runner.runtime_metadata_path
    if not path.is_file():
        raise ValueError(
            f"Managed runtime metadata is missing: {path}; start the local runtime first"
        )
    metadata = json.loads(path.read_text())
    if (
        metadata.get("status") != "ready"
        or metadata.get("base_url", "").rstrip("/") != config.model.base_url
        or metadata.get("model_alias") != config.model.model
    ):
        raise ValueError(
            "Runtime metadata does not identify the configured ready endpoint and model"
        )
    pid = metadata.get("pid")
    if (
        not isinstance(pid, int)
        or not metadata.get("process_identity")
        or _process_identity(pid) != metadata["process_identity"]
    ):
        raise ValueError("Runtime process is absent or differs from its saved process identity")
    if config.runner.server_pid is not None and config.runner.server_pid != pid:
        raise ValueError("Configured server_pid does not match the managed runtime process")
    command = metadata.get("command") or []
    if "--model" not in command:
        raise ValueError("Runtime command does not record its model path")
    model_path = Path(command[command.index("--model") + 1]).resolve()
    digest = sha256_file(model_path)
    if digest != metadata.get("model_sha256"):
        raise ValueError("Runtime model file SHA256 does not match the deployment metadata")
    server_url = config.model.base_url.removesuffix("/v1")
    with urlopen(server_url + "/props", timeout=5) as response:
        props = json.load(response)
    with urlopen(config.model.base_url + "/models", timeout=5) as response:
        models = json.load(response)
    if config.model.model not in {model.get("id") for model in models.get("data", [])}:
        raise ValueError("Configured model alias is not currently served")
    if (
        props.get("model_alias") != config.model.model
        or Path(props.get("model_path", "")).resolve() != model_path
        or props.get("default_generation_settings", {}).get("n_ctx") != metadata.get("context_size")
    ):
        raise ValueError("Live server properties do not match the managed deployment")
    semantic_keys = (
        "base_url",
        "command",
        "context_size",
        "model_alias",
        "model_revision",
        "model_sha256",
        "llama_release",
        "llama_commit",
        "runtime_version",
    )
    semantic = {key: metadata.get(key) for key in semantic_keys}
    semantic["effective_server_props"] = {
        key: props.get(key)
        for key in (
            "build_info",
            "model_path",
            "model_alias",
            "total_slots",
            "chat_template",
            "default_generation_settings",
        )
    }
    return {
        "semantic_identity": semantic,
        "metadata_snapshot": metadata,
        "live_server_props": props,
        "live_models": models,
        "model_digest_verified": True,
        "captured_at": datetime.now(UTC).isoformat(),
    }, pid


@contextmanager
def episode_deadline(seconds: float):
    """A wall-clock alarm also bounds a blocked client or simulated tool."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Run evaluation on the main thread so episode timeouts can be enforced")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0] != 0:
        raise RuntimeError("An existing process alarm prevents safe episode timeout enforcement")

    def timeout_handler(signum, frame):
        raise EpisodeTimeoutError(f"Episode exceeded {seconds:g} seconds")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class ServerMemorySampler:
    """Sample RSS for the explicitly identified local inference server only."""

    def __init__(self, pid: int | None):
        self.pid = pid
        self.stop_event = threading.Event()
        self.samples: list[int] = []
        self.error: str | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.pid is None:
            return

        def sample():
            try:
                process = psutil.Process(self.pid)
                while not self.stop_event.is_set():
                    self.samples.append(process.memory_info().rss)
                    self.stop_event.wait(0.1)
            except (psutil.Error, OSError) as error:
                self.error = str(error)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        return {
            "pid": self.pid,
            "peak_rss_bytes": max(self.samples) if self.samples else None,
            "samples": len(self.samples),
            "interval_seconds": 0.1,
            "error": self.error,
            "scope": "server_process_rss",
        }


def make_injections(
    suite: Any, agent: LocalAgent, attack_name: str, user_task: Any, injection_task: Any
) -> tuple[dict[str, str], dict[str, str]]:
    upstream_name = attack_name.removesuffix("_append")
    if upstream_name == "important_instructions_no_model_name":
        # Upstream's subclass still calls a model-name registry lookup before
        # replacing that name. Qwen is absent from the registry. Construct the
        # same upstream fixed template with its intended generic model name.
        attack = FixedJailbreakAttack(ImportantInstructionsAttack._JB_STRING, suite, agent)
        attack.user_name = "Emma Johnson"
        attack.model_name = "the AI language model"
    else:
        attack = load_attack(upstream_name, suite, agent)
    payloads = attack.attack(user_task, injection_task)
    defaults = suite.get_injection_vector_defaults()
    combined = {vector: defaults[vector] + "\n\n" + payload for vector, payload in payloads.items()}
    return payloads, combined


def run_episode(
    config: ExperimentConfig,
    episode: dict[str, Any],
    episode_dir: Path,
    identity: str,
    *,
    client: Any = None,
    runtime_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    suite = get_suite(config.experiment.benchmark_version, config.experiment.suite)
    user_task = suite.get_user_task_by_id(episode["user_task_id"])
    injection_task = (
        suite.get_injection_task_by_id(episode["injection_task_id"])
        if episode["condition"] == "attack"
        else None
    )
    agent = LocalAgent(config, episode_dir, client=client)
    started = time.monotonic()
    memory = ServerMemorySampler(config.runner.server_pid)
    memory.start()
    result = {
        **episode,
        "identity": identity,
        "status": "error",
        "utility": None,
        "attack_success": None,
        "refusal": "manual_review",
        "started_at": datetime.now(UTC).isoformat(),
    }
    payloads: dict[str, str] = {}
    injections: dict[str, str] = {}
    try:
        with episode_deadline(config.runner.episode_timeout_seconds):
            if injection_task is not None:
                payloads, injections = make_injections(
                    suite, agent, config.experiment.attack, user_task, injection_task
                )
                agent.injections = payloads
            utility, attack_success = suite.run_task_with_pipeline(
                agent, user_task, injection_task, injections
            )
            result.update(
                status="completed",
                utility=bool(utility),
                attack_success=bool(attack_success) if injection_task else None,
            )
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if runtime_guard is not None:
            try:
                runtime_guard()
            except RuntimeDeploymentChangedError as error:
                result.update(
                    status="error",
                    utility=None,
                    attack_success=None,
                    retryable=True,
                    error={"type": type(error).__name__, "message": str(error)},
                )
        result.update(
            duration_seconds=time.monotonic() - started,
            server_memory=memory.stop(),
            requests=agent.requests,
            tool_calls=agent.tool_calls,
            tool_errors=agent.tool_errors,
            usage=agent.usage,
            finish_reason=agent.finish_reason,
            observed_injection_vectors=sorted(agent.observed_vectors),
        )
        trace = {
            **episode,
            "identity": identity,
            "user_prompt": user_task.PROMPT,
            "injection_goal": injection_task.GOAL if injection_task else None,
            "attack_variant": config.experiment.attack if injection_task else None,
            "attack_payloads": payloads,
            "injection_defaults": {
                vector: suite.get_injection_vector_defaults()[vector] for vector in injections
            },
            "injections": injections,
            **agent.trace(),
        }
        atomic_json(episode_dir / "trace.json", trace)
    atomic_json(episode_dir / "result.json", result)
    return result


def run_experiment(
    config: ExperimentConfig, run_id: str | None = None, resume: bool = False
) -> dict[str, Any]:
    if resume and not run_id:
        raise ValueError("--resume requires an explicit run ID")
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("Run ID must be a path-safe name of at most 128 characters")
    episodes = plan_episodes(config)
    implementation = implementation_identity()
    runtime_snapshot, pid = capture_runtime(config)
    config = config.model_copy(
        update={"runner": config.runner.model_copy(update={"server_pid": pid})}
    )
    identity = stable_hash(
        {
            "config": config.identity_config(),
            "episodes": episodes,
            "implementation": implementation,
            "runtime": runtime_snapshot["semantic_identity"],
        }
    )
    expected_process = runtime_snapshot.get("metadata_snapshot", {}).get("process_identity")

    def runtime_guard():
        if expected_process is not None:
            guard_runtime_process(pid, expected_process)

    root = config.runner.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / run_id
    if resume:
        if not (run_dir / "manifest.json").is_file():
            raise ValueError(f"No resumable manifest at {run_dir}")
    else:
        run_dir.mkdir()
    with (run_dir / ".run.lock").open("a") as lock:
        completed_loop = False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"Another evaluator owns {run_dir}") from error
        manifest_path = run_dir / "manifest.json"
        if resume:
            manifest = json.loads(manifest_path.read_text())
            if manifest["identity"] != identity:
                raise ValueError(
                    "Resume refused: configuration, tasks, deployment, or implementation changed"
                )
            manifest.setdefault("resume_runtime_observations", []).append(runtime_snapshot)
        else:
            manifest = {
                "schema_version": 1,
                "run_id": run_id,
                "identity": identity,
                "created_at": datetime.now(UTC).isoformat(),
                "config": config.model_dump(mode="json"),
                "implementation": implementation,
                "runtime": runtime_snapshot,
                "platform": {
                    "python": platform.python_version(),
                    "system": platform.platform(),
                    "machine": platform.machine(),
                },
                "episodes": episodes,
                "status": "running",
            }
            atomic_json(manifest_path, manifest)
        try:
            for episode in episodes:
                episode_dir = run_dir / "episodes" / episode["episode_id"]
                episode_dir.mkdir(parents=True, exist_ok=True)
                result_path = episode_dir / "result.json"
                if result_path.exists():
                    existing = json.loads(result_path.read_text())
                    if existing.get("identity") != identity:
                        raise ValueError(f"Result identity mismatch at {result_path}")
                    if not existing.get("retryable"):
                        continue
                # Retain interrupted attempt evidence before retrying it.
                remnants = [path for path in episode_dir.iterdir() if path.is_file()]
                if remnants:
                    archive = episode_dir / "interrupted_attempts" / uuid.uuid4().hex
                    archive.mkdir(parents=True)
                    for path in remnants:
                        path.rename(archive / path.name)
                runtime_guard()
                result = run_episode(
                    config, episode, episode_dir, identity, runtime_guard=runtime_guard
                )
                summarize_run(run_dir)
                if result.get("retryable"):
                    raise RuntimeDeploymentChangedError(result["error"]["message"])
            completed_loop = True
        finally:
            summary = summarize_run(run_dir)
            manifest["status"] = (
                "finalized"
                if completed_loop and summary["finalized_episodes"] == len(episodes)
                else "interrupted"
            )
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            atomic_json(manifest_path, manifest)
    return summary
