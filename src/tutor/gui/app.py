"""The Flask app: routes, and the checks every request passes first.

Who may use it. The server listens on 127.0.0.1 only, but any web page in the same browser can
still send requests to localhost. So each launch makes a random token, given once in the
URL that `python -m tutor.gui` opens; the first request swaps it for a cookie and redirects to
the same page without it, so the token does not stay in the address bar or the history. A
request without the token gets 403. The cookie is HttpOnly (no script reads it) and
SameSite=Strict (another site's page cannot make the browser send it).

DNS rebinding: a hostile site can make its own domain name point at 127.0.0.1, and the browser
would then treat this server as that site. The Host header still carries the hostile name,
so only 127.0.0.1 and localhost with this server's port are served.

What a page shows. Jinja2 escapes every value (Flask turns it on for .html templates), so
generated text cannot become markup or script; the one exception is a question's Markdown,
which rendering.py turns into HTML with raw HTML off. The Content-Security-Policy
allows scripts from this server only, with no inline script, as a second line. This step only
reads: every route is GET and changes nothing.

Actions. A route that does something on this machine (open a folder, open VS Code) answers
POST only, so a link or an image cannot trigger it; the SameSite cookie already keeps other
sites out, and the Origin header must also be this server's. The action then redirects to its
page (303) with a fixed notice code, so a reload does not repeat it and the page never echoes
text from the request.

Long tasks (step 3) run existing CLI commands in the background (tasks.py, runner.py); their
page polls the end of the log once a second with htmx, which runs with eval, script tags and
its inline styles switched off, so the CSP stays as strict.

Generation (step 4) is such a task: the new-batch form starts `python -m tutor.graph plan`,
and the plan page approves, sends back or continues it with `resume` (generation.py). The
form is the one page that shows what a request sent: when a value is refused, the form comes
back with the values typed, escaped like any other.
"""

from __future__ import annotations

import hmac
import sys
from pathlib import Path
from types import ModuleType

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.eval import report
from tutor.gui import courses, generation, launch, rendering, tasks

COOKIE = "tutor_token"
_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": under it the browser sends `Origin: null` on every POST (the Fetch
    # spec; seen in Chrome, 2026-11-06), and the Origin check refused our own buttons. Under
    # "same-origin" this server's pages send their origin, and other sites get no referrer.
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",  # pages hold private course material
}
# How each state reads on a page, in the report's order.
STATUS_LABELS = {report.PASSED: "passed", report.STARTED: "started",
                 report.UNLOGGED: "changed, no run logged", report.NOT_STARTED: "not started"}
# What an action can report back, by code; nothing else is ever shown. (kind, text)
NOTICES = {
    "opened-folder": ("ok", "Opened in the file manager."),
    "opened-ide": ("ok", "Opened in VS Code: the course folder, with this exercise's student.py."),
    "no-vscode": ("bad", "VS Code was not found. Install it, or in VS Code run "
                         "\"Shell Command: Install 'code' command in PATH\"."),
    "no-file-manager": ("bad", "No file manager command (xdg-open) was found."),
    "failed": ("bad", "It could not be started."),
    "busy": ("bad", "This course already has a task running; it is shown here. Start the "
                    "next one when it ends."),
    "stopping": ("ok", "Stopping: every process of the task got Ctrl+C, and what is still "
                       "running after 10 s is killed."),
    "not-running": ("bad", "This task is not running."),
    "opened-plan": ("ok", "Opened plan.json in VS Code. Save it there; approving uses the "
                          "saved file. Reload this page to see your changes."),
    "feedback-empty": ("bad", "Write what to change first: plain text, 2000 characters at "
                              "most."),
    "plan-moved": ("bad", "The batch is no longer at that step; this page shows where it is "
                          "now."),
}
RECENT_TASKS = 5


