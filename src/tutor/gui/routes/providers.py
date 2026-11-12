"""The Providers page (owner, 2026-11-12): the shared models' provider cards in llm.toml,
each with its key (keys.py: in .env, never sent back to the browser) and its models; and
the keys no card names, with the Add form the Keys page had (the page this one replaces:
its /keys/ addresses come here).

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import dataclasses

from flask import Flask, abort, current_app, redirect, render_template, request, url_for

from tutor import llm
from tutor.gui import catalog, courses, keys, llm_settings, providers
from tutor.gui.i18n import N_
from tutor.gui.llm_roles import ROLES
from tutor.gui.routes.common import start_task


def _used_by(root) -> dict[str, list[str]]:
    """Shared model name -> the "course: role" entries naming it."""
    users: dict[str, list[str]] = {}
    for course in courses.find_courses(root)[0]:
        for role, name in llm_settings.file_section(course).get("roles", {}).items():
            if role in ROLES and isinstance(name, str):
                users.setdefault(name, []).append(f"{course.workspace.course}: {role}")
    return users


def providers_page(problems: list[str], code: int = 200, name: str = "", provider: str = ""):
    """The page. `provider`: an id from the kept models.dev list (catalog.py); choosing it
    fills the Add-a-key form's name with the variable that provider's key goes in (owner,
    2026-11-10). `name`: a name to start that form with, as a "not set" link gives it."""
    root = current_app.config["ROOT"]
    found = courses.find_courses(root)[0]
    card_users = providers.key_users(root)
    users = {**llm_settings.key_users(found)}
    for key, who in card_users.items():
        users.setdefault(key, []).extend(who)
    rows = keys.rows(current_app.config["ENV_FILE"], users)
    by_name = {row.name: row for row in rows}
    others = [row for row in rows if row.name not in card_users]
    listed = catalog.load(root)
    # A server on this machine has no key; a variable name the form would refuse is left out.
    choices = [p for p in listed.providers if keys.NAME.match(p.key_env)] if listed else []
    chosen = next((p for p in choices if p.id == provider), None)
    if chosen:
        name = chosen.key_env
    # The chosen name's key is in .env already: the form says so and replaces it, instead
    # of looking as if a second key were being added.
    existing = next((r for r in rows if r.name == name and r.is_set), None)
    return render_template("providers.html", problems=problems, name=name,
                           cards=providers.cards(root, _used_by(root)), key_rows=by_name,
                           other_rows=others, unassigned=providers.unassigned_models(root),
                           file_problem=providers.problem(root), file=llm.SHARED_FILE,
                           providers=choices, chosen=chosen, existing=existing), code


def model_page(card: providers.CardView, original: str | None, values: dict,
               problems: list[str], code: int = 200, shelf: dict | None = None):
    return render_template("shared_model.html", card=card, original=original, values=values,
                           problems=problems, shelf=shelf,
                           defaults=llm_settings.provider_defaults()), code


def _card_or_404(name: str) -> providers.CardView:
    found = next((c for c in providers.cards(current_app.config["ROOT"]) if c.name == name),
                 None)
    if found is None:
        abort(404, N_("No such provider."))
    return found


def _shelf(card: providers.CardView, model_id: str, show_all: bool) -> dict:
    """The models the card's provider offers, for the form above the fields (the course
    page's New model shelf, cut to this one provider, whose address the card fixes): a
    server on this machine is asked for its own models at the card's address; a cloud
    provider's come from the kept models.dev list, the entry at the card's address, else
    the first of the card's kind; custom has none."""
    root = current_app.config["ROOT"]
    found = catalog.load(root)
    provider, problem = None, ""
    if card.kind in catalog.LOCAL:
        provider = dataclasses.replace(catalog.local_provider(card.kind), base_url=card.base_url)
        listed, problem = catalog.installed(card.base_url)
        provider = dataclasses.replace(provider, models=listed)
    elif found and card.kind != "custom":
        provider = next((p for p in found.providers
                         if catalog.same_url(p.base_url, card.base_url)), None) or next(
            (p for p in found.providers if catalog.KNOWN.get(p.id) == card.kind), None)
    model = next((m for m in provider.models if m.id == model_id), None) if provider else None
    # A model the card has already is not offered again (owner, 2026-11-12).
    have = {m.model for m in card.models}
    listed = [m for m in provider.models if m.id not in have] if provider else []
    shown = listed if show_all else listed[:catalog.SHOWN]
    if model and model not in shown:
        shown.append(model)
    return {"catalog": found, "stale": "" if found else catalog.stale(root),
            "provider": provider, "model": model, "models": shown,
            "hidden": len(listed) - len(shown), "problem": problem,
            "all_added": bool(provider and provider.models and not listed),
            "key_set": bool(card.api_key_env in keys.read(current_app.config["ENV_FILE"]))}


