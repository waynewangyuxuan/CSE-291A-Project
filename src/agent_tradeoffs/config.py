"""Strict, frozen experiment settings and their reproducible identity."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_tradeoffs.prompt_policy import PromptPolicy, prompt_identity


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentSettings(Settings):
    suite: Literal["banking", "slack", "travel", "workspace"] = "banking"
    benchmark_version: Literal["v1", "v1.1", "v1.1.1", "v1.1.2", "v1.2", "v1.2.1", "v1.2.2"] = (
        "v1.2.2"
    )
    task_ids: tuple[str, ...] = ("user_task_0",)
    injection_task_ids: tuple[str, ...] = ("injection_task_0",)
    mode: Literal["clean", "attack", "paired"] = "paired"
    attack: Literal[
        "direct_append",
        "ignore_previous_append",
        "system_message_append",
        "injecagent_append",
        "important_instructions_no_model_name_append",
    ] = "ignore_previous_append"

    @field_validator("task_ids", "injection_task_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Task IDs must be unique")
        if any(not item or "/" in item or "\\" in item for item in value):
            raise ValueError("Task IDs must be nonempty path-safe strings")
        return value

    @model_validator(mode="after")
    def selected_tasks(self) -> ExperimentSettings:
        if not self.task_ids:
            raise ValueError("Select at least one user task")
        if self.mode != "clean" and not self.injection_task_ids:
            raise ValueError("Attack and paired runs require injection_task_ids")
        return self


class ModelSettings(Settings):
    model: str = "qwen3-8b-q4_k_m"
    base_url: str = "http://127.0.0.1:8080/v1"
    api_key_env: str = "LOCAL_LLM_API_KEY"
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=2048, gt=0)
    seed: int = Field(default=0, ge=0)
    extra_body: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}}
    )

    @field_validator("base_url")
    @classmethod
    def loopback_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("This local baseline requires a loopback OpenAI-compatible endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, a query, or a fragment")
        return value.rstrip("/")

    @field_validator("extra_body")
    @classmethod
    def no_request_overrides(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = {
            "messages",
            "tools",
            "model",
            "temperature",
            "max_tokens",
            "seed",
            "stream",
            "tool_choice",
        }
        if reserved.intersection(value):
            raise ValueError("extra_body must not override captured request settings")
        # Fail at configuration time instead of halfway through an episode.
        json.dumps(value, allow_nan=False)
        return value


class RunnerSettings(Settings):
    max_tool_steps: int = Field(default=15, ge=1)
    request_timeout_seconds: float = Field(default=120, gt=0)
    episode_timeout_seconds: float = Field(default=600, gt=0)
    output_root: Path = Path("artifacts/runs")
    runtime_metadata_path: Path = Path("artifacts/runtime/server.json")
    server_pid: int | None = Field(default=None, gt=0)
    compact_artifacts: bool = False
    min_free_disk_bytes: int = Field(default=128 * 1024**2, ge=0)


class ContextSettings(Settings):
    # A rejected policy is preferable to silently claiming truncation/summarization.
    policy: Literal["full"] = "full"


class PromptSettings(Settings):
    policy: PromptPolicy = "default"


class ExperimentConfig(Settings):
    experiment: ExperimentSettings = Field(default_factory=ExperimentSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)

    def identity_config(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["resolved_system_prompt"] = prompt_identity(self.prompt.policy)
        value["runner"].pop("output_root")
        value["runner"].pop("server_pid")
        value["runner"].pop("runtime_metadata_path")
        return value

    def fingerprint(self) -> str:
        return stable_hash(self.identity_config())


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def load_config(path: str | Path) -> ExperimentConfig:
    """Load TOML. Relative artifact paths are relative to the working directory."""
    with Path(path).open("rb") as handle:
        return ExperimentConfig.model_validate(tomllib.load(handle))
