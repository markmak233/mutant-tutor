"""The language part of a course's Environment page (owner, 2026-11-07; docs/design.md, "C").

One list, Python first (always: the tests are pytest), each row its [requires] toolchain's
version; below it each language's own settings (Python's packages and tools; C's flags and
headers). Adding C writes [languages.c] with its compiler flags (proposed from the gcc lines
the lectures show) and `gcc` in [requires]; removing it takes both away, with [safety]
allow_headers. A C course's tests run only in the container, so "host" is refused for it
(environment.save_runner); on this machine `gcc` may be another compiler (macOS's is clang),
which the learner's own runs can use, so that row is shown as differing, not as missing.

The rest of the page ([requires] as a table, where tests run, the installs, the requirements
draft) is environment.py, whose rules for [requires] this module uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tomlkit

from tutor import languages, workspace
from tutor.gui import toml_edit
from tutor.gui.courses import Course
from tutor.gui.environment import check_requires, runner, save_requires
from tutor.gui.toml_edit import put
from tutor.testkit_c import DEFAULT_FLAGS, check_flags

IMAGE_GCC = languages.C.default_version
HEADER = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*\.h$")  # e.g. unistd.h, sys/stat.h


@dataclass(frozen=True)
class LanguageView:
    """Step 1's language part. `flags`: what the box starts with (the course's flags, else
    the lectures', else the default); `problems`: tutor.languages.problems of the file."""
    c: bool
    flags: str
    headers: str
    suggestion: languages.FlagSuggestion | None
    problems: list[str]


def language_view(course: Course) -> LanguageView:
    data = _data(course)
    table = data.get("languages", {}).get("c", {}) if languages.is_c(data) else {}
    suggestion = languages.suggested_c_flags(languages.lecture_pages(course.workspace.root))
    flags = table.get("flags") if isinstance(table, dict) else None
    if not isinstance(flags, list):
        flags = suggestion.flags if suggestion else list(DEFAULT_FLAGS)
    headers = data.get("safety", {}).get("allow_headers", [])
    return LanguageView(languages.is_c(data), " ".join(map(str, flags)),
                        " ".join(map(str, headers)) if isinstance(headers, list) else "",
                        suggestion, languages.problems(data))


def save_language(course: Course, c: bool, flags_text: str, headers_text: str) -> list[str]:
    """Tick C: [languages.c] flags, [safety] allow_headers, and `gcc` in [requires] when it
    is not there, then VS Code's .vscode/c_cpp_properties.json with the flags' C standard.
    Untick: [languages.c], allow_headers, `gcc` (C's row in the language list) and that file
    go; the rest of [requires] stays."""
    data = _data(course)
    if not isinstance(data.get("languages", {}), dict):
        return ["[languages] in workspace.toml is not a table of languages: fix it by hand."]
    flags = flags_text.split()
    headers = headers_text.replace(",", " ").split()
    problems = []
    if c:
        try:
            check_flags(flags, "The compiler flags")
        except ValueError:
            problems.append('The compiler flags must be options separated by spaces, such as '
                            '"-Wall -std=c99".')
        problems += [f"{h} is not a header name such as unistd.h or sys/stat.h." for h in headers
                     if not HEADER.match(h)]
    if problems:
        return problems
    others = {k: v for k, v in data.get("languages", {}).items() if k != "c"}
    expected_languages = {**others, **({"c": {"flags": flags}} if c else {})}
    safety = {k: v for k, v in data.get("safety", {}).items() if k != "allow_headers"}
    expected_safety = {**safety, **({"allow_headers": headers} if c and headers else {})}
    # From the file as it is now, not as the page loaded it: accepting a gcc candidate writes
    # [requires] first and then adds C, which must keep the version just written.
    before = data.get("requires", {}) if isinstance(data.get("requires"), dict) else {}
    requires = dict(before)
    if c and "gcc" not in requires:
        requires["gcc"] = IMAGE_GCC
    elif not c:
        requires.pop("gcc", None)  # C's version in the language list goes with it
    expected = {"languages": expected_languages, "safety": expected_safety,
                "requires": requires}

    def change(doc) -> None:
        if c:
            if "languages" not in doc:
                doc["languages"] = tomlkit.table(is_super_table=True)
            table = doc["languages"].get("c")
            if table is None:
                table = tomlkit.table()
                doc["languages"]["c"] = table
            put(table, "flags", flags)
        elif "languages" in doc and "c" in doc["languages"]:
            del doc["languages"]["c"]
            if not doc["languages"]:
                del doc["languages"]
        if "allow_headers" in expected_safety:
            if "safety" not in doc:
                doc["safety"] = tomlkit.table()
            put(doc["safety"], "allow_headers", headers)
        elif "safety" in doc and "allow_headers" in doc["safety"]:
            del doc["safety"]["allow_headers"]
            if not doc["safety"]:
                del doc["safety"]
        if requires != before:
            if "requires" not in doc:
                doc["requires"] = tomlkit.table()
            if c:
                put(doc["requires"], "gcc", IMAGE_GCC)
            else:
                del doc["requires"]["gcc"]

    problems = toml_edit.edit_tables(course.workspace.root, change, expected)
    if not problems:
        # VS Code's C settings follow the flags (or go with C), unless the course has its own.
        workspace.write_c_editor_config(course.workspace.root, flags if c else None)
    return problems


