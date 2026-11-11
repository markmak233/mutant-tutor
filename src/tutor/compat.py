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

And one probe, for packages no rule names yet: removed_imports reads a package's source for
imports of standard-library modules the running Python removed (REMOVED_MODULES), so the
check names every one at once instead of the first that fails.

A rule is added by hand, with its measurement, never by a model: a name a model suggests may
be a package nobody checked (docs/design.md, "Version conflicts").
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from tutor import package_bounds


@dataclass(frozen=True)
class Companion:
    """`needs` comes with `package` on Python `python` and later."""

    package: str  # a [requires] name
    python: str  # the first Python minor version that needs it, e.g. "3.12"
    needs: str  # a pip package name
    provides: str  # the removed module it brings back, if any ("" otherwise)
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
    Companion("mrjob", "3.12", "setuptools", "distutils",
              "mrjob imports `distutils`, which Python 3.12 removed; setuptools' shim provides "
              "it (measured 2026-10-26)"),
    Companion("mrjob", "3.13", "standard-pipes", "pipes",
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
            and _family(requires[rule.package], rule.version)
            and _minor(python) >= _minor(rule.python)]


def _family(required: str, prefix: str) -> bool:
    """Numeric bounds cannot bypass a known bad family that they still admit."""
    if required.startswith(("=", "!", "<", ">", "~")):
        try:
            return package_bounds.intersects_prefix(required, prefix)
        except ValueError:
            return False  # malformed declarations are reported by requires_problems
    return required.split(".")[:len(prefix.split("."))] == prefix.split(".")


# Standard-library modules each Python removed, by the version that removed them (What's New
# in Python 3.12: PEP 632 and the long-deprecated ones; 3.13: PEP 594 and lib2to3). 3.14
# removed no whole module. Facts for the scan, never package suggestions.
REMOVED_MODULES = {
    **dict.fromkeys(("distutils", "imp", "asynchat", "asyncore", "smtpd"), "3.12"),
    **dict.fromkeys(("aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap",
                     "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd",
                     "sunau", "telnetlib", "uu", "xdrlib", "lib2to3"), "3.13"),
}
# A line that imports a module by its top-level name: `import pipes`, `import distutils.x`,
# `from distutils.version import ...`, indented too (inside a try or a function). Not
# `import foo.pipes` nor `from . import pipes`: those are a package's own modules.
_IMPORT = re.compile(r"^[ \t]*(?:import|from)[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?=[.\s,;]|$)",
                     re.MULTILINE)


def removed_imports(sources: Iterable[Path], python: str) -> dict[str, str]:
    """The standard-library modules that `python` (or an earlier version) removed and these
    source files import: module -> the version that removed it. A file that cannot be read
    is skipped: this names what it can, and the run itself remains the proof."""
    gone = {module: version for module, version in REMOVED_MODULES.items()
            if _minor(python) >= _minor(version)}
    found = {}
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.update({m: gone[m] for m in _IMPORT.findall(text) if m in gone})
    return dict(sorted(found.items()))
