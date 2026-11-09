"""Image build/inventory records and actual course-venv version comparison."""

import sys

from flask import Flask, render_template

from tutor import image_records
from tutor.gui import environment
from tutor.gui.routes.common import course_or_404, start_task


def register(app: Flask) -> None:
    @app.get("/c/<name>/environment/image-record")
    def image_record_view(name):
        course = course_or_404(name)
        view = image_records.view(course.workspace)
        host = environment.check(course) if view.status == "current" else None
        rows = (image_records.comparison(course.workspace, view.data, host.results)
                if host is not None else [])
        return render_template("image_record.html", course=course, view=view, host=host, rows=rows,
                               history=image_records.history(course.workspace))

    @app.post("/c/<name>/environment/image-record/refresh")
    def refresh_image_record(name):
        course = course_or_404(name)
        # The GUI's current tool code reads the selected image; no course-venv package,
        # implicit .env, model, build, pull or install is involved in a metadata refresh.
        command = [sys.executable, "-m", "tutor.image_records", str(course.workspace.root)]
        return start_task(course, "Record current image versions", [command],
                          meta={"kind": "image-record"})
