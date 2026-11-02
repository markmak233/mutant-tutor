"""How the retrieval routes that use a model ask it, and what keeps their stored answers honest.

A route keeps what a model answered, so that asking again costs nothing. That is only safe if
a changed setting can never replay an answer made under the old one, so each complete
configuration gets a folder of its own (`settings_folder`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path

from tutor import llm

# (purpose, system prompt, user text, JSON Schema, output limit) -> the reply's text
Ask = Callable[[str, str, str, dict, int], str]


class ModelAsker:
    """Calls the model, and adds up what it used, so a run can say what it cost."""

    def __init__(self, config: llm.LLMConfig, log_path: Path | None = None,
                 thinking: bool = False) -> None:
        self._config = config
        self._log_path = log_path
        self._thinking = thinking
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def settings(self) -> dict:
        """What the model's answers depend on: the model, and how hard it is asked to think."""
        config = self._config
        return {"provider": config.provider, "model": config.model, "base_url": config.base_url,
                "thinking": self._thinking,
                "request": config.thinking_on if self._thinking else list(config.thinking_off)}

    def __call__(self, purpose: str, system: str, user: str, schema: dict,
                 max_tokens: int) -> str:
        reply = llm.chat(self._config,
                         [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                         purpose=purpose, log_path=self._log_path, max_tokens=max_tokens,
                         thinking=self._thinking, response_schema=schema)
        self.calls += 1
        self.input_tokens += reply.input_tokens or 0
        self.output_tokens += reply.output_tokens or 0
        if reply.finish_reason == "length":
            raise ValueError(f"{purpose}: the reply was cut off at {max_tokens} tokens")
        return reply.text


def digest(*parts: object) -> str:
    """A key for what a stored answer depends on: any change makes it a different key."""
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def settings_folder(parent: Path, settings: dict) -> Path:
    """The folder of one complete configuration, named by its model and a hash of the rest. It
    holds the settings in the open, so a person can tell the folders apart."""
    model = str(settings.get("model", "model")).replace("/", "_")
    folder = parent / f"{model}-{digest(settings)[:10]}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps(settings, indent=1) + "\n", encoding="utf-8")
    return folder


def usage_of(ask: Ask) -> tuple[int, int]:
    """Tokens in and out so far, for an asker that counts them (a test's fake model does not)."""
    return getattr(ask, "input_tokens", 0), getattr(ask, "output_tokens", 0)


def first_run_cost(rows: Iterable[dict]) -> str:
    """What the stored answers cost when they were first made, per answer. A later run reads
    them from disk in no time, so that run's own time says nothing about the route."""
    known = [row for row in rows if "seconds" in row]
    if not known:
        return "cost not recorded"
    count = len(known)
    return (f"first run, per query: {sum(row['tokens_in'] for row in known) // count} tokens in, "
            f"{sum(row['tokens_out'] for row in known) // count} out, "
            f"{sum(row['seconds'] for row in known) / count:.1f} s")
