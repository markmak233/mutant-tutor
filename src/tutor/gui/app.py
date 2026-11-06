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

Lectures and new courses (step 5): uploads go into the course's lecture folder and are read
by `python -m tutor.materials` as a task; a new course is `python -m tutor.workspace init`
as a task, after its PDFs are saved (lectures.py). Uploads and the other forms refuse with
the problems listed, each naming the file it is about.

Settings (step 6): the Keys page writes `.env` (keys.py), and a course's Models page its
workspace.toml's [llm] (llm_settings.py). A key is never sent back to the browser: a page
shows its last 4 characters, and a refused key is not put back in the form.

Environment (step 7a): a course's Environment page checks this machine against [requires]
with the course's venv, edits [requires] and [isolation], and starts the two installs the
GUI may run, Python packages into the course's .venv and the test image (environment.py).
The checks that ask a process or Docker load into the page with htmx, so it opens at once.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from types import ModuleType

from flask import Flask, abort, make_response, redirect, render_template, request, url_for

from tutor import llm
from tutor.eval import report
from tutor.gui import (
    courses,
    coverage,
    environment,
    generation,
    keys,
    launch,
    lectures,
    llm_settings,
    rendering,
    tasks,
)

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
    "key-saved": ("ok", "Saved in .env. Commands started from now on use it."),
    "key-deleted": ("ok", "Removed from .env."),
    "model-saved": ("ok", "Saved in workspace.toml (the file before is workspace.toml.bak)."),
    "model-deleted": ("ok", "Removed from workspace.toml (the file before is "
                            "workspace.toml.bak)."),
    "roles-saved": ("ok", "Roles saved in workspace.toml (the file before is "
                          "workspace.toml.bak)."),
    "models-copied": ("ok", "Models copied into workspace.toml. Keys are shared through "
                            ".env, so they work here too."),
    "nothing-to-transcribe": ("ok", "No image page is waiting for the vision model."),
    "no-vision-model": ("bad", "No vision model is set: add `vision = \"<profile>\"` to "
                               "[llm.roles] in workspace.toml."),
    # Before a task that runs tests: nothing was started (courses.container_problem).
    "docker-missing": ("bad", "Nothing was started: Docker is not installed (no `docker` "
                              "command), and tests in a container need it. Install Docker "
                              "Desktop (docker.com), start it, then try again."),
    "docker-stopped": ("bad", "Nothing was started: Docker Desktop is not running, or not "
                              "answering. Start it, wait until it says it is running, then try "
                              "again."),
    "image-missing": ("bad", "Nothing was started: this course's test image is missing or out "
                             "of date. Build it on the course's Environment page, then try "
                             "again."),
    "requires-saved": ("ok", "[requires] saved in workspace.toml (the file before is "
                             "workspace.toml.bak). Install into .venv, and build the image if "
                             "tests run in the container, so both match it."),
    "runner-saved": ("ok", "Saved in workspace.toml (the file before is workspace.toml.bak). "
                           "Validation and generation started from now on use it."),
    "no-venv": ("bad", "This course has no .venv yet: install into .venv first."),
    "cannot-build": ("bad", "[requires] has no image recipe: see the test image part below."),
    "no-setup-model": ("bad", "Nothing was started: this course has no model for it. Choose "
                              "one for the setup, plan or generate role on the Models page."),
}
# What a task that succeeded reports when it goes back to its page (app.return_to).
NOTICES.update({
    "lectures-read": ("ok", "Finished: the table shows what the cache holds now."),
    "validated": ("ok", "Validation finished: every exercise passed it."),
    "models-checked": ("ok", "Every model answered the check."),
    "installed": ("ok", "Installed into .venv: step 2's check below is fresh."),
    "image-built": ("ok", "The test image is built and checked: step 4 below."),
    "draft-ready": ("ok", "The lectures were looked at: tick what to add in step 1."),
    "coverage-drafted": ("ok", "The draft is ready below: check it against the slides."),
    "coverage-used": ("ok", "coverage.md is written from the draft. Edit it as batches are made."),
    "opened-coverage": ("ok", "Opened in VS Code. Reload this page to see your changes."),
    "coverage-added": ("ok", "The ticked lectures were added at the end of coverage.md; nothing "
                             "else in it changed (the file before is coverage.md.bak)."),
})
ENVIRONMENT_NOTICES = {"install": "installed", "build": "image-built",
                       "requires-draft": "draft-ready"}
