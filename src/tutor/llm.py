"""A thin client for OpenAI-compatible chat and embedding APIs (docs/design.md, [3] model
access), plus Anthropic's own Messages API (tutor/llm_anthropic.py, provider "anthropic").

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

`python -m tutor.llm check [PROFILE ...]` sends each profile one tiny request, so a wrong key
or model name shows before a run that spends more.

How parallel calls start (owner, 2026-11-12: "each family its own way"): a lecture's first
generate call goes alone so its shared pages enter the provider's prompt cache, and the
lecture's other calls start when that cache can be read, which differs by provider
(`parallel_start`, defaults in PARALLEL_START). `max_parallel` caps the calls one endpoint
gets at once. Both can be set per profile:

    [llm.profiles.deepseek]
    provider = "deepseek"
    model = "deepseek-v4-flash"
    parallel_start = "first-event+5"       # together | first-event | first-event+N | first-done
    max_parallel = 4
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
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

# Per provider: the default base URL, and the request fields that turn thinking off, tried
# in order (a model that rejects one with HTTP 400 gets the next). GLM-5.3 models refuse
# `thinking: disabled` and accept only reasoning_effort low / high / max.
PROVIDERS = {
    # Servers on this machine (owner, 2026-11-10; LM Studio since 2026-10): each at the port
    # its docs give, with the request field its docs give for thinking off. A local server
    # may ignore a field it does not know instead of refusing it, so a switch that did not
    # take is not noticed here: `check` reports thinking that stayed on (Reply.reasoning_chars).
    "lmstudio": ("http://localhost:1234/v1", ({"reasoning_effort": "none"},)),
    # Ollama's OpenAI layer takes reasoning_effort ("none": off; tried 2026-11-10 on 0.30.2,
    # where its native `think: false` is ignored on this path, with no error).
    "ollama": ("http://localhost:11434/v1", ({"reasoning_effort": "none"},)),
    # llama-server: the chat template's switch (Qwen's name for it), else its budget.
    "llamacpp": ("http://localhost:8080/v1",
                 ({"chat_template_kwargs": {"enable_thinking": False}},
                  {"reasoning_budget": 0})),
    "vllm": ("http://localhost:8000/v1", ({"chat_template_kwargs": {"enable_thinking": False}},)),
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
    # Claude through its own Messages API (tutor/llm_anthropic.py): no thinking switches
    # here, as the depth is part of that request body (`output_config.effort`).
    "anthropic": ("https://api.anthropic.com/v1", ()),
    # No server: a coding assistant answers request files (tutor/exchange.py). Its "model"
    # names the assistant (default: the profile name), so two profiles of one assistant
    # (one for the oracle) are recognised as the same reader.
    "assistant": (None, ()),
}
# The thinking-off switch each model accepted, by (provider, base URL, model): its index in
# the profile's thinking_off. A model that refuses the first switch (GLM-5.3 refuses
# `thinking: disabled` and takes reasoning_effort low) is then refused once per run, not on
# every call (owner, 2026-11-07). Per process: every run tries the first switch again, so a
# provider that starts accepting it is noticed.
_ACCEPTED: dict[tuple[str, str, str], int] = {}
# Providers that send token usage in a streamed reply's last piece when asked to with
# stream_options (documented); others stream without the option, which may be refused.
STREAM_USAGE = {"openai", "deepseek"}
PROFILE_FIELDS = {"provider", "model", "base_url", "api_key_env", "reasoning_effort",
                  "reasoning_effort_on", "timeout_s", "temperature", "parallel_start",
                  "max_parallel"}
# When a lecture's other generate calls start, after its first call (graph/nodes.py,
# CacheGate): "together" (no wait: for a server with no prompt cache), "first-event" (as
# soon as the first reply begins to stream), "first-event+N" (N seconds after that), or
# "first-done" (when the first call has returned). Per provider, from measurements
# (2026-11-11/12: a 23k-33k-token shared prefix, one call started, three released, their
# cached share or, on a server that reports none, their time to the first text):
#   anthropic: 99.9% at the first event (the docs: readable once the response streams);
#   deepseek: 0% at the first event (0.6 s in), 99% at 3, 8, 15 and 30 s (owner chose 3);
#   glm: 0% at 3 and 30 s, 99.9% after the first call ended;
#   lmstudio (gemma 26B, 4 slots): released at the first event the others reused the
#     prefix (first text 4 s against 78-103 s cold), but the four replies decoding together
#     slowed the first: the whole set took 84 s against 68 s after the first ended, and 183 s
#     all together; ollama (one request at a time by default) queues them, any choice ~16 s.
# Every other provider (openai, custom, the other local servers) is not measured, so
# "first-done": never slower to the cache than waiting, at worst slower to start.
PARALLEL_START = {"anthropic": "first-event", "deepseek": "first-event+3"}
DEFAULT_PARALLEL_START = "first-done"
_START = re.compile(r"^(together|first-event|first-done|first-event\+(\d{1,3}(\.\d+)?))$")
MAX_PARALLEL_LIMIT = 32  # the most a profile's max_parallel may say (a typo's guard)
# A profile's `temperature = "default"`: the one value it takes. Requests then carry no
# temperature, for a model that refuses one (Claude Opus 5.5 answered HTTP 400,
# "`temperature` is deprecated for this model", 2026-11-09). Without the field every
# request asks for 0, as before, so no other profile's requests change.
MODEL_TEMPERATURE = "default"
# The line that puts a schema into the prompt for a provider that cannot take it as a request
# field (DeepSeek's JSON mode; Anthropic for a schema its structured output refuses). Byte
# for byte: DeepSeek's generate and repair prompts, pinned, are built from it.
SCHEMA_IN_PROMPT = "Reply with one JSON object that fits this JSON Schema:\n"
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
    # False (a profile's `temperature = "default"`): no temperature in the request.
    send_temperature: bool = True
    # How a lecture's other calls start after its first (PARALLEL_START), checked form.
    parallel_start: str = DEFAULT_PARALLEL_START
    # At most this many calls to this endpoint at once (None: the course's parallel only).
    max_parallel: int | None = None


class Prompt(str):
    """A prompt given in parts, the part every call of a kind shares first (a lecture's
    pages, say), then what this call adds. To every reader it is the one string the parts
    make: every OpenAI-shaped provider, the assistant's request files and their ids, the
    tests' fakes and the prompt pins see exactly the bytes a plain string would give. Only
    the anthropic provider looks at `parts`, to put a cache breakpoint after each shared
    part (docs/design.md, "Prompt layout for caching"). Equal to its plain string."""

    __slots__ = ("parts",)

    def __new__(cls, *parts: str) -> Prompt:
        prompt = super().__new__(cls, "".join(parts))
        prompt.parts = parts
        return prompt


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float
    finish_reason: str | None = None  # "length": cut off at max_tokens
    # Input tokens served from the provider's prompt cache (cheaper), when it reports them.
    cached_tokens: int | None = None
    # Input tokens written to that cache this call (Anthropic prices them above base),
    # when the provider reports them apart; counted in input_tokens as well.
    cache_write_tokens: int | None = None
    # Characters of hidden thinking the reply carried (`reasoning_content` or `reasoning`
    # beside the content, or a <think> block inside it): 0 when none came, None when the
    # provider's shape is not read for it. A call with thinking off that carries some shows
    # the switch did not take (a local server that ignores a field it does not know).
    reasoning_chars: int | None = None


def unfenced(text: str) -> str:
    """A reply's text without a ```json fence around it, as assistants and chat models
    often write one even when asked for JSON alone (Sonnet 5.5 on the generate prompt,
    2026-11-11: two first drafts of four failed to parse at line 1, column 1; the prompt
    cannot ask Anthropic's structured output for help, as the draft schema has a free-form
    map). The first line of the fence goes, whatever follows the backticks ("json"), and
    the last fence with it; text without a fence comes back stripped only."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped


