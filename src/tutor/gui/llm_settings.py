"""A course's models (GUI step 6): the profiles and roles of its workspace.toml's [llm].

The GUI writes workspace.toml here, and only its [llm] section. tomlkit keeps the rest of the
file as the owner wrote it, comments included (owner, 2026-11-06); a value changed loses only
its own line's comment. Every save is checked before the file is replaced: the result read
back with tomllib must have exactly the [llm] intended, worked out from the old data and not
from tomlkit, and every other table unchanged. The file before is kept as workspace.toml.bak.

Profiles are checked by llm.py's own rules (no key in the file, known fields, a model, a
base URL where the provider has none), roles by the rule generation enforces: the oracle
comes from another provider than the models that write and repair the exercise. Keys stay in
`.env` (keys.py); a profile names its variable.
"""

from __future__ import annotations

import copy
import os
import re
import secrets
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field

import tomlkit

from tutor import llm
from tutor.config import WORKSPACE_FILE
from tutor.gui import keys
from tutor.gui.courses import Course

# The roles a page offers, grouped by what they are for, with what each does.
ROLE_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Reading lectures", [("vision", "reads the slides that are pictures")]),
    ("Generating exercises", [
        ("plan", "plans a batch (empty: the generate model)"),
        ("generate", "writes each exercise"),
        ("oracle", "writes an independent check from the question alone; another provider "
                   "than generate and repair"),
        ("repair", "takes over the repairs from the second on (empty: the generate model)")]),
    ("While you learn", [("hint", "gives hints on your attempts"),
                         ("judge", "scores hints and questions in evaluations")]),
    ("Experiments", [("heldout", "writes held-out test data"),
                     ("concept", "builds the concept index for retrieval"),
                     ("embedding", "turns pages into vectors for retrieval"),
                     ("agent", "searches the lectures in the agentic retrieval route")]),
]
ROLES = [role for _, group in ROLE_GROUPS for role, _ in group]
PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
# A profile's fields in the order a new one is written (llm.PROFILE_FIELDS, ordered).
FIELDS = ("provider", "model", "api_key_env", "base_url", "reasoning_effort",
          "reasoning_effort_on", "timeout_s")
_WORD = re.compile(r"^[a-z]+$")


@dataclass(frozen=True)
class ProfileView:
    name: str
    data: dict  # its fields as in the file
    roles: list[str] = field(default_factory=list)  # the roles that use it


