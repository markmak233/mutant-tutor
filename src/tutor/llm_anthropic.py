"""Anthropic's Messages API behind `tutor.llm.chat()` (docs/design.md, "Anthropic provider").

A profile with `provider = "anthropic"` sends Claude's own request shape instead of an
OpenAI-style chat completion. Anthropic also serves an OpenAI-compatible layer, but its
documentation calls that layer a way "to test and compare model capabilities", not a
production path, and it offers no prompt caching: the judge's whole-lecture prompts would
pay full price on every call. The native shape is small enough to speak directly, with the
standard library, as llm.py does for the other providers.

What differs from a chat completion, and how this module maps it:

- The system prompt is a top-level `system` field, not a message; `max_tokens` is required.
- Thinking is adaptive and on by default on the Claude 5.5 models (Opus cannot turn it off
  at all), so no `thinking` field is sent. Depth goes through `output_config.effort`:
  `thinking=False` sends the profile's `reasoning_effort` (default "low"), `thinking=True`
  the profile's `reasoning_effort_on`, or nothing, which is the model's default.
- No temperature: the 5.5 models refuse one (HTTP 400, 2026-11-09), and a profile that uses
  this provider says `temperature = "default"` in spirit whether or not it writes it.
- A JSON schema goes to `output_config.format` (structured output); the reply's text is the
  JSON. Anthropic refuses a schema with value constraints it does not enforce
  (`exclusiveMinimum`, `maxItems`, ...: HTTP 400, found 2026-11-10 with the draft and doctor
  schemas), so those keywords are dropped from the copy sent; the caller's pydantic
  validation still applies them to the reply. A schema with a free-form map (the draft's
  `data_files: dict[str, str]`, `additionalProperties` with a schema) cannot be sent at all:
  that call puts the schema into the prompt instead, as DeepSeek's JSON mode does, and the
  caller validates the reply as it always does. Today only generate and repair have such a
  schema, and both run on DeepSeek; the roles on Claude (plan, oracle, judge, doctor) get
  structured output.
- The reply is a list of content blocks; the text blocks are joined, thinking blocks are
  left out. `stop_reason` is mapped to the finish reasons callers already read
  (`max_tokens` -> "length", which nodes.py shows as "cut off at max_tokens").
- Usage: Anthropic's `input_tokens` leaves out the tokens read from or written to the
  prompt cache, so `Reply.input_tokens` adds the three back together (OpenAI's
  prompt_tokens includes cached tokens; costs are computed from the total), and the cache
  read and write counts are kept apart on the reply and in the call log.
- Streaming is a different event stream (message_start, content_block_delta, message_delta,
  message_stop), put back together into the same Reply.

Cache breakpoints: a caller that gives a message's content as an `llm.Prompt` (the shared
part first, then this call's own) gets one text block per part, with `cache_control` on
every part but the last; the system prompt's last block is marked too. Anthropic then
reuses the prefix up to a breakpoint for 5 minutes at a tenth of the input price or less
(a write costs 1.25 times), so the items of one lecture, and the repair rounds of one item,
pay for its pages once. A plain string is one block with no breakpoint. At most 4
breakpoints per request (the API's limit): the system's and the first three of the parts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from tutor import llm

VERSION = "2026-10-21"  # the anthropic-version header the current docs use
# A reply with no cap from the caller: the pipeline's default for a thinking reply
# (graph/settings.py); the model's thinking counts towards it.
DEFAULT_MAX_TOKENS = 32_000
EFFORT_OFF = "low"  # thinking "off": the lowest effort every 5.5 model takes
CACHE = {"type": "ephemeral"}  # a cache breakpoint with the default 5-minute lifetime
MAX_BREAKPOINTS = 4  # the API's limit per request
# Anthropic's stop reasons -> the finish reasons callers read ("length": cut off).
STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}
# JSON Schema keywords Anthropic's structured output refuses (docs, "JSON Schema
# limitations", checked 2026-11-10): value and size constraints it does not enforce. They are
# left out of the schema sent; the model still sees the field's type and description.
UNSUPPORTED_KEYWORDS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "pattern",
    "minProperties", "maxProperties"})
# An OpenAI-style data URL image part: `data:image/png;base64,....`
_DATA_URL = re.compile(r"^data:(?P<media>image/[\w.+-]+);base64,(?P<data>.*)$", re.DOTALL)


def chat(config: llm.LLMConfig, messages: list[dict], *, purpose: str,
         log_path: Path | None, max_tokens: int | None, response_schema: dict | None,
         stream: bool, on_progress: Callable[[int], None] | None, thinking: bool) -> llm.Reply:
    """llm.chat() for an anthropic profile: one request, no thinking-switch variants to try
    (effort is part of the body), every failure a logged LLMError."""
    body = payload(config, messages, max_tokens=max_tokens, response_schema=response_schema,
                   stream=stream, thinking=thinking)
    try:
        return llm.exchange(config, "/messages", body, purpose, log_path,
                            lambda response: _reply(_read(response, stream, on_progress)))
    except llm.Rejected as e:
        # HTTP 400 is final here: there is no other variant of the request to try, and
        # Anthropic's message (an unsupported schema keyword, say) says what was wrong.
        raise llm.LLMError(str(e)) from None


def payload(config: llm.LLMConfig, messages: list[dict], *, max_tokens: int | None = None,
            response_schema: dict | None = None, stream: bool = False,
            thinking: bool = True) -> dict:
    """The Messages API body for these chat-style messages. Pure: no network, no key."""
    system = [block for m in messages if m["role"] == "system" for block in blocks(m["content"])]
    body: dict = {"model": config.model,
                  "max_tokens": DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
                  "messages": [{"role": m["role"], "content": blocks(m["content"])}
                               for m in messages if m["role"] != "system"]}
    if system:
        # The system prompt is the same for every call of a kind: always a breakpoint
        # (a prompt under the model's minimum, 512 tokens, is simply not cached).
        system[-1]["cache_control"] = CACHE
        body["system"] = system
    _cap_breakpoints(body)
    output: dict = {}
    level = effort(config, thinking)
    if level:
        output["effort"] = level
    if response_schema is not None:
        schema = supported(response_schema)
        if structurable(schema):
            output["format"] = {"type": "json_schema", "schema": schema}
        else:
            # The schema as text, after the last user message's content (the same line
            # DeepSeek's JSON mode uses); the caller's validation decides, as there.
            body["messages"][-1]["content"].append(
                {"type": "text", "text": llm.SCHEMA_IN_PROMPT + json.dumps(response_schema)})
    if output:
        body["output_config"] = output
    if stream:
        body["stream"] = True
    return body


def effort(config: llm.LLMConfig, thinking: bool) -> str | None:
    """The effort level for this call, from the profile's reasoning_effort fields: off ->
    `reasoning_effort` or "low"; on -> `reasoning_effort_on`, or None for the model's own
    default. The fields are the same ones the OpenAI-style providers send as a request
    field, so a profile keeps one vocabulary whichever provider it names."""
    if thinking:
        return config.thinking_on.get("reasoning_effort") or None
    for variant in config.thinking_off:
        if variant.get("reasoning_effort"):
            return variant["reasoning_effort"]
    return EFFORT_OFF


def supported(schema):
    """A copy of the schema without the keywords Anthropic refuses, at every depth (object
    properties, array items, $defs, anyOf branches). Only a dict's keys are keywords: a
    property *named* "pattern", say, sits under "properties" and keeps its name, as the
    walk removes keywords from a schema dict, not from the "properties" map it holds."""
    if isinstance(schema, list):
        return [supported(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_KEYWORDS:
            continue
        if key in ("properties", "$defs", "definitions", "patternProperties"):
            # Maps from a name to a schema: the names are not keywords.
            out[key] = {name: supported(sub) for name, sub in value.items()}
        else:
            out[key] = supported(value)
    if (out.get("type") == "object" or "properties" in out) and "additionalProperties" not in out:
        # Required by the API for every object ("must be explicitly set to false",
        # 2026-11-10, the coverage draft's schema); pydantic writes it only for a model
        # with extra="forbid". The caller's model still decides what it accepts.
        out["additionalProperties"] = False
    return out


def structurable(schema) -> bool:
    """Whether Anthropic's structured output takes this (cleaned) schema: no free-form map
    anywhere, i.e. `additionalProperties` nowhere but as false (the API's own rule, HTTP 400
    "Please set 'additionalProperties' to false", 2026-11-10)."""
    if isinstance(schema, list):
        return all(structurable(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    if schema.get("additionalProperties", False) is not False or "patternProperties" in schema:
        return False
    return all(structurable(value) for value in schema.values())


def blocks(content: str | list[dict]) -> list[dict]:
    """Content blocks from a message's content: an llm.Prompt becomes one text block per
    part with a cache breakpoint after each shared part (empty parts left out: the API
    refuses an empty text block); any other string one text block; a list of OpenAI-style
    parts (text, image_url with a data URL) the matching blocks, a part already in
    Anthropic's shape passing through."""
    if isinstance(content, llm.Prompt):
        parts = [{"type": "text", "text": part} for part in content.parts if part]
        for block in parts[:-1]:
            block["cache_control"] = CACHE
        return parts or [{"type": "text", "text": ""}]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for part in content:
        kind = part.get("type")
        if kind == "image_url":
            match = _DATA_URL.match(part["image_url"]["url"])
            if not match:
                raise llm.LLMError("an image for the anthropic provider must be a data URL "
                                   "(llm.image_message makes one)")
            out.append({"type": "image", "source": {
                "type": "base64", "media_type": match["media"], "data": match["data"]}})
        else:
            out.append(part)  # a text block, or any block already in Anthropic's shape
    return out


