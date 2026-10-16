"""The initial baseline sends the complete message history on every request."""

from copy import deepcopy
from typing import Any

import yaml


def full_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a request snapshot without mutating or shortening history."""
    return deepcopy(messages)


def exposed_vectors(messages: list[dict[str, Any]], injections: dict[str, str]) -> list[str]:
    """Find payloads in tool messages actually sent to the model.

    YAML tool formatting can change whitespace and escape quotes. Match raw
    text and decoded YAML string values; this is not a claim the model noticed it.
    Full request payloads remain the authoritative evidence for manual review.
    """

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    tool_text = []
    for message in messages:
        if message["role"] != "tool":
            continue
        content = str(message.get("content") or "")
        tool_text.append(" ".join(content.split()))
        try:
            tool_text.extend(" ".join(value.split()) for value in strings(yaml.safe_load(content)))
        except yaml.YAMLError:
            pass
    return sorted(
        vector
        for vector, payload in injections.items()
        if any(" ".join(payload.split()) in text for text in tool_text)
    )
