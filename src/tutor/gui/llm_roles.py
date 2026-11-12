"""A course's roles (GUI step 6): which model each task of the course uses, its
workspace.toml's [llm.roles], and how independent the oracle's reading is.

Split from llm_settings.py (2026-11-12, when it passed 500 lines; no behaviour changed):
that module keeps the models themselves (the profile form, its checks, the checked write
of [llm] through toml_edit), which this one uses. Roles are checked by the rule generation
enforces: the oracle comes from another provider than the models that write and repair
the exercise (warned about, not refused), and one assistant profile cannot be both (its
requests would mix in one queue).
"""

from __future__ import annotations

import copy

import tomlkit

from tutor import llm

# Through the module, looked up at call time: a test's stand-in for one of its helpers
# (llm_settings._tables, say) reaches the calls made here too.
from tutor.gui import llm_settings
from tutor.gui.courses import Course
from tutor.gui.i18n import N_
from tutor.gui.i18n import gettext_ as _
from tutor.gui.toml_edit import put as _put

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


def merged_roles(course: Course, chosen: dict[str, str]) -> dict[str, str]:
    """The roles as they would be saved: the file's, with each chosen one set, or removed
    when chosen as none ("")."""
    saved = llm_settings.section(course).get("roles", {})
    roles = {**saved, **{r: n for r, n in chosen.items() if n}}
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
    for name, data in llm_settings.section(course).get("profiles", {}).items():
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
    known = llm_settings.section(course).get("profiles", {})
    oracle = roles.get("oracle")
    if oracle not in known:
        return []
    makers = profile_makers(course, found)

    def source(name: str) -> tuple[str, str] | None:
        try:
            return llm_settings._source(course, name, known)
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
    old = llm_settings.section(course)
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
            problems.append(_("The oracle and %(writer)s are one assistant profile "
                              "(%(profile)s), whose requests would mix: give the oracle a "
                              "second profile of the assistant, answered in a fresh context "
                              "(docs/assistant-mode.md).") % {"writer": writer, "profile": oracle})
    if problems or dry_run:
        return problems
    expected = copy.deepcopy(old)
    expected["roles"] = roles

    def change(doc) -> None:
        table = llm_settings._tables(doc)[1]
        for role, name in chosen.items():
            if name:
                _put(table, role, name)
            elif role in table:
                del table[role]

    return llm_settings._edit(course, change, expected)


def copy_models(course: Course, source: Course) -> list[str]:
    """The other course's profiles and roles, into a course that has none yet."""
    if llm_settings.section(course).get("profiles"):
        return [_("This course has models already; change them one by one.")]
    data = llm_settings.section(source)
    if not data.get("profiles"):
        return [_("%(course)s has no models to copy.") % {"course": source.folder}]
    expected = copy.deepcopy(llm_settings.section(course))
    expected["profiles"] = copy.deepcopy(data["profiles"])
    expected["roles"] = copy.deepcopy(data.get("roles", {}))

    def change(doc) -> None:
        table, roles = llm_settings._tables(doc)
        for name, values in data["profiles"].items():
            item = tomlkit.table()
            for k, v in values.items():
                item[k] = v
            table[name] = item  # tomlkit sets tables it writes itself apart
        for role, name in data.get("roles", {}).items():
            _put(roles, role, name)

    return llm_settings._edit(course, change, expected)