def _cap_breakpoints(body: dict) -> None:
    """No more than MAX_BREAKPOINTS `cache_control` marks in the request: the system's
    stays, then the earliest of the messages' (the largest shared parts come first), the
    rest are removed. Our callers set at most two; this keeps a longer Prompt legal."""
    marked = [block for message in body["messages"] for block in message["content"]
              if "cache_control" in block]
    allowed = MAX_BREAKPOINTS - sum("cache_control" in b for b in body.get("system", []))
    for block in marked[max(allowed, 0):]:
        del block["cache_control"]


def _read(response, stream: bool, on_progress: Callable[[int], None] | None) -> dict:
    """The Message object: parsed from one JSON body, or rebuilt from the event stream."""
    if stream:
        return _read_stream(response, on_progress)
    return json.loads(response.read().decode("utf-8", errors="replace"))


def _reply(message: dict) -> llm.Reply:
    """A Reply from a Message object (seconds are filled in by llm.exchange)."""
    content = message["content"]
    if not isinstance(content, list):  # TypeError: llm.exchange reports an unexpected reply
        raise TypeError("content is not a list of blocks")
    text = "".join(block.get("text") or "" for block in content if block.get("type") == "text")
    usage = message.get("usage") or {}
    fresh = usage.get("input_tokens")
    read = usage.get("cache_read_input_tokens")
    written = usage.get("cache_creation_input_tokens")
    total = None if fresh is None else fresh + (read or 0) + (written or 0)
    stop = message.get("stop_reason")
    return llm.Reply(text, total, usage.get("output_tokens"), 0.0,
                     STOP_REASONS.get(stop, stop), cached_tokens=read,
                     cache_write_tokens=written)


