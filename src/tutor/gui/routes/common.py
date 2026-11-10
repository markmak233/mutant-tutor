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

# How each state reads on a page, in the report's order.
STATUS_LABELS = {report.PASSED: "passed", report.STARTED: "started",
                 report.UNLOGGED: "changed, no run logged", report.NOT_STARTED: "not started"}
# What an action can report back, by code; nothing else is ever shown. (kind, text)
NOTICES = {
    "bounds-saved": ("ok", "Package bounds saved. Install/rebuild and check the course again."),
    "opened-folder": ("ok", "Opened in the file manager."),
    "opened-ide": ("ok", "Opened in VS Code: the course folder, with this exercise's student "
                          "file."),
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
    "note-bad": ("bad", "The note was not used: write plain text, 2000 characters at most, "
                        "or leave it empty to try again without one."),
    "cap-bad": ("bad", "Nothing started: the output cap can only be the one offered."),
    "catalog-fetched": ("ok", "The list from models.dev is up to date: choose a provider."),
    "folder-cancelled": ("ok", "Nothing was saved from the folder, and its files are no "
                               "longer kept."),
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
    "python-not-offered": ("bad", "Nothing was started: that Python is not one step 2's check "
                                  "offers now. Check again, then choose from its list."),
    "cannot-build": ("bad", "[requires] has no image recipe: see the test image part below."),
    "no-setup-model": ("bad", "Nothing was started: this course has no model for it. Choose "
                              "one for the setup, plan or generate role on the Models page."),
    "language-saved": ("ok", "Saved in workspace.toml (the file before is workspace.toml.bak), "
                             "and VS Code's C settings in .vscode/c_cpp_properties.json. A C "
                             "course's tests need the container and a test image with gcc: "
                             "steps 3 and 4 below."),
    "language-saved-own-editor": ("ok", "Saved in workspace.toml (the file before is "
                                        "workspace.toml.bak). .vscode/c_cpp_properties.json is "
                                        "this course's own, so it was left as it is: set its "
                                        "cStandard to match the flags. A C course's tests need "
                                        "the container and a test image with gcc: steps 3 and "
                                        "4 below."),
    "language-removed": ("ok", "C is no longer one of this course's languages, and gcc is no "
                               "longer in [requires] (the file before is workspace.toml.bak)."),
    "language-added": ("ok", "C added: [languages.c] with the compiler flags below, gcc in "
                             "[requires], and VS Code's C settings in "
                             ".vscode/c_cpp_properties.json (the file before is "
                             "workspace.toml.bak). Check C's settings below; its tests need the "
                             "container and a test image with gcc: steps 3 and 4."),
}
# What a task that succeeded reports when it goes back to its page (app.return_to).
NOTICES.update({
    "lectures-read": ("ok", "Finished: the table shows what the cache holds now."),
    "validated": ("ok", "Validation finished: every exercise passed it."),
    "models-checked": ("ok", "Every model answered the check."),
    "installed": ("ok", "Installed into .venv: step 2's check below is fresh."),
    "image-built": ("ok", "The test image is built and checked: step 4 below."),
    "image-recorded": ("ok", "Current image versions recorded; "
                             "see step 4 below for the version comparison."),
    "draft-ready": ("ok", "The lectures were looked at: tick what to add in step 1."),
    "coverage-drafted": ("ok", "The draft is ready below: check it against the slides."),
    "coverage-used": ("ok", "coverage.md is written from the draft. Edit it as batches are made."),
    "opened-coverage": ("ok", "Opened in VS Code. Reload this page to see your changes."),
    "coverage-added": ("ok", "The ticked lectures were added at the end of coverage.md; nothing "
                             "else in it changed (the file before is coverage.md.bak)."),
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


def waiting_for(course: courses.Course, task: tasks.Task) -> str:
    """What a silent running task is most likely waiting for, by its kind, as a quiet
    task's line names it (tasks.quiet_s); models by name, never a key."""
    kind = task.meta.get("kind")
    models = dict(generation.models(course))
    if kind in (generation.PLAN, generation.FEEDBACK):
        return f"the plan model, {models.get('plan', 'the generate model')}"
    if kind in (generation.APPROVE, generation.CONTINUE, generation.RETRY):
        return (f"the models writing and checking exercises (generate: "
                f"{models.get('generate', '?')})")
    if kind in ("requires-draft", "coverage-draft"):
        return f"the model reading the lectures, {environment.setup_model(course) or '?'}"
    if kind == "vision":
        return f"the vision model, {lectures.vision_model(course) or '?'}"
    return {"check": "the models being checked", "install": "pip, which may be downloading",
            "create": "pip, which may be downloading", "build": "docker build, which may be "
            "downloading", "validate": "the exercises' test runs",
            "read": "the lectures being read"}.get(kind, "the command")
