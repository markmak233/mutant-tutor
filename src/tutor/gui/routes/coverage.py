"""The Coverage page: the course's coverage.json (gui/coverage.py), the draft that makes or
extends it, converting a hand-kept coverage.md, and editing points and lectures.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, coverage, environment, tasks
from tutor.gui.i18n import N_
from tutor.gui.i18n import gettext_ as _
from tutor.gui.routes.common import act, course_or_404, start_task


def coverage_page(course: courses.Course, problems: list[str], code: int = 200):
    return render_template("coverage.html", course=course, view=coverage.view(course),
                           problems=problems, statuses=coverage.STATUSES,
                           running=tasks.running(course.workspace.root)), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""
    launcher = app.config["LAUNCHER"]  # what opens folders and VS Code

    def back(name: str, notice: str):
        return redirect(url_for("coverage_view", name=name) + f"?notice={notice}", code=303)

    @app.get("/c/<name>/coverage/")
    def coverage_view(name: str):
        return coverage_page(course_or_404(name), [])

    @app.post("/c/<name>/coverage/draft")
    def draft_coverage(name: str):
        """A model splits the lectures not drafted yet, or changed since, into knowledge
        points (it may cost money, so only on this click). With data, each answer is
        merged in as it comes; without, the answers wait for "Use as coverage". `all=1`
        asks for every lecture again (the owner's points and marks are kept)."""
        course = course_or_404(name)
        if environment.setup_model(course) is None:
            return back(name, "no-setup-model")
        every = request.form.get("all") == "1"
        has_data = coverage.view(course).store is not None
        command = coverage.draft_command(every) if has_data else coverage.first_draft_command()
        title = ("Draft every lecture again" if every else
                 "Draft coverage from the lectures" if not has_data else
                 "Draft the new or changed lectures")
        return start_task(course, title, [command], meta={"kind": "coverage-draft"})

    @app.post("/c/<name>/coverage/use")
    def use_coverage(name: str):
        """The draft becomes coverage.json, only for a course with neither file."""
        course = course_or_404(name)
        problems = coverage.use_draft(course)
        return coverage_page(course, problems, 400) if problems else back(name, "coverage-used")

    @app.post("/c/<name>/coverage/convert")
    def convert_coverage(name: str):
        """A hand-kept coverage.md read into coverage.json, once; the file before kept."""
        course = course_or_404(name)
        problems = coverage.convert(course)
        return (coverage_page(course, problems, 400) if problems
                else back(name, "coverage-converted"))

    @app.post("/c/<name>/coverage/render")
    def render_coverage(name: str):
        """coverage.md written again from the data, after a hand edit of the file."""
        course = course_or_404(name)
        problems = coverage.render_again(course)
        return (coverage_page(course, problems, 400) if problems
                else back(name, "coverage-rendered"))

    @app.post("/c/<name>/coverage/l/<lecture>/notes")
    def save_lecture_notes(name: str, lecture: str):
        course = course_or_404(name)
        problems = coverage.save_lecture(course, lecture, request.form.get("title", ""),
                                         request.form.get("notes", ""))
        return coverage_page(course, problems, 400) if problems else back(name, "coverage-saved")

    @app.post("/c/<name>/coverage/l/<lecture>/add")
    def add_coverage_point(name: str, lecture: str):
        course = course_or_404(name)
        values, problems = coverage.point_fields(request.form)
        problems = problems or coverage.add_point(course, lecture, values)
        return coverage_page(course, problems, 400) if problems else back(name, "coverage-saved")

    @app.get("/c/<name>/coverage/l/<lecture>/p/<point_id>/")
    def coverage_point(name: str, lecture: str, point_id: str):
        """One point's form: its fields as they are, to change and save."""
        course = course_or_404(name)
        found = coverage.find_point(course, lecture, point_id)
        if found is None:
            abort(404, N_("No such point in the data."))
        return render_template("coverage_point.html", course=course, lecture=lecture,
                               point=found[1], statuses=coverage.STATUSES, problems=[],
                               covered=", ".join(f"{c.mark} ({c.batch})" if c.batch else c.mark
                                                 for c in found[1].covered_by))

    @app.post("/c/<name>/coverage/l/<lecture>/p/<point_id>/save")
    def save_coverage_point(name: str, lecture: str, point_id: str):
        course = course_or_404(name)
        values, problems = coverage.point_fields(request.form)
        problems = problems or coverage.save_point(course, lecture, point_id, values)
        if problems:
            found = coverage.find_point(course, lecture, point_id)
            if found is None:
                abort(404, N_("No such point in the data."))
            return render_template("coverage_point.html", course=course, lecture=lecture,
                                   point=found[1], statuses=coverage.STATUSES,
                                   problems=problems,
                                   covered=request.form.get("covered", "")), 400
        return back(name, "coverage-saved")

    @app.post("/c/<name>/coverage/l/<lecture>/p/<point_id>/drop")
    def drop_coverage_point(name: str, lecture: str, point_id: str):
        """The owner confirms a removal a draft proposed, or deletes a point."""
        course = course_or_404(name)
        problems = coverage.drop_point(course, lecture, point_id)
        return coverage_page(course, problems, 400) if problems else back(name, "coverage-saved")

    @app.post("/c/<name>/coverage/l/<lecture>/p/<point_id>/keep")
    def keep_coverage_point(name: str, lecture: str, point_id: str):
        """A point a draft no longer found stays, as the owner's."""
        course = course_or_404(name)
        problems = coverage.keep_point(course, lecture, point_id)
        return coverage_page(course, problems, 400) if problems else back(name, "coverage-saved")

    @app.post("/c/<name>/coverage/edit")
    def edit_coverage(name: str):
        """coverage.json (or coverage.md before the conversion) in VS Code."""
        course = course_or_404(name)
        root = course.workspace.root
        which = (coverage.STORE_FILE if (root / coverage.STORE_FILE).is_file()
                 else coverage.COVERAGE_FILE)
        if request.form.get("file") == "draft":
            which = coverage.DRAFT_FILE
        path = root / which
        if not path.is_file():
            abort(404, _("There is no %(file)s.") % {"file": which})
        return act(lambda: launcher.open_in_vscode(root, path),
                   url_for("coverage_view", name=name), done="opened-coverage")
