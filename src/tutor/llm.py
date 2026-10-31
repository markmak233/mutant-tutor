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

    [llm.profiles.claude-code]
    provider = "assistant"                 # no API: a coding assistant answers request
                                           # files in <workspace>/.exchange/claude-code/

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
from collections.abc import Callable, Mapping
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
    # One model per size (e.g. deepseek-v4-flash) with thinking on by default (2026).
    "deepseek": ("https://api.deepseek.com",
                 ({"thinking": {"type": "disabled"}}, {"reasoning_effort": "none"})),
    "custom": (None, ()),  # any other OpenAI-compatible server; base_url required
    # No server: a coding assistant answers request files (tutor/exchange.py). Its "model"
    # names the assistant (default: the profile name), so two profiles of one assistant
    # (one for the oracle) are recognised as the same reader.
    "assistant": (None, ()),
}
# Providers that send token usage in a streamed reply's last piece when asked to with
# stream_options (documented); others stream without the option, which may be refused.
STREAM_USAGE = {"openai", "deepseek"}
PROFILE_FIELDS = {"provider", "model", "base_url", "api_key_env", "reasoning_effort",
                  "reasoning_effort_on", "timeout_s"}
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
    # Request fields when thinking is on: a profile's `reasoning_effort_on`, e.g. DeepSeek's
    # "low" (its default is "high"). Empty: the model's default depth.
    thinking_on: dict = field(default_factory=dict)
    queue: Path | None = None  # an assistant profile's request folder (provider "assistant")


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
    return _from_profile(name, profiles[name], env, workspace)


def roles(workspace: Path | None = None) -> dict[str, str]:
    """The workspace's [llm.roles]: role -> profile name ({} without an [llm] section)."""
    return dict(_llm_section(workspace).get("roles", {}))


def _llm_section(workspace: Path | None) -> dict:
    if workspace is None or not (workspace / "workspace.toml").is_file():
        return {}
    with (workspace / "workspace.toml").open("rb") as f:
        return tomllib.load(f).get("llm", {})


def _from_profile(name: str, data: dict, env: Mapping[str, str],
                  workspace: Path | None = None) -> LLMConfig:
    where = f"[llm.profiles.{name}]"
    if "api_key" in data or any(isinstance(v, str) and KEY_LIKE.match(v) for v in data.values()):
        raise LLMError(f"{where} holds a key: keys belong in .env, named by api_key_env")
    unknown = set(data) - PROFILE_FIELDS
    if unknown:
        raise LLMError(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
    provider = data.get("provider")
    if provider not in PROVIDERS:
        raise LLMError(f"{where} provider must be one of {', '.join(PROVIDERS)}")
    if provider == "assistant":
        # Its "base_url" is the queue's name, so the oracle check (another provider than
        # the writer) tells two assistant profiles apart by their queues.
        queue = workspace / ".exchange" / name if workspace else None
        return LLMConfig(name, provider, f"assistant:{name}", data.get("model", name),
                         queue=queue)
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
    thinking_on = ({"reasoning_effort": data["reasoning_effort_on"]}
                   if "reasoning_effort_on" in data else {})
    return LLMConfig(name, provider, base_url.rstrip("/"), data["model"], api_key,
                     float(data.get("timeout_s", 300)), thinking_off, thinking_on=thinking_on)


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
                     env.get("LLM_API_KEY") or None, config.timeout_s, config.thinking_off,
                     thinking_on=config.thinking_on)


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
         response_schema: dict | None = None, stream: bool = False,
         on_progress: Callable[[int], None] | None = None) -> Reply:
    """One chat completion. `purpose` names the caller in the call log (e.g. "vision").
    An assistant profile answers from its request files instead (tutor/exchange.py): the
    stored answer, or AwaitingAnswer after writing the request.
    `thinking=False` turns a reasoning model's hidden thinking off (or as low as the model
    allows), for tasks such as copying text from an image that need none.
    `response_schema` (a JSON Schema) asks for structured output: the reply text is then
    JSON fitting the schema. GLM-5.3-Flash and LM Studio enforce it (tested 2026-10-28: keys
    the prompt asked for but the schema forbids were left out); the caller still parses and
    validates it, since a cut-off reply is not valid JSON.
    `stream=True` receives the reply in pieces: `timeout_s` then bounds each pause, not the
    whole reply, and `on_progress` is called with the characters received so far."""
    if config.provider == "assistant":
        from tutor import exchange  # imported here: exchange builds on this module

        start = time.monotonic()
        reply = exchange.ask(config, messages, purpose, response_schema)  # or AwaitingAnswer
        _log(log_path, purpose, config, reply, time.monotonic() - start, None)
        return reply
    openai = config.provider == "openai"
    body = {"model": config.model, "messages": messages}
    if not openai:
        # OpenAI reasoning models accept only their default temperature.
        body["temperature"] = temperature
    if max_tokens is not None:
        # OpenAI reasoning models refuse max_tokens; the limit there includes reasoning.
        body["max_completion_tokens" if openai else "max_tokens"] = max_tokens
    if response_schema is not None and config.provider == "deepseek":
        # DeepSeek's chat API documents only JSON mode, and only with thinking off: the
        # schema goes into the prompt instead, and the caller's validation still decides.
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [*messages, {"role": "user", "content": (
            "Reply with one JSON object that fits this JSON Schema:\n"
            + json.dumps(response_schema))}]
    elif response_schema is not None:
        # OpenAI's strict mode needs every field required, which a schema with defaults
        # (optional fields) is not; without it the schema still guides the reply, and the
        # caller's validation catches what does not fit.
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "output", "schema": response_schema, "strict": not openai}}
    if stream:
        body["stream"] = True
        if config.provider in STREAM_USAGE:
            body["stream_options"] = {"include_usage": True}
    if thinking:
        variants = [config.thinking_on]
    else:
        variants = list(config.thinking_off) or [{}]
    for number, extra in enumerate(variants, 1):
        try:
            return _post(config, {**body, **extra}, purpose, log_path, on_progress)
        except _Rejected as e:
            if number == len(variants):
                raise LLMError(str(e)) from None
    raise AssertionError("unreachable")


