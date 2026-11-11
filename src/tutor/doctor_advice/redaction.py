"""Best-effort secret/path removal for explicit advice preview, transmission and output.

This cannot prove arbitrary logs contain no private material: the owner selects and
reviews the record. Known credentials are never interpolated into an error or repr.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

# Names are inspected, not printed; include common credential spellings plus the actual
# selected profile value even when its environment variable has an unusual name.
_SECRET = re.compile(r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|APIKEY)(?:_|$)"
                     r"|^(?:AUTHORIZATION|DOCKER_AUTH_CONFIG)$", re.IGNORECASE)


def secret_name(name: str) -> bool:
    """Whether a variable is named like a credential (the rule the redactor uses)."""
    return bool(_SECRET.search(name))


_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9+/._~=-]+", re.IGNORECASE)
_ASSIGN = re.compile(
    r'''(?i)(["']?\b(?:[A-Z_][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)'''
    r'''[A-Z0-9_]*|authorization|password|passwd|key|token|secret)["']?\s*[:=]\s*)'''
    r'''(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)''')
# The one assignment whose value is a variable's NAME, not a credential: a model profile's
# `api_key_env = "DEEPSEEK_API_KEY"`. Its shape is upper-case words joined by underscores,
# which no provider's key has; anything else on that line is hidden like any other.
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
# Quoted paths keep spaces; unquoted forms stop at prose delimiters. Do not treat tag
# closing slashes or URL slashes as paths; graph.material must still mark injected tags.
_QUOTED_PATH = re.compile(r'''(["'])(?:/(?!/)|[A-Za-z]:[\\/]|\\\\)[^"'\r\n]+\1''')
_PATH = re.compile(r'''(?<![\w:/<])/(?!/)[^\s"'<>:,;]+|'''
                   r'''\b[A-Za-z]:[\\/][^\s"'<>;,]+|\\\\[^\s"'<>;,]+''')


def _assignment(match: re.Match) -> str:
    """Hide an assignment's value, except a profile's `api_key_env` naming a variable.

    Without the exception the changes shown before a repair read
    `api_key_env = [credential redacted]`, hiding nothing secret. The name must also look
    like a credential's variable (secret_name), so a pasted key never passes as a name.
    """
    head = match.group(1)
    name = head.rstrip().rstrip(":=").strip().strip("\"'")
    value = match.group(0)[len(head):].strip("\"'")
    if name == "api_key_env" and _ENV_NAME.fullmatch(value) and secret_name(value):
        return match.group(0)
    return head + "[credential redacted]"


def _variants(value: str) -> set[str]:
    """Logs may contain JSON-escaped or URL-encoded credentials/paths."""
    return {value, json.dumps(value)[1:-1], quote(value, safe=""), quote_plus(value, safe="")}


@dataclass(frozen=True)
class Redactor:
    """A private immutable snapshot; repr deliberately excludes every secret/path."""

    secrets: tuple[str, ...] = field(default=(), repr=False)
    paths: tuple[str, ...] = field(default=(), repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str], api_value: str | None = None,
                         paths: tuple[str, ...] = ()) -> Redactor:
        values = {v for k, v in env.items() if v and _SECRET.search(k)}
        if api_value:
            values.add(api_value)
        return cls(tuple(sorted(values, key=len, reverse=True)), paths)

    def clean(self, value: str) -> str:
        # Exact known values precede heuristics, so headers/URLs do not hide part of one.
        for secret in self.secrets:
            for variant in sorted(_variants(secret), key=len, reverse=True):
                value = value.replace(variant, "[credential redacted]")
        value = _CONTROL.sub("", _ANSI.sub("", value))
        # Control sequences may have split a known value, or occurred in the value itself.
        for secret in self.secrets:
            clean = _CONTROL.sub("", _ANSI.sub("", secret))
            if clean:
                for variant in sorted(_variants(clean), key=len, reverse=True):
                    value = value.replace(variant, "[credential redacted]")
        value = _BEARER.sub(r"\1 [credential redacted]", value)
        value = _ASSIGN.sub(_assignment, value)
        value = _URL.sub(lambda match: self.url(match.group()), value)
        for path in sorted(self.paths, key=len, reverse=True):
            if path:
                for variant in sorted(_variants(path), key=len, reverse=True):
                    value = value.replace(variant, "[path redacted]")
        value = _QUOTED_PATH.sub('"[path redacted]"', value)
        return _PATH.sub("[path redacted]", value)

    def contains_secret(self, value: str) -> bool:
        """A final payload check also covers encoded credentials in configuration fields."""
        return any(part in value for secret in self.secrets for part in _variants(secret))

    @staticmethod
    def url(value: str, *, origin: bool = False) -> str:
        """No URL userinfo, query or fragment is necessary for a diagnosis/destination."""
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if not host or parsed.scheme not in ("http", "https"):
                return "[URL redacted]"
            host = f"[{host}]" if ":" in host else host
            if parsed.port is not None:
                host += f":{parsed.port}"
            return urlunsplit((parsed.scheme, host, "" if origin else parsed.path, "", ""))
        except ValueError:
            return "[URL redacted]"
