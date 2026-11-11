"""The Keys page (GUI step 6): API keys in .env, never sent back to the browser.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, current_app, redirect, render_template, request, url_for

from tutor.gui import catalog, courses, keys, llm_settings


def keys_page(problems: list[str], code: int = 200, name: str = "", provider: str = ""):
    """The page. `provider`: an id from the kept models.dev list (catalog.py, the one the
    Models page fills a new model from); choosing it fills the Add form's name with the
    variable that provider's key goes in (owner, 2026-11-10). Only an id found in the list
    is used, and nothing is asked of the network here. `name`: a name to start the form
    with, as the Models page's "not set" links give it."""
    root = current_app.config["ROOT"]
    found = courses.find_courses(root)[0]
    rows = keys.rows(current_app.config["ENV_FILE"], llm_settings.key_users(found))
    listed = catalog.load(root)
    # A server on this machine has no key; a variable name the form would refuse is left out.
    providers = [p for p in listed.providers if keys.NAME.match(p.key_env)] if listed else []
    chosen = next((p for p in providers if p.id == provider), None)
    if chosen:
        name = chosen.key_env
    # The chosen name's key is in .env already: the form says so and replaces it, instead
    # of looking as if a second key were being added.
    existing = next((r for r in rows if r.name == name and r.is_set), None)
    return render_template("keys.html", problems=problems, name=name, rows=rows,
                           providers=providers, chosen=chosen, existing=existing,
                           first_course=found[0].folder if found else None), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/keys/")
    def key_list():
        """?provider=ID fills the Add form's name from the kept list; ?name=NAME starts it
        with that name (a valid one only). Neither changes anything."""
        name = request.args.get("name", "")
        return keys_page([], name=name if keys.NAME.match(name) else "",
                         provider=request.args.get("provider", ""))

    @app.post("/keys/set")
    def set_key():
        """Add or replace a key. The value is never put back in a page, even when refused."""
        name = request.form.get("name", "").strip()
        problem = keys.set_key(app.config["ENV_FILE"], name, request.form.get("value", ""))
        if problem:
            return keys_page([problem], 400, name if keys.NAME.match(name) else "")
        return redirect(url_for("key_list") + "?notice=key-saved", code=303)

    @app.post("/keys/delete")
    def delete_key():
        found = courses.find_courses(app.config["ROOT"])[0]
        problem = keys.delete_key(app.config["ENV_FILE"], request.form.get("name", ""),
                                  llm_settings.key_users(found))
        if problem:
            return keys_page([problem], 400)
        return redirect(url_for("key_list") + "?notice=key-deleted", code=303)