RECENT_TASKS = 5
UPLOAD_LIMIT_BYTES = 500 * 1024 * 1024  # one upload request, all its files together


def create_app(root: Path, token: str, port: int | None = None,
               launcher: ModuleType = launch, env_file: Path | None = None) -> Flask:
    """`root`: the folder holding the course folders. `port`: the one the server listens on,
    for the Host check; set later through app.config["PORT"] when the server picks it.
    `launcher`: what opens folders and VS Code (tests pass a stand-in). `env_file`: where
    the Keys page keeps keys, by default the repository's .env, which the commands read."""
    app = Flask(__name__)
    # MAX_CONTENT_LENGTH: a request larger than this gets 413 before it is read; each PDF has
    # its own limit (lectures.MAX_PDF_BYTES).
    app.config.update(ROOT=root.resolve(), TOKEN=token, PORT=port,
                      MAX_CONTENT_LENGTH=UPLOAD_LIMIT_BYTES,
                      ENV_FILE=env_file or tasks.REPO / ".env")

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
    @app.errorhandler(413)
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
                               plans=generation.open_plans(view.course, history),
                               gaps=lectures.setup_gaps(view.course,
                                                        lectures.lecture_rows(view.course)))

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
        """A course's task, or the task of a course being created (no workspace.toml yet)."""
        course = (courses.get_course(app.config["ROOT"], name)
                  or lectures.being_created(app.config["ROOT"], name))
        if course is None:
            abort(404, "No such course.")
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

    def return_to(course: courses.Course, task: tasks.Task) -> tuple[str, str, str | None] | None:
        """Where a task goes back to once it ends (owner, 2026-11-06): (page, its name, the
        notice for a task that succeeded), from the task's kind and names, through url_for,
        never a URL kept in task.json, so an edited file cannot send the browser elsewhere.
        None: the task page is the only place for it."""
        kind, name = task.meta.get("kind"), course.folder
        label, batch = task.meta.get("label"), task.meta.get("batch")
        if kind in ("read", "vision"):
            return url_for("lecture_list", name=name), "Lectures", "lectures-read"
        if kind == "validate" and isinstance(batch, str):
            return url_for("batch", name=name, batch=batch), batch, "validated"
        if kind == "check":
            return url_for("models", name=name), "Models", "models-checked"
        if kind == "coverage-draft":
            return url_for("coverage_view", name=name), "Coverage", "coverage-drafted"
        if kind in ENVIRONMENT_NOTICES:
            return (url_for("environment_view", name=name), "Environment",
                    ENVIRONMENT_NOTICES[kind])
        if kind in generation.KINDS and isinstance(label, str):
            return url_for("plan", name=name, label=label), f"plan {label}", None
        if kind == "create":
            return url_for("course", name=name), course.workspace.course, None
        return None

    def task_parts(course: courses.Course, task: tasks.Task) -> dict:
        """What both the task page and its polled part show; a new course's stages too."""
        log, cut = tasks.tail(task)
        create = task.meta.get("kind") == "create"
        return {"course": course, "task": task, "log": log, "cut": cut,
                "back": return_to(course, task),
                "stages": lectures.create_progress(course, task) if create else None,
                "elapsed": lectures.elapsed(task) if create else ""}

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
        if problem := courses.container_problem(course):  # `plan` would stop at once
            return new_batch_page(course, values, [NOTICES[problem][1]], 409)
        # How the number was set, so the plan page can tell a plan stopped at its limit.
        how_many = {"count": batch.count} if batch.count else {"max_count": batch.max_count}
        command = generation.plan_command(batch, courses.task_python(course))
        return start_task(course, f"Plan {batch.label}", [command],
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
                  kind: str, course: courses.Course, validate: bool = False):
        """Start a `resume` for the plan if it is at the step the button was for; else back
        to its page, which shows where it is now (another tab, or a terminal, moved it)."""
        back = url_for("plan", name=name, label=label)
        if not allowed:
            return redirect(f"{back}?notice=plan-moved", code=303)
        if problem := courses.container_problem(course):
            return redirect(f"{back}?notice={problem}", code=303)
        commands = [command]
        if validate:  # a run that generates: then the batch's verdicts, in the same task
            commands.append(generation.validate_command(label, courses.task_python(course)))
        return start_task(course, title, commands, meta={"kind": kind, "label": label},
                          then=back)

    @app.post("/c/<name>/plans/<label>/approve")
    def approve_plan(name: str, label: str):
        """Generate the batch from plan.json as it is now; this is what spends money."""
        course, view = plan_or_404(name, label)
        command = generation.resume_command(label, approve=True,
                                            python=courses.task_python(course))
        return plan_task(name, label, view.can_approve, f"Generate {label}", command,
                         generation.APPROVE, course, validate=True)

    @app.post("/c/<name>/plans/<label>/feedback")
    def send_back_plan(name: str, label: str):
        course, view = plan_or_404(name, label)
        feedback = generation.clean_text(request.form.get("feedback", ""))
        if not feedback:
            return redirect(url_for("plan", name=name, label=label) + "?notice=feedback-empty",
                            code=303)
        return plan_task(name, label, view.can_approve, f"Revise plan {label}",
                         generation.resume_command(label, feedback=feedback,
                                                  python=courses.task_python(course)),
                         generation.FEEDBACK, course)

    @app.post("/c/<name>/plans/<label>/continue")
    def continue_plan(name: str, label: str):
        course, view = plan_or_404(name, label)
        return plan_task(name, label, view.can_continue, f"Continue {label}",
                         generation.resume_command(label, python=courses.task_python(course)),
                         generation.CONTINUE, course, validate=True)

    @app.post("/c/<name>/plans/<label>/edit")
    def edit_plan(name: str, label: str):
        """plan.json in VS Code: the CLI approves the file as the owner left it."""
        course, view = plan_or_404(name, label)
        if view.draft is None or not (view.draft / "plan.json").is_file():
            abort(404, "This plan has no plan.json yet.")
        return act(lambda: launcher.open_in_vscode(course.workspace.root,
                                                   view.draft / "plan.json"),
                   url_for("plan", name=name, label=label), done="opened-plan")

    # --- lectures and new courses (step 5) ----------------------------------------------------

    def lectures_page(course: courses.Course, problems: list[str], code: int = 200):
        rows = lectures.lecture_rows(course)
        return render_template("lectures.html", course=course, rows=rows, problems=problems,
                               vision=lectures.vision_model(course),
                               max_mb=lectures.MAX_PDF_BYTES // (1024 * 1024),
                               running=tasks.running(course.workspace.root)), code

    @app.get("/c/<name>/lectures/")
    def lecture_list(name: str):
        return lectures_page(course_or_404(name), [])

    @app.post("/c/<name>/lectures/upload")
    def upload_lectures(name: str):
        """Save the PDFs, all or none, then read their text layer (free) as a task."""
        course = course_or_404(name)
        saved, problems = lectures.save_pdfs(course.workspace.lecture_dir,
                                             request.files.getlist("pdf"))
        if problems:
            return lectures_page(course, problems, 400)
        title = f"Read {saved[0].name}" if len(saved) == 1 else f"Read {len(saved)} lectures"
        return start_task(course, title, [lectures.read_command(saved)],
                          meta={"kind": "read"})

    @app.post("/c/<name>/lectures/read")
    def read_lectures(name: str):
        """The text layer of every PDF; pages read before and unchanged are skipped."""
        course = course_or_404(name)
        rows = lectures.lecture_rows(course)
        if not rows:
            abort(404, "This course has no lectures yet.")
        folder = course.workspace.lecture_dir
        return start_task(course, "Read the lectures",
                          [lectures.read_command(folder / r.name for r in rows)],
                          meta={"kind": "read"})

    @app.post("/c/<name>/lectures/transcribe")
    def transcribe_lectures(name: str):
        """The vision model reads the image pages: this costs money, so only on a click."""
        course = course_or_404(name)
        back = url_for("lecture_list", name=name)
        waiting = [r for r in lectures.lecture_rows(course) if r.waiting]
        if not waiting:
            return redirect(f"{back}?notice=nothing-to-transcribe", code=303)
        if lectures.vision_model(course) is None:
            return redirect(f"{back}?notice=no-vision-model", code=303)
        folder = course.workspace.lecture_dir
        pages = sum(r.waiting for r in waiting)
        return start_task(course, f"Transcribe {pages} image pages",
                          [lectures.read_command((folder / r.name for r in waiting),
                                                 vision=True)],
                          meta={"kind": "vision"})

    def new_course_page(values: dict, problems: list[str], code: int = 200):
        return render_template("new_course.html", root=app.config["ROOT"], values=values,
                               problems=problems,
                               max_mb=lectures.MAX_PDF_BYTES // (1024 * 1024)), code

    @app.get("/new-course")
    def new_course():
        return new_course_page({"folder": "", "course": ""}, [])

    @app.post("/new-course")
    def create_course():
        """Save the PDFs into the new folder's lectures/, then run `tutor.workspace init`
        (the course's venv: minutes, and the network) and read the PDFs, as one task."""
        root = app.config["ROOT"]
        values = {"folder": request.form.get("folder", ""),
                  "course": request.form.get("course", "")}
        folder, course_id, problems = lectures.check_new_course(root, values["folder"],
                                                                values["course"])
        if folder is None:
            return new_course_page(values, problems, 400)
        files = [f for f in request.files.getlist("pdf") if f.filename]
        saved: list[Path] = []
        if files:
            saved, problems = lectures.save_pdfs(folder / "lectures", files)
            if problems:
                lectures.remove_new(folder)
                return new_course_page(values, problems, 400)
        (folder / "lectures").mkdir(parents=True, exist_ok=True)
        commands = [lectures.init_command(folder, course_id)]
        if saved:
            commands.append(lectures.read_command(saved))
        env = {"WORKSPACE_DIR": str(folder), "LECTURE_DIR": str(folder / "lectures")}
        task = tasks.start(folder, f"Create {folder.name}", commands, env,
                           meta={"kind": "create"})
        return redirect(url_for("task", name=folder.name, task_id=task.id), code=303)

    # --- settings (step 6): keys in .env, a course's models in its workspace.toml -------------

    def keys_page(problems: list[str], code: int = 200, name: str = ""):
        found = courses.find_courses(app.config["ROOT"])[0]
        return render_template("keys.html", problems=problems, name=name,
                               rows=keys.rows(app.config["ENV_FILE"],
                                              llm_settings.key_users(found))), code

    @app.get("/keys/")
    def key_list():
        return keys_page([])

    @app.post("/keys/set")
    def set_key():
        """Add or replace a key. The value is never put back in a page, even when refused."""
        name = request.form.get("name", "").strip()
        problem = keys.set_key(app.config["ENV_FILE"], name, request.form.get("value", ""))
        if problem:
            return keys_page([problem], 400, name if keys.NAME.match(name) else "")
        return redirect(url_for("key_list") + "?notice=key-saved", code=303)

    @app.post("/keys/delete")
    def delete_key():
        found = courses.find_courses(app.config["ROOT"])[0]
        problem = keys.delete_key(app.config["ENV_FILE"], request.form.get("name", ""),
                                  llm_settings.key_users(found))
        if problem:
            return keys_page([problem], 400)
        return redirect(url_for("key_list") + "?notice=key-deleted", code=303)

    def models_page(course: courses.Course, problems: list[str], code: int = 200):
        others = [c for c in courses.find_courses(app.config["ROOT"])[0]
                  if c.folder != course.folder and llm_settings.section(c).get("profiles")]
        values = keys.read(app.config["ENV_FILE"])
        return render_template("models.html", course=course, problems=problems,
                               profiles=llm_settings.profiles(course),
                               roles=llm_settings.section(course).get("roles", {}),
                               groups=llm_settings.ROLE_GROUPS, others=others,
                               key_set={n for n in values} | set(os.environ)), code

    @app.get("/c/<name>/models/")
    def models(name: str):
        return models_page(course_or_404(name), [])

    def profile_page(course: courses.Course, original: str | None, values: dict,
                     problems: list[str], code: int = 200):
        return render_template("profile.html", course=course, original=original,
                               values=values, problems=problems, fields=llm_settings.FIELDS,
                               providers=list(llm.PROVIDERS)), code

    @app.get("/c/<name>/models/new")
    def new_model(name: str):
        return profile_page(course_or_404(name), None, {"name": ""}, [])

    @app.get("/c/<name>/models/m/<profile>/")
    def edit_model(name: str, profile: str):
        course = course_or_404(name)
        data = llm_settings.section(course).get("profiles", {}).get(profile)
        if not isinstance(data, dict):
            abort(404, "No such model.")
        return profile_page(course, profile, {"name": profile, **{k: str(v) for k, v in
                                                                  data.items()}}, [])

    @app.post("/c/<name>/models/save")
    def save_model(name: str):
        course = course_or_404(name)
        form = {k: request.form.get(k, "") for k in ("name", *llm_settings.FIELDS)}
        original = request.form.get("original") or None
        profile = form["name"].strip()
        data, problems = llm_settings.check_profile(course, profile, form)
        if data is not None:
            problems = llm_settings.save_profile(course, original, profile, data)
        if problems:
            return profile_page(course, original, form, problems, 400)
        return redirect(url_for("models", name=name) + "?notice=model-saved", code=303)

    @app.post("/c/<name>/models/m/<profile>/delete")
    def delete_model(name: str, profile: str):
        course = course_or_404(name)
        problems = llm_settings.delete_profile(course, profile)
        if problems:
            return models_page(course, problems, 400)
        return redirect(url_for("models", name=name) + "?notice=model-deleted", code=303)

    @app.post("/c/<name>/models/roles")
    def save_roles(name: str):
        course = course_or_404(name)
        chosen = {role: request.form.get(role, "") for role in llm_settings.ROLES
                  if role in request.form}
        problems = llm_settings.save_roles(course, chosen)
        if problems:
            return models_page(course, problems, 400)
        return redirect(url_for("models", name=name) + "?notice=roles-saved", code=303)

    @app.post("/c/<name>/models/copy")
    def copy_models(name: str):
        course = course_or_404(name)
        source = courses.get_course(app.config["ROOT"], request.form.get("source", ""))
        if source is None:
            abort(404, "No such course.")
        problems = llm_settings.copy_models(course, source)
        if problems:
            return models_page(course, problems, 400)
        return redirect(url_for("models", name=name) + "?notice=models-copied", code=303)

    @app.post("/c/<name>/models/check")
    def check_models(name: str):
        """One tiny request per model (`python -m tutor.llm check`), as a task."""
        course = course_or_404(name)
        known = [p.name for p in llm_settings.profiles(course)]
        wanted = request.form.get("profile", "")
        names = [wanted] if wanted else known
        if not names or any(n not in known for n in names):
            abort(404, "No such model.")
        title = f"Check {wanted}" if wanted else "Check the models"
        return start_task(course, title, [llm_settings.check_command(names)],
                          meta={"kind": "check"})

    # --- environment (step 7a): [requires], where tests run, the venv and the image ----------

    def environment_page(course: courses.Course, problems: list[str], code: int = 200,
                         rows: list[environment.ListRow] | None = None,
                         chosen: dict[str, str] | None = None):
        """`rows`: a refused [requires] form's rows, `chosen`: a refused draft form's ticked
        entries and their versions; both shown back as typed."""
        draft = environment.draft_view(course)
        if rows is None:
            rows = environment.list_rows(course, list(course.workspace.requires.items())
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
                               running=tasks.running(course.workspace.root)), code

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
        """The part htmx loads: envcheck in the course's venv (a process, 0.2 s warm)."""
        course = course_or_404(name)
        return render_template("_env_check.html", course=course,
                               check=environment.check(course), kind=environment.kind)

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
        table, problems = environment.check_requires(names, versions,
                                                     environment.runner(course))
        allow, import_problems = environment.check_imports(
            list(zip(names, keys, strict=False)), ticked, modules, environment.allowed(course))
        problems += import_problems
        if table is not None and allow is not None:
            problems = environment.save_requires(course, table, allow)
        if problems:
            typed = [environment.ListRow(n, v, k, modules.get(k, ""), k in ticked)
                     for n, v, k in zip(names, versions, keys, strict=False) if n or v]
            return environment_page(course, problems, 400, typed)
        return redirect(url_for("environment_view", name=name) + "?notice=requires-saved",
                        code=303)

    @app.post("/c/<name>/environment/draft")
    def draft_requires(name: str):
        """A model looks for the software in each lecture not asked yet (it may cost money,
        so only on this click); its answers wait on the page for the owner's ticks."""
        course = course_or_404(name)
        if environment.setup_model(course) is None:
            return redirect(url_for("environment_view", name=name) + "?notice=no-setup-model",
                            code=303)
        return start_task(course, "Find requirements in the lectures",
                          [environment.draft_command()], meta={"kind": "requires-draft"})

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

    # --- coverage.md (owner, 2026-11-06): the course's table, and a model's draft of it --------

    def coverage_page(course: courses.Course, problems: list[str], code: int = 200):
        found = coverage.view(course)
        return render_template("coverage.html", course=course, view=found, problems=problems,
                               missing=coverage.missing(course, found),
                               running=tasks.running(course.workspace.root)), code

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

    return app
