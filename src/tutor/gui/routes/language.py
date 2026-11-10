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

    def models() -> list[tuple[str, str, bool]]:
        """The drop-down's models: every course's chat profiles (environment.chat_profiles),
        as (value "folder|profile", label "course: profile (provider: model)", chosen).
        The same provider and model in several courses is listed once, under the first
        course (its key and log serve); chosen: the first course's setup model."""
        found, seen = [], set()
        for course in courses.find_courses(app.config["ROOT"])[0]:
            setup = environment.setup_profile(course)
            for name, provider, model in environment.chat_profiles(course):
                if (provider, model) in seen:
                    continue
                seen.add((provider, model))
                found.append((f"{course.folder}|{name}",
                              f"{course.workspace.course}: {name} ({provider}: {model})",
                              not any(chosen for _v, _l, chosen in found)
                              and setup is not None and setup[0] == name))
        return found

    def chosen_profile() -> tuple[courses.Course, str] | None:
        """The form's model, "folder|profile", when the course has that chat profile."""
        folder, _, profile = request.form.get("model", "").partition("|")
        course = course_or_404(folder)
        names = [name for name, _p, _m in environment.chat_profiles(course)]
        return (course, profile) if profile in names else None

    @app.get("/language")
    def language_page():
        root = app.config["ROOT"]
        strings = translate.english()
        # Each language's file: (strings it has, those kept as the English), for its line.
        files = {code: i18n.translations_for(code, root)
                 for code in i18n.available(root) if code != i18n.ENGLISH}
        return render_template("language.html", chosen=i18n.chosen(root), models=models(),
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
        """`python -m tutor.gui.translate NAME --profile P` (or `--code TAG`, a language
        from the built-in table) as a task of the course whose profile it asks (paid, so
        only on this click); `again=1` asks for every string."""
        row = language_table.by_code(request.form.get("code", ""))
        name = row.name if row else " ".join(request.form.get("name", "").split())
        picked = chosen_profile()
        if not i18n.slug(name) or i18n.slug(name) == i18n.ENGLISH:
            return redirect(url_for("language_page", notice="no-language-name"), code=303)
        if picked is None:
            return redirect(url_for("language_page", notice="no-translate-model"), code=303)
        course, profile = picked
        command = [sys.executable, "-m", "tutor.gui.translate",
                   "--root", str(app.config["ROOT"]), "--profile", profile]
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
