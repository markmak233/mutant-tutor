"""A long task's page, the part of it htmx polls, and Stop. Where a task goes back
to once it ends, and what its "Run again" posts to, are decided here.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import re

from flask import (
    Flask,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from tutor.gui import course_setup, courses, generation, tasks
from tutor.gui.i18n import N_
from tutor.gui.i18n import gettext_ as _
from tutor.gui.routes.common import ENVIRONMENT_NOTICES, waiting_for


def task_or_404(name: str, task_id: str) -> tuple[courses.Course, tasks.Task]:
    """A course's task, or the task of a course being created (no workspace.toml yet)."""
    course = (courses.get_course(current_app.config["ROOT"], name)
              or course_setup.being_created(current_app.config["ROOT"], name))
    if course is None:
        abort(404, N_("No such course."))
    task = tasks.get(course.workspace.root, task_id)
    if task is None:
        abort(404, N_("No such task."))
    return course, task


def return_to(course: courses.Course, task: tasks.Task) -> tuple[str, str, str | None] | None:
    """Where a task goes back to once it ends (owner, 2026-11-06): (page, its name, the
    notice for a task that succeeded), from the task's kind and names, through url_for,
    never a URL kept in task.json, so an edited file cannot send the browser elsewhere.
    None: the task page is the only place for it."""
    kind, name = task.meta.get("kind"), course.folder
    label, batch = task.meta.get("label"), task.meta.get("batch")
    if kind in ("read", "vision"):
        return url_for("lecture_list", name=name), _("Lectures"), "lectures-read"
    if kind == "validate" and isinstance(batch, str):
        return url_for("batch", name=name, batch=batch), batch, "validated"
    exercise = task.meta.get("exercise")
    if kind == "learner-test" and isinstance(batch, str) and isinstance(exercise, str):
        return (url_for("exercise", name=name, batch=batch, exercise=exercise), exercise,
                "tests-passed")
    if kind in ("check", "timing") and task.meta.get("from") == "providers":
        # Started from the Providers page (a shared model): back there, not to a course.
        notice = "models-checked" if kind == "check" else None
        return url_for("provider_list"), _("Providers"), notice
    if kind == "check":
        return url_for("models", name=name), _("Models"), "models-checked"
    if kind == "doctor":
        identifier = task.meta.get("doctor_input", "")
        if isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{32}", identifier):
            action = task.meta.get("action")
            step = {"advice": 2, "independent": 2, "verify": 3, "repair-preview": 4,
                    "apply": 4, "undo": 4, "finish": 4}.get(action, 1)
            if action == "preview" and task.meta.get("fix_step") == 2:
                step = 2
            return (url_for("environment_fix", name=name, identifier=identifier, step=step),
                    _("Setup fix"), None)
        return (url_for("environment_view", name=name) + "#host-install", _("Environment"),
                None)
    if kind == "python-download":
        # Retry returns to a fresh catalog/confirmation form, never silently repeats a download.
        return url_for("python_downloads", name=name), _("Optional Python"), None
    if kind == "coverage-draft":
        return url_for("coverage_view", name=name), _("Coverage"), "coverage-drafted"
    if kind in ENVIRONMENT_NOTICES:
        return (url_for("environment_view", name=name), _("Environment"),
                ENVIRONMENT_NOTICES[kind])
    if kind in generation.KINDS and isinstance(label, str):
        return url_for("plan", name=name, label=label), _("plan %(label)s") % {"label": label}, None
    if kind == "create":
        return url_for("course", name=name), course.workspace.course, None
    return None


