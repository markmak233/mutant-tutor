"""The Lectures page (GUI step 5): uploaded files or a whole folder to tick, read into
the cache as a task, and the image pages sent to the vision model on a click.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, lectures, tasks
from tutor.gui.i18n import N_
from tutor.gui.routes.common import UPLOAD_LIMIT_BYTES, course_or_404, start_task


def lectures_page(course: courses.Course, problems: list[str], code: int = 200):
    rows = lectures.lecture_rows(course)
    return render_template("lectures.html", course=course, rows=rows, problems=problems,
                           vision=lectures.vision_model(course),
                           max_mb=lectures.MAX_LECTURE_BYTES // (1024 * 1024),
                           limit_mb=UPLOAD_LIMIT_BYTES // (1024 * 1024),
                           running=tasks.running(course.workspace.root)), code


def read_saved(course: courses.Course, saved: list[Path]):
    """Read the text of the lectures just saved (free), as a task."""
    title = f"Read {saved[0].name}" if len(saved) == 1 else f"Read {len(saved)} lectures"
    return start_task(course, title, [lectures.read_command(saved)],
                      meta={"kind": "read"})


def staged_or_404(course: courses.Course, token: str) -> lectures.StagedFolder:
    staged = lectures.staged_folder(course.workspace.root, token)
    if staged is None:
        abort(404, N_("This folder's files are no longer kept: they were saved, or another "
                   "folder was chosen since. Choose it again on the Lectures page."))
    return staged


def folder_page(course: courses.Course, staged: lectures.StagedFolder, picked: set[int],
                problems: list[str], code: int = 200):
    return render_template("lecture_folder.html", course=course, staged=staged,
                           picked=picked, problems=problems), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/c/<name>/lectures/")
    def lecture_list(name: str):
        return lectures_page(course_or_404(name), [])

    @app.post("/c/<name>/lectures/upload")
    def upload_lectures(name: str):
        """Save the lecture files, all or none, then read their text (free) as a task."""
        course = course_or_404(name)
        saved, problems = lectures.save_lectures(course.workspace.lecture_dir,
                                                 request.files.getlist("lectures"))
        if problems:
            return lectures_page(course, problems, 400)
        return read_saved(course, saved)

    @app.post("/c/<name>/lectures/folder")
    def stage_lecture_folder(name: str):
        """Keep a chosen folder's PDFs and web pages aside, then show them to tick: a folder
        holds what is no lecture too (exams, solutions), and a lecture is not deleted here."""
        course = course_or_404(name)
        request.max_form_parts = lectures.MAX_FOLDER_FILES  # a folder sends every file in it
        token, problems = lectures.stage_folder(course.workspace.root,
                                                course.workspace.lecture_dir,
                                                request.files.getlist("folder"))
        if token is None:
            return lectures_page(course, problems, 400)
        return redirect(url_for("lecture_folder", name=name, token=token), code=303)

    @app.get("/c/<name>/lectures/folder/<token>/")
    def lecture_folder(name: str, token: str):
        """The staged folder's lectures, each ticked unless it cannot be saved."""
        course = course_or_404(name)
        staged = staged_or_404(course, token)
        return folder_page(course, staged,
                           {f.index for f in staged.files if f.problem is None}, [])

    @app.post("/c/<name>/lectures/folder/<token>/tick")
    def tick_lecture_folder(name: str, token: str):
        """Tick all, none, the others, or one subfolder's files: the same page with the ticks
        the form sent, changed (lectures.ticks). Nothing is saved."""
        course = course_or_404(name)
        staged = staged_or_404(course, token)
        picked = lectures.ticks(staged, request.form.getlist("pick"),
                                request.form.get("tick", ""))
        return folder_page(course, staged, picked, [])

    @app.post("/c/<name>/lectures/folder/<token>/save")
    def save_lecture_folder(name: str, token: str):
        """Save the ticked files as lectures, all or none, then read them as an upload is."""
        course = course_or_404(name)
        staged = staged_or_404(course, token)
        picks = request.form.getlist("pick")
        saved, problems = lectures.save_staged(course.workspace.root,
                                               course.workspace.lecture_dir, staged, picks)
        if problems:
            return folder_page(course, staged,
                               {f.index for f in staged.files if str(f.index) in picks},
                               problems, 400)
        return read_saved(course, saved)

    @app.post("/c/<name>/lectures/folder/<token>/cancel")
    def cancel_lecture_folder(name: str, token: str):
        course = course_or_404(name)
        lectures.discard_staged(course.workspace.root, token)
        return redirect(url_for("lecture_list", name=name) + "?notice=folder-cancelled",
                        code=303)

    @app.post("/c/<name>/lectures/read")
    def read_lectures(name: str):
        """The text of every lecture; pages read before and unchanged are skipped."""
        course = course_or_404(name)
        rows = lectures.lecture_rows(course)
        if not rows:
            abort(404, N_("This course has no lectures yet."))
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
