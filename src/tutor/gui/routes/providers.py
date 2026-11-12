"""The Providers page (owner, 2026-11-12): the shared models' provider cards in llm.toml,
each with its key (keys.py: in .env, never sent back to the browser) and its models; and
the keys no card names, with the Add form the Keys page had (the page this one replaces:
its /keys/ addresses come here).

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, abort, current_app, redirect, render_template, request, url_for

from tutor import llm
from tutor.gui import catalog, courses, keys, llm_settings, providers
from tutor.gui.i18n import N_
from tutor.gui.llm_roles import ROLES


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
                           providers=choices, chosen=chosen, existing=existing,
                           first_course=found[0].folder if found else None), code


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
