"""How the retrieval routes that use a model ask it, and add up what that cost."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from tutor import llm

# (purpose, system prompt, user text, JSON Schema, output limit) -> the reply's text
Ask = Callable[[str, str, str, dict, int], str]


class ModelAsker:
    """Calls the model, and adds up what it used, so a run can say what it cost."""

    def __init__(self, config: llm.LLMConfig, log_path: Path | None = None) -> None:
        self._config = config
        self._log_path = log_path
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def __call__(self, purpose: str, system: str, user: str, schema: dict,
                 max_tokens: int) -> str:
        reply = llm.chat(self._config,
                         [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                         purpose=purpose, log_path=self._log_path, max_tokens=max_tokens,
                         thinking=False, response_schema=schema)
        self.calls += 1
        self.input_tokens += reply.input_tokens or 0
        self.output_tokens += reply.output_tokens or 0
        if reply.finish_reason == "length":
            raise ValueError(f"{purpose}: the reply was cut off at {max_tokens} tokens")
        return reply.text


def digest(*parts: object) -> str:
    """A key for what a stored answer depends on: any change makes it a different key."""
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()
