"""A course's models (GUI step 6): the profiles and roles of its workspace.toml's [llm].

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
from tutor.gui.i18n import N_
from tutor.gui.toml_edit import put as _put
from tutor.gui.toml_edit import spaced as _spaced

# The roles a page offers, grouped by what they are for, with what each does. The titles
# and descriptions are marked N_(): the Models page translates them when it shows them.
ROLE_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (N_("Reading lectures"), [
        ("vision", N_("reads the slides that are pictures")),
        ("setup", N_("finds the software the lectures use, to draft [requires] (empty: the "
                     "plan model, else generate)"))]),
    (N_("Generating exercises"), [
        ("plan", N_("plans a batch (empty: the generate model)")),
        ("generate", N_("writes each exercise")),
        ("oracle", N_("writes an independent check from the question alone: best a model by "
                      "another provider and maker than generate and repair (each model's "
                      "maker follows its name, where models.dev knows it)")),
        ("repair", N_("takes over the repairs from the second on (empty: the generate "
                      "model)"))]),
    (N_("While you learn"), [
        ("hint", N_("gives hints on your attempts")),
        ("judge", N_("scores hints and questions in evaluations"))]),
    (N_("Setup diagnosis"), [
        ("doctor", N_("proposes repairs only when you explicitly request advice"))]),
    (N_("Experiments"), [
        ("heldout", N_("writes held-out test data")),
        ("concept", N_("builds the concept index for retrieval")),
        ("embedding", N_("turns pages into vectors for retrieval")),
        ("agent", N_("searches the lectures in the agentic retrieval route"))]),
]
ROLES = [role for _, group in ROLE_GROUPS for role, _ in group]
PROFILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
# A profile's fields in the order a new one is written (llm.PROFILE_FIELDS, ordered).
FIELDS = ("provider", "model", "api_key_env", "base_url", "reasoning_effort",
          "reasoning_effort_on", "timeout_s", "temperature")
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
    base URL ("" for none), and how it turns thinking off, e.g. 'thinking {"type":
    "disabled"}, else reasoning_effort "none"' ("" for no way of its own)."""
    return {name: {"base_url": url or "",
                   "thinking_off": ", else ".join(" ".join(f"{k} {json.dumps(v)}"
                                                          for k, v in fields.items())
                                                 for fields in off)}
            for name, (url, off) in llm.PROVIDERS.items()}


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
    elif "base_url" in data and (provider := data.get("provider")) in llm.PROVIDERS:
        # The form shows a known provider's own URL (owner, 2026-11-07); kept out of the
        # file, it follows llm.py if that changes. Another known provider's own URL here
        # means the provider was changed without Apply, or a known one hides as custom.
        if same_url(data["base_url"], llm.PROVIDERS[provider][0] or ""):
            del data["base_url"]
        elif other := next((p for p, (url, _) in llm.PROVIDERS.items()
                            if url and p != provider and same_url(data["base_url"], url)),
                           None):
            problems.append(f"The base URL is {other}'s own address, but the provider is "
                            f"{provider}: choose {other} as the provider (it knows how to "
                            "turn its thinking off), or after changing the provider click "
                            "Apply, which puts that provider's own address here.")
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


def merged_roles(course: Course, chosen: dict[str, str]) -> dict[str, str]:
    """The roles as they would be saved: the file's, with each chosen one set, or removed
    when chosen as none ("")."""
    roles = {**section(course).get("roles", {}), **{r: n for r, n in chosen.items() if n}}
    for role, name in chosen.items():
        if not name:
            roles.pop(role, None)
    return roles


def profile_makers(course: Course, found) -> dict[str, str]:
    """Each profile's maker, as the models.dev list (`found`, a catalog.Catalog or None) says:
    who made the model it calls, at its base URL; profiles it does not know are left out."""
    if not found:
        return {}
    makers = {}
    for name, data in section(course).get("profiles", {}).items():
        try:
            config = llm._from_profile(name, data, {data.get("api_key_env", ""): "x"},
                                       course.workspace.root)
        except (llm.LLMError, TypeError, ValueError, AttributeError):
            continue
        if config.provider != "assistant" and (maker := found.maker(config.base_url,
                                                                    config.model)):
            makers[name] = maker
    return makers


