"""Immutable advice records, caller-supplied facts and a strict JSON wire contract.

Standard library only beyond existing environment rules. Parsing never extracts JSON
from prose, accepts duplicate fields, or echoes untrusted values in error messages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from tutor.envcheck import VERSION
from tutor.package_bounds import NOT_PIP, numeric_specifier

# PyPA names: ASCII alphanumerics at both ends; runs of separators share an identity.
# https://packaging.python.org/en/latest/specifications/name-normalization/
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SEPARATORS = re.compile(r"[-_.]+")
MAX_REPLY_CHARS = 65_536


class AdviceError(ValueError):
    """Malformed advice or input facts; the message contains no supplied values."""


def text(value: object) -> str:
    """Strict, nonblank text: no coercion of numbers, booleans, lists or null."""
    if not isinstance(value, str) or not value.strip():
        raise AdviceError("expected nonblank text")
    return value


def package_name(value: object) -> str:
    """Validate before normalizing so paths, options and invisible suffixes cannot alias."""
    name = text(value)
    if not _NAME.fullmatch(name):
        raise AdviceError("expected a plain distribution name")
    return _SEPARATORS.sub("-", name).lower()


def release(value: object, *, interpreter: bool = False) -> str:
    """Reuse the existing dotted-prefix grammar; Python facts need major and minor.

    The length bound keeps integer normalization bounded even with caller-supplied facts.
    This parses numeric releases only, not the full packaging version language.
    """
    version = text(value)
    if len(version) > 128 or not version.isascii() or not VERSION.fullmatch(version):
        raise AdviceError("expected a numeric version prefix")
    parts = version.split(".")
    if interpreter and len(parts) < 2:
        raise AdviceError("Python versions need at least major and minor")
    return ".".join(str(int(part)) for part in parts)


@dataclass(frozen=True)
class Change:
    """One proposed operation; shape validation is independent of name permissions."""

    kind: Literal["python", "version", "add"]
    python: str | None = None
    package: str | None = None
    spec: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "python":
            text(self.python)
            if self.package is not None or self.spec is not None:
                raise AdviceError("a Python change cannot have package or spec fields")
        elif self.kind in ("version", "add"):
            text(self.package)
            if self.python is not None:
                raise AdviceError("a package change cannot have a Python field")
            if self.kind == "version" or self.spec is not None:
                text(self.spec)
        else:
            raise AdviceError("unknown change kind")


@dataclass(frozen=True)
class Advice:
    """Explanations are material; a separate field holds an unknown package for review."""

    cause: str
    version_changes: tuple[str, ...]
    changes: tuple[Change, ...]
    needs_new_package: str | None = None

    def __post_init__(self) -> None:
        text(self.cause)
        for name in ("version_changes", "changes"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)):
                raise AdviceError("explanations and changes must be arrays")
            object.__setattr__(self, name, tuple(value))
        for explanation in self.version_changes:
            text(explanation)
        if len(self.changes) > 3 or any(not isinstance(c, Change) for c in self.changes):
            raise AdviceError("expected zero to three structured changes")
        if self.needs_new_package is not None:
            text(self.needs_new_package)


@dataclass(frozen=True)
class AdviceContext:
    """A snapshot, not a probe: installed means pip distribution names, not import names.

    Available Python versions are caller-offered choices, at a consistent precision.
    Copies prevent mutations of a caller's dict/list after review from changing permission.
    Invalid base declarations/conflicts may still need repair, so no install is attempted.
    """

    requires: Mapping[str, str]
    installed: frozenset[str]
    current_python: str
    available_pythons: frozenset[str]
    python_locked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.requires, Mapping) or type(self.python_locked) is not bool:
            raise AdviceError("expected requirement mapping and boolean Python lock")
        declared = {}
        for name, version in self.requires.items():
            name = package_name(name)
            if name in declared:
                raise AdviceError("duplicate requirement identities")
            try:
                declared[name] = (release(version) if VERSION.fullmatch(version) else
                                  numeric_specifier(version))
                if name in NOT_PIP:
                    declared[name] = release(version)
            except (ValueError, TypeError):
                raise AdviceError("expected a numeric prefix or package bound") from None
        object.__setattr__(self, "requires", MappingProxyType(declared))
        for name, normalize in (("installed", package_name), ("available_pythons", release)):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple, set, frozenset)):
                raise AdviceError("expected a collection of supplied names or versions")
            values = (normalize(v, interpreter=True) if name == "available_pythons"
                      else normalize(v) for v in values)
            object.__setattr__(self, name, frozenset(values))
        object.__setattr__(self, "current_python", release(self.current_python, interpreter=True))


@dataclass(frozen=True)
class GateResult:
    """Only accepted replies release permitted changes; no result can claim verification."""

    status: Literal["accepted", "rejected", "stopped", "new-package-review"]
    reason: str
    changes: tuple[Change, ...] = ()
    needs_new_package: str | None = None
    cause: str = ""
    version_changes: tuple[str, ...] = ()
    verified: bool = field(default=False, init=False)


def reply_schema() -> dict:
    """Return fresh JSON Schema data for future callers; mutating it cannot change parsing."""
    nullable = {"type": ["string", "null"]}
    change = {"type": "object", "additionalProperties": False,
              "required": ["kind", "python", "package", "spec"],
              "properties": {"kind": {"type": "string", "enum": ["python", "version", "add"]},
                             **{name: dict(nullable) for name in ("python", "package", "spec")}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["cause", "version_changes", "changes", "needs_new_package"],
            "properties": {"cause": {"type": "string", "minLength": 1},
                           "version_changes": {"type": "array", "items": {"type": "string"}},
                           "changes": {"type": "array", "items": change, "maxItems": 3},
                           "needs_new_package": dict(nullable)}}


def _object(pairs: list[tuple[str, object]]) -> dict:
    """Reject repeated JSON properties rather than silently taking the last value."""
    obj = {}
    for name, value in pairs:
        if name in obj:
            raise AdviceError("duplicate JSON fields are not allowed")
        obj[name] = value
    return obj


def _constant(value: str):
    """NaN/Infinity are not JSON, despite the standard decoder's permissive default."""
    raise AdviceError("nonfinite JSON values are not allowed")


def parse_reply(raw: str) -> Advice:
    """Parse one complete object; typed records then enforce field relationships."""
    if not isinstance(raw, str) or len(raw) > MAX_REPLY_CHARS:
        raise AdviceError("expected a bounded JSON reply")
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise AdviceError("expected one strict JSON object without duplicate fields") from exc
    schema = reply_schema()
    if not isinstance(data, dict) or set(data) != set(schema["required"]):
        raise AdviceError("reply fields do not match the advice contract")
    if not isinstance(data["changes"], list):
        raise AdviceError("changes must be an array")
    # All fields are required even when null, matching the providers' strict schema shape.
    fields = set(schema["properties"]["changes"]["items"]["required"])
    changes = []
    for item in data["changes"]:
        if not isinstance(item, dict) or set(item) != fields:
            raise AdviceError("change fields do not match the advice contract")
        changes.append(Change(**item))
    return Advice(data["cause"], data["version_changes"], changes, data["needs_new_package"])
