"""The Coverage page: the course's coverage.md, a model's draft of it, and adding
new lectures' sections.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, coverage, environment, tasks
from tutor.gui.routes.common import act, course_or_404, start_task


def coverage_page(course: courses.Course, problems: list[str], code: int = 200):
    found = coverage.view(course)
    return render_template("coverage.html", course=course, view=found, problems=problems,
                           missing=coverage.missing(course, found),
                           running=tasks.running(course.workspace.root)), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""
    launcher = app.config["LAUNCHER"]  # what opens folders and VS Code

    @app.get("/c/<name>/coverage/")
    def coverage_view(name: str):
        return coverage_page(course_or_404(name), [])

    @app.post("/c/<name>/coverage/draft")
    def draft_coverage(name: str):
        """A model splits each lecture not asked yet into knowledge points (it may cost
        money, so only on this click); coverage.md itself is not touched."""
        course = course_or_404(name)
        if environment.setup_model(course) is None:
            return redirect(url_for("coverage_view", name=name) + "?notice=no-setup-model",
                            code=303)
        return start_task(course, "Draft coverage.md from the lectures",
                          [coverage.draft_command()], meta={"kind": "coverage-draft"})

    @app.post("/c/<name>/coverage/use")
    def use_coverage(name: str):
        """The draft becomes coverage.md, only for a course that has none."""
        course = course_or_404(name)
        problems = coverage.use_draft(course)
        if problems:
            return coverage_page(course, problems, 400)
        return redirect(url_for("coverage_view", name=name) + "?notice=coverage-used",
                        code=303)

    @app.post("/c/<name>/coverage/add")
    def add_coverage(name: str):
        """The ticked lectures' sections, appended to coverage.md; nothing in it changes."""
        course = course_or_404(name)
        problems = coverage.add_sections(course, request.form.getlist("lecture"))
        if problems:
            return coverage_page(course, problems, 400)
        return redirect(url_for("coverage_view", name=name) + "?notice=coverage-added",
                        code=303)

    @app.post("/c/<name>/coverage/edit")
    def edit_coverage(name: str):
        """coverage.md, or the draft to copy from, in VS Code."""
        course = course_or_404(name)
        which = coverage.COVERAGE_FILE if request.form.get("file") != "draft" \
            else coverage.DRAFT_FILE
        path = course.workspace.root / which
        if not path.is_file():
            abort(404, f"There is no {which}.")
        return act(lambda: launcher.open_in_vscode(course.workspace.root, path),
                   url_for("coverage_view", name=name), done="opened-coverage")
