"""A thin client for OpenAI-compatible chat APIs (docs/design.md, [3] model access).

Standard library only. Models are named profiles in the workspace's workspace.toml, and
roles (vision, generate, ...) say which profile a task uses:

    [llm.profiles.gemma-local]
    provider = "lmstudio"                  # sets the default base URL and thinking switch
    model = "google/gemma-4-26b-a4b-qat"

    [llm.profiles.glm-flash]
    provider = "glm"
    model = "glm-5.3-flash"
    api_key_env = "ZHIPU_API_KEY"          # the variable's name; the key stays in .env

    [llm.roles]
    vision = "gemma-local"

Keys live only in the environment (`.env`), never in the TOML: a profile with an `api_key`
field or a key-like value is refused. Without an [llm] section, LLM_PROVIDER / LLM_MODEL /
LLM_BASE_URL / LLM_API_KEY form a single default profile.

Every call appends one JSON line to a call log (purpose, profile, model, tokens including
those served from the provider's prompt cache, seconds, outcome); the key is never logged.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Per provider: the default base URL, and the request fields that turn thinking off, tried
# in order (a model that rejects one with HTTP 400 gets the next). GLM-5.3 models refuse
# `thinking: disabled` and accept only reasoning_effort low / high / max.
PROVIDERS = {
    "lmstudio": ("http://localhost:1234/v1", ({"reasoning_effort": "none"},)),
    "glm": ("https://open.bigmodel.cn/api/paas/v4",
            ({"thinking": {"type": "disabled"}}, {"reasoning_effort": "low"})),
    # Reasoning models differ in their lowest setting; the first one accepted is used.
    "openai": ("https://api.openai.com/v1",
               ({"reasoning_effort": "none"}, {"reasoning_effort": "minimal"},
                {"reasoning_effort": "low"})),
    "custom": (None, ()),  # any other OpenAI-compatible server; base_url required
}
PROFILE_FIELDS = {"provider", "model", "base_url", "api_key_env", "reasoning_effort",
                  "timeout_s"}
KEY_LIKE = re.compile(r"^(sk-[\w-]{16,}|[0-9a-f]{32}\.\w{8,})$")


class LLMError(Exception):
    """The model could not be configured, reached, or answered with an error. The message
    says why."""


@dataclass(frozen=True)
class LLMConfig:
    name: str  # the profile name
    provider: str
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)  # repr=False: never in a traceback
    timeout_s: float = 300.0
    thinking_off: tuple[dict, ...] = ()  # request fields to try, in order


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float
    finish_reason: str | None = None  # "length": cut off at max_tokens
    # Input tokens served from the provider's prompt cache (cheaper), when it reports them.
    cached_tokens: int | None = None


def config_for(role: str, profile: str | None = None, workspace: Path | None = None,
               environ: Mapping[str, str] | None = None) -> LLMConfig:
    """The model for `role`: the named `profile` if given, else the workspace's
    [llm.roles] entry, else the LLM_* environment variables."""
    env = os.environ if environ is None else environ
    section = _llm_section(workspace)
    if not section:
        if profile:
            raise LLMError(f"profile {profile!r} given, but workspace.toml has no [llm] section")
        return _from_env(env)
    profiles = section.get("profiles", {})
    name = profile or section.get("roles", {}).get(role)
    if not name:
        raise LLMError(f"no model for {role!r}: add `{role} = \"<profile>\"` to [llm.roles] "
                       f"in workspace.toml, or pass --profile")
    if name not in profiles:
        raise LLMError(f"workspace.toml has no [llm.profiles.{name}]; "
                       f"profiles: {', '.join(sorted(profiles)) or 'none'}")
    return _from_profile(name, profiles[name], env)


def _llm_section(workspace: Path | None) -> dict:
    if workspace is None or not (workspace / "workspace.toml").is_file():
        return {}
    with (workspace / "workspace.toml").open("rb") as f:
        return tomllib.load(f).get("llm", {})


def _from_profile(name: str, data: dict, env: Mapping[str, str]) -> LLMConfig:
    where = f"[llm.profiles.{name}]"
    if "api_key" in data or any(isinstance(v, str) and KEY_LIKE.match(v) for v in data.values()):
        raise LLMError(f"{where} holds a key: keys belong in .env, named by api_key_env")
    unknown = set(data) - PROFILE_FIELDS
    if unknown:
        raise LLMError(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
    provider = data.get("provider")
    if provider not in PROVIDERS:
        raise LLMError(f"{where} provider must be one of {', '.join(PROVIDERS)}")
    if not data.get("model"):
        raise LLMError(f"{where} needs a model")
    default_url, thinking_off = PROVIDERS[provider]
    base_url = data.get("base_url", default_url)
    if not base_url:
        raise LLMError(f"{where} needs a base_url (provider {provider!r} has no default)")
    api_key = None
    if "api_key_env" in data:
        api_key = env.get(data["api_key_env"])
        if not api_key:
            raise LLMError(f"{where} needs {data['api_key_env']} set in .env")
    if "reasoning_effort" in data:
        thinking_off = ({"reasoning_effort": data["reasoning_effort"]},)
    return LLMConfig(name, provider, base_url.rstrip("/"), data["model"], api_key,
                     float(data.get("timeout_s", 300)), thinking_off)


def _from_env(env: Mapping[str, str]) -> LLMConfig:
    data = {"provider": env.get("LLM_PROVIDER", "lmstudio"), "model": env.get("LLM_MODEL")}
    if env.get("LLM_BASE_URL"):
        data["base_url"] = env["LLM_BASE_URL"]
    if env.get("LLM_TIMEOUT_S"):
        data["timeout_s"] = env["LLM_TIMEOUT_S"]
    if not data["model"]:
        raise LLMError("no model configured: add an [llm] section to workspace.toml "
                       "(see docs/setup) or set LLM_MODEL in .env")
    config = _from_profile("env", data, env)
    return LLMConfig(config.name, config.provider, config.base_url, config.model,
                     env.get("LLM_API_KEY") or None, config.timeout_s, config.thinking_off)


def image_message(prompt: str, png: bytes) -> dict:
    """A user message with text and one PNG image, in the OpenAI content-parts format."""
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": url}},
    ]}


def chat(config: LLMConfig, messages: list[dict], *, purpose: str,
         log_path: Path | None = None, max_tokens: int | None = None,
         temperature: float = 0.0, thinking: bool = True,
         response_schema: dict | None = None) -> Reply:
    """One chat completion. `purpose` names the caller in the call log (e.g. "vision").
    `thinking=False` turns a reasoning model's hidden thinking off (or as low as the model
    allows), for tasks such as copying text from an image that need none.
    `response_schema` (a JSON Schema) asks for structured output: the reply text is then
    JSON fitting the schema. GLM-5.3-Flash and LM Studio enforce it (tested 2026-10-28: keys
    the prompt asked for but the schema forbids were left out); the caller still parses and
    validates it, since a cut-off reply is not valid JSON."""
    openai = config.provider == "openai"
    body = {"model": config.model, "messages": messages}
    if not openai:
        # OpenAI reasoning models accept only their default temperature.
        body["temperature"] = temperature
    if max_tokens is not None:
        # OpenAI reasoning models refuse max_tokens; the limit there includes reasoning.
        body["max_completion_tokens" if openai else "max_tokens"] = max_tokens
    if response_schema is not None:
        # OpenAI's strict mode needs every field required, which a schema with defaults
        # (optional fields) is not; without it the schema still guides the reply, and the
        # caller's validation catches what does not fit.
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "output", "schema": response_schema, "strict": not openai}}
    variants = [{}] if thinking or not config.thinking_off else list(config.thinking_off)
    for number, extra in enumerate(variants, 1):
        try:
            return _post(config, {**body, **extra}, purpose, log_path)
        except _Rejected as e:
            if number == len(variants):
                raise LLMError(str(e)) from None
    raise AssertionError("unreachable")


class _Rejected(Exception):
    """HTTP 400: the request (often a thinking switch) was refused; another variant may work."""


def _post(config: LLMConfig, body: dict, purpose: str, log_path: Path | None) -> Reply:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(f"{config.base_url}/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            # A stray byte that is not UTF-8 in a long reply should cost one character
            # (the caller validates the text), not the whole call.
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        error = f"HTTP {e.code} from {config.base_url}: {e.read()[:300].decode(errors='replace')}"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        if e.code == 400:
            raise _Rejected(error) from None
        raise LLMError(error) from e
    except TimeoutError as e:
        # The server answers but the reply takes longer than timeout_s (a long generation
        # with thinking on), not an unreachable server.
        error = (f"no reply from {config.base_url} within {config.timeout_s:.0f} s; raise "
                 f"timeout_s in [llm.profiles.{config.name}] for long replies")
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    except (urllib.error.URLError, OSError) as e:
        error = f"cannot reach {config.base_url} ({e}); is the server running?"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        error = f"unexpected reply from {config.base_url}: {e!r}"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    usage = data.get("usage") or {}
    reply = Reply(text, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                  time.monotonic() - start, choice.get("finish_reason"),
                  (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
    _log(log_path, purpose, config, reply, reply.seconds, None)
    return reply


def _log(path: Path | None, purpose: str, config: LLMConfig, reply: Reply | None,
         seconds: float, error: str | None) -> None:
    if path is None:
        return
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "purpose": purpose,
        "profile": config.name,
        "model": config.model,
        "base_url": config.base_url,
        "input_tokens": reply.input_tokens if reply else None,
        "cached_tokens": reply.cached_tokens if reply else None,
        "output_tokens": reply.output_tokens if reply else None,
        "seconds": round(seconds, 2),
        "finish_reason": reply.finish_reason if reply else None,
        "outcome": "ok" if error is None else "error",
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
