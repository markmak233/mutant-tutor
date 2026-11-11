"""Explicit optional interpreter download page, separate from read-only doctor checks."""

import subprocess
import sys

from flask import Flask, render_template, request

from tutor import python_manager
from tutor.gui.routes.common import course_or_404, start_task


def register(app: Flask) -> None:
    def show(course, error=None, code=200):
        rows = []
        try:
            results = python_manager.course_results(course.workspace)
            rows = [(entry, python_manager.problems(course.workspace.effective_requires,
                                                    entry, results))
                    for entry in python_manager.catalog()]
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        return render_template("python_downloads.html", course=course, rows=rows, error=error,
                               install_url=python_manager.INSTALL_URL), code

    @app.get("/c/<name>/environment/python-downloads")
    def python_downloads(name):
        return show(course_or_404(name))

    @app.post("/c/<name>/environment/python-downloads")
    def download_python(name):
        course = course_or_404(name)
        identifier = request.form.get("identifier", "")
        if request.form.get("confirm") != "yes":
            return show(course, "Confirm the interpreter download first.", 400)
        try:
            results = python_manager.course_results(course.workspace)
            offered = [e.identifier for e in python_manager.catalog()
                       if not python_manager.problems(course.workspace.effective_requires,
                                                       e, results)]
            if identifier not in offered:
                raise ValueError("That interpreter is not offered now; check the list again.")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return show(course, str(exc), 400)
        # Worker repeats all checks at action time; a form cannot supply arbitrary uv args.
        command = [sys.executable, "-m", "tutor.python_manager", "install",
                   str(course.workspace.root), identifier, "--confirm"]
        return start_task(course, "Download optional Python interpreter", [command],
                          meta={"kind": "python-download"})
