"""The Language page (gui/i18n.py, gui/translate.py): the languages the GUI has, the
switch between them (English always among them), and "Translate into ..." , which has a
course's model write a new language's file as a task.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import sys

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, environment, i18n, translate
from tutor.gui.routes.common import course_or_404, start_task


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    def with_model() -> list[tuple[courses.Course, str]]:
        """The courses whose models a translation could ask, with the model's name."""
        found = []
        for course in courses.find_courses(app.config["ROOT"])[0]:
            model = environment.setup_model(course)
            if model:
                found.append((course, model))
        return found

    @app.get("/language")
    def language_page():
        root = app.config["ROOT"]
        strings = translate.english()
        return render_template("language.html", chosen=i18n.chosen(root), models=with_model(),
                               strings=len(strings), tokens_k=translate.tokens_about(strings),
                               folder=root / i18n.TRANSLATIONS)

    @app.post("/language")
    def set_language():
        """The switch: the choice is kept for every course; back to the page it was on."""
        root = app.config["ROOT"]
        language = request.form.get("language", "")
        if language not in i18n.available(root):
            abort(400, "No such language.")
        i18n.choose(root, language)
        back = request.form.get("back", "")
        if not back.startswith("/") or back.startswith("//"):
            back = url_for("language_page")
        return redirect(back, code=303)

    @app.post("/language/translate")
    def translate_language():
        """`python -m tutor.gui.translate NAME` as a task of the course whose model it
        asks (paid, so only on this click); `again=1` asks for every string."""
        name = " ".join(request.form.get("name", "").split())
        course = course_or_404(request.form.get("course", ""))
        if not i18n.slug(name) or i18n.slug(name) == i18n.ENGLISH:
            return redirect(url_for("language_page", notice="no-language-name"), code=303)
        if environment.setup_model(course) is None:
            return redirect(url_for("language_page", notice="no-setup-model"), code=303)
        command = [sys.executable, "-m", "tutor.gui.translate", name,
                   "--root", str(app.config["ROOT"])]
        if request.form.get("again") == "1":
            command.append("--again")
        return start_task(course, f"Translate the GUI into {name}", [command],
                          meta={"kind": "translate"})
