"""What every page area of the GUI shares: the notices an action may report, how a request
finds its course, batch or exercise (404 otherwise), and how an action starts a task or calls
the launcher and goes back to its page.

The helpers read the app's settings through `current_app` (the app answering the request), so
the route modules in this package can be plain modules, each with a `register(app)`.
"""

from __future__ import annotations

from flask import abort, current_app, redirect, url_for

from tutor.eval import report
from tutor.gui import courses, environment, generation, launch, lectures, tasks
from tutor.gui.i18n import N_
from tutor.gui.i18n import gettext_ as _

# How each state reads on a page, in the report's order.
STATUS_LABELS = {report.PASSED: N_("passed"), report.STARTED: N_("started"),
                 report.UNLOGGED: N_("changed, no run logged"),
                 report.NOT_STARTED: N_("not started")}  # shown through _() by the pages
# What an action can report back, by code; nothing else is ever shown. (kind, text)
NOTICES = {
    "bounds-saved": ("ok", N_("Package bounds saved. Install/rebuild and check the course again.")),
    "opened-folder": ("ok", N_("Opened in the file manager.")),
    "opened-ide": ("ok", N_("Opened in VS Code: the course folder, with this exercise's student "
                          "file.")),
    "no-vscode": ("bad", N_("VS Code was not found. Install it, or in VS Code run "
                         "\"Shell Command: Install 'code' command in PATH\".")),
    "no-file-manager": ("bad", N_("No file manager command (xdg-open) was found.")),
    "failed": ("bad", N_("It could not be started.")),
    "tests-passed": ("ok", N_("Your tests passed in the course image; the attempt is recorded.")),
    "found-allowed": ("ok", N_("Added to [safety]: the exercises may use what you ticked.")),
    "tests-host-course": ("bad", N_("This course runs its tests on the host: run pytest in the "
                                 "exercise folder from the course's terminal.")),
    "queued": ("ok", N_("Queued: it starts when the course's running task ends (a course runs "
                     "one at a time). Until then it can be removed from the course page.")),
    "removed": ("ok", N_("Removed from the queue; nothing ran.")),
    "not-queued": ("bad", N_("That task is not waiting any more: it has started or ended.")),
    "stopping": ("ok", N_("Stopping: every process of the task got Ctrl+C, and what is still "
                       "running after 10 s is killed.")),
    "not-running": ("bad", N_("This task is not running.")),
    "opened-plan": ("ok", N_("Opened plan.json in VS Code. Save it there; approving uses the "
                          "saved file. Reload this page to see your changes.")),
    "feedback-empty": ("bad", N_("Write what to change first: plain text, 2000 characters at "
                              "most.")),
    "plan-moved": ("bad", N_("The batch is no longer at that step; this page shows where it is "
                          "now.")),
    "note-bad": ("bad", N_("The note was not used: write plain text, 2000 characters at most, "
                        "or leave it empty to try again without one.")),
    "cap-bad": ("bad", N_("Nothing started: the output cap can only be the one offered.")),
    "catalog-fetched": ("ok", N_("The list from models.dev is up to date: choose a provider.")),
    "folder-cancelled": ("ok", N_("Nothing was saved from the folder, and its files are no "
                               "longer kept.")),
    "key-saved": ("ok", N_("Saved in .env. Commands started from now on use it.")),
    "key-deleted": ("ok", N_("Removed from .env.")),
    "model-saved": ("ok", N_("Saved in workspace.toml (the file before is workspace.toml.bak).")),
    "model-deleted": ("ok", N_("Removed from workspace.toml (the file before is "
                            "workspace.toml.bak).")),
    "roles-saved": ("ok", N_("Roles saved in workspace.toml (the file before is "
                          "workspace.toml.bak).")),
    "models-copied": ("ok", N_("Models copied into workspace.toml. Keys are shared through "
                            ".env, so they work here too.")),
    "nothing-to-transcribe": ("ok", N_("No image page is waiting for the vision model.")),
    "no-vision-model": ("bad", N_("No vision model is set: add `vision = \"<profile>\"` to "
                               "[llm.roles] in workspace.toml.")),
    # Before a task that runs tests: nothing was started (courses.container_problem).
    "docker-missing": ("bad", N_("Nothing was started: Docker is not installed (no `docker` "
                              "command), and tests in a container need it. Install Docker "
                              "Desktop (docker.com), start it, then try again.")),
    "docker-stopped": ("bad", N_("Nothing was started: Docker Desktop is not running, or not "
                              "answering. Start it, wait until it says it is running, then try "
                              "again.")),
    "image-missing": ("bad", N_("Nothing was started: this course's test image is missing or out "
                             "of date. Build it on the course's Environment page, then try "
                             "again.")),
    "requires-saved": ("ok", N_("[requires] saved in workspace.toml (the file before is "
                             "workspace.toml.bak). Install into .venv, and build the image if "
                             "tests run in the container, so both match it.")),
    "runner-saved": ("ok", N_("Saved in workspace.toml (the file before is workspace.toml.bak). "
                           "Validation and generation started from now on use it.")),
    "no-venv": ("bad", N_("This course has no .venv yet: install into .venv first.")),
    "python-not-offered": ("bad", N_("Nothing was started: that Python is not one step 2's check "
                                  "offers now. Check again, then choose from its list.")),
    "cannot-build": ("bad", N_("[requires] has no image recipe: see the test image part below.")),
    "no-setup-model": ("bad", N_("Nothing was started: this course has no model for it. Choose "
                              "one for the setup, plan or generate role on the Models page.")),
    "language-saved": ("ok", N_("Saved in workspace.toml (the file before is workspace.toml.bak), "
                             "and VS Code's C settings in .vscode/c_cpp_properties.json. A C "
                             "course's tests need the container and a test image with gcc: "
                             "steps 3 and 4 below.")),
    "language-saved-own-editor": ("ok", N_("Saved in workspace.toml (the file before is "
                                        "workspace.toml.bak). .vscode/c_cpp_properties.json is "
                                        "this course's own, so it was left as it is: set its "
                                        "cStandard to match the flags. A C course's tests need "
                                        "the container and a test image with gcc: steps 3 and "
                                        "4 below.")),
    "language-removed": ("ok", N_("C is no longer one of this course's languages, and gcc is no "
                               "longer in [requires] (the file before is workspace.toml.bak).")),
    "language-added": ("ok", N_("C added: [languages.c] with the compiler flags below, gcc in "
                             "[requires], and VS Code's C settings in "
                             ".vscode/c_cpp_properties.json (the file before is "
                             "workspace.toml.bak). Check C's settings below; its tests need the "
                             "container and a test image with gcc: steps 3 and 4.")),
}
# What a task that succeeded reports when it goes back to its page (app.return_to).
NOTICES.update({
    "no-language-name": ("bad", N_("Choose a language from the list, or name one, e.g. 中文.")),
    "language-deleted": ("ok", N_("The language's file is deleted; English shows where it was "
                                  "in use.")),
    "no-translate-model": ("bad", N_("Nothing was started: choose a model. A course gets one on "
                                     "its Models page.")),
    "lectures-read": ("ok", N_("Finished: the table shows what the cache holds now.")),
    "validated": ("ok", N_("Validation finished: every exercise passed it.")),
    "models-checked": ("ok", N_("Every model answered the check.")),
    "installed": ("ok", N_("Installed into .venv: step 2's check below is fresh.")),
    "image-built": ("ok", N_("The test image is built and checked: step 4 below.")),
    "image-recorded": ("ok", N_("Current image versions recorded; "
                             "see step 4 below for the version comparison.")),
    "draft-ready": ("ok", N_("The lectures were looked at: tick what to add in step 1.")),
    "coverage-drafted": ("ok", N_("The draft is ready below: check it against the slides.")),
    "coverage-used": ("ok", N_("coverage.json is made from the draft, and coverage.md rendered "
                            "from it. Edit points here as batches are made.")),
    "coverage-converted": ("ok", N_("coverage.md was read into coverage.json; the file before is "
                                 "coverage.md.bak. Check the lines the conversion flagged.")),
    "coverage-rendered": ("ok", N_("coverage.md was written again from the data.")),
    "coverage-saved": ("ok", N_("Saved in coverage.json; coverage.md was rendered again.")),
    "opened-coverage": ("ok", N_("Opened in VS Code. Reload this page to see your changes.")),
})
ENVIRONMENT_NOTICES = {"install": "installed", "build": "image-built",
                       "requires-draft": "draft-ready", "image-record": "image-recorded"}
