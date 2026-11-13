"""Count the exact chat template rendered by the pinned local inference service."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

from agent_tradeoffs.config import stable_hash


class RequestTokenCounter:
    def __init__(self, base_url: str, log_path: Path, remaining: Callable[[], float]):
        self.base_url = base_url.removesuffix("/v1")
        self.log_path = log_path
        self.remaining = remaining
        self.seconds = 0.0

    def post(self, route: str, payload: dict) -> dict:
        request = Request(
            self.base_url + route,
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=min(30, self.remaining())) as response:
            return json.load(response)

    def count(self, payload: dict) -> int:
        started = time.monotonic()
        record = {"event": "rendered_token_count", "payload_sha256": stable_hash(payload)}
        try:
            prompt = self.post("/apply-template", payload)["prompt"]
            if not isinstance(prompt, str):
                raise ValueError("Local chat template did not return a text prompt")
            tokens = self.post(
                "/tokenize", {"content": prompt, "add_special": True, "parse_special": True}
            )["tokens"]
            if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
                raise ValueError("Local tokenizer did not return token IDs")
            record.update(
                prompt_tokens=len(tokens),
                rendered_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                rendered_prompt=prompt,
            )
            return len(tokens)
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error))
            raise
        finally:
            elapsed = time.monotonic() - started
            self.seconds += elapsed
            record["seconds"] = elapsed
            with self.log_path.open("a") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
