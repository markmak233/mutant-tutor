"""Explicit advice preparation and request; no installation, verification or file writes.

Probe facts first. Only an explicit env_file is read, into a private mapping. Messages
are sanitized once for both preview and send; only reviewed structured changes leave
this layer, never a provider's raw text. Test callers inject chat at call time.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from tutor import config, llm
from tutor.doctor_advice import prompts
from tutor.doctor_advice.facts import Facts
from tutor.doctor_advice.gate import review_reply
from tutor.doctor_advice.models import GateResult, reply_schema
from tutor.doctor_advice.redaction import Redactor
from tutor.doctor_rows import Row

MAX_FAILURE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 128 * 1024


class AdviceInputError(ValueError):
    """A bad explicitly selected input is a usage error, before a paid request."""


class AdviceSetupError(ValueError):
    """Advice cannot be prepared; no credential values are included in the message."""


def read_failure(path: Path) -> str:
    """Bound the actual read, not just stat size; never accept a credential file as a log."""
    try:
        if path.name.lower().startswith(".env") or path.resolve().name.lower().startswith(".env"):
            raise AdviceInputError("choose a failure log, not an .env credential file")
        if not path.is_file():
            raise AdviceInputError("failure input must be an existing regular UTF-8 file")
        with path.open("rb") as handle:
            raw = handle.read(MAX_FAILURE_BYTES + 1)
        if len(raw) > MAX_FAILURE_BYTES:
            raise AdviceInputError("failure record exceeds 64 KiB; select a smaller reviewed log")
        text = raw.decode("utf-8")
        if not text.strip():
            raise AdviceInputError("select a nonempty failure record")
        return text
    except (OSError, UnicodeError):
        raise AdviceInputError("failure input must be a readable UTF-8 file") from None


@dataclass(frozen=True)
class Prepared:
    """Private immutable preparation; repr cannot expose credentials or selected log text."""

    facts: Facts = field(repr=False)
    model: llm.LLMConfig = field(repr=False)
    redactor: Redactor = field(repr=False)
    parts: tuple[tuple[str, str], ...] = field(repr=False)
    destination: str

    @property
    def messages(self) -> list[dict]:
        return [{"role": role, "content": content} for role, content in self.parts]

    def preview(self) -> str:
        """Show the actual sanitized message layout, plus a native schema where applicable."""
        body = llm.chat_payload(self.model, self.messages, response_schema=reply_schema(),
                                schema_first=True, max_tokens=2048)
        return json.dumps(body, indent=2, ensure_ascii=False)


def prepare(facts: Facts, failure: str, *, course: Path | None = None,
            profile: str | None = None, env_file: Path | None = None,
            environ: Mapping[str, str] | None = None) -> Prepared:
    """Select an explicit doctor role/profile, never a generation role or implicit .env."""
    env = dict(os.environ if environ is None else environ)
    if env_file is not None:
        try:
            if not env_file.is_file():
                raise AdviceInputError("--env-file must name an existing readable UTF-8 file")
            config.load_env(env_file, environ=env)
        except (OSError, UnicodeError):
            raise AdviceInputError("--env-file must name a readable UTF-8 file") from None
    # No API/config check call: resolve configuration only, without spending a request.
    try:
        model = llm.config_for("doctor", profile, course, environ=env)
    except (llm.LLMError, ValueError, TypeError, AttributeError, KeyError):
        raise AdviceSetupError("configure a doctor role/--profile or exported LLM_* settings"
                               ) from None
    if model.provider == "assistant":
        raise AdviceSetupError("doctor advice needs an API/local-server profile; "
                               "assistant request files are not supported in this read-only mode")
    if not isinstance(model.model, str) or not model.model.strip():
        raise AdviceSetupError("configure a nonempty string model identifier for doctor")
    try:
        endpoint = urlsplit(model.base_url)
        if (endpoint.scheme not in ("http", "https") or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError
        _ = endpoint.port  # malformed ports are also configuration errors
        if not math.isfinite(model.timeout_s) or model.timeout_s <= 0:
            raise ValueError
    except (llm.LLMError, ValueError, TypeError):
        raise AdviceSetupError("configure a doctor role/--profile or exported LLM_* settings; "
                               "use HTTP(S) and a finite positive timeout") from None
    redactor = Redactor.from_environment(env, model.api_key,
                                        (str(course),) if course else ())
    if redactor.clean(model.model) != model.model:
        raise AdviceSetupError("model identifier has private/control data; correct its profile")
    if redactor.contains_secret(model.base_url):
        raise AdviceSetupError("put model credentials in api_key_env, not in its endpoint")
    parts = prompts.messages(facts, failure, redactor)
    check_messages(model, parts, redactor)
    destination = redactor.clean(
        f"{model.provider}/{model.model} at {redactor.url(model.base_url, origin=True)}")
    return Prepared(facts, model, redactor,
                    tuple((m["role"], m["content"]) for m in parts), destination)


def check_messages(model: llm.LLMConfig, messages: list[dict], redactor: Redactor, *,
                   thinking: bool = False) -> None:
    """Bound complete requests after every history addition and credential-redaction step.

    Check every provider compatibility variant and both output caps. Byte bounds are
    neither a token count nor a cost estimate. This never contacts the provider.
    """
    for cap in (2048, 4096):
        body = llm.chat_payload(model, messages, response_schema=reply_schema(),
                                schema_first=True, max_tokens=cap)
        variants = (model.thinking_on,) if thinking else (model.thinking_off or ({},))
        for variant in variants:
            encoded = json.dumps({**body, **variant})
            if redactor.contains_secret(encoded):
                raise AdviceSetupError("private data remains in payload; correct its configuration")
            if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise AdviceInputError("advice payload exceeds 128 KiB; use a smaller reviewed log")


def request(prepared: Prepared, *, chat: Callable | None = None) -> GateResult:
    """One logical request, one retry for truncation only; never return a partial reply."""
    chat = llm.chat if chat is None else chat
    for cap in (2048, 4096):
        reply = chat(prepared.model, prepared.messages, purpose="doctor-advice", log_path=None,
                     response_schema=reply_schema(), thinking=False, max_tokens=cap,
                     schema_first=True)
        if reply.finish_reason != "length":
            return review_reply(reply.text, prepared.facts.context)
    raise AdviceSetupError("model reply was truncated twice; choose another doctor profile")


def result_row(result: GateResult, redactor: Redactor) -> Row:
    """Structured output only; unknown-package candidates and raw explanations stay private."""
    if result.status == "rejected":
        return Row("FAIL", "model advice rejected: " + redactor.clean(result.reason),
                   "review the doctor profile/failure record and request advice again")
    if result.status == "new-package-review":
        return Row("WARN", "model advice needs a new package; independent review is pending",
                   "run --review-new-package with --second-profile for independent PyPI review")
    if result.status == "stopped":
        return Row("WARN", "model advice: no permitted repair found (unverified)",
                   "review the failure record; no repair has been applied")
    proposed = ["Python " + c.python if c.kind == "python" else
                f"{c.kind} {c.package}{c.spec or ''}" for c in result.changes]
    return Row("WARN", "model-proposed, unverified: " + redactor.clean("; ".join(proposed)),
               "review these proposed changes; no install or verification has run")