class _StreamCut(Exception):
    """A stream ended with neither [DONE] nor a finish reason: the reply may be incomplete."""


class _Rejected(Exception):
    """HTTP 400: the request (often a thinking switch) was refused; another variant may work."""


def _post(config: LLMConfig, body: dict, purpose: str, log_path: Path | None,
          on_progress: Callable[[int], None] | None = None) -> Reply:
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
            data = (_read_stream(response, on_progress) if body.get("stream")
                    else json.loads(response.read().decode("utf-8", errors="replace")))
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        # Redacted before it is cut, so a key on the cut cannot be left half there.
        body = _redact(e.read()[:4000].decode(errors="replace"), config)
        error = f"HTTP {e.code} from {config.base_url}: {body[:300]}"
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
        error = _redact(f"cannot reach {config.base_url} ({e}); is the server running?", config)
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    except _StreamCut as e:
        error = f"{e} from {config.base_url}; try again"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        error = _redact(f"unexpected reply from {config.base_url}: {e!r}", config)
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    usage = data.get("usage") or {}
    reply = Reply(text, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                  time.monotonic() - start, choice.get("finish_reason"),
                  (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
    _log(log_path, purpose, config, reply, reply.seconds, None)
    return reply


def _redact(text: str, config: LLMConfig) -> str:
    """An error body may echo the request (a proxy returning the Authorization header, a
    provider quoting the key it refused): the key never reaches a message or the log."""
    if config.api_key:
        text = text.replace(config.api_key, "[key]")
    return _SECRET.sub("[key]", text)


_SECRET = re.compile(r"(?<=Bearer )\S+|sk-[\w-]{16,}")


def _read_stream(response, on_progress: Callable[[int], None] | None) -> dict:
    """A streamed completion (server-sent events, one `data: {json}` line per piece) put
    back together as the reply a call without streaming returns."""
    parts, received, finish, usage, done = [], 0, None, None, False
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            done = True
            break
        chunk = json.loads(payload)
        if "error" in chunk:
            raise KeyError(f"error in the stream: {chunk['error']}")
        usage = chunk.get("usage") or usage  # the last piece, when the provider sends it
        for choice in chunk.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                parts.append(text)
                received += len(text)
                if on_progress:
                    on_progress(received)
            finish = choice.get("finish_reason") or finish
    if not done and finish is None:
        # The connection closed early; what arrived must not pass for a whole reply.
        raise _StreamCut(f"the stream ended after {received} characters without finishing")
    return {"choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}],
            "usage": usage or {}}


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
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
