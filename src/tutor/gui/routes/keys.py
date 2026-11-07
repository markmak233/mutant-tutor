"""The Keys page (GUI step 6): API keys in .env, never sent back to the browser.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, current_app, redirect, render_template, request, url_for

from tutor.gui import courses, keys, llm_settings


def keys_page(problems: list[str], code: int = 200, name: str = ""):
    found = courses.find_courses(current_app.config["ROOT"])[0]
    return render_template("keys.html", problems=problems, name=name,
                           rows=keys.rows(current_app.config["ENV_FILE"],
                                          llm_settings.key_users(found))), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/keys/")
    def key_list():
        return keys_page([])

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