@dataclass(frozen=True)
class LanguageRow:
    """A row of step 1's language list: the language, its toolchain's version in [requires]
    ("" when none), and whether it can be removed (not Python, which runs the tests)."""
    language: languages.Language
    version: str
    removable: bool


@dataclass(frozen=True)
class LanguagesView:
    """Step 1's language list. `addable`: supported languages not in the course yet;
    `not_yet`: the names this tool knows but cannot generate for; `hint`: why a language
    may be the course's (the lectures compile C), shown beside "Add a language"."""
    rows: list[LanguageRow]
    addable: list[languages.Language]
    not_yet: list[str]
    hint: languages.FlagSuggestion | None


def languages_view(course: Course) -> LanguagesView:
    data = _data(course)
    chosen = languages.course_languages(data)
    requires = course.workspace.requires
    rows = [LanguageRow(lang, requires.get(lang.toolchain, ""), lang is not languages.PYTHON)
            for lang in chosen]
    addable = [lang for lang in languages.LANGUAGES.values() if lang not in chosen]
    hint = (languages.suggested_c_flags(languages.lecture_pages(course.workspace.root))
            if languages.C in addable else None)
    return LanguagesView(rows, addable, languages.not_supported_yet(), hint)


def toolchains(course: Course) -> set[str]:
    """The [requires] names the language list shows (python, and gcc for a C course), which
    the packages-and-tools list leaves out and keeps as they are when it is saved."""
    return {lang.toolchain for lang in languages.course_languages(_data(course))}


def package_rows(course: Course) -> list[tuple[str, str]]:
    """[requires] without the language list's rows: Python's packages and tools."""
    shown = toolchains(course)
    return [(n, v) for n, v in course.workspace.requires.items() if n not in shown]


def with_toolchains(course: Course, names: list[str], versions: list[str]
                    ) -> tuple[list[str], list[str], list[str]]:
    """The packages list as saved, with the language list's rows put back first (the form
    does not carry them); a typed row naming one of them is refused, as its version is set
    in the language list."""
    shown = toolchains(course)
    typed = [n.strip().lower() for n in names]
    problems = [f"{name} is set in the language list above, not here."
                for name in sorted(shown) if name in typed]
    kept = [(n, v) for n, v in course.workspace.requires.items() if n in shown]
    return ([n for n, _ in kept] + names, [v for _, v in kept] + versions, problems)


def save_language_versions(course: Course, versions: dict[str, str]) -> list[str]:
    """The language list's versions ({toolchain: version as typed}) into [requires], by the
    same rules as the packages list."""
    table = dict(course.workspace.requires)
    for toolchain, version in versions.items():
        if toolchain in toolchains(course):
            table[toolchain] = version.strip()
    checked, problems = check_requires(list(table), list(table.values()), runner(course))
    return problems or save_requires(course, checked)


def add_language(course: Course, key: str) -> list[str]:
    """Add a supported language: for C, [languages.c] with the lectures' flags (else the
    default), `gcc` in [requires] and VS Code's C settings; its own settings are then set
    below the list."""
    if key != languages.C.key:
        return [f"{key!r} is not a language that can be added."]
    view = language_view(course)
    return save_language(course, True, view.flags, view.headers)


def remove_language(course: Course, key: str) -> list[str]:
    if key != languages.C.key:
        return [f"{key!r} is not a language that can be removed."]
    return save_language(course, False, "", "")


def _data(course: Course) -> dict:
    return toml_edit.read(course.workspace.root)