def create_app(root: Path, token: str, port: int | None = None,
               launcher: ModuleType = launch) -> Flask:
    """`root`: the folder holding the course folders. `port`: the one the server listens on,
    for the Host check; set later through app.config["PORT"] when the server picks it.
    `launcher`: what opens folders and VS Code (tests pass a stand-in)."""
    app = Flask(__name__)
    app.config.update(ROOT=root.resolve(), TOKEN=token, PORT=port)

    @app.before_request
    def check_request():
        port = app.config["PORT"]
        if request.host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            abort(403, "This server answers only at 127.0.0.1 or localhost.")
        given = request.args.get("token")
        if given is not None and hmac.compare_digest(given, token):
            # Swap the token in the URL for the cookie, and show the page without it.
            response = redirect(request.path)
            response.set_cookie(COOKIE, token, httponly=True, samesite="Strict")
            return response
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            abort(403, "Open the link that `python -m tutor.gui` printed.")
        if request.method != "GET" and request.headers.get("Origin") != f"http://{request.host}":
            abort(403, "An action must come from this GUI's own pages.")
        return None

    @app.context_processor
    def notice():
        found = NOTICES.get(request.args.get("notice", ""))
        return {"notice": {"kind": found[0], "text": found[1]} if found else None}

    @app.after_request
    def add_headers(response):
        response.headers.update(_HEADERS)
        return response

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    def error(e):
        return render_template("error.html", code=e.code, message=e.description), e.code

    def course_or_404(name: str) -> courses.Course:
        course = courses.get_course(app.config["ROOT"], name)
        if course is None:
            abort(404, "No such course.")
        return course

    def batch_or_404(name: str, batch: str) -> tuple[courses.Course, courses.BatchView]:
        course = course_or_404(name)
        view = courses.batch_view(course, batch)
        if view is None:
            abort(404, "No such batch.")
        return course, view

    def exercise_or_404(name: str, batch: str, exercise: str):
        """Only an exercise the batch's manifest lists, as on its page."""
        course, view = batch_or_404(name, batch)
        row = next((r for r in view.exercises if r.id == exercise), None)
        folder = courses.exercise_dir(course, batch, exercise)
        if row is None or folder is None:
            abort(404, "No such exercise.")
        return course, view, row, folder

    def act(action, back: str, done: str = "opened-folder"):
        """Run one launcher call, then go back to the page with what happened: `done`, or the
        LaunchError's code. Both are keys of NOTICES."""
        try:
            action()
        except launch.LaunchError as e:
            done = str(e)
        return redirect(f"{back}?notice={done}", code=303)

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
        history = tasks.list_tasks(view.course.workspace.root)
        return render_template("course.html", view=view, labels=STATUS_LABELS,
                               tasks=history[:RECENT_TASKS],
                               plans=generation.open_plans(view.course, history))

    @app.get("/c/<name>/b/<batch>/")
    def batch(name: str, batch: str):
        course, view = batch_or_404(name, batch)
        return render_template("batch.html", course=course, batch=view, labels=STATUS_LABELS)

    @app.get("/c/<name>/b/<batch>/e/<exercise>/")
    def exercise(name: str, batch: str, exercise: str):
        course, view, row, _ = exercise_or_404(name, batch, exercise)
        text = courses.question(course, batch, exercise)
        return render_template("exercise.html", course=course, batch=view, row=row,
                               question=rendering.question_html(text) if text else None,
                               labels=STATUS_LABELS)

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
        student = folder / "student.py"

        def ide():
            launcher.open_in_vscode(course.workspace.root,
                                    student if student.is_file() else folder)

        return act(ide, url_for("exercise", name=name, batch=batch, exercise=exercise),
                   done="opened-ide")

    # --- long tasks: CLI commands in the background, one per course at a time --------------

    def task_or_404(name: str, task_id: str) -> tuple[courses.Course, tasks.Task]:
        course = course_or_404(name)
        task = tasks.get(course.workspace.root, task_id)
        if task is None:
            abort(404, "No such task.")
        return course, task

    def start_task(course: courses.Course, title: str, commands: list[list[str]],
                   meta: dict | None = None, then: str | None = None):
        """Start the task, then go to `then` (default: the task's page); to the running task
        instead if the course has one. `meta`: see tasks.Task."""
        env = {"WORKSPACE_DIR": str(course.workspace.root),
               "LECTURE_DIR": str(course.workspace.lecture_dir)}
        try:
            task = tasks.start(course.workspace.root, title, commands, env, meta=meta)
        except tasks.TaskBusy as busy:
            return redirect(url_for("task", name=course.folder, task_id=busy.task.id)
                            + "?notice=busy", code=303)
        return redirect(then or url_for("task", name=course.folder, task_id=task.id), code=303)

    @app.post("/c/<name>/b/<batch>/validate")
    def validate_batch(name: str, batch: str):
        """`python -m tutor.validation` on every exercise the manifest lists, in its order.
        Each run rewrites that exercise's validation.json, as on the command line."""
        course, view = batch_or_404(name, batch)
        commands = []
        for row in view.exercises:
            exercise = courses.exercise_dir(course, batch, row.id)
            solution = courses.solution_dir(course, batch, row.id)
            if exercise and solution:
                commands.append([sys.executable, "-m", "tutor.validation", str(exercise),
                                 str(solution)])
        if not commands:
            abort(404, "This batch has no exercise to validate.")
        return start_task(course, f"Validate {batch}", commands)

    @app.get("/c/<name>/tasks/<task_id>/")
    def task(name: str, task_id: str):
        course, task = task_or_404(name, task_id)
        log, cut = tasks.tail(task)
        return render_template("task.html", course=course, task=task, log=log, cut=cut)

    @app.get("/c/<name>/tasks/<task_id>/log")
    def task_log(name: str, task_id: str):
        """The part of the task page that htmx fetches every second while the task runs."""
        course, task = task_or_404(name, task_id)
        log, cut = tasks.tail(task)
        return render_template("_task_log.html", course=course, task=task, log=log, cut=cut)

    @app.post("/c/<name>/tasks/<task_id>/stop")
    def stop_task(name: str, task_id: str):
        _, task = task_or_404(name, task_id)
        notice = "stopping" if tasks.stop(task) else "not-running"
        label = task.meta.get("label")  # a generation task: back to its plan's page
        back = (url_for("plan", name=name, label=label) if isinstance(label, str)
                else url_for("task", name=name, task_id=task_id))
        return redirect(f"{back}?notice={notice}", code=303)

    # --- generation (step 4): the CLI's `plan` and `resume`, as tasks ------------------------

    def new_batch_page(course: courses.Course, values: dict, problems: list[str], code: int):
        ready, unread = generation.lectures(course)
        shown = {"label": "", "lectures": [], "count": "", "prefs": "",
                 **generation.defaults(course), **values}
        return render_template("new_batch.html", course=course, ready=ready, unread=unread,
                               values=shown, problems=problems,
                               models=generation.models(course),
                               running=tasks.running(course.workspace.root)), code

    @app.get("/c/<name>/new-batch")
    def new_batch(name: str):
        return new_batch_page(course_or_404(name), {}, [], 200)

    @app.post("/c/<name>/new-batch")
    def start_plan(name: str):
        """`plan`: the plan model proposes the batch, and the command pauses for the owner."""
        course = course_or_404(name)
        form = request.form
        values = {"label": form.get("label", ""), "lectures": form.getlist("lecture"),
                  "mode": form.get("mode", ""), "how": form.get("how", ""),
                  "count": form.get("count", ""), "max_count": form.get("max_count", ""),
                  "prefs": form.get("prefs", ""), "hide_answers": form.get("hide_answers") == "on"}
        batch, problems = generation.check_form(
            course, values["label"], values["lectures"], values["mode"], values["how"],
            values["count"], values["max_count"], values["prefs"], values["hide_answers"])
        if batch is None:
            return new_batch_page(course, values, problems, 400)
        # How the number was set, so the plan page can tell a plan stopped at its limit.
        how_many = {"count": batch.count} if batch.count else {"max_count": batch.max_count}
        return start_task(course, f"Plan {batch.label}", [generation.plan_command(batch)],
                          meta={"kind": generation.PLAN, "label": batch.label, **how_many},
                          then=url_for("plan", name=name, label=batch.label))

    def plan_or_404(name: str, label: str) -> tuple[courses.Course, generation.PlanView]:
        course = course_or_404(name)
        view = generation.plan_view(course, label)
        if view is None:
            abort(404, "No such plan.")
        return course, view

    def plan_page(template: str, name: str, label: str):
        course, view = plan_or_404(name, label)
        log, cut = tasks.tail(view.task) if view.task else ("", False)
        return render_template(template, course=course, plan=view, log=log, cut=cut,
                               models=generation.models(course), explain=generation.EXPLAIN)

    @app.get("/c/<name>/plans/<label>/")
    def plan(name: str, label: str):
        return plan_page("plan.html", name, label)

    @app.get("/c/<name>/plans/<label>/live")
    def plan_live(name: str, label: str):
        """The part of the plan page that htmx fetches every second while a task runs."""
        return plan_page("_plan_live.html", name, label)

    def plan_task(name: str, label: str, allowed: bool, title: str, command: list[str],
                  kind: str, course: courses.Course):
        """Start a `resume` for the plan if it is at the step the button was for; else back
        to its page, which shows where it is now (another tab, or a terminal, moved it)."""
        back = url_for("plan", name=name, label=label)
        if not allowed:
            return redirect(f"{back}?notice=plan-moved", code=303)
        return start_task(course, title, [command], meta={"kind": kind, "label": label},
                          then=back)

    @app.post("/c/<name>/plans/<label>/approve")
    def approve_plan(name: str, label: str):
        """Generate the batch from plan.json as it is now; this is what spends money."""
        course, view = plan_or_404(name, label)
        return plan_task(name, label, view.can_approve, f"Generate {label}",
                         generation.resume_command(label, approve=True), generation.APPROVE,
                         course)

    @app.post("/c/<name>/plans/<label>/feedback")
    def send_back_plan(name: str, label: str):
        course, view = plan_or_404(name, label)
        feedback = generation.clean_text(request.form.get("feedback", ""))
        if not feedback:
            return redirect(url_for("plan", name=name, label=label) + "?notice=feedback-empty",
                            code=303)
        return plan_task(name, label, view.can_approve, f"Revise plan {label}",
                         generation.resume_command(label, feedback=feedback),
                         generation.FEEDBACK, course)

    @app.post("/c/<name>/plans/<label>/continue")
    def continue_plan(name: str, label: str):
        course, view = plan_or_404(name, label)
        return plan_task(name, label, view.can_continue, f"Continue {label}",
                         generation.resume_command(label), generation.CONTINUE, course)

    @app.post("/c/<name>/plans/<label>/edit")
    def edit_plan(name: str, label: str):
        """plan.json in VS Code: the CLI approves the file as the owner left it."""
        course, view = plan_or_404(name, label)
        if view.draft is None or not (view.draft / "plan.json").is_file():
            abort(404, "This plan has no plan.json yet.")
        return act(lambda: launcher.open_in_vscode(course.workspace.root,
                                                   view.draft / "plan.json"),
                   url_for("plan", name=name, label=label), done="opened-plan")

    return app