def json_body(text: str) -> str:
    """The JSON object or array in a reply asked for JSON alone: the text without a fence,
    and, when it still does not start with `{` or `[`, from the first `{` to the last `}`
    (Sonnet 5.5 on the generate prompt, 2026-11-11: "Here is the exercise as one JSON
    object. ..." and then the object). Text with no brace at all comes back as it is, so
    the parser's error names it."""
    stripped = unfenced(text)
    if stripped[:1] in ("{", "[") or "{" not in stripped:
        return stripped
    return stripped[stripped.index("{"):stripped.rindex("}") + 1 if "}" in stripped else None]


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
    # Only the one value: a number here would look like a setting the calls then ignore
    # (callers choose the temperature; the profile only says the model takes none).
    if data.get("temperature", MODEL_TEMPERATURE) != MODEL_TEMPERATURE:
        raise LLMError(f'{where} temperature can only be "{MODEL_TEMPERATURE}" (none is sent, '
                       "for a model that refuses one); leave it out to send 0")
    start = data.get("parallel_start", PARALLEL_START.get(provider, DEFAULT_PARALLEL_START))
    if not isinstance(start, str) or not _START.match(start):
        raise LLMError(f'{where} parallel_start must be "together", "first-event", '
                       f'"first-event+N" (seconds, e.g. "first-event+3") or "first-done", '
                       f"not {start!r}")
    cap = data.get("max_parallel")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int)
                            or not 1 <= cap <= MAX_PARALLEL_LIMIT):
        raise LLMError(f"{where} max_parallel must be a whole number from 1 to "
                       f"{MAX_PARALLEL_LIMIT}, not {cap!r}")
    return LLMConfig(name, provider, base_url.rstrip("/"), data["model"], api_key,
                     float(data.get("timeout_s", 300)), thinking_off, thinking_on=thinking_on,
                     send_temperature="temperature" not in data, parallel_start=start,
                     max_parallel=cap)


