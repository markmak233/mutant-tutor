"""A course's models (GUI step 6): the profiles of its workspace.toml's [llm]. The roles,
[llm.roles], are llm_roles.py's (split off 2026-11-12, when this file passed 500 lines),
which uses the reading and checked writing here.

The GUI writes workspace.toml here, and only its [llm] section, through toml_edit: tomlkit
keeps the rest of the file as the owner wrote it, and every save is checked against the
[llm] intended, worked out here from the old data, before the file is replaced.

Profiles are checked by llm.py's own rules (no key in the file, known fields, a model, a
base URL where the provider has none), roles by the rule generation enforces: the oracle
comes from another provider than the models that write and repair the exercise. Keys stay in
`.env` (keys.py); a profile names its variable.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit

from tutor import llm
from tutor.config import WORKSPACE_FILE
from tutor.gui import keys, toml_edit
from tutor.gui.catalog import same_url
from tutor.gui.courses import Course, find_courses
from tutor.gui.i18n import gettext_ as _
from tutor.gui.i18n import ngettext_
from tutor.gui.toml_edit import put as _put
from tutor.gui.toml_edit import spaced as _spaced

PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
# A profile's fields in the order a new one is written (llm.PROFILE_FIELDS, ordered).
FIELDS = ("provider", "model", "api_key_env", "base_url", "reasoning_effort",
          "reasoning_effort_on", "timeout_s", "temperature", "parallel_start", "max_parallel")
# The form's own box beside parallel_start: the N of "first-event+N", which the file keeps
# inside the one value. FORM_FIELDS is everything the model form sends.
START_SECONDS = "parallel_start_s"
FORM_FIELDS = ("name", *FIELDS, START_SECONDS)
# parallel_start's choices in the form; "first-event+" takes its seconds from the box.
STARTS = ("together", "first-event", "first-event+", "first-done")
START_SECONDS_MAX = 600  # a lecture's first call waits for no more than this (GATE_WAIT_S)
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


# A new model's time limit per call, filled into the New model form (owner, 2026-11-07):
# plan and generate calls with thinking can run past llm.py's default of 300 s (one plan call
# took 12 minutes), and every cloud profile had been given 900 by hand.
NEW_TIMEOUT_S = 900


def provider_defaults() -> dict[str, dict[str, str]]:
    """What each provider llm.py knows brings by itself, as the model form shows it: its
    base URL ("" for none), how it turns thinking off, e.g. 'thinking {"type":
    "disabled"}, else reasoning_effort "none"' ("" for no way of its own), and how a
    lecture's other calls start after the first (llm.PARALLEL_START, measured)."""
    return {name: {"base_url": url or "",
                   "thinking_off": ", else ".join(" ".join(f"{k} {json.dumps(v)}"
                                                          for k, v in fields.items())
                                                 for fields in off),
                   "parallel_start": llm.PARALLEL_START.get(name, llm.DEFAULT_PARALLEL_START)}
            for name, (url, off) in llm.PROVIDERS.items()}


def form_values(name: str, data: dict) -> dict[str, str]:
    """A profile from the file as the form shows it: every value a string, and a
    "first-event+N" parallel_start split into the choice and its seconds' box."""
    values = {"name": name, **{k: str(v) for k, v in data.items()}}
    start, plus = values.get("parallel_start", ""), "first-event+"
    if start.startswith(plus):
        values["parallel_start"], values[START_SECONDS] = plus, start[len(plus):]
    return values


def _check_start(data: dict, form: dict[str, str], problems: list[str]) -> None:
    """parallel_start and max_parallel from the form into the file's form, in `data`:
    the choice and its seconds joined ("first-event+10"); empty choice: the provider's
    default, so nothing is written; max_parallel a whole number."""
    choice = data.pop("parallel_start", "")
    if choice == "first-event+":
        try:
            seconds = float(form.get(START_SECONDS, "").strip())
        except ValueError:
            seconds = 0
        if not 0 < seconds <= START_SECONDS_MAX:
            problems.append(_("Seconds after the first reply begins: a number from 0.5 to "
                              "%(most)s.") % {"most": START_SECONDS_MAX})
        else:
            shown = int(seconds) if seconds.is_integer() else round(seconds, 1)
            data["parallel_start"] = f"first-event+{shown}"
    elif choice in STARTS:
        data["parallel_start"] = choice
    elif choice:
        problems.append(_("Choose how a lecture's other calls start from the list."))
    if "max_parallel" in data:
        try:
            cap = int(data["max_parallel"])
        except ValueError:
            cap = 0
        if not 1 <= cap <= llm.MAX_PARALLEL_LIMIT:
            problems.append(_("At most this many calls at once: a whole number from 1 to "
                              "%(most)s, or empty for no cap.") % {"most": llm.MAX_PARALLEL_LIMIT})
        else:
            data["max_parallel"] = cap