def again_url(course: courses.Course, task: tasks.Task) -> str | None:
    """For a task that failed or was stopped: the action that started it, which its "Run
    again" button posts to (owner, 2026-11-06). The commands are made again by that
    route, with its checks, never read back from task.json, which code run on this
    machine could have changed. Only tasks safe to run twice: generating has Continue
    and "Try again" on its plan page, and a new course its own page."""
    kind, name, batch = task.meta.get("kind"), course.folder, task.meta.get("batch")
    if kind == "validate" and isinstance(batch, str):
        return url_for("validate_batch", name=name, batch=batch)
    exercise = task.meta.get("exercise")
    if kind == "learner-test" and isinstance(batch, str) and isinstance(exercise, str):
        return url_for("test_exercise", name=name, batch=batch, exercise=exercise)
    endpoint = {"read": "read_lectures", "vision": "transcribe_lectures",
                "check": "check_models", "install": "install_packages",
                "build": "build_image", "requires-draft": "draft_requires",
                "coverage-draft": "draft_coverage"}.get(kind)
    return url_for(endpoint, name=name) if endpoint else None


def task_parts(course: courses.Course, task: tasks.Task) -> dict:
    """What both the task page and its polled part show; a new course's stages too."""
    # Doctor previews/diffs must fit in full before their explicit send/apply action.
    # Worker bounds keep them below this; ordinary long-task logs retain their old cap.
    log, cut = (tasks.tail(task, limit=512 * 1024) if task.meta.get("kind") == "doctor"
                else tasks.tail(task))
    create = task.meta.get("kind") == "create"
    ahead = [t for t in tasks.pending(course.workspace.root) if t.id < task.id] \
        if task.queued else []
    return {"course": course, "task": task, "log": log, "cut": cut, "ahead": ahead,
            "back": return_to(course, task), "quiet": tasks.quiet_s(task),
            "again": again_url(course, task),
            "waiting_for": waiting_for(course, task),
            "stages": course_setup.create_progress(course, task) if create else None,
            "elapsed": course_setup.elapsed(task) if create else ""}


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/c/<name>/tasks/<task_id>/")
    def task(name: str, task_id: str):
        course, task = task_or_404(name, task_id)
        return render_template("task.html", **task_parts(course, task),
                               ready=courses.get_course(app.config["ROOT"], name) is not None)

    @app.get("/c/<name>/tasks/<task_id>/log")
    def task_log(name: str, task_id: str):
        """The part of the task page that htmx fetches every second while the task runs.
        A task that ends with every command done sends htmx back to the page it was started
        from (HX-Redirect, a navigation in the same window, no script of ours), with a notice
        saying what finished; one that failed or was stopped stays on its log, where the
        error is, with a link back. Only the poll redirects: opening an old task's page
        shows its log. A new course goes on to its page once it is ready."""
        course, task = task_or_404(name, task_id)
        if task.queued:
            tasks.kick(course.workspace.root)  # nothing running? then it is this one's turn
            course, task = task_or_404(name, task_id)
        parts = task_parts(course, task)
        response = make_response(render_template("_task_log.html", **parts))
        back = parts["back"]
        ready = (task.meta.get("kind") != "create"
                 or courses.get_course(app.config["ROOT"], name) is not None)
        if (back and ready and task.status == tasks.DONE
                and request.headers.get("HX-Request") == "true"):
            response.headers["HX-Redirect"] = back[0] + (f"?notice={back[2]}" if back[2]
                                                         else "")
        return response

    @app.post("/c/<name>/tasks/<task_id>/remove")
    def remove_task(name: str, task_id: str):
        """A queued task leaves the queue; one that started meanwhile stays."""
        course, task = task_or_404(name, task_id)
        notice = "removed" if tasks.remove(task) else "not-queued"
        back = (url_for("course", name=name) if notice == "removed"
                else url_for("task", name=name, task_id=task_id))
        return redirect(f"{back}?notice={notice}", code=303)

    @app.post("/c/<name>/tasks/<task_id>/stop")
    def stop_task(name: str, task_id: str):
        _, task = task_or_404(name, task_id)
        notice = "stopping" if tasks.stop(task) else "not-running"
        label = task.meta.get("label")  # a generation task: back to its plan's page
        back = (url_for("plan", name=name, label=label) if isinstance(label, str)
                else url_for("task", name=name, task_id=task_id))
        return redirect(f"{back}?notice={notice}", code=303)