def start_wait(config: LLMConfig) -> float | None:
    """The seconds after the first call's first streamed event when a lecture's other calls
    may start: 0.0 for "first-event", N for "first-event+N"; None for "first-done" (they
    wait for the first call's end) and for "together" (the caller skips the wait)."""
    match = _START.match(config.parallel_start)
    if match is None or match[1] in ("first-done", "together"):
        return None
    return float(match[2]) if match[2] else 0.0


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
    return replace(config, api_key=env.get("LLM_API_KEY") or None)


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
         on_progress: Callable[[int], None] | None = None,
         schema_first: bool = False) -> Reply:
    """One chat completion. `purpose` names the caller in the call log (e.g. "vision").
    An assistant profile answers from its request files instead (tutor/exchange.py): the
    stored answer, or AwaitingAnswer after writing the request.
    `thinking=False` turns a reasoning model's hidden thinking off (or as low as the model
    allows), for tasks such as copying text from an image that need none.
    `response_schema` (a JSON Schema) asks for structured output: the reply text is then
    JSON fitting the schema. GLM-5.3-Flash and LM Studio enforce it (tested 2026-10-28: keys
    the prompt asked for but the schema forbids were left out); the caller still parses and
    validates it, since a cut-off reply is not valid JSON.
    `schema_first=True` places DeepSeek's shared schema before variable messages. Existing
    callers keep the original layout; doctor uses the pure payload builder for preview.
    `stream=True` receives the reply in pieces: `timeout_s` then bounds each pause, not the
    whole reply, and `on_progress` is called with the characters received so far."""
    if config.provider == "assistant":
        from tutor import exchange  # imported here: exchange builds on this module

        start = time.monotonic()
        reply = exchange.ask(config, messages, purpose, response_schema)  # or AwaitingAnswer
        _log(log_path, purpose, config, reply, time.monotonic() - start, None)
        return reply
    if config.provider == "anthropic":
        from tutor import llm_anthropic  # imported here: it builds on this module

        return llm_anthropic.chat(config, messages, purpose=purpose, log_path=log_path,
                                  max_tokens=max_tokens, response_schema=response_schema,
                                  stream=stream, on_progress=on_progress, thinking=thinking)
    body = chat_payload(config, messages, max_tokens=max_tokens, temperature=temperature,
                        response_schema=response_schema, stream=stream, schema_first=schema_first)
    key = (config.provider, config.base_url, config.model)
    if thinking:
        variants, first = [config.thinking_on], 0
    else:
        variants = list(config.thinking_off) or [{}]
        first = min(_ACCEPTED.get(key, 0), len(variants) - 1)  # the switch it took before
    for number in range(first, len(variants)):
        try:
            reply = _post(config, {**body, **variants[number]}, purpose, log_path, on_progress)
        except Rejected as e:
            if number == len(variants) - 1:
                raise LLMError(str(e)) from None
            continue
        if not thinking:
            _ACCEPTED[key] = number
        return reply
    raise AssertionError("unreachable")


