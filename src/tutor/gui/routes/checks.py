"""The batch page's checks after generation: the judge and the held-out check as tasks."""

from __future__ import annotations

from flask import Flask, redirect, url_for

from tutor.gui import after_checks, courses, environment
from tutor.gui.routes.common import batch_or_404, start_task


def register(app: Flask) -> None:
    def refused(name: str, batch: str, notice: str):
        """Back to the batch page with why nothing was started."""
        return redirect(url_for("batch", name=name, batch=batch) + f"?notice={notice}", code=303)

    @app.post("/c/<name>/b/<batch>/judge")
    def judge_batch(name: str, batch: str):
        """`python -m tutor.judge <batch>`: the `judge` model rates every exercise's teaching
        quality into solutions/<id>/quality.json. An exercise whose inputs are unchanged
        since the same model judged it is kept without a call (judge/teaching.write)."""
        course, _view = batch_or_404(name, batch)
        if environment.is_c_course(course):
            return refused(name, batch, "judge-python-only")  # its prompt is Python's so far
        if after_checks.role_model(course, "judge") is None:
            return refused(name, batch, "no-judge-model")
        command = [courses.task_python(course), "-m", "tutor.judge",
                   str(courses.batch_dir(course, batch))]
        return start_task(course, f"Judge {batch}", [command],
                          meta={"kind": after_checks.JUDGE, "batch": batch})

    @app.post("/c/<name>/b/<batch>/heldout/write")
    def write_heldout(name: str, batch: str):
        """`python -m tutor.heldout_writer <batch>`: the `heldout` model writes, from the
        question and the slides alone, a correct solution, the slides' way and typical
        mistakes into solutions/<id>/heldout/model/. It only writes files; the check below
        runs them. Unchanged inputs are kept without a call."""
        course, _view = batch_or_404(name, batch)
        if after_checks.role_model(course, "heldout") is None:
            return refused(name, batch, "no-heldout-model")
        command = [courses.task_python(course), "-m", "tutor.heldout_writer",
                   str(courses.batch_dir(course, batch))]
        return start_task(course, f"Write held-out solutions: {batch}", [command],
                          meta={"kind": after_checks.WRITE, "batch": batch})

    @app.post("/c/<name>/b/<batch>/heldout/check")
    def check_heldout(name: str, batch: str):
        """`python -m tutor.validation.heldout <batch>`: the exercises' own tests on code
        the generator never saw (the owner's student files, and a model's if written), into
        solutions/<id>/heldout.json. No model is called. It exits 1 when something is
        flagged, so that task stays on its log, where the flagged checks are."""
        course, _view = batch_or_404(name, batch)
        if problem := courses.container_problem(course):
            return refused(name, batch, problem)
        command = [courses.task_python(course), "-m", "tutor.validation.heldout",
                   str(courses.batch_dir(course, batch))]
        return start_task(course, f"Held-out check: {batch}", [command],
                          meta={"kind": after_checks.CHECK, "batch": batch})