def check_profile(course: Course, name: str, form: dict[str, str]
                  ) -> tuple[dict | None, list[str]]:
    """A profile's fields from a form, or the problems."""
    data = {k: v for k in FIELDS if (v := form.get(k, "").strip())}
    problems = []
    _check_start(data, form, problems)
    if not PROFILE.match(name):
        problems.append(_("A model's name takes lowercase letters, digits, '.', '_' and "
                          "'-', e.g. deepseek-flash."))
    if data.get("provider") not in llm.PROVIDERS:
        problems.append(_("Choose a provider: %(providers)s.")
                        % {"providers": ", ".join(llm.PROVIDERS)})
    if "api_key_env" in data and not keys.NAME.match(data["api_key_env"]):
        problems.append(_("The key's name is a variable in .env, e.g. DEEPSEEK_API_KEY: "
                          "capital letters, digits and '_'."))
    if "base_url" in data and not data["base_url"].startswith(("http://", "https://")):
        problems.append(_("The base URL starts with http:// or https://."))
    elif "base_url" in data and (provider := data.get("provider")) in llm.PROVIDERS:
        # The form shows a known provider's own URL (owner, 2026-11-07); kept out of the
        # file, it follows llm.py if that changes. Another known provider's own URL here
        # means the provider was changed without Apply, or a known one hides as custom.
        if same_url(data["base_url"], llm.PROVIDERS[provider][0] or ""):
            del data["base_url"]
        elif other := next((p for p, (url, _) in llm.PROVIDERS.items()
                            if url and p != provider and same_url(data["base_url"], url)),
                           None):
            problems.append(_("The base URL is %(other)s's own address, but the provider "
                              "is %(provider)s: choose %(other)s as the provider (it knows "
                              "how to turn its thinking off), or after changing the "
                              "provider click Apply, which puts that provider's own address "
                              "here.") % {"other": other, "provider": provider})
    for effort in ("reasoning_effort", "reasoning_effort_on"):
        if effort in data and not _WORD.match(data[effort]):
            problems.append(_("%(field)s: one word such as low or high.") % {"field": effort})
    if "timeout_s" in data:
        try:
            seconds = float(data["timeout_s"])
        except ValueError:
            seconds = 0
        if not 0 < seconds <= 3600:
            problems.append(_("The time limit is a number of seconds, 1 to 3600."))
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
        return [_("That model is no longer in workspace.toml; reload the page.")]
    if name != original and name in names:
        return [_("A model named %(name)s exists already.") % {"name": name}]
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
        return [_("That model is no longer in workspace.toml.")]
    used = sorted(r for r, p in old.get("roles", {}).items() if p == name)
    if used:
        return [ngettext_("%(name)s is used by %(roles)s: choose another model for it first.",
                          "%(name)s is used by %(roles)s: choose another model for them "
                          "first.", len(used)) % {"name": name, "roles": ", ".join(used)}]
    expected = copy.deepcopy(old)
    del expected["profiles"][name]
    def change(doc) -> None:
        # `del`: [llm.profiles.*] split by other tables reads back as a proxy without remove().
        del _tables(doc)[0][name]

    return _edit(course, change, expected)


def key_elsewhere(root: Path, course: Course, name: str, data: dict) -> list[str]:
    """The problem when the profile's key name is used by a profile of any course under
    `root` at another host: one key belongs to one provider, and a profile that sends it
    elsewhere (a changed URL in the models.dev list, say) would give it away on every call.
    The same host at another path (z.ai's two sites are two keys) passes; so does a profile
    with no key (a server on this machine)."""
    key, url = data.get("api_key_env"), data.get("base_url")
    if not key:
        return []
    host = _host(url or llm.PROVIDERS[data["provider"]][0] or "")
    for other in find_courses(root)[0]:
        for p_name, p_data in section(other).get("profiles", {}).items():
            if not isinstance(p_data, dict) or p_data.get("api_key_env") != key:
                continue
            if other.folder == course.folder and p_name == name:
                continue  # the profile being saved, as it was
            there = _host(p_data.get("base_url")
                          or (llm.PROVIDERS.get(p_data.get("provider"), (None,))[0] or ""))
            if there and host and there != host:
                return [_("%(key)s is the key %(profile)s (%(course)s) sends to %(there)s; "
                          "this model would send it to %(host)s. One key goes to one "
                          "provider: give this model's key another name, or check the base "
                          "URL (a key sent to the wrong host is given away).")
                        % {"key": key, "profile": p_name, "course": other.workspace.course,
                           "there": there, "host": host}]
    return []


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def check_command(names: list[str]) -> list[str]:
    """`python -m tutor.llm check` on these profiles (names checked against the file)."""
    return [sys.executable, "-m", "tutor.llm", "check", *names]


def timing_command(name: str) -> list[str]:
    """`python -m tutor.llm_timing` on one profile (the name checked against the file):
    when its provider's cache can be read, to choose its parallel_start."""
    return [sys.executable, "-m", "tutor.llm_timing", name]


def measurable(profile: ProfileView) -> bool:
    """A chat model on an API: not an assistant (no calls), not an embedding model (only
    the embedding role uses it: no chat endpoint)."""
    return (profile.data.get("provider") != "assistant"
            and not (profile.roles and set(profile.roles) == {"embedding"}))


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


def _edit(course: Course, change: Callable, expected: dict) -> list[str]:
    """Write [llm] through toml_edit, which checks the result before replacing the file."""
    return toml_edit.edit(course.workspace.root, "llm", change, expected)

