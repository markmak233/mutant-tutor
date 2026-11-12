"""The shared models' providers (owner, 2026-11-12): the cards of `<root>/llm.toml`.

Every model of every course lives once in llm.toml beside the courses (docs/design.md,
"Shared models"; llm.py's section() reads it for a run). A provider card is one endpoint:
the provider llm.py knows (`kind`), its address, the name of the variable its key is in,
how a lecture's other calls start after the first (`parallel_start`, measured defaults in
llm.PARALLEL_START) and how many calls it takes at once (`max_parallel`); its models
(`[profiles.NAME]` with `provider = CARD`) take those from it. The Providers page shows one
card per provider with its key (keys.py: in `.env`, shown by its tail only) and its models.

Writes go through toml_edit.edit_path: tomlkit keeps the file as written, each save is
checked against the tables intended before the file is replaced, the one before kept as
llm.toml.bak. The fields a card may have are checked here (a name, a known kind, an address
with a scheme, a key's variable name, the start and the cap as the model form checks them),
then by llm.card's own rules.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from tutor import llm
from tutor.gui import keys, llm_settings, toml_edit
from tutor.gui.catalog import same_url
from tutor.gui.i18n import gettext_ as _
from tutor.gui.toml_edit import put as _put
from tutor.gui.toml_edit import spaced as _spaced

# A card's fields in the order a new one is written (llm.PROVIDER_FIELDS, ordered).
FIELDS = ("kind", "base_url", "api_key_env", "parallel_start", "max_parallel")
FORM_FIELDS = ("name", *FIELDS, llm_settings.START_SECONDS)
# A shared model's own fields, in the order written: its card gives it the provider, the
# address, the key and the cap, so the form has none of those; `provider` is the card.
MODEL_FIELDS = ("model", "reasoning_effort", "reasoning_effort_on", "timeout_s", "temperature",
                "parallel_start")
MODEL_FORM_FIELDS = ("name", *MODEL_FIELDS, llm_settings.START_SECONDS)
# The kinds a card can be: every provider llm.py calls over an API. An assistant (request
# files, no endpoint, no key) needs no card: a profile names it as its provider directly.
KINDS = tuple(k for k in llm.PROVIDERS if k != "assistant")
_WORD = re.compile(r"^[a-z]+$")


@dataclass(frozen=True)
class ModelView:
    """One model of a card, as the page lists it."""

    name: str
    model: str
    data: dict  # its fields as in the file
    used_by: list[str] = field(default_factory=list)  # "course: role" entries naming it


@dataclass(frozen=True)
class CardView:
    """One provider card, with what its kind brings filled in for the page."""

    name: str
    data: dict  # the card's fields as in the file
    kind: str
    base_url: str  # the card's own, else the kind's own address ("" for custom/none)
    own_url: bool  # the address is the card's own, not the kind's default
    api_key_env: str  # "" for none (a server on this machine)
    parallel_start: str  # the card's, else the kind's measured default
    own_start: bool
    max_parallel: int | None
    models: list[ModelView]
    problem: str = ""  # why llm.py cannot read this card, if it cannot


def path(root: Path) -> Path:
    return root / llm.SHARED_FILE


def read(root: Path) -> dict:
    """The shared file as written; {} when there is none or it does not parse (the page
    then says so through `problem()`)."""
    try:
        return llm.read_shared_file(path(root))
    except llm.LLMError:
        return {}


def problem(root: Path) -> str:
    """Why the shared file cannot be read, or ""."""
    try:
        llm.read_shared_file(path(root))
    except llm.LLMError as e:
        return str(e)
    return ""


def cards(root: Path, used_by: dict[str, list[str]] | None = None) -> list[CardView]:
    """The cards in the file's order, each with its models (the file's order too).
    `used_by`: model name -> the "course: role" entries naming it, for the page."""
    data = read(root)
    found = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    used_by = used_by or {}
    views = []
    for name, raw in found.items():
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", name))
        default_url = (llm.PROVIDERS.get(kind) or (None,))[0] or ""
        own_url = "base_url" in raw
        why = ""
        try:
            llm.card(name, raw)
        except llm.LLMError as e:
            why = str(e)
        models = [ModelView(m, str(d.get("model") or m), dict(d), sorted(used_by.get(m, [])))
                  for m, d in profiles.items()
                  if isinstance(d, dict) and d.get("provider") == name]
        views.append(CardView(
            name, dict(raw), kind, str(raw.get("base_url", default_url)), own_url,
            str(raw.get("api_key_env", "")), str(raw.get(
                "parallel_start", llm.PARALLEL_START.get(kind, llm.DEFAULT_PARALLEL_START))),
            "parallel_start" in raw, raw.get("max_parallel"), models, why))
    return views


def unassigned_models(root: Path) -> list[ModelView]:
    """Shared models whose `provider` is a kind llm.py knows, not a card (written by
    hand, say): listed apart on the page, as a card can be made for them."""
    data = read(root)
    found = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    return [ModelView(m, str(d.get("model") or m), dict(d)) for m, d in profiles.items()
            if isinstance(d, dict) and d.get("provider") not in found]


def key_users(root: Path) -> dict[str, list[str]]:
    """Each key's name -> the cards that name it ("provider: NAME"), for the keys page
    rows beside the courses' own profiles (llm_settings.key_users)."""
    users: dict[str, list[str]] = {}
    for view in cards(root):
        if view.api_key_env:
            users.setdefault(view.api_key_env, []).append(f"provider: {view.name}")
    return users


