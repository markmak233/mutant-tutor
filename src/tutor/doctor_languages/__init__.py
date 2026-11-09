"""Doctor's per-language parts, matching tutor.languages and graph/prompt_parts.

A third language adds one module defining its LanguageDoctor, its entry in DOCTORS,
a shipped example, and tests for its machine checks and platform problem. Nothing in
doctor.py changes. Frameworks use envcheck requirements, not this language registry.
"""

from collections.abc import Callable
from dataclasses import dataclass

from tutor.doctor_rows import Row


def _no_checks(run: Callable) -> list[Row]:
    """Most languages have no additional machine-wide constraints."""
    return []


def _no_problem(platform: str, runner: str | None) -> str | None:
    """Host practice is allowed unless the language explicitly says otherwise."""
    return None


@dataclass(frozen=True)
class LanguageDoctor:
    """Machine conditions, host practice limits, and validation policy are independent."""

    key: str
    smoke_example: str
    machine_checks: Callable[[Callable], list[Row]] = _no_checks
    platform_problem: Callable[[str, str | None], str | None] = _no_problem
    validation_needs_container: bool = False


# Load parts only after their dataclass exists, avoiding a registry/part import cycle.
from tutor.doctor_languages.c import DOCTOR as C  # noqa: E402
from tutor.doctor_languages.python import DOCTOR as PYTHON  # noqa: E402

DOCTORS = {part.key: part for part in (PYTHON, C)}
