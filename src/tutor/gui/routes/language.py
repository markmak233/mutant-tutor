"""The Language page (gui/i18n.py, gui/translate.py): the languages the GUI has, the
switch between them (English always among them), and "Translate into ..." , which has a
course's model write a new language's file as a task.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import sys

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, environment, i18n, language_table, translate
from tutor.gui.i18n import N_
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
        # Each language's file: (strings it has, those kept as the English), for its line.
        files = {code: i18n.translations_for(code, root)
                 for code in i18n.available(root) if code != i18n.ENGLISH}
        return render_template("language.html", chosen=i18n.chosen(root), models=with_model(),
                               strings=len(strings), tokens_k=translate.tokens_about(strings),
                               folder=root / i18n.TRANSLATIONS,
                               counts={code: t.counts() for code, t in files.items()},
                               notes={code: t.note for code, t in files.items()},
                               table=language_table.LANGUAGES)

    @app.post("/language")
    def set_language():
        """The switch: the choice is kept for every course; back to the page it was on."""
        root = app.config["ROOT"]
        language = request.form.get("language", "")
        if language not in i18n.available(root):
            abort(400, N_("No such language."))
        i18n.choose(root, language)
        back = request.form.get("back", "")
        if not back.startswith("/") or back.startswith("//"):
            back = url_for("language_page")
        return redirect(back, code=303)

    @app.post("/language/translate")
    def translate_language():
        """`python -m tutor.gui.translate NAME` (or `--code TAG`, a language from the
        built-in table) as a task of the course whose model it asks (paid, so only on this
        click); `again=1` asks for every string."""
        row = language_table.by_code(request.form.get("code", ""))
        name = row.name if row else " ".join(request.form.get("name", "").split())
        course = course_or_404(request.form.get("course", ""))
        if not i18n.slug(name) or i18n.slug(name) == i18n.ENGLISH:
            return redirect(url_for("language_page", notice="no-language-name"), code=303)
        if environment.setup_model(course) is None:
            return redirect(url_for("language_page", notice="no-setup-model"), code=303)
        command = [sys.executable, "-m", "tutor.gui.translate",
                   "--root", str(app.config["ROOT"])]
        if row:  # the table's tag names the file and gives the name, note and direction
            command += ["--code", row.code]
        # The note to the translator (register, wording): kept in the file, sent each run.
        note = " ".join(request.form.get("note", "").split())
        if note:
            command += ["--note", note]
        if request.form.get("again") == "1":
            command.append("--again")
        if not row:
            command += ["--", name]  # after "--": a name starting with "-" is not an option
        return start_task(course, f"Translate the GUI into {name}", [command],
                          meta={"kind": "translate"})