def form_values(name: str, data: dict) -> dict[str, str]:
    """A card from the file as the form shows it (as llm_settings.form_values does for a
    model): strings, and "first-event+N" split into the choice and its seconds."""
    values = {"name": name, **{k: str(v) for k, v in data.items()}}
    values.setdefault("kind", name)
    start, plus = values.get("parallel_start", ""), "first-event+"
    if start.startswith(plus):
        values["parallel_start"], values[llm_settings.START_SECONDS] = plus, start[len(plus):]
    return values


def check_card(name: str, form: dict[str, str]) -> tuple[dict | None, list[str]]:
    """A card's fields from the form, or the problems. The kind's own address is left out
    of the file (so it follows llm.py); the kind is left out when it is the card's name."""
    data = {k: v for k in FIELDS if (v := form.get(k, "").strip())}
    problems: list[str] = []
    if not llm.NAME.match(name):
        problems.append(_("A provider's name takes lowercase letters, digits, '.', '_' and "
                          "'-', e.g. deepseek or zai."))
    kind = data.get("kind", name)
    if kind not in KINDS:
        problems.append(_("Choose which provider it is: %(kinds)s.")
                        % {"kinds": ", ".join(KINDS)})
    elif kind == name:
        data.pop("kind", None)
    default_url = (llm.PROVIDERS.get(kind) or (None,))[0] or ""
    if "base_url" in data:
        if not data["base_url"].startswith(("http://", "https://")):
            problems.append(_("The base URL starts with http:// or https://."))
        elif default_url and same_url(data["base_url"], default_url):
            del data["base_url"]  # the kind's own address: kept out, it follows llm.py
    elif kind in KINDS and not default_url:
        problems.append(_("This provider has no address of its own: give the base URL."))
    if "api_key_env" in data and not keys.NAME.match(data["api_key_env"]):
        problems.append(_("The key's name is a variable in .env, e.g. DEEPSEEK_API_KEY: "
                          "capital letters, digits and '_'."))
    llm_settings.check_start(data, form, problems)
    if not problems:
        try:
            llm.card(name, data)
        except llm.LLMError as e:
            problems.append(str(e))
    return (None if problems else data), problems


def save_card(root: Path, original: str | None, name: str, data: dict) -> list[str]:
    """Add a card, or change `original` (renamed to `name`, its models following it)."""
    old = read(root)
    found = old.get("providers", {}) if isinstance(old.get("providers"), dict) else {}
    if original is not None and original not in found:
        return [_("That provider is no longer in llm.toml; reload the page.")]
    if name != original and name in found:
        return [_("A provider named %(name)s exists already.") % {"name": name}]
    expected = copy.deepcopy(old)
    expected.setdefault("providers", {}).pop(original or name, None)
    expected["providers"][name] = data
    expected.setdefault("profiles", {})
    if original and original != name:
        for model in expected["profiles"].values():
            if isinstance(model, dict) and model.get("provider") == original:
                model["provider"] = name

    def change(doc) -> None:
        providers, profiles = _tables(doc)
        if original is None:
            item = tomlkit.table()
            for k in FIELDS:
                if k in data:
                    item[k] = data[k]
            providers[name] = _spaced(item)
            return
        if original != name:
            providers[name] = providers[original]
            del providers[original]
            for model in profiles.values():
                if model.get("provider") == original:
                    _put(model, "provider", name)
        item = providers[name]
        for k in list(item.keys()):
            if k not in data:
                del item[k]
        for k in FIELDS:
            if k in data and item.get(k) != data[k]:
                _put(item, k, data[k])

    return toml_edit.edit_path(path(root), change,
                               {"providers": expected["providers"],
                                "profiles": expected["profiles"]})