def _read_stream(response, on_progress: Callable[[int], None] | None) -> dict:
    """The event stream (server-sent events, `data: {json}` lines with a `type`) put back
    together as a Message object. Only text deltas are kept: thinking blocks are left out as
    they are from a reply sent whole. Usage arrives in two pieces: the input side in
    message_start, the output side (cumulative) in message_delta, with the stop reason.
    `ping` and any event type this code does not know are skipped, as the docs ask."""
    parts: list[str] = []
    received = 0
    message: dict = {"content": [], "stop_reason": None, "usage": {}}
    done = False
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue  # `event:` lines repeat the type; blank lines separate events
        event = json.loads(line.removeprefix("data:").strip())
        kind = event.get("type")
        if kind == "message_start":
            message["usage"].update((event.get("message") or {}).get("usage") or {})
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                parts.append(delta["text"])
                received += len(delta["text"])
                if on_progress:
                    on_progress(received)
        elif kind == "message_delta":
            stop = (event.get("delta") or {}).get("stop_reason")
            message["stop_reason"] = stop or message["stop_reason"]
            message["usage"].update(event.get("usage") or {})
        elif kind == "message_stop":
            done = True
            break
        elif kind == "error":
            # The server failed after the stream began (overloaded, say): the same error
            # path as a malformed reply, which llm.exchange reports and logs.
            raise KeyError(f"error in the stream: {event.get('error')}")
    if not done:
        # The connection closed early; what arrived must not pass for a whole reply.
        raise llm.StreamCut(f"the stream ended after {received} characters without finishing")
    message["content"] = [{"type": "text", "text": "".join(parts)}]
    return message