RECENT_TASKS = 5
C_NEEDS_CONTAINER = ("A C course's exercises are validated only in a container, so a batch "
                     "starts once the course runs its tests in one: choose it on the "
                     "Environment page (step 3) and build the test image (step 4).")
UPLOAD_LIMIT_BYTES = 500 * 1024 * 1024  # one upload request, all its files together


def course_or_404(name: str) -> courses.Course:
    course = courses.get_course(current_app.config["ROOT"], name)
    if course is None:
        abort(404, N_("No such course."))
    return course


def batch_or_404(name: str, batch: str) -> tuple[courses.Course, courses.BatchView]:
    course = course_or_404(name)
    view = courses.batch_view(course, batch)
    if view is None:
        abort(404, N_("No such batch."))
    return course, view


def exercise_or_404(name: str, batch: str, exercise: str):
    """Only an exercise the batch's manifest lists, as on its page."""
    course, view = batch_or_404(name, batch)
    row = next((r for r in view.exercises if r.id == exercise), None)
    folder = courses.exercise_dir(course, batch, exercise)
    if row is None or folder is None:
        abort(404, N_("No such exercise."))
    return course, view, row, folder


def act(action, back: str, done: str = "opened-folder"):
    """Run one launcher call, then go back to the page with what happened: `done`, or the
    LaunchError's code. Both are keys of NOTICES."""
    try:
        action()
    except launch.LaunchError as e:
        done = str(e)
    return redirect(f"{back}?notice={done}", code=303)