def delete_card(root: Path, name: str) -> list[str]:
    """Remove a card no model names."""
    old = read(root)
    found = old.get("providers", {}) if isinstance(old.get("providers"), dict) else {}
    if name not in found:
        return [_("That provider is no longer in llm.toml; reload the page.")]
    models = [m for m, d in (old.get("profiles") or {}).items()
              if isinstance(d, dict) and d.get("provider") == name]
    if models:
        return [_("%(name)s is the provider of %(models)s: remove or move those models first.")
                % {"name": name, "models": ", ".join(sorted(models))}]
    expected = copy.deepcopy(old)
    del expected["providers"][name]

    def change(doc) -> None:
        del _tables(doc)[0][name]

    return toml_edit.edit_path(path(root), change, {"providers": expected["providers"]})


def _tables(doc):
    """[providers] and [profiles] in the parsed file, made if missing."""
    for table in ("providers", "profiles"):
        if table not in doc:
            doc[table] = tomlkit.table(is_super_table=True)
    return doc["providers"], doc["profiles"]


# --- a card's models ---------------------------------------------------------------------------


def model_form_values(name: str, data: dict) -> dict[str, str]:
    """A shared model from the file as its form shows it: strings, the start split."""
    values = llm_settings.form_values(name, data)
    values.pop("provider", None)
    return values


def check_model(root: Path, card: str, name: str, form: dict[str, str]
                ) -> tuple[dict | None, list[str]]:
    """A shared model's fields from the form, or the problems: its own fields checked as
    the course form checks them, then the whole, with its card's fields under it, by
    llm.py's rules (a stand-in value for the key, which stays in .env)."""
    data = {k: v for k in MODEL_FIELDS if (v := form.get(k, "").strip())}
    problems: list[str] = []
    if not llm.NAME.match(name):
        problems.append(_("A model's name takes lowercase letters, digits, '.', '_' and "
                          "'-', e.g. deepseek-flash."))
    found = read(root).get("providers", {})
    raw = found.get(card) if isinstance(found, dict) else None
    if not isinstance(raw, dict):
        problems.append(_("That provider is no longer in llm.toml; reload the page."))
    elif not data.get("model"):
        problems.append(_("Give the provider's name for the model."))
    llm_settings.check_start(data, form, problems)
    llm_settings.check_tuning(data, problems)
    if not problems:
        try:
            given = llm.card(card, raw)
            whole = {**given, **data}
            llm._from_profile(name, whole, {whole.get("api_key_env", ""): "x"})
        except llm.LLMError as e:
            problems.append(str(e))
    return (None if problems else {"provider": card, **data}), problems


def save_model(root: Path, original: str | None, name: str, data: dict,
               used_by: dict[str, list[str]]) -> list[str]:
    """Add a shared model, or change `original` (renamed to `name`). A rename of a model a
    course's role names is refused: the roles are in the courses' own files, which this
    page does not write (the owner changes the role, or the name stays)."""
    old = read(root)
    found = old.get("profiles", {}) if isinstance(old.get("profiles"), dict) else {}
    if original is not None and original not in found:
        return [_("That model is no longer in llm.toml; reload the page.")]
    if name != original and name in found:
        return [_("A model named %(name)s exists already.") % {"name": name}]
    if original and name != original and used_by.get(original):
        return [_("%(name)s is named by %(roles)s: a renamed model would leave those roles "
                  "empty. Change the roles first, or keep the name.")
                % {"name": original, "roles": ", ".join(used_by[original])}]
    expected = copy.deepcopy(old)
    expected.setdefault("providers", {})
    expected.setdefault("profiles", {}).pop(original or name, None)
    expected["profiles"][name] = data

    def change(doc) -> None:
        profiles = _tables(doc)[1]
        if original is None:
            item = tomlkit.table()
            for k in ("provider", *MODEL_FIELDS):
                if k in data:
                    item[k] = data[k]
            profiles[name] = _spaced(item)
            return
        if original != name:
            profiles[name] = profiles[original]
            del profiles[original]
        item = profiles[name]
        for k in list(item.keys()):
            if k not in data:
                del item[k]
        for k in ("provider", *MODEL_FIELDS):
            if k in data and item.get(k) != data[k]:
                _put(item, k, data[k])

    return toml_edit.edit_path(path(root), change,
                               {"providers": expected["providers"],
                                "profiles": expected["profiles"]})


def delete_model(root: Path, name: str, used_by: dict[str, list[str]]) -> list[str]:
    """Remove a shared model no course's role names."""
    old = read(root)
    found = old.get("profiles", {}) if isinstance(old.get("profiles"), dict) else {}
    if name not in found:
        return [_("That model is no longer in llm.toml; reload the page.")]
    if used_by.get(name):
        return [_("%(name)s is named by %(roles)s: change those roles first.")
                % {"name": name, "roles": ", ".join(used_by[name])}]
    expected = copy.deepcopy(old)
    del expected["profiles"][name]

    def change(doc) -> None:
        del _tables(doc)[1][name]

    return toml_edit.edit_path(path(root), change, {"profiles": expected["profiles"]})