def _first_course(root, model: str):
    """The course a check or a measurement of a shared model runs as a task of: the first
    whose roles name it, else the first course (tasks and their logs live in a course)."""
    found = courses.find_courses(root)[0]
    users = _used_by(root).get(model, [])
    for course in found:
        if any(u.startswith(course.workspace.course + ": ") for u in users):
            return course
    return found[0] if found else None


def card_page(original: str | None, values: dict, problems: list[str], code: int = 200):
    return render_template("provider.html", original=original, values=values,
                           problems=problems, kinds=providers.KINDS,
                           defaults=llm_settings.provider_defaults()), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/providers/")
    def provider_list():
        """?provider=ID fills the Add-a-key form's name from the kept list; ?name=NAME
        starts it with that name (a valid one only). Neither changes anything."""
        name = request.args.get("name", "")
        return providers_page([], name=name if keys.NAME.match(name) else "",
                              provider=request.args.get("provider", ""))

    @app.get("/keys/")
    def key_list():
        """The Keys page's address: its keys are on the Providers page now."""
        args = {k: v for k in ("name", "provider") if (v := request.args.get(k))}
        return redirect(url_for("provider_list", **args) + "#keys", code=302)

    @app.get("/providers/new")
    def new_provider():
        return card_page(None, _applied() or {"name": "", "kind": ""}, [])

    @app.get("/providers/p/<name>/")
    def edit_provider(name: str):
        data = providers.read(app.config["ROOT"]).get("providers", {}).get(name)
        if not isinstance(data, dict):
            abort(404, N_("No such provider."))
        return card_page(name, _applied() or providers.form_values(name, data), [])

    @app.post("/providers/save")
    def save_provider():
        form = {k: request.form.get(k, "") for k in providers.FORM_FIELDS}
        original = request.form.get("original") or None
        name = form["name"].strip()
        data, problems = providers.check_card(name, form)
        if data is not None:
            problems = providers.save_card(app.config["ROOT"], original, name, data)
        if problems:
            return card_page(original, form, problems, 400)
        return redirect(url_for("provider_list") + "?notice=provider-saved", code=303)

    @app.post("/providers/p/<name>/delete")
    def delete_provider(name: str):
        problems = providers.delete_card(app.config["ROOT"], name)
        if problems:
            return providers_page(problems, 400)
        return redirect(url_for("provider_list") + "?notice=provider-removed", code=303)

    @app.get("/providers/p/<name>/models/new")
    def new_shared_model(name: str):
        """A blank form for a model of this card, with the provider's models above it:
        `model` fills the form from one, `all` lists every model instead of the newest."""
        card = _card_or_404(name)
        shelf = _shelf(card, request.args.get("model", ""), bool(request.args.get("all")))
        values: dict = {"name": ""}
        if shelf["provider"] and shelf["model"]:
            taken = set(providers.read(app.config["ROOT"]).get("profiles", {}))
            values, shelf["notes"] = catalog.fill(shelf["provider"], shelf["model"], taken)
            values = {k: v for k, v in values.items() if k in providers.MODEL_FORM_FIELDS}
        values = _applied_model() or {**values, "timeout_s": values.get("timeout_s")
                                      or str(llm_settings.NEW_TIMEOUT_S)}
        return model_page(card, None, values, [], shelf=shelf)

    @app.post("/providers/p/<name>/models/catalog")
    def refresh_shared_catalog(name: str):
        """Download models.dev's list again, then back to this card's New model."""
        card = _card_or_404(name)
        if problem := catalog.refresh(app.config["ROOT"]):
            shelf = _shelf(card, "", False)
            return model_page(card, None, {"name": ""}, [problem], 502, shelf=shelf)
        return redirect(url_for("new_shared_model", name=name, notice="catalog-fetched"),
                        code=303)

    @app.get("/providers/m/<model>/")
    def edit_shared_model(model: str):
        data = providers.read(app.config["ROOT"]).get("profiles", {}).get(model)
        if not isinstance(data, dict):
            abort(404, N_("No such model."))
        card = _card_or_404(str(data.get("provider", "")))
        return model_page(card, model, _applied_model() or providers.model_form_values(
            model, data), [])

    @app.post("/providers/models/save")
    def save_shared_model():
        form = {k: request.form.get(k, "") for k in providers.MODEL_FORM_FIELDS}
        original = request.form.get("original") or None
        card_name = request.form.get("card", "")
        card = _card_or_404(card_name)
        name = form["name"].strip()
        root = app.config["ROOT"]
        data, problems = providers.check_model(root, card_name, name, form)
        if data is not None:
            problems = providers.save_model(root, original, name, data, _used_by(root))
        if problems:
            return model_page(card, original, form, problems, 400)
        return redirect(url_for("provider_list") + "?notice=provider-saved", code=303)

    @app.post("/providers/m/<model>/delete")
    def delete_shared_model(model: str):
        root = app.config["ROOT"]
        problems = providers.delete_model(root, model, _used_by(root))
        if problems:
            return providers_page(problems, 400)
        return redirect(url_for("provider_list") + "?notice=provider-removed", code=303)

    @app.post("/providers/m/<model>/check")
    def check_shared_model(model: str):
        """One tiny request to the model (`python -m tutor.llm check`), as a task of the
        first course that names it (a task runs in a course; the shared models are read
        from any course)."""
        root = app.config["ROOT"]
        if model not in providers.read(root).get("profiles", {}):
            abort(404, N_("No such model."))
        course = _first_course(root, model)
        if course is None:
            return providers_page([N_("Add a course first: a check runs as a task of one.")], 400)
        return start_task(course, f"Check {model}", [llm_settings.check_command([model])],
                          meta={"kind": "check", "from": "providers"})

    @app.post("/providers/m/<model>/timing")
    def time_shared_model(model: str):
        """When the model's provider can serve its cache (`python -m tutor.llm_timing`), as
        a task of the first course that names it: about sixteen paid calls."""
        root = app.config["ROOT"]
        if model not in providers.read(root).get("profiles", {}):
            abort(404, N_("No such model."))
        course = _first_course(root, model)
        if course is None:
            return providers_page([N_("Add a course first: a measurement runs as a task of "
                                      "one.")], 400)
        return start_task(course, f"Measure how {model}'s cache starts",
                          [llm_settings.timing_command(model)],
                          meta={"kind": "timing", "from": "providers"})

    @app.post("/keys/set")
    def set_key():
        """Add or replace a key. The value is never put back in a page, even when refused."""
        name = request.form.get("name", "").strip()
        problem = keys.set_key(app.config["ENV_FILE"], name, request.form.get("value", ""))
        if problem:
            return providers_page([problem], 400, name if keys.NAME.match(name) else "")
        return redirect(url_for("provider_list") + "?notice=key-saved#keys", code=303)

    @app.post("/keys/delete")
    def delete_key():
        root = app.config["ROOT"]
        users = llm_settings.key_users(courses.find_courses(root)[0])
        for key, who in providers.key_users(root).items():
            users.setdefault(key, []).extend(who)
        problem = keys.delete_key(app.config["ENV_FILE"], request.form.get("name", ""), users)
        if problem:
            return providers_page([problem], 400)
        return redirect(url_for("provider_list") + "?notice=key-deleted#keys", code=303)


def _applied_model() -> dict | None:
    """The model form as a reload sends it back (none of its fields follow a choice, so
    only a `model` link from the shelf re-renders it); None otherwise."""
    if not request.args.get("apply"):
        return None
    return {k: request.args.get(k, "") for k in providers.MODEL_FORM_FIELDS}


def _applied() -> dict | None:
    """The card form as Apply sent it back (a GET of every field, after the kind was
    changed), so the page shows that kind's address; None without Apply."""
    if not request.args.get("apply"):
        return None
    values = {k: request.args.get(k, "") for k in providers.FORM_FIELDS}
    if any(url and catalog.same_url(values["base_url"], url)
           for url, _ in llm.PROVIDERS.values()):
        values["base_url"] = ""  # the kind before's own address: the new kind shows its own
    return values
