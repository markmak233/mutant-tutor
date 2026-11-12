"""Moving a course's own models into the shared llm.toml ("Move to shared"; owner,
2026-11-12: the models span every course, a course keeps only its roles).

A course set up before the shared file has its models in its workspace.toml's
[llm.profiles]. The move, on the owner's click per course, puts each of them in the shared
file and takes it out of the course's; the course's [llm.roles] stay as they are, since the
names do not change, so the course runs the same models afterwards.

How a profile moves (plan()):
- Its provider card: the shared card of the same kind at the same address (the profile's
  own base_url, else the kind's), if there is one; else a new card, named after the kind
  (deepseek), or `kind-2`, `kind-3` when that name is taken by a card at another address
  or with another key. A card gets the key's name from the first profile that made it; a
  profile whose key's name differs from its card's stays in the course (one address, one
  key: the owner decides which), as does one whose card llm.py cannot read.
- The model: the profile's fields without the card's (provider, base_url, api_key_env),
  with `provider` naming the card. An assistant profile (no endpoint, no key) moves with
  `provider = "assistant"` and no card: its request folder is the course's own either way.
- A shared model of the same name already: equal (the same card and fields) merges, nothing
  written for it; different stays in the course and the page says so (rename or remove one).

The write (apply()): the shared file first, through toml_edit's checked write (backup
llm.toml.bak); only when that went in, the course's own, which drops the moved profiles
(backup workspace.toml.bak). A failure between the two leaves the models in both files,
which is harmless: the course's own is read first until it is removed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import tomlkit

from tutor import llm
from tutor.gui import llm_settings, providers, toml_edit
from tutor.gui.catalog import same_url
from tutor.gui.courses import Course
from tutor.gui.i18n import N_
from tutor.gui.i18n import gettext_ as _

CARD_FIELDS = ("base_url", "api_key_env")  # a profile's fields that go to its card
STAYS = {  # why a profile stays in the course, by key; the page translates them
    "differs": N_("a shared model of this name differs (rename or remove one)"),
    "key": N_("its key's name differs from the card's at this address"),
    "unreadable": N_("its provider card cannot be read"),
}


@dataclass
class Plan:
    """What one click would do, worked out from both files as they are."""

    cards: dict[str, dict] = field(default_factory=dict)  # new cards to write
    models: dict[str, dict] = field(default_factory=dict)  # models to write, by name
    merged: list[str] = field(default_factory=list)  # equal to a shared one: nothing written
    stays: dict[str, str] = field(default_factory=dict)  # name -> a STAYS key

    @property
    def moved(self) -> list[str]:
        """The profiles the course's file would lose: written to the shared one or merged."""
        return sorted([*self.models, *self.merged])


def plan(root, course: Course) -> Plan:
    own = llm_settings.file_section(course).get("profiles") or {}
    shared = providers.read(root)
    cards = {n: dict(c) for n, c in (shared.get("providers") or {}).items() if isinstance(c, dict)}
    models = {n: dict(m) for n, m in (shared.get("profiles") or {}).items() if isinstance(m, dict)}
    result = Plan()
    for name, data in own.items():
        if not isinstance(data, dict):
            continue
        kind = data.get("provider")
        if kind == "assistant":
            model = dict(data)
        else:
            card = _card_for(name, kind, data, cards, result)
            if card is None:
                continue  # result.stays says why
            model = {"provider": card, **{k: v for k, v in data.items()
                                          if k not in ("provider", *CARD_FIELDS)}}
        if name in models:
            if models[name] == model:
                result.merged.append(name)
            else:
                result.stays[name] = "differs"
            continue
        result.models[name] = model
        models[name] = model
    return result


def _card_for(name: str, kind: str | None, data: dict, cards: dict[str, dict],
              result: Plan) -> str | None:
    """The card the profile `name`'s endpoint is: an existing one, or a new one noted in
    `result.cards` (and in `cards`, for the profiles after it); None when the profile must
    stay, the reason filed in result.stays."""
    if kind not in llm.PROVIDERS:
        result.stays[name] = "unreadable"
        return None
    url = data.get("base_url") or (llm.PROVIDERS[kind][0] or "")
    key = data.get("api_key_env", "")
    for card_name, card in cards.items():
        card_kind = card.get("kind", card_name)
        card_url = card.get("base_url") or (llm.PROVIDERS.get(card_kind, (None,))[0] or "")
        if card_kind == kind and same_url(card_url, url):
            if card.get("api_key_env", "") != key:
                result.stays[name] = "key"
                return None
            return card_name
    card_name, n = kind, 2
    while card_name in cards:
        card_name, n = f"{kind}-{n}", n + 1
    card = {}
    if card_name != kind:
        card["kind"] = kind
    if data.get("base_url") and not same_url(data["base_url"], llm.PROVIDERS[kind][0] or ""):
        card["base_url"] = data["base_url"]
    if key:
        card["api_key_env"] = key
    try:
        llm.card(card_name, card)
    except llm.LLMError:
        result.stays[name] = "unreadable"
        return None
    cards[card_name] = card
    result.cards[card_name] = card
    return card_name


def apply(root, course: Course) -> list[str]:
    """Move what plan() says can move; [] when done, else the problem (nothing half done:
    the course's file changes only after the shared one did)."""
    own = llm_settings.file_section(course)
    moving = plan(root, course)
    if not moving.moved and not moving.cards:
        return [_("Nothing to move: every model of this course is shared already, or stays "
                  "for the reason shown.")]
    shared = providers.read(root)
    expected = copy.deepcopy(shared)
    expected.setdefault("providers", {}).update(moving.cards)
    expected.setdefault("profiles", {}).update(moving.models)

    def change_shared(doc) -> None:
        cards, profiles = providers._tables(doc)
        for name, data in moving.cards.items():
            item = tomlkit.table()
            for k in providers.FIELDS:
                if k in data:
                    item[k] = data[k]
            cards[name] = toml_edit.spaced(item)
        for name, data in moving.models.items():
            item = tomlkit.table()
            for k in ("provider", *providers.MODEL_FIELDS):
                if k in data:
                    item[k] = data[k]
            profiles[name] = toml_edit.spaced(item)

    problems = toml_edit.edit_path(providers.path(root), change_shared,
                                   {"providers": expected["providers"],
                                    "profiles": expected["profiles"]})
    if problems:
        return problems
    expected_own = copy.deepcopy(own)
    for name in moving.moved:
        expected_own.get("profiles", {}).pop(name, None)

    def change_own(doc) -> None:
        # [llm.profiles] looked up again before each deletion: when [llm.roles] sits between
        # two runs of [llm.profiles.X] tables (a real course's file, 2026-11-12), tomlkit
        # gives a proxy over the runs whose indices go stale after a deletion, and the
        # next `del` on the same proxy raised IndexError; a fresh lookup rebuilds them.
        for name in moving.moved:
            table = llm_settings._tables(doc)[0]
            if name in table:
                del table[name]

    return llm_settings._edit(course, change_own, expected_own)
