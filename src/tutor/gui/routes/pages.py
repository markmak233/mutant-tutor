"""Read-only pages (the courses, a course, a batch, an exercise) and the actions on
them: open a folder or VS Code, and validate a batch.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, abort, redirect, render_template, url_for

from tutor.gui import (
    course_setup,
    courses,
    environment,
    generation,
    lectures,
    plan_items,
    rendering,
    tasks,
)
from tutor.gui.routes.common import (
    RECENT_TASKS,
    STATUS_LABELS,
    act,
    batch_or_404,
    course_or_404,
    exercise_or_404,
    start_task,
)
from tutor.languages import student_path


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""
    launcher = app.config["LAUNCHER"]  # what opens folders and VS Code

    @app.get("/favicon.ico")
    def favicon():
        """Browsers ask for it on their own; no icon, and no 404 in their console."""
        return "", 204

    @app.get("/")
    def home():
        found, problems = courses.find_courses(app.config["ROOT"])
        return render_template("courses.html", root=app.config["ROOT"],
                               views=[courses.course_view(c) for c in found], problems=problems,
                               labels=STATUS_LABELS)

    @app.get("/c/<name>/")
    def course(name: str):
        view = courses.course_view(course_or_404(name))
        tasks.kick(view.course.workspace.root)  # a queue a dead runner left: it goes on
        history = tasks.list_tasks(view.course.workspace.root)
        queue = [t for t in reversed(history) if t.queued]
        return render_template("course.html", view=view, labels=STATUS_LABELS,
                               tasks=[t for t in history if not t.queued][:RECENT_TASKS],
                               queue=queue,
                               plans=generation.open_plans(view.course, history),
                               gaps=course_setup.setup_gaps(view.course,
                                                        lectures.lecture_rows(view.course)))

    @app.get("/c/<name>/b/<batch>/")
    def batch(name: str, batch: str):
        course, view = batch_or_404(name, batch)
        return render_template("batch.html", course=course, batch=view, labels=STATUS_LABELS,
                               missing=plan_items.not_succeeded(course, batch),
                               plan_label=generation.plan_label(course, batch))

    @app.get("/c/<name>/b/<batch>/e/<exercise>/")
    def exercise(name: str, batch: str, exercise: str):
        course, view, row, folder = exercise_or_404(name, batch, exercise)
        text = courses.question(course, batch, exercise)
        # The file the learner writes in: student.py, or student.c in a C exercise.
        student = student_path(folder).name
        # This exercise's runs: the logged attempts, and the test tasks started from this
        # page (their logs), newest first.
        runs = [t for t in tasks.list_tasks(course.workspace.root)
                if t.meta.get("kind") == "learner-test" and t.meta.get("batch") == batch
                and t.meta.get("exercise") == exercise]
        return render_template("exercise.html", course=course, batch=view, row=row,
                               question=rendering.question_html(text) if text else None,
                               attempts=courses.exercise_attempts(course, batch, exercise),
                               test_tasks=runs,
                               labels=STATUS_LABELS, student=student,
                               # "Run my tests" only where tutor test works: a container
                               # course (owner, 2026-11-10); a host course runs pytest.
                               in_container=environment.runner(course) == "container",
                               question_in=("the opening comment" if student.endswith(".c")
                                            else "the docstring"))

    @app.post("/c/<name>/b/<batch>/e/<exercise>/test")
    def test_exercise(name: str, batch: str, exercise: str):
        """The learner's own tests in the course image (`tutor test`, the command the
        README gives for the terminal), as a task: live output on the task page, the
        attempt recorded on the host as by the terminal (owner, 2026-11-10: a button, so
        no terminal is needed). A run whose tests fail is a failed task, which stays on
        its log, where the failures are; one that passes goes back to the exercise."""
        course, _, row, folder = exercise_or_404(name, batch, exercise)
        back = url_for("exercise", name=name, batch=batch, exercise=exercise)
        if environment.runner(course) != "container":
            return redirect(f"{back}?notice=tests-host-course", code=303)
        if problem := courses.container_problem(course):
            return redirect(f"{back}?notice={problem}", code=303)
        command = [courses.task_python(course), "-m", "tutor", "test", str(folder)]
        return start_task(course, f"Run my tests: {row.id}", [command],
                          meta={"kind": "learner-test", "batch": batch, "exercise": exercise})

    # --- actions: POST only, each back to its page with a notice ----------------------------

    @app.post("/c/<name>/open-folder")
    def open_course_folder(name: str):
        folder = course_or_404(name).workspace.root
        return act(lambda: launcher.open_folder(folder), url_for("course", name=name))

    @app.post("/c/<name>/b/<batch>/open-folder")
    def open_batch_folder(name: str, batch: str):
        course, _ = batch_or_404(name, batch)
        folder = courses.exercises_dir(course, batch)
        if folder is None:
            abort(404, "This batch has no exercises folder.")
        return act(lambda: launcher.open_folder(folder),
                   url_for("batch", name=name, batch=batch))

    @app.post("/c/<name>/b/<batch>/e/<exercise>/open-folder")
    def open_exercise_folder(name: str, batch: str, exercise: str):
        *_, folder = exercise_or_404(name, batch, exercise)
        return act(lambda: launcher.open_folder(folder),
                   url_for("exercise", name=name, batch=batch, exercise=exercise))

    @app.post("/c/<name>/b/<batch>/e/<exercise>/open-ide")
    def open_exercise_in_ide(name: str, batch: str, exercise: str):
        course, _, _, folder = exercise_or_404(name, batch, exercise)
        student = student_path(folder)  # student.py, or student.c in a C exercise

        def ide():
            launcher.open_in_vscode(course.workspace.root,
                                    student if student.is_file() else folder)

        return act(ide, url_for("exercise", name=name, batch=batch, exercise=exercise),
                   done="opened-ide")

    @app.post("/c/<name>/b/<batch>/validate")
    def validate_batch(name: str, batch: str):
        """`python -m tutor.validation` on every exercise the manifest lists, in its order.
        Each run rewrites that exercise's validation.json, as on the command line."""
        course, view = batch_or_404(name, batch)
        if problem := courses.container_problem(course):
            return redirect(url_for("batch", name=name, batch=batch) + f"?notice={problem}",
                            code=303)
        commands = []
        python = courses.task_python(course)  # the course's .venv on this machine
        for row in view.exercises:
            exercise = courses.exercise_dir(course, batch, row.id)
            solution = courses.solution_dir(course, batch, row.id)
            if exercise and solution:
                commands.append([python, "-m", "tutor.validation", str(exercise),
                                 str(solution)])
        if not commands:
            abort(404, "This batch has no exercise to validate.")
        return start_task(course, f"Validate {batch}", commands,
                          meta={"kind": "validate", "batch": batch})
