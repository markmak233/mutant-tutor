"""A course's Environment page (GUI step 7): [requires] and its draft, the
languages, where tests run, the .venv install and the test image.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import secrets

from flask import Flask, redirect, render_template, request, url_for

from tutor import pythons, workspace
from tutor.gui import courses, environment, languages_page, python_switch, tasks
from tutor.gui.routes.common import course_or_404, start_task


def environment_page(course: courses.Course, problems: list[str], code: int = 200,
                     rows: list[environment.ListRow] | None = None,
                     chosen: dict[str, str] | None = None,
                     typed_language: dict | None = None):
    """`rows`: a refused [requires] form's rows, `chosen`: a refused draft form's ticked
    entries and their versions, `typed_language`: a refused language form's values; all
    shown back as typed."""
    draft = environment.draft_view(course)
    if rows is None:
        # The packages and tools list: [requires] without the language list's rows.
        rows = environment.list_rows(course, languages_page.package_rows(course)
                                     or [("", "")], draft.draft)
    allowed = environment.allowed(course)
    return render_template("environment.html", course=course, problems=problems,
                           rows=rows, kind=environment.kind, draft=draft,
                           chosen=chosen or {},
                           others_allowed=[m for m in allowed
                                           if m not in {r.module for r in rows}],
                           not_pip=environment.NOT_PIP, runner=environment.runner(course),
                           has_venv=environment.venv_python(course).exists(),
                           install=environment.install_shown(course),
                           running=tasks.running(course.workspace.root),
                           language=languages_page.language_view(course),
                           languages=languages_page.languages_view(course),
                           typed_language=typed_language), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/c/<name>/environment/")
    def environment_view(name: str):
        return environment_page(course_or_404(name), [])

    @app.get("/c/<name>/environment/requires-row")
    def requires_row(name: str):
        """A blank [requires] row for "+ Add a row"; "−" asks for it too and drops the reply,
        as htmx needs a request before a swap. It reads and writes nothing."""
        row = environment.ListRow("", "", f"n{secrets.token_hex(4)}", "", False)
        return render_template("_requires_row.html", course=course_or_404(name), row=row,
                               kind=environment.kind, not_pip=environment.NOT_PIP)

    @app.get("/c/<name>/environment/check")
    def environment_check(name: str):
        """The part htmx loads: envcheck in the course's venv (a process, 0.2 s warm), and
        when it finds what another Python could fix, the Pythons on this machine."""
        course = course_or_404(name)
        check = environment.check(course)
        return render_template("_env_check.html", course=course, check=check,
                               kind=environment.kind, differs=environment.differs,
                               switch=switch_options(course, check))

    def switch_options(course: courses.Course, check: environment.Check):
        """The Pythons to offer, or [] when the check found nothing another one could fix."""
        if check.problem or not python_switch.related(check.results):
            return []
        current = pythons.ask(environment.venv_python(course))
        return python_switch.options(course.workspace.effective_requires, check.results,
                                     current.version if current else None)

    @app.post("/c/<name>/environment/python")
    def switch_python(name: str):
        """Make the .venv anew with another Python: only one this page offers now, worked out
        again here, so a form cannot name any other program to run."""
        course = course_or_404(name)
        chosen = request.form.get("python", "")
        offered = [o.found for o in switch_options(course, environment.check(course))
                   if o.offered]
        found = next((f for f in offered if str(f.path) == chosen), None)
        if found is None:
            return redirect(url_for("environment_view", name=name) + "?notice=python-not-offered",
                            code=303)
        return start_task(course, f"Make .venv with Python {found.minor}",
                          [python_switch.command(course.workspace.root, found.path)],
                          meta={"kind": "install"})

    @app.get("/c/<name>/environment/docker")
    def environment_docker(name: str):
        """The part htmx loads: Docker may take seconds to answer while its VM wakes."""
        course = course_or_404(name)
        return render_template("_env_docker.html", course=course,
                               view=environment.docker_view(course),
                               scale=environment.IMAGE_SCALE)

    @app.post("/c/<name>/environment/requires")
    def save_requires(name: str):
        course = course_or_404(name)
        form = request.form
        names, versions, keys = form.getlist("name"), form.getlist("version"), form.getlist("key")
        keys += [f"x{i}" for i in range(len(keys), len(names))]  # a form sent without keys
        ticked = set(form.getlist("import"))
        modules = {k: form.get(f"module:{k}", "") for k in keys}
        # The language list's rows (python, gcc) are not in this form: put back as they are.
        all_names, all_versions, problems = languages_page.with_toolchains(course, names, versions)
        table, more = environment.check_requires(all_names, all_versions,
                                                 environment.runner(course))
        problems += more
        allow, import_problems = environment.check_imports(
            list(zip(names, keys, strict=False)), ticked, modules, environment.allowed(course))
        problems += import_problems
        if not problems and table is not None and allow is not None:
            problems = environment.save_requires(course, table, allow)
        if problems:
            typed = [environment.ListRow(n, v, k, modules.get(k, ""), k in ticked)
                     for n, v, k in zip(names, versions, keys, strict=False) if n or v]
            return environment_page(course, problems, 400, typed)
        return redirect(url_for("environment_view", name=name) + "?notice=requires-saved",
                        code=303)

    @app.post("/c/<name>/environment/draft")
    def draft_requires(name: str):
        """A model looks for the software in each lecture not asked yet, or with "again" in
        every lecture (it may cost money, so only on this click); its answers wait on the page
        for the owner's ticks."""
        course = course_or_404(name)
        if environment.setup_model(course) is None:
            return redirect(url_for("environment_view", name=name) + "?notice=no-setup-model",
                            code=303)
        again = request.form.get("again") == "1"
        return start_task(course, "Find requirements in every lecture again" if again
                          else "Find requirements in the lectures",
                          [environment.draft_command(again)], meta={"kind": "requires-draft"})

    @app.post("/c/<name>/environment/languages")
    def save_language_versions(name: str):
        """The language list's versions (Python's, gcc for C) into [requires]."""
        course = course_or_404(name)
        versions = {k.removeprefix("version:"): v for k, v in request.form.items()
                    if k.startswith("version:")}
        problems = languages_page.save_language_versions(course, versions)
        if problems:
            return environment_page(course, problems, 400)
        return redirect(url_for("environment_view", name=name) + "?notice=requires-saved",
                        code=303)

    @app.post("/c/<name>/environment/languages/add")
    def add_language(name: str):
        course = course_or_404(name)
        problems = languages_page.add_language(course, request.form.get("language", ""))
        if problems:
            return environment_page(course, problems, 400)
        notice = ("language-saved-own-editor" if workspace.c_editor_config_kept(
            course.workspace.root) else "language-added")
        return redirect(url_for("environment_view", name=name) + f"?notice={notice}", code=303)

    @app.post("/c/<name>/environment/languages/remove")
    def remove_language(name: str):
        course = course_or_404(name)
        problems = languages_page.remove_language(course, request.form.get("language", ""))
        if problems:
            return environment_page(course, problems, 400)
        return redirect(url_for("environment_view", name=name) + "?notice=language-removed",
                        code=303)

    @app.post("/c/<name>/environment/language")
    def save_language(name: str):
        """Tick or untick C, with its compiler flags and extra headers (owner, 2026-11-07)."""
        course = course_or_404(name)
        form = request.form
        typed = {"c": form.get("c") == "on", "flags": form.get("flags", ""),
                 "headers": form.get("headers", "")}
        problems = languages_page.save_language(course, typed["c"], typed["flags"],
                                             typed["headers"])
        if problems:
            return environment_page(course, problems, 400, typed_language=typed)
        notice = ("language-removed" if not typed["c"] else "language-saved-own-editor"
                  if workspace.c_editor_config_kept(course.workspace.root) else "language-saved")
        return redirect(url_for("environment_view", name=name) + f"?notice={notice}", code=303)

    @app.post("/c/<name>/environment/accept")
    def accept_requires(name: str):
        """Add the ticked candidates to [requires]; only names the draft holds."""
        course = course_or_404(name)
        known = {row.candidate.name for row in environment.draft_view(course).rows}
        chosen = {n: request.form.get(f"version:{n}", "").strip()
                  for n in request.form.getlist("accept") if n in known}
        problems = environment.accept(course, chosen)
        if problems:
            return environment_page(course, problems, 400, chosen=chosen)
        return redirect(url_for("environment_view", name=name) + "?notice=requires-saved",
                        code=303)

    @app.post("/c/<name>/environment/runner")
    def save_runner(name: str):
        course = course_or_404(name)
        problems = environment.save_runner(course, request.form.get("runner", ""),
                                           request.form.get("understood") == "on")
        if problems:
            return environment_page(course, problems, 400)
        return redirect(url_for("environment_view", name=name) + "?notice=runner-saved",
                        code=303)

    @app.post("/c/<name>/environment/install")
    def install_packages(name: str):
        """`tutor.workspace init` again: the .venv if missing, this package, [requires]."""
        course = course_or_404(name)
        return start_task(course, "Install into .venv", [environment.install_command(course)],
                          meta={"kind": "install"})

    @app.post("/c/<name>/environment/build")
    def build_image(name: str):
        """`tutor.container build` with the course's venv; Docker is asked first."""
        course = course_or_404(name)
        back = url_for("environment_view", name=name)
        if not environment.venv_python(course).exists():
            return redirect(f"{back}?notice=no-venv", code=303)
        view = environment.docker_view(course)
        if view.state in ("cannot-build", "docker-missing", "docker-stopped"):
            return redirect(f"{back}?notice={view.state}", code=303)
        return start_task(course, "Build the test image", [environment.build_command(course)],
                          meta={"kind": "build"})
