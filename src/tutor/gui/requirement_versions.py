"""Persist the GUI's one version cell in the existing two declaration tables.

Prefixes stay in [requires]; restricted numeric comparisons stay in [package-bounds].
All callers pass the complete list, including language toolchains, so a save can move or
remove entries without retaining a second, hidden declaration. No installs happen here.
"""

from __future__ import annotations

import tomlkit

from tutor import package_bounds
from tutor.envcheck import requires_problems
from tutor.gui import toml_edit
from tutor.gui.toml_edit import put


def partition(table: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Normalize comparisons with the shared parser; toolchains remain prefix-only."""
    prefixes, bounds, seen = {}, {}, set()
    for name, version in table.items():
        identity = package_bounds.identity(name)
        if identity in seen:
            raise ValueError(f"{name} is listed twice.")
        seen.add(identity)
        if package_bounds.VERSION.fullmatch(version):
            prefixes[name] = version
        else:
            if name in package_bounds.NOT_PIP:
                raise ValueError(f"{name}: use a version prefix only; comparisons are for "
                                 "pip packages.")
            bounds[identity] = package_bounds.numeric_specifier(version)
    # Retain configuration's duplicate-identity and unsupported-toolchain checks.
    package_bounds.merge(prefixes, bounds)
    return prefixes, bounds


def _update(doc, name: str, values: dict) -> None:
    """Edit entries in place: unrelated comments/table headers retain their spelling."""
    if name not in doc and values:
        doc[name] = tomlkit.table()
    if name not in doc:
        return
    section = doc[name]
    for key in list(section.keys()):
        if key not in values:
            del section[key]
    for key, value in values.items():
        if section.get(key) != value:
            put(section, key, value)


def save(course, table: dict[str, str], allow: list[str] | None = None) -> list[str]:
    """One checked write/backup for both version tables and optional import consent."""
    if problems := requires_problems(table, allow_bounds=True):
        return problems
    try:
        prefixes, bounds = partition(table)
    except ValueError as exc:
        return [str(exc)]
    expected = {"requires": prefixes, "package-bounds": bounds}
    safety = toml_edit.read(course.workspace.root).get("safety", {})
    if allow is not None and allow != safety.get("allow_imports", []):
        expected["safety"] = {**{k: v for k, v in safety.items() if k != "allow_imports"},
                              **({"allow_imports": allow} if allow else {})}

    def change(doc):
        # Updating both tables in one transaction makes a prefix/bound move indivisible.
        _update(doc, "requires", prefixes)
        _update(doc, "package-bounds", bounds)
        if "safety" in expected:
            if "safety" not in doc:
                new = tomlkit.table()
                new.comment("modules exercises may import besides the core ones")
                doc["safety"] = new
            if allow:
                put(doc["safety"], "allow_imports", allow)
            elif "allow_imports" in doc["safety"]:
                del doc["safety"]["allow_imports"]

    return toml_edit.edit_tables(course.workspace.root, change, expected)
