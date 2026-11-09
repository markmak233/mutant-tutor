"""Step 4's read-only inventory summary, full history, and explicit metadata refresh."""

import sys

from flask import Flask, render_template

from tutor import image_records
from tutor.gui import image_record_page, tasks
from tutor.gui.routes.common import course_or_404, start_task
from tutor.image_recipe import ContainerError


def register(app: Flask) -> None:
    @app.get("/c/<name>/environment/image-record/summary")
    def environment_image_record(name):
        """Load slow image identity/venv reads separately from the Environment page."""
        course = course_or_404(name)
        return render_template("_image_record_summary.html", course=course,
                               record=image_record_page.snapshot(course),
                               running=tasks.running(course.workspace.root))

    @app.get("/c/<name>/environment/image-record")
    def image_record_view(name):
        course = course_or_404(name)
        record = image_record_page.snapshot(course)
        # A recipe/path failure is already an unavailable record, not a page-level crash.
        # Preserve the refresh/configuration path even when history cannot be enumerated.
        history, history_problem = [], None
        try:
            history = image_records.history(course.workspace)
        except (ContainerError, OSError):
            history_problem = "History could not be read. Check the requirements and image first."
        return render_template("image_record.html", course=course, view=record.view,
                               host=record.host, rows=record.rows,
                               history=history, history_problem=history_problem,
                               running=tasks.running(course.workspace.root))

    @app.post("/c/<name>/environment/image-record/refresh")
    def refresh_image_record(name):
        course = course_or_404(name)
        # The GUI's current tool code reads the selected image; no course-venv package,
        # implicit .env, model, build, pull or install is involved in a metadata refresh.
        command = [sys.executable, "-m", "tutor.image_records", str(course.workspace.root)]
        return start_task(course, "Record current image versions", [command],
                          meta={"kind": "image-record"})