def chat_payload(config: LLMConfig, messages: list[dict], *, max_tokens: int | None = None,
                 temperature: float = 0.0, response_schema: dict | None = None,
                 stream: bool = False, schema_first: bool = False,
                 thinking: bool = True) -> dict:
    """Pure provider payload layout, shared by sending and doctor preview/size checks.

    The default is the original chat payload byte for byte. Provider compatibility
    variants are added by chat; no credential headers, network or file access happens here.
    `thinking` matters only to an anthropic profile, whose depth is part of the body
    (the others add it as a variant in chat); `schema_first` and `temperature` do not
    apply there (the schema is a field of its own, and it sends no temperature).
    """
    if config.provider == "anthropic":
        from tutor import llm_anthropic  # imported here: it builds on this module

        return llm_anthropic.payload(config, messages, max_tokens=max_tokens,
                                     response_schema=response_schema, stream=stream,
                                     thinking=thinking)
    openai = config.provider == "openai"
    body = {"model": config.model, "messages": messages}
    if not openai and config.send_temperature:
        # OpenAI reasoning models accept only their default temperature, and so does a
        # profile that says `temperature = "default"`.
        body["temperature"] = temperature
    if max_tokens is not None:
        # OpenAI reasoning models refuse max_tokens; the limit there includes reasoning.
        body["max_completion_tokens" if openai else "max_tokens"] = max_tokens
    if response_schema is not None and config.provider == "deepseek":
        # DeepSeek's chat API documents only JSON mode, and only with thinking off: the
        # schema goes into the prompt instead, and the caller's validation still decides.
        body["response_format"] = {"type": "json_object"}
        schema_message = {"role": "system" if schema_first else "user",
                          "content": SCHEMA_IN_PROMPT + json.dumps(response_schema)}
        # Put shared instructions and schema ahead of dynamic facts only when requested.
        if schema_first:
            at = next((i for i, m in enumerate(messages) if m["role"] != "system"), len(messages))
            body["messages"] = [*messages[:at], schema_message, *messages[at:]]
        else:
            body["messages"] = [*messages, schema_message]
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
    return body


def embed(config: LLMConfig, texts: list[str], *, purpose: str, log_path: Path | None = None,
          dimensions: int | None = None) -> list[list[float]]:
    """One vector per text, in their order, from the provider's /embeddings endpoint.
    `dimensions` asks for vectors of that size, for models that offer several. The caller
    keeps a batch within the provider's limits. Logged like a chat call, with no output
    tokens, so the call log still adds up to the whole cost."""
    if config.provider == "assistant":
        raise LLMError(f"profile {config.name!r} has no API to embed with: use a provider "
                       f"with an /embeddings endpoint")
    if config.provider == "anthropic":
        raise LLMError(f"profile {config.name!r}: Anthropic offers no embedding model; use an "
                       f"lmstudio, openai or glm profile for the embedding role")
    body: dict = {"model": config.model, "input": texts}
    if dimensions is not None:
        body["dimensions"] = dimensions
    request = urllib.request.Request(f"{config.base_url}/embeddings",
                                     data=json.dumps(body).encode(), headers=_headers(config))
    start = time.monotonic()
    error = None
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        # The reply says which input each vector belongs to, so sort by that, not by order.
        vectors = [row["embedding"] for row in sorted(data["data"], key=lambda r: r["index"])]
        if len(vectors) != len(texts):
            error = f"{len(vectors)} vectors for {len(texts)} texts from {config.base_url}"
    except urllib.error.HTTPError as e:
        # Redacted before it is cut, so a key on the cut cannot be left half there.
        reply_text = _redact(e.read()[:4000].decode(errors="replace"), config)
        error = f"HTTP {e.code} from {config.base_url}: {reply_text[:300]}"
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        error = _redact(f"cannot reach {config.base_url} ({e}); is the server running?", config)
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        error = _redact(f"unexpected reply from {config.base_url}: {e!r}", config)
    seconds = time.monotonic() - start
    if error is not None:
        _log(log_path, purpose, config, None, seconds, error)
        raise LLMError(error)
    tokens = (data.get("usage") or {}).get("prompt_tokens")
    _log(log_path, purpose, config, Reply("", tokens, None, seconds), seconds, None)
    return vectors


