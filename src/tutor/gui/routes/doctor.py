"""Setup-fix wizard and legacy POST actions, sharing the existing guarded workers.

GETs only read current diagnostics/results. Every preview/send/install/apply is an
explicit POST. Step guards complement (never replace) worker fingerprints and gates.
"""

from __future__ import annotations

import re
import sys

from flask import Flask, abort, current_app, redirect, render_template, request, url_for

from tutor.gui import doctor_page, fix_state, llm_settings, tasks
from tutor.gui.routes.common import course_or_404, start_task


def page(course, *, view=None, form=None, problems=(), step=1, code=200):
    """Render one bounded step; no model/profile credential loader runs in the server."""
    if form is None:
        role = llm_settings.section(course).get("roles", {}).get("doctor", "")
        form = view.form if view else {"profile": role, "second": "", "module": "", "failure": ""}
    step = min(max(step, 1), view.highest if view else 1)
    return render_template("setup_fix.html", course=course, view=view, form=form, step=step,
                           problems=problems, profiles=llm_settings.profiles(course),
                           running=tasks.running(course.workspace.root)), code


def busy(course):
    if task := tasks.running(course.workspace.root):
        return redirect(url_for("task", name=course.folder, task_id=task.id) + "?notice=busy", 303)
    return None


def register(app: Flask) -> None:
    @app.get("/c/<name>/doctor/", strict_slashes=False)
    def doctor_view(name):
        course_or_404(name)
        return redirect(url_for("environment_view", name=name) + "#host-install", 302)

    @app.get("/c/<name>/environment/fix/<identifier>")
    def environment_fix(name, identifier):
        course = course_or_404(name)
        try:
            if re.fullmatch(r"[0-9a-f]{32}", identifier):
                state = fix_state.view(course, identifier)
                return page(course, view=state,
                            step=request.args.get("step", state.highest, type=int))
            if re.fullmatch(r"[0-9a-f]{64}", identifier):
                failure, module = fix_state.seed(course, identifier)
                role = llm_settings.section(course).get("roles", {}).get("doctor", "")
                return page(course, form={"failure": failure, "module": module,
                                          "profile": role, "second": ""})
        except (OSError, ValueError, TypeError):
            abort(404, "This setup problem changed. Return to Environment and check again.")
        abort(404)

    @app.post("/c/<name>/doctor/input")
    def doctor_input(name):
        course = course_or_404(name)
        values = {k: request.form.get(k, "") for k in ("failure", "profile", "second", "module")}
        if response := busy(course):
            return response
        try:
            identifier = doctor_page.create_input(course, **values)
            command = doctor_page.command(course, identifier, "preview",
                                          current_app.config["ENV_FILE"])
        except (ValueError, OSError):
            return page(course, form=values, problems=["Choose configured profiles and a nonempty "
                        "failure of at most 64 KiB; use a plain dotted module name."], code=400)
        return start_task(course, "Preview setup fix data", [command],
                          meta={"kind": "doctor", "doctor_input": identifier, "action": "preview"})

    @app.post("/c/<name>/doctor/input/<identifier>/<action>")
    def doctor_action(name, identifier, action):
        course = course_or_404(name)
        if response := busy(course):
            return response
        state = None
        try:
            state = fix_state.view(course, identifier)
            new_package = state.advice_ready and state.advice.get("status") == "new-package-review"
            returning = 1
            if action == "prepare-independent":
                if not new_package or state.verified or state.applied:
                    raise ValueError("Independent review is only for a newly proposed package.")
                fix_state.choose_second(course, identifier, request.form.get("second", ""))
                action, returning = "preview", 2
            elif action == "advice":
                if not state.preview_ready:
                    raise ValueError("Preview the complete selected data before sending.")
            elif action == "independent":
                if not new_package or not state.form["second"] or not state.preview_ready:
                    raise ValueError("Choose a second profile and preview both destinations first.")
            elif action == "verify":
                if (not state.advice_ready or state.advice.get("status") != "accepted"
                        or not state.preview_ready or not state.form["module"] or state.verified
                        or request.form.get("confirmed") != "on"):
                    raise ValueError("Review permitted advice, fill the module "
                                     "and confirm testing.")
            elif action == "repair-preview":
                if not state.verified or state.applied:
                    raise ValueError("The temporary check must pass before showing changes.")
            elif action == "apply":
                if (not state.diff_ready or state.applied or state.retained
                        or request.form.get("confirmed") != "on"):
                    raise ValueError("Review the complete changes and confirm applying them.")
            elif action != "preview":
                raise ValueError("Unknown setup action.")
            command = doctor_page.command(course, identifier, action,
                                          current_app.config["ENV_FILE"])
        except (ValueError, OSError, TypeError) as exc:
            message = (str(exc) if isinstance(exc, ValueError)
                       else "The setup record is unavailable.")
            return page(course, view=state, problems=[message],
                        step=state.highest if state else 1, code=400)
        return start_task(course, "Setup fix: " + action, [command],
                          meta={"kind": "doctor", "doctor_input": identifier, "action": action,
                                "fix_step": returning})

    @app.post("/c/<name>/doctor/retained/<action>")
    def doctor_retained(name, action):
        course = course_or_404(name)
        if response := busy(course):
            return response
        identifier = request.form.get("input", "")
        state = None
        try:
            if identifier:
                state = fix_state.view(course, identifier)
            if action not in {"undo", "finish"} or request.form.get("confirmed") != "on":
                raise ValueError("Confirm Undo or Keep it before continuing.")
        except (ValueError, OSError, TypeError):
            return page(course, view=state, problems=["Confirm Undo or Keep it."],
                        step=4, code=400)
        command = [sys.executable, "-m", "tutor", "doctor", str(course.workspace.root),
                   "--undo-repair" if action == "undo" else "--finish-repair"]
        if action == "finish":
            command.append("--confirm")
        return start_task(course, "Setup fix: " + ("Undo" if action == "undo" else "Keep it"),
                          [command], meta={"kind": "doctor", "action": action,
                                           "doctor_input": identifier})