def role_warnings(course: Course, roles: dict[str, str], found) -> list[str]:
    """Why the oracle is a less independent reading than it should be, for each model that
    writes the exercise (generate, and repair unless it is none, when generate repairs):
    one model, one provider at one address, or one maker through different sites (z.ai and
    bigmodel.cn for GLM, OpenRouter for DeepSeek), as the models.dev list says (`found`, a
    catalog.Catalog or None). Warnings, not refusals (owner, 2026-11-07): not everyone pays
    for two providers, and the page asks before saving. A role set to none is not compared;
    one assistant profile for both is refused by save_roles."""
    known = section(course).get("profiles", {})
    oracle = roles.get("oracle")
    if oracle not in known:
        return []
    makers = profile_makers(course, found)

    def source(name: str) -> tuple[str, str] | None:
        try:
            return _source(course, name, known)
        except (llm.LLMError, TypeError, ValueError, AttributeError):
            return None

    warnings = []
    for writer in ("generate", "repair"):
        name = roles.get(writer)
        if name not in known or (writer == "repair" and name == roles.get("generate")):
            continue
        here, there = source(oracle), source(name)
        if here is None or there is None:
            continue  # a profile llm.py cannot read: its own page says why
        if here[0] == there[0] == "assistant" and name != oracle:
            if known[oracle].get("model", oracle) == known[name].get("model", name):
                warnings.append(f"The oracle ({oracle}) and {writer} ({name}) are one "
                                "assistant through two profiles: answer the oracle's requests "
                                "in a fresh context that has not seen the exercise "
                                "(docs/assistant-mode.md).")
        elif name == oracle:
            if here[0] != "assistant":  # one assistant profile for both is refused
                warnings.append(f"The oracle and {writer} are one model ({oracle}): its check "
                                "is a second reading by the model that wrote the exercise, "
                                "apart from it, but the least independent one.")
        elif here == there:
            warnings.append(f"The oracle ({oracle}) and {writer} ({name}) come from one "
                            "provider: its check is a second reading by that provider's "
                            "model, apart from the exercise but less independent than "
                            "another provider's.")
        elif makers.get(oracle) and makers.get(name) == makers.get(oracle):
            warnings.append(f"The oracle ({oracle}) and {writer} ({name}) are both models "
                            f"made by {makers[oracle]} (so says models.dev), only called "
                            "through different sites. One maker's models share training "
                            "data and habits, so its reading is less independent than "
                            "another maker's.")
    return warnings


def save_roles(course: Course, chosen: dict[str, str], dry_run: bool = False) -> list[str]:
    """Set each offered role to a profile, or to none (""). Roles not offered stay.
    `dry_run`: only the problems, nothing written."""
    old = section(course)
    known = old.get("profiles", {})
    problems = [f"{role}: no model named {name}." for role, name in chosen.items()
                if name and name not in known]
    if problems:
        return problems
    roles = merged_roles(course, chosen)
    oracle = roles.get("oracle")
    for writer in ("generate", "repair"):
        # One assistant profile for both: their requests would mix in one queue. Any other
        # oracle close to a writer is a warning (role_warnings), as in the graph.
        if oracle and roles.get(writer) == oracle and known[oracle].get(
                "provider") == "assistant":
            problems.append(f"The oracle and {writer} are one assistant profile ({oracle}), "
                            "whose requests would mix: give the oracle a second profile of "
                            "the assistant, answered in a fresh context "
                            "(docs/assistant-mode.md).")
    if problems or dry_run:
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
                return [f"{key} is the key {p_name} ({other.workspace.course}) sends to "
                        f"{there}; this model would send it to {host}. One key goes to one "
                        "provider: give this model's key another name, or check the base "
                        "URL (a key sent to the wrong host is given away)."]
    return []


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


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


def _edit(course: Course, change: Callable, expected: dict) -> list[str]:
    """Write [llm] through toml_edit, which checks the result before replacing the file."""
    return toml_edit.edit(course.workspace.root, "llm", change, expected)

