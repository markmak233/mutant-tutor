"""New course: `tutor.workspace init` on a new folder, as a task.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, current_app, redirect, render_template, request, url_for

from tutor.gui import lectures, tasks


def new_course_page(values: dict, problems: list[str], code: int = 200):
    return render_template("new_course.html", root=current_app.config["ROOT"], values=values,
                           problems=problems), code


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/new-course")
    def new_course():
        return new_course_page({"folder": "", "course": ""}, [])

    @app.post("/new-course")
    def create_course():
        """Run `tutor.workspace init` (the course's venv: minutes, and the network) on a new
        folder, as a task. No lectures here (owner, 2026-11-07): the Lectures page adds them,
        as files or a whole folder to tick, once the course exists."""
        root = app.config["ROOT"]
        values = {"folder": request.form.get("folder", ""),
                  "course": request.form.get("course", "")}
        folder, course_id, problems = lectures.check_new_course(root, values["folder"],
                                                                values["course"])
        if folder is None:
            return new_course_page(values, problems, 400)
        (folder / "lectures").mkdir(parents=True)  # the folder is new: check_new_course
        env = {"WORKSPACE_DIR": str(folder), "LECTURE_DIR": str(folder / "lectures")}
        task = tasks.start(folder, f"Create {folder.name}",
                           [lectures.init_command(folder, course_id)], env,
                           meta={"kind": "create"})
        return redirect(url_for("task", name=folder.name, task_id=task.id), code=303)