def section(course: Course) -> dict:
    """The course's [llm], {} without one (or when the file cannot be read)."""
    try:
        with (course.workspace.root / WORKSPACE_FILE).open("rb") as f:
            data = tomllib.load(f).get("llm", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def profiles(course: Course) -> list[ProfileView]:
    data = section(course)
    roles = data.get("roles", {})
    return [ProfileView(name, dict(values), sorted(r for r, p in roles.items() if p == name))
            for name, values in sorted(data.get("profiles", {}).items())
            if isinstance(values, dict)]


def key_users(all_courses: list[Course]) -> dict[str, list[str]]:
    """Key name -> "course: profile" for every profile that names one, across courses."""
    users: dict[str, list[str]] = {}
    for course in all_courses:
        for profile in profiles(course):
            name = profile.data.get("api_key_env")
            if isinstance(name, str):
                users.setdefault(name, []).append(f"{course.folder}: {profile.name}")
    return users


def check_profile(course: Course, name: str, form: dict[str, str]
                  ) -> tuple[dict | None, list[str]]:
    """A profile's fields from a form, or the problems."""
    data = {k: v for k in FIELDS if (v := form.get(k, "").strip())}
    problems = []
    if not PROFILE.match(name):
        problems.append("A model's name takes lowercase letters, digits, '.', '_' and '-', "
                        "e.g. deepseek-flash.")
    if data.get("provider") not in llm.PROVIDERS:
        problems.append(f"Choose a provider: {', '.join(llm.PROVIDERS)}.")
    if "api_key_env" in data and not keys.NAME.match(data["api_key_env"]):
        problems.append("The key's name is a variable in .env, e.g. DEEPSEEK_API_KEY: "
                        "capital letters, digits and '_'.")
    if "base_url" in data and not data["base_url"].startswith(("http://", "https://")):
        problems.append("The base URL starts with http:// or https://.")
    for effort in ("reasoning_effort", "reasoning_effort_on"):
        if effort in data and not _WORD.match(data[effort]):
            problems.append(f"{effort}: one word such as low or high.")
    if "timeout_s" in data:
        try:
            seconds = float(data["timeout_s"])
        except ValueError:
            seconds = 0
        if not 0 < seconds <= 3600:
            problems.append("The time limit is a number of seconds, 1 to 3600.")
        else:
            data["timeout_s"] = int(seconds) if seconds.is_integer() else seconds
    if not problems:
        try:  # llm.py's own rules; a stand-in value for the key, which stays in .env
            llm._from_profile(name, data, {data.get("api_key_env", ""): "x"},
                              course.workspace.root)
        except llm.LLMError as e:
            problems.append(str(e))
    return (None if problems else data), problems


def save_profile(course: Course, original: str | None, name: str, data: dict) -> list[str]:
    """Add a profile, or change `original` (renamed to `name`, its roles following it)."""
    old = section(course)
    names = set(old.get("profiles", {}))
    if original is not None and original not in names:
        return ["That model is no longer in workspace.toml; reload the page."]
    if name != original and name in names:
        return [f"A model named {name} exists already."]
    expected = copy.deepcopy(old)
    expected.setdefault("profiles", {}).pop(original or name, None)
    expected["profiles"][name] = data
    if original and original != name:
        for role, used in expected.get("roles", {}).items():
            if used == original:
                expected["roles"][role] = name

    def change(doc) -> None:
        table = _tables(doc)[0]
        if original is None:
            item = tomlkit.table()
            for k in FIELDS:
                if k in data:
                    item[k] = data[k]
            table[name] = _spaced(item)
            return
        if original != name:
            table[name] = table[original]
            del table[original]
            roles = _tables(doc)[1]
            for role in list(roles.keys()):
                if roles[role] == original:
                    _put(roles, role, name)
        item = table[name]
        for k in list(item.keys()):
            if k not in data:
                del item[k]
        for k in FIELDS:
            if k in data and item.get(k) != data[k]:
                _put(item, k, data[k])

    return _edit(course, change, expected)


def delete_profile(course: Course, name: str) -> list[str]:
    old = section(course)
    if name not in old.get("profiles", {}):
        return ["That model is no longer in workspace.toml."]
    used = sorted(r for r, p in old.get("roles", {}).items() if p == name)
    if used:
        return [f"{name} is used by {', '.join(used)}: choose another model for "
                f"{'them' if len(used) > 1 else 'it'} first."]
    expected = copy.deepcopy(old)
    del expected["profiles"][name]
    def change(doc) -> None:
        # `del`: [llm.profiles.*] split by other tables reads back as a proxy without remove().
        del _tables(doc)[0][name]

    return _edit(course, change, expected)


def save_roles(course: Course, chosen: dict[str, str]) -> list[str]:
    """Set each offered role to a profile, or to none (""). Roles not offered stay."""
    old = section(course)
    known = old.get("profiles", {})
    problems = [f"{role}: no model named {name}." for role, name in chosen.items()
                if name and name not in known]
    if problems:
        return problems
    roles = {**old.get("roles", {}), **{r: n for r, n in chosen.items() if n}}
    for role, name in chosen.items():
        if not name:
            roles.pop(role, None)
    oracle = roles.get("oracle")
    for writer in ("generate", "repair"):
        if oracle and roles.get(writer) and _source(course, oracle, known) == _source(
                course, roles[writer], known):
            problems.append(f"The oracle ({oracle}) and {writer} ({roles[writer]}) come from "
                            "one provider: an oracle from the same provider is not an "
                            "independent reading. Choose another oracle.")
    if problems:
        return problems
    expected = copy.deepcopy(old)
    expected["roles"] = roles

    def change(doc) -> None:
        table = _tables(doc)[1]
        for role, name in chosen.items():
            if name:
                _put(table, role, name)
            elif role in table:
                del table[role]

    return _edit(course, change, expected)


def copy_models(course: Course, source: Course) -> list[str]:
    """The other course's profiles and roles, into a course that has none yet."""
    if section(course).get("profiles"):
        return ["This course has models already; change them one by one."]
    data = section(source)
    if not data.get("profiles"):
        return [f"{source.folder} has no models to copy."]
    expected = copy.deepcopy(section(course))
    expected["profiles"] = copy.deepcopy(data["profiles"])
    expected["roles"] = copy.deepcopy(data.get("roles", {}))

    def change(doc) -> None:
        table, roles = _tables(doc)
        for name, values in data["profiles"].items():
            item = tomlkit.table()
            for k, v in values.items():
                item[k] = v
            table[name] = item  # tomlkit sets tables it writes itself apart
        for role, name in data.get("roles", {}).items():
            _put(roles, role, name)

    return _edit(course, change, expected)


def check_command(names: list[str]) -> list[str]:
    """`python -m tutor.llm check` on these profiles (names checked against the file)."""
    return [sys.executable, "-m", "tutor.llm", "check", *names]


def _source(course: Course, name: str, known: dict) -> tuple[str, str]:
    """(provider, base URL) of a profile, as generation tells two providers apart."""
    data = known[name]
    config = llm._from_profile(name, data, {data.get("api_key_env", ""): "x"},
                               course.workspace.root)
    return config.provider, config.base_url


def _tables(doc):
    """[llm.profiles] and [llm.roles] in the parsed file, made if missing."""
    if "llm" not in doc:
        doc["llm"] = tomlkit.table(is_super_table=True)
    section = doc["llm"]
    if "profiles" not in section:
        section["profiles"] = tomlkit.table(is_super_table=True)
    if "roles" not in section:
        section["roles"] = tomlkit.table()
    return section["profiles"], section["roles"]


def _spaced(table):
    """A new table ending in a blank line, so the header after it is not run into it."""
    table.add(tomlkit.nl())
    return table


def _put(table, key: str, value) -> None:
    """table[key] = value; a new key goes right after the table's last key, not after the
    comments and blank lines that end the table, which belong to what follows it. That
    uses tomlkit's Container._insert_after (pinned below 1.0, and a test shows it holds);
    without it, plain assignment, which is only less tidy."""
    if key in table:
        table[key] = value
        return
    last = [k for k, _ in table.value.body if k is not None]
    try:
        table.value._insert_after(last[-1], key, value)
    except (AttributeError, IndexError, KeyError):
        table[key] = value


def _bare(llm_section: dict) -> dict:
    """[llm] without empty tables: an empty [llm.roles] may be written out or not."""
    return {k: v for k, v in llm_section.items() if v != {}}


def _edit(course: Course, change: Callable, expected: dict) -> list[str]:
    """Apply `change` to the file with tomlkit, check the result, keep a backup, replace."""
    path = course.workspace.root / WORKSPACE_FILE
    text = path.read_text(encoding="utf-8")
    doc = tomlkit.parse(text)
    change(doc)
    new = tomlkit.dumps(doc)
    before, after = tomllib.loads(text), tomllib.loads(new)
    if _bare(after.get("llm", {})) != _bare(expected) or {
            k: v for k, v in after.items() if k != "llm"} != {
            k: v for k, v in before.items() if k != "llm"}:
        return ["workspace.toml could not be changed safely, so it was left as it was. "
                "Edit it by hand, or report this."]
    path.with_name(WORKSPACE_FILE + ".bak").write_text(text, encoding="utf-8")
    tmp = path.with_name(f".{WORKSPACE_FILE}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(new, encoding="utf-8", newline="")
    os.replace(tmp, path)
    return []