def start_task(course: courses.Course, title: str, commands: list[list[str]],
               meta: dict | None = None, then: str | None = None):
    """Start the task, or queue it behind the course's running one (tasks.start), then go
    to `then` (default: the task's page), with a notice when it was queued. `meta`: see
    tasks.Task."""
    env = {"WORKSPACE_DIR": str(course.workspace.root),
           "LECTURE_DIR": str(course.workspace.lecture_dir)}
    task = tasks.start(course.workspace.root, title, commands, env, meta=meta)
    target = then or url_for("task", name=course.folder, task_id=task.id)
    if task.queued:
        return redirect(f"{target}{'&' if '?' in target else '?'}notice=queued", code=303)
    return redirect(target, code=303)


def waiting_for(course: courses.Course, task: tasks.Task) -> str:
    """What a silent running task is most likely waiting for, by its kind, as a quiet
    task's line names it (tasks.quiet_s); models by name, never a key."""
    kind = task.meta.get("kind")
    models = dict(generation.models(course))
    if kind in (generation.PLAN, generation.FEEDBACK):
        return (_("the plan model, %(model)s")
                % {"model": models.get("plan", _("the generate model"))})
    if kind in (generation.APPROVE, generation.CONTINUE, generation.RETRY):
        return (_("the models writing and checking exercises (generate: %(model)s)")
                % {"model": models.get("generate", "?")})
    if kind in ("requires-draft", "coverage-draft"):
        return (_("the model reading the lectures, %(model)s")
                % {"model": environment.setup_model(course) or "?"})
    if kind == "vision":
        return _("the vision model, %(model)s") % {"model": lectures.vision_model(course) or "?"}
    return _({"check": N_("the models being checked"),
              "timing": N_("the model being measured, about sixteen calls"),
              "install": N_("pip, which may be downloading"),
              "create": N_("pip, which may be downloading"),
              "build": N_("docker build, which may be downloading"),
              "validate": N_("the exercises' test runs"),
              "read": N_("the lectures being read")}.get(kind, N_("the command")))
