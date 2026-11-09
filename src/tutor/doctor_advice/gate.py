"""Pure permission gate for advice: accepted means permitted, never repaired or verified.

Names come only from caller-supplied facts and existing course-specific compat rules.
The numeric specifier subset is intentionally smaller than PEP 440; this gate does not
resolve packages, generate commands or decide whether a proposed environment will run.
"""

from __future__ import annotations

import re
from dataclasses import replace

from tutor import compat, languages
from tutor.doctor_advice.models import (
    AdviceContext,
    AdviceError,
    Change,
    GateResult,
    package_name,
    parse_reply,
    release,
)
from tutor.envcheck import NOT_PIP, version_matches

# Allow plain numeric comparisons joined by commas, with horizontal whitespace only.
# No wildcard, pre/post/dev/local release, epoch, URL, marker or pip option syntax.
# https://packaging.python.org/en/latest/specifications/version-specifiers/
_CLAUSE = re.compile(r"(==|!=|>=|<=|~=|<|>)[ \t]*([0-9]+(?:\.[0-9]+)*)")


def numeric_specifier(spec: str) -> str:
    """Normalize only syntax we support; do not present this as a dependency solver."""
    if "\n" in spec or "\r" in spec:
        raise AdviceError("multiline version specifiers are not allowed")
    clauses = []
    for raw in spec.split(","):
        match = _CLAUSE.fullmatch(raw.strip(" \t"))
        if match is None:
            raise AdviceError("expected a plain numeric version constraint")
        operator, version = match.groups()
        if operator == "~=" and "." not in version:
            raise AdviceError("compatible-release constraints need at least two components")
        clauses.append(operator + version)
    return ",".join(clauses)


def _pip_package(name: str) -> bool:
    """Runtime/toolchain names cannot be smuggled into package repairs via installed names."""
    return name not in NOT_PIP and languages.toolchain_problem(name) is None


def _python(change: Change, context: AdviceContext) -> str:
    """An offered choice must be another usable Python, not a no-op spelling of this one."""
    proposed = release(change.python, interpreter=True)
    if context.python_locked:
        raise AdviceError("the course Python is locked")
    if proposed not in context.available_pythons:
        raise AdviceError("Python is not among the offered interpreter versions")
    if tuple(int(p) for p in proposed.split(".")[:2]) < (3, 11):
        raise AdviceError("the proposed Python cannot run this tool")
    if (version_matches(proposed, context.current_python)
            or version_matches(context.current_python, proposed)):
        raise AdviceError("the Python change is the current interpreter or an ambiguous no-op")
    # Stay conservative: broad new package bounds have not been resolved or verified.
    # A Python move already ruled out by the current declarations is not offered as safe.
    if compat.conflicts(context.requires, proposed):
        raise AdviceError("the proposed Python has a known conflict with the course requirements")
    return proposed


def review_reply(raw: str, context: AdviceContext) -> GateResult:
    """Return no partial changes when any field or permission check fails.

    A caller supplies an immutable context; environment inspection and execution are
    deliberately absent. New-package review exposes only the separate name and prose.
    """
    try:
        advice = parse_reply(raw)
        python_changes = [c for c in advice.changes if c.kind == "python"]
        if len(python_changes) > 1:
            raise AdviceError("at most one Python change is allowed")
        # Compute the target before checking any package, so array order grants no rights.
        target = _python(python_changes[0], context) if python_changes else context.current_python
        allowed = {name for name in (*context.requires, *context.installed) if _pip_package(name)}
        allowed.update(package_name(rule.needs) for rule in compat.needed(context.requires, target))
        permitted = []
        seen = set()
        for change in advice.changes:
            if change.kind == "python":
                permitted.append(replace(change, python=target))
                continue
            name = package_name(change.package)
            if not _pip_package(name) or name not in allowed:
                raise AdviceError("package is outside the permitted course/installed/companion set")
            if name in seen:
                raise AdviceError("duplicate or conflicting changes name the same package")
            seen.add(name)
            spec = numeric_specifier(change.spec) if change.spec is not None else None
            permitted.append(replace(change, package=name, spec=spec))
        if advice.needs_new_package is not None:
            name = package_name(advice.needs_new_package)
            if not _pip_package(name) or name in allowed:
                raise AdviceError("new-package review must name an unknown distribution")
            return GateResult("new-package-review", "a new package needs independent review; "
                              "no changes are released", needs_new_package=name,
                              cause=advice.cause, version_changes=advice.version_changes)
        if not permitted:
            return GateResult("stopped", "no changes proposed; nothing has been fixed or verified",
                              cause=advice.cause, version_changes=advice.version_changes)
        return GateResult("accepted", "permitted changes only; not installed or verified",
                          tuple(permitted), cause=advice.cause,
                          version_changes=advice.version_changes)
    except AdviceError as exc:
        # Errors are static messages, not copies of untrusted commands, credentials or prose.
        return GateResult("rejected", str(exc))
