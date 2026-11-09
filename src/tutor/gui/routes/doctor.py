"""Doctor results, explicit model/repair tasks and bound editing on the authenticated GUI."""

from __future__ import annotations

import sys

from flask import Flask, current_app, redirect, render_template, request, url_for

from tutor.doctor_advice import transaction
from tutor.gui import doctor_page, environment, llm_settings, tasks
from tutor.gui.routes.common import course_or_404, start_task


def page(course, *, problems=(), rows=None, form=None, bound_rows=None, code=200):
    """Initial rendering performs no probes or paid calls; checks are explicitly requested."""
    role = llm_settings.section(course).get("roles", {}).get("doctor", "")
    values = {"failure": "", "profile": role,
              "second": "", "module": ""} if form is None else form
    if bound_rows is None:
        bound_rows = [*course.workspace.bounds.items(), ("", ""), ("", ""), ("", "")]
    return render_template("doctor.html", course=course, problems=problems, rows=rows, form=values,
                           profiles=llm_settings.profiles(course),
                           inputs=doctor_page.inputs(course),
                           bound_rows=bound_rows, runner=environment.runner(course),
                           retained=(course.workspace.root / transaction.RECEIPT).is_file(),
                           running=tasks.running(course.workspace.root)), code


def register(app: Flask) -> None:
    @app.get("/c/<name>/doctor/")
    def doctor_view(name):
        course = course_or_404(name)
        rows = doctor_page.report(course) if request.args.get("check") == "1" else None
        return page(course, rows=rows)

    @app.post("/c/<name>/doctor/input")
    def doctor_input(name):
        course = course_or_404(name)
        values = {k: request.form.get(k, "") for k in ("failure", "profile", "second", "module")}
        if busy := tasks.running(course.workspace.root):
            return redirect(url_for("task", name=name, task_id=busy.id) + "?notice=busy", code=303)
        try:
            identifier = doctor_page.create_input(course, **values)
            command = doctor_page.command(course, identifier, "preview",
                                          current_app.config["ENV_FILE"])
        except (ValueError, OSError) as exc:
            message = (str(exc) if isinstance(exc, ValueError)
                       else "Private input could not be saved.")
            return page(course, form=values, problems=[message], code=400)
        return start_task(course, "Preview selected doctor data", [command],
                          meta={"kind": "doctor", "doctor_input": identifier, "action": "preview"})

    @app.post("/c/<name>/doctor/input/<identifier>/<action>")
    def doctor_action(name, identifier, action):
        course = course_or_404(name)
        try:
            folder, data = doctor_page.selected(course, identifier)
            if action != "preview" and not (folder / "preview.json").is_file():
                raise ValueError("Preview the selected record before sending or verifying it.")
            if action in {"verify", "apply"} and request.form.get("confirmed") != "on":
                raise ValueError("Review and tick the temporary-install/application confirmation.")
            command = doctor_page.command(course, identifier, action,
                                          current_app.config["ENV_FILE"])
        except (ValueError, OSError, TypeError):
            return page(course, problems=["Action refused: check its input, preview "
                                          "and confirmation."],
                        code=400)
        return start_task(course, "Doctor: " + action, [command],
                          meta={"kind": "doctor", "doctor_input": identifier, "action": action})

    @app.post("/c/<name>/doctor/bounds")
    def doctor_bounds(name):
        course = course_or_404(name)
        names, specs = request.form.getlist("name"), request.form.getlist("spec")
        if problems := doctor_page.save_bounds(course, names, specs):
            return page(course, problems=problems, bound_rows=list(zip(names, specs, strict=False)),
                        code=400)
        return redirect(url_for("doctor_view", name=name) + "?notice=bounds-saved", code=303)

    @app.post("/c/<name>/doctor/retained/<action>")
    def doctor_retained(name, action):
        course = course_or_404(name)
        if action not in {"undo", "finish"} or request.form.get("confirmed") != "on":
            return page(course, problems=["Confirm restoring or discarding the retained repair."],
                        code=400)
        command = [sys.executable, "-m", "tutor", "doctor", str(course.workspace.root),
                   "--undo-repair" if action == "undo" else "--finish-repair"]
        if action == "finish":
            command.append("--confirm")
        return start_task(course, "Doctor: " + action + " retained repair", [command],
                          meta={"kind": "doctor", "action": action})
