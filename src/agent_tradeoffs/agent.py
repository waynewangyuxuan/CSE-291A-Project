"""A bounded OpenAI-compatible agent over AgentDojo's simulated tools."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import openai
import yaml
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import FunctionCall
from agentdojo.types import text_content_block_from_string
from pydantic import BaseModel

from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.context import exposed_vectors, full_context
from agent_tradeoffs.prompt_policy import PromptPolicy, prompt_identity, system_prompt


class EpisodeTimeoutError(TimeoutError):
    pass


class ToolStepLimitError(RuntimeError):
    pass


class InvalidModelOutputError(ValueError):
    pass


def json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(value, default=json_default, allow_nan=False) + "\n")
        handle.flush()


def tool_result_to_text(value: Any) -> str:
    """Match AgentDojo's default YAML formatting, without provider imports."""
    if isinstance(value, BaseModel):
        return yaml.safe_dump(value.model_dump()).strip()
    if isinstance(value, (list, tuple)):
        items = [item.model_dump() if isinstance(item, BaseModel) else str(item) for item in value]
        return yaml.safe_dump(items).strip()
    return str(value)


def load_system_message(policy: PromptPolicy = "default") -> str:
    return system_prompt(policy)


class LocalAgent(BasePipelineElement):
    """Preserve AgentDojo scoring while recording every actual model request.

    There are no SDK retries: an infrastructure failure remains visible rather
    than silently adding unrecorded model calls. Model-generated invalid tool
    arguments are returned to the model through AgentDojo's runtime as usual.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        episode_dir: Path,
        injections: dict[str, str] | None = None,
        *,
        client: Any = None,
    ) -> None:
        self.config = config
        self.name = config.model.model
        self.episode_dir = episode_dir
        self.injections = injections or {}
        self.client = client or openai.OpenAI(
            base_url=config.model.base_url,
            api_key=os.environ.get(config.model.api_key_env, "local-no-key"),
            max_retries=0,
            timeout=config.runner.request_timeout_seconds,
        )
        self.messages: list[dict[str, Any]] = []
        self.requests = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.usage: dict[str, int] = {}
        self.finish_reason: str | None = None
        self.observed_vectors: set[str] = set()
        self.initial_environment: dict[str, Any] | None = None
        self.environment: Any = None
        self.started_at = time.monotonic()
        self.deadline = self.started_at + config.runner.episode_timeout_seconds

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise EpisodeTimeoutError("Episode exceeded its wall-clock budget")
        return remaining

    def trace(self) -> dict[str, Any]:
        return {
            "messages": self.messages,
            "system_prompt": prompt_identity(self.config.prompt.policy),
            "initial_environment": self.initial_environment,
            "final_environment": self.environment.model_dump(mode="json")
            if self.environment is not None
            else None,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "requests": self.requests,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "observed_injection_vectors": sorted(self.observed_vectors),
            "exposure_match_method": "normalized_whitespace_match_raw_or_yaml_decoded_tool_strings",
        }

    def query(
        self, query: str, runtime: Any, env: Any, messages: Any = (), extra_args: Any = None
    ) -> tuple:
        if messages:
            raise ValueError("A LocalAgent instance executes one fresh episode")
        self.initial_environment = env.model_dump(mode="json")
        self.environment = env
        self.messages = [
            {
                "role": "system",
                "content": [
                    text_content_block_from_string(load_system_message(self.config.prompt.policy))
                ],
            },
            {"role": "user", "content": [text_content_block_from_string(query)]},
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": function.name,
                    "description": function.description,
                    "parameters": function.parameters.model_json_schema(),
                },
            }
            for function in runtime.functions.values()
        ]
        while True:
            request_messages = full_context(
                [to_openai_message(message) for message in self.messages]
            )
            self.observed_vectors.update(exposed_vectors(request_messages, self.injections))
            # extra_body is flattened into the HTTP JSON payload by the SDK. Save
            # that exact merged payload (excluding authentication headers).
            payload = {
                "model": self.config.model.model,
                "messages": request_messages,
                "temperature": self.config.model.temperature,
                "max_tokens": self.config.model.max_tokens,
                "seed": self.config.model.seed,
                "stream": False,
                **self.config.model.extra_body,
            }
            if tools:
                payload.update(tools=tools, tool_choice="auto")
            request_index = self.requests
            self.requests += 1
            timeout = min(self.config.runner.request_timeout_seconds, self.remaining())
            append_jsonl(
                self.episode_dir / "requests.jsonl",
                {
                    "event": "request",
                    "index": request_index,
                    "wall_time": time.time(),
                    "payload": payload,
                    "timeout_seconds": timeout,
                },
            )
            started = time.monotonic()
            try:
                kwargs = {
                    key: value
                    for key, value in payload.items()
                    if key not in self.config.model.extra_body
                }
                response = self.client.chat.completions.create(
                    **kwargs, extra_body=self.config.model.extra_body, timeout=timeout
                )
                response_data = response.model_dump(mode="json")
                append_jsonl(
                    self.episode_dir / "requests.jsonl",
                    {
                        "event": "response",
                        "index": request_index,
                        "latency_seconds": time.monotonic() - started,
                        "response": response_data,
                    },
                )
            except Exception as error:
                append_jsonl(
                    self.episode_dir / "requests.jsonl",
                    {
                        "event": "request_error",
                        "index": request_index,
                        "latency_seconds": time.monotonic() - started,
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                )
                raise
            usage = response_data.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(usage.get(key), int):
                    self.usage[key] = self.usage.get(key, 0) + usage[key]
            if not response_data.get("choices"):
                raise InvalidModelOutputError("Completion contains no choices")
            choice = response_data["choices"][0]
            self.finish_reason = choice.get("finish_reason")
            output = choice["message"]
            parsed_calls = parse_tool_calls(output.get("tool_calls") or [])
            content = output.get("content")
            if content is not None and not isinstance(content, str):
                raise InvalidModelOutputError("Assistant content must be a string or null")
            self.messages.append(
                {
                    "role": "assistant",
                    "content": [text_content_block_from_string(content)]
                    if content is not None
                    else None,
                    "tool_calls": parsed_calls or None,
                }
            )
            self.remaining()
            if self.finish_reason in {"length", "content_filter"}:
                raise InvalidModelOutputError(
                    f"Completion stopped with finish_reason={self.finish_reason}"
                )
            if not parsed_calls:
                if content is None or not content.strip():
                    raise InvalidModelOutputError(
                        "Completion contains neither tool calls nor a final answer"
                    )
                return query, runtime, env, self.messages, extra_args or {}
            for call in parsed_calls:
                self.remaining()
                if self.tool_calls >= self.config.runner.max_tool_steps:
                    raise ToolStepLimitError(
                        f"Exceeded {self.config.runner.max_tool_steps} simulated tool calls"
                    )
                self.tool_calls += 1
                result, error = runtime.run_function(env, call.function, call.args)
                self.tool_errors += int(error is not None)
                self.messages.append(
                    {
                        "role": "tool",
                        "content": [text_content_block_from_string(tool_result_to_text(result))],
                        "tool_call": call,
                        "tool_call_id": call.id,
                        "error": error,
                    }
                )
                self.remaining()


def parse_tool_calls(calls: list[dict[str, Any]]) -> list[FunctionCall]:
    result = []
    for call in calls:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
            if not isinstance(call.get("id"), str) or not call["id"]:
                raise ValueError("tool call requires a nonempty id")
            result.append(FunctionCall(function=function["name"], args=arguments, id=call["id"]))
        except (ValueError, KeyError, TypeError) as error:
            raise InvalidModelOutputError(f"Invalid structured tool call: {error}") from error
    if len({call.id for call in result}) != len(result):
        raise InvalidModelOutputError("Tool call IDs must be unique within a completion")
    return result


def to_openai_message(message: dict[str, Any]) -> dict[str, Any]:
    blocks = message.get("content")
    content = "\n".join(block["content"] for block in blocks) if blocks is not None else None
    result = {"role": message["role"], "content": content}
    if message["role"] == "assistant" and message.get("tool_calls"):
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function, "arguments": json.dumps(call.args)},
            }
            for call in message["tool_calls"]
        ]
    if message["role"] == "tool":
        result.update(tool_call_id=message["tool_call_id"], content=message.get("error") or content)
    return result