class StreamCut(Exception):
    """A stream ended before its end mark ([DONE], or Anthropic's message_stop): the reply
    may be incomplete. Raised by a stream reader; exchange() turns it into an LLMError."""


class Rejected(Exception):
    """HTTP 400: the request (often a thinking switch) was refused; another variant may work."""


def _headers(config: LLMConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.provider == "anthropic":
        from tutor import llm_anthropic

        headers["anthropic-version"] = llm_anthropic.VERSION
        if config.api_key:
            headers["x-api-key"] = config.api_key
    elif config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _post(config: LLMConfig, body: dict, purpose: str, log_path: Path | None,
          on_progress: Callable[[int], None] | None = None) -> Reply:
    """One chat completion request, read in the OpenAI shape."""
    def read(response) -> Reply:
        # A stray byte that is not UTF-8 in a long reply should cost one character
        # (the caller validates the text), not the whole call.
        data = (_read_stream(response, on_progress) if body.get("stream")
                else json.loads(response.read().decode("utf-8", errors="replace")))
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        message = choice["message"]
        return Reply(message["content"] or "", usage.get("prompt_tokens"),
                     usage.get("completion_tokens"), 0.0, choice.get("finish_reason"),
                     (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                     reasoning_chars=reasoning_chars(message))

    return exchange(config, "/chat/completions", body, purpose, log_path, read)


def exchange(config: LLMConfig, path: str, body: dict, purpose: str, log_path: Path | None,
             read: Callable[[object], Reply]) -> Reply:
    """POST `body` to the profile's server at `path` and return `read(response)` with its
    seconds filled in, logged. Every way the call can fail becomes a logged LLMError whose
    message says what to do, with the key never in it: an HTTP error (400 is Rejected, so
    chat() can try its next thinking switch), a reply slower than timeout_s, an unreachable
    server, a stream cut short, or a body of another shape than `read` expects. The
    anthropic module shares this; only `path`, the headers and `read` differ."""
    request = urllib.request.Request(f"{config.base_url}{path}",
                                     data=json.dumps(body).encode(), headers=_headers(config))
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            reply = read(response)
    except urllib.error.HTTPError as e:
        # Redacted before it is cut, so a key on the cut cannot be left half there.
        body = _redact(e.read()[:4000].decode(errors="replace"), config)
        error = f"HTTP {e.code} from {config.base_url}: {body[:300]}"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        if e.code == 400:
            raise Rejected(error) from None
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
    except StreamCut as e:
        error = f"{e} from {config.base_url}; try again"
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        error = _redact(f"unexpected reply from {config.base_url}: {e!r}", config)
        _log(log_path, purpose, config, None, time.monotonic() - start, error)
        raise LLMError(error) from e
    reply = replace(reply, seconds=time.monotonic() - start)
    _log(log_path, purpose, config, reply, reply.seconds, None)
    return reply


def _redact(text: str, config: LLMConfig) -> str:
    """An error body may echo the request (a proxy returning the Authorization header, a
    provider quoting the key it refused): the key never reaches a message or the log."""
    if config.api_key:
        text = text.replace(config.api_key, "[key]")
    return _SECRET.sub("[key]", text)


_SECRET = re.compile(r"(?<=Bearer )\S+|sk-[\w-]{16,}")


THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)


def reasoning_chars(message: dict) -> int:
    """How much hidden thinking a reply's message carried: the `reasoning_content` field
    (DeepSeek, vLLM, llama.cpp, LM Studio) or `reasoning` (Ollama) beside the content, and
    a <think> block a server left inside the content (one that did not parse it out)."""
    apart = message.get("reasoning_content") or message.get("reasoning") or ""
    inside = THINK_BLOCK.match(message.get("content") or "")
    return len(apart) + (len(inside.group(0)) if inside else 0)


def _read_stream(response, on_progress: Callable[[int], None] | None) -> dict:
    """A streamed completion (server-sent events, one `data: {json}` line per piece) put
    back together as the reply a call without streaming returns. `on_progress(0)` at the
    first piece says the stream has begun (as the anthropic reader does at message_start;
    the cache gate in graph.nodes opens on it), then the characters received so far at
    each piece of text."""
    parts, received, finish, usage, done = [], 0, None, None, False
    thought = 0  # characters of reasoning_content / reasoning in the pieces
    begun = False
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        if not begun:
            begun = True
            if on_progress:
                on_progress(0)
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            done = True
            break
        chunk = json.loads(payload)
        if "error" in chunk:
            raise KeyError(f"error in the stream: {chunk['error']}")
        usage = chunk.get("usage") or usage  # the last piece, when the provider sends it
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            thought += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
            text = delta.get("content")
            if text:
                parts.append(text)
                received += len(text)
                if on_progress:
                    on_progress(received)
            finish = choice.get("finish_reason") or finish
    if not done and finish is None:
        # The connection closed early; what arrived must not pass for a whole reply.
        raise StreamCut(f"the stream ended after {received} characters without finishing")
    return {"choices": [{"message": {"content": "".join(parts), "reasoning": "x" * thought},
                         "finish_reason": finish}], "usage": usage or {}}


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
        "cache_write_tokens": reply.cache_write_tokens if reply else None,
        "output_tokens": reply.output_tokens if reply else None,
        "seconds": round(seconds, 2),
        "finish_reason": reply.finish_reason if reply else None,
        "reasoning_chars": reply.reasoning_chars if reply else None,
        "outcome": "ok" if error is None else "error",
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# --- python -m tutor.llm check: does each model answer? ---------------------------------------

CHECK_PROMPT = "Reply with the single word: ok"


def check(config: LLMConfig, log_path: Path | None = None, embedding: bool = False) -> str:
    """One tiny request, to see that the key, the address and the model name work, before a
    run that would spend more finds out halfway (the GUI's "Check" button). Thinking off and
    a few output tokens: it costs next to nothing. Returns a line saying how it went; raises
    LLMError, with the provider's reason, when it did not."""
    if config.provider == "assistant":
        return "nothing to check: a coding assistant answers its requests"
    started = time.monotonic()
    if embedding:
        vectors = embed(config, ["ok"], purpose="check", log_path=log_path)
        return f"ok, {len(vectors[0])} dimensions, {time.monotonic() - started:.1f} s"
    reply = chat(config, [{"role": "user", "content": CHECK_PROMPT}], purpose="check",
                 log_path=log_path, max_tokens=32, thinking=False)
    said = " ".join(reply.text.split())[:40] or "(an empty reply)"
    line = f"ok, answered {said!r} in {reply.seconds:.1f} s"
    if reply.reasoning_chars:
        # The switch for thinking off did not take: a local server ignores a field it does
        # not know instead of refusing it (which would have made chat() try the next one).
        line += (f"; but thinking stayed on ({reply.reasoning_chars} characters of it): the "
                 "provider's switch for thinking off was ignored. Set reasoning_effort in the "
                 "profile to what this model takes, or start the server with thinking off.")
    return line


def main(argv: list[str] | None = None) -> int:
    """python -m tutor.llm check [PROFILE ...]: every profile of the workspace, or those named."""
    import sys

    from tutor.config import WorkspaceError, load_env, load_workspace

    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "check":
        print("Usage: python -m tutor.llm check [PROFILE ...]  (default: every profile in "
              "workspace.toml's [llm.profiles])", file=sys.stderr)
        return 2
    load_env()
    try:
        workspace = load_workspace().root
    except WorkspaceError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    section = _llm_section(workspace)
    names = args[1:] or sorted(section.get("profiles", {}))
    if not names:
        print("workspace.toml has no [llm.profiles] to check", file=sys.stderr)
        return 1
    used: dict[str, set[str]] = {}
    for role, name in section.get("roles", {}).items():
        used.setdefault(name, set()).add(role)
    failed = 0
    for name in names:
        try:
            config = config_for("check", name, workspace)
            # A profile only the embedding role uses is an embedding model: no chat endpoint.
            line = check(config, workspace / "logs" / "llm_calls.jsonl",
                         embedding=used.get(name) == {"embedding"})
        except LLMError as e:
            failed += 1
            line = f"failed: {e}"
        print(f"{name}: {line}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
