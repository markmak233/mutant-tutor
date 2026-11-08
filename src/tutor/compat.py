"""Known version conflicts that no tool reports before a run fails (docs/design.md, "Version
conflicts").

What a tool can be asked is asked (a compiler about its flags, a JDK about its release, pip
about what a package declares); a rule here is only for what installs and imports cleanly and
then fails while a job runs, such as mrjob on a Python that removed a module it imports. Each
rule was measured, and CI's Python versions test the ones that apply to them.

Two kinds:
- a companion: a pip package that must come with a [requires] package from some Python on;
  the venv install and the image add it, and the check reports it missing;
- a conflict: a [requires] package version that does not work from some Python on; the
  [requires] check refuses it, and the check reports it.

A rule is added by hand, with its measurement, never by a model: a name a model suggests may
be a package nobody checked (docs/design.md, "Version conflicts").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Companion:
    """`needs` comes with `package` on Python `python` and later."""

    package: str  # a [requires] name
    python: str  # the first Python minor version that needs it, e.g. "3.12"
    needs: str  # a pip package name
    why: str


@dataclass(frozen=True)
class Conflict:
    """`package` at a [requires] version starting with `version` fails on Python `python`
    and later."""

    package: str
    version: str  # a [requires] prefix, e.g. "3.5"
    python: str
    why: str  # what fails, and what to choose instead


COMPANIONS = (
    # mrjob 0.7.4 is its last release and is not maintained; its tasks import these.
    Companion("mrjob", "3.12", "setuptools",
              "mrjob imports `distutils`, which Python 3.12 removed; setuptools' shim provides "
              "it (measured 2026-10-26)"),
    Companion("mrjob", "3.13", "standard-pipes",
              "mrjob imports `pipes`, which Python 3.13 removed; standard-pipes is the removed "
              "module as it was (measured 2026-11-08)"),
)

CONFLICTS = (
    # Measured 2026-11-08, PySpark 3.5.9 on Python 3.14.10: every RDD job failed with
    # "PicklingError: Could not serialize object: RecursionError".
    Conflict("pyspark", "3.5", "3.14",
             "PySpark 3.5 cannot send functions to its workers on Python 3.14 (PicklingError); "
             "use Python 3.13 or older for this course"),
)


def _minor(version: str) -> tuple[int, ...]:
    """"3.13.2" -> (3, 13): rules name minor versions."""
    return tuple(int(part) for part in version.split(".")[:2])


def companions(requires: Mapping[str, str], python: str | None = None) -> list[str]:
    """The companion packages of `requires`, as pip specs. With `python` (the version the
    packages go into, as an image's is known): only those it needs. Without: each with an
    environment marker, so pip decides by the Python it installs into, as for a .venv that
    may already exist with another Python than the one running this."""
    found = []
    for rule in COMPANIONS:
        if rule.package not in requires:
            continue
        if python is None:
            found.append(f"{rule.needs}; python_version >= '{rule.python}'")
        elif _minor(python) >= _minor(rule.python) and rule.needs not in found:
            found.append(rule.needs)
    return found


def needed(requires: Mapping[str, str], python: str) -> list[Companion]:
    """The companion rules that apply to `requires` on `python`, for the check to look for."""
    return [rule for rule in COMPANIONS
            if rule.package in requires and _minor(python) >= _minor(rule.python)]


def conflicts(requires: Mapping[str, str], python: str) -> list[str]:
    """What in `requires` does not work on `python`, one line each; [] when nothing."""
    return [f"[requires] {rule.package} = {requires[rule.package]!r} on Python {python}: "
            f"{rule.why}"
            for rule in CONFLICTS
            if rule.package in requires
            and requires[rule.package].split(".")[:len(rule.version.split("."))]
            == rule.version.split(".")
            and _minor(python) >= _minor(rule.python)]
