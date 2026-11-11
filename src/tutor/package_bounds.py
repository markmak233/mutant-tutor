"""Restricted numeric pip bounds shared by workspace checks, recipes and doctor.

This is deliberately not a complete PEP 440 parser/resolver. Prefix semantics remain
unchanged; only the separate package-bounds table accepts these comparisons.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

NOT_PIP = ("python", "java", "hadoop", "gcc")
VERSION = re.compile(r"^\d+(\.\d+)*$")
_CLAUSE = re.compile(r"(==|!=|>=|<=|~=|<|>)[ \t]*([0-9]+(?:\.[0-9]+)*)")
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def identity(name: str) -> str:
    """PyPA case/separator identity, after validating plain non-option names."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("expected a plain package name")
    return re.sub(r"[-_.]+", "-", name).lower()


def numeric_specifier(spec: str) -> str:
    """Normalize the existing doctor's comparison subset, never URLs/options/markers."""
    if not isinstance(spec, str) or len(spec) > 512 or "\n" in spec or "\r" in spec:
        raise ValueError("expected a bounded single-line numeric version constraint")
    clauses = []
    for raw in spec.split(","):
        match = _CLAUSE.fullmatch(raw.strip(" \t"))
        if match is None:
            raise ValueError("expected a plain numeric version constraint")
        operator, version = match.groups()
        if len(version) > 128:
            raise ValueError("version constraint is too long")
        if operator == "~=" and "." not in version:
            raise ValueError("compatible-release constraints need at least two components")
        clauses.append(operator + version)
    return ",".join(clauses)


def _release(value: str) -> tuple[int, ...]:
    """Numeric comparison pads trailing zeros; unsupported installed versions fail closed."""
    if len(value) > 128 or not value.isascii() or not VERSION.fullmatch(value):
        raise ValueError("installed release is not plain numeric")
    result = [int(p) for p in value.split(".")]
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return tuple(result)


def _interval(spec: str):
    """Intersection of numeric comparisons, with exact excluded points kept separately."""
    low, high, excluded = ((0,), True), None, set()
    for clause in numeric_specifier(spec).split(","):
        operator, version = _CLAUSE.fullmatch(clause).groups()
        value = _release(version)
        lower = ((value, operator in {">=", "==", "~="})
                 if operator in {">", ">=", "==", "~="} else None)
        upper = (value, operator in {"<=", "=="}) if operator in {"<", "<=", "=="} else None
        if operator == "~=":
            parts = [int(p) for p in version.split(".")[:-1]]
            parts[-1] += 1
            upper = (_release(".".join(map(str, parts))), False)
        if lower and (lower[0] > low[0] or lower[0] == low[0] and not lower[1]):
            low = lower
        if upper and (high is None or upper[0] < high[0]
                      or upper[0] == high[0] and not upper[1]):
            high = upper
        if operator == "!=":
            excluded.add(value)
    return low, high, excluded


def matches(found: str, spec: str) -> bool:
    """An unparseable installed release never produces a healthy bound-check result."""
    try:
        value = _release(found)
        low, high, excluded = _interval(spec)
        return (value not in excluded and (value > low[0] or value == low[0] and low[1])
                and (high is None or value < high[0] or value == high[0] and high[1]))
    except ValueError:
        return False


def intersects_prefix(spec: str, prefix: str) -> bool:
    """Conservative overlap with a known-bad release family, including open boundaries."""
    parts = [int(p) for p in prefix.split(".")]
    parts[-1] += 1
    low, high, excluded = _interval(spec + ",>=" + prefix + ",<" + ".".join(map(str, parts)))
    return high is None or low[0] < high[0] or (
        low[0] == high[0] and low[1] and high[1] and low[0] not in excluded)


def merge(requires: Mapping[str, str], bounds: object) -> dict[str, str]:
    """No implicit overrides: a package must occur in only one declaration table."""
    if not isinstance(bounds, Mapping):
        raise ValueError("[package-bounds] must be a table")
    if not bounds:
        return dict(requires)  # preserve legacy declaration diagnostics when no new table exists
    # Lazy: languages' C helper imports config, which itself uses this pure module.
    from tutor import languages

    result, seen = dict(requires), {identity(n) for n in requires}
    for raw, spec in bounds.items():
        name = identity(raw)
        if name in NOT_PIP or languages.toolchain_problem(name):
            raise ValueError("[package-bounds] accepts pip packages, not runtime/toolchain names")
        if name in seen:
            raise ValueError("package identities must appear once across requires/package-bounds")
        result[name] = numeric_specifier(spec)
        seen.add(name)
    return result
