"""Generation (GUI step 4): the new-batch form starts the CLI's `plan`, and the plan
page approves, sends back, continues or tries an exercise again with `resume`.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import courses, environment, generation, plan_items, plan_selection, tasks
from tutor.gui.routes.common import (
    C_NEEDS_CONTAINER,
    NOTICES,
    act,
    course_or_404,
    start_task,
    waiting_for,
)


def new_batch_page(course: courses.Course, values: dict, problems: list[str], code: int):
    ready, unread = generation.lectures(course)
    shown = {"label": "", "lectures": [], "count": "", "prefs": "",
             **generation.defaults(course), **values}
    return render_template("new_batch.html", course=course, ready=ready, unread=unread,
                           # Lectures coverage.md has nothing for: marked, as a plan for them
                           # comes from the page titles alone (owner, 2026-11-10).
                           no_coverage=set(generation.coverage_missing(course, ready)),
                           values=shown, problems=problems,
                           models=generation.models(course),
                           running=tasks.running(course.workspace.root),
                           c_blocked=environment.c_without_container(course)), code


def plan_or_404(name: str, label: str) -> tuple[courses.Course, generation.PlanView]:
    course = course_or_404(name)
    view = generation.plan_view(course, label)
    if view is None:
        abort(404, "No such plan.")
    return course, view


def plan_page(template: str, name: str, label: str, error: str = "", checked=None):
    course, view = plan_or_404(name, label)
    log, cut = tasks.tail(view.task) if view.task else ("", False)
    quiet = tasks.quiet_s(view.task) if view.task else None
    return render_template(template, course=course, plan=view, log=log, cut=cut,
                           models=plan_selection.effective_models(course, view),
                           explain=generation.EXPLAIN, selection_error=error,
                           checked=checked, course_busy=tasks.running(course.workspace.root),
                           statuses=plan_items.item_statuses(course, view), quiet=quiet,
                           retry_cap=plan_items.RETRY_CAP,
                           # While the plan waits for approval: the lectures it was made for
                           # that coverage.md has nothing on (owner, 2026-11-10).
                           no_coverage=generation.coverage_missing(
                               course, view.source_lectures) if view.can_approve else [],
                           waiting_for=waiting_for(course, view.task) if quiet else "")


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


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""
    launcher = app.config["LAUNCHER"]  # what opens folders and VS Code

    @app.get("/c/<name>/new-batch")
    def new_batch(name: str):
        return new_batch_page(course_or_404(name), {}, [], 200)

    @app.post("/c/<name>/new-batch")
    def start_plan(name: str):
        """`plan`: the plan model proposes the batch, and the command pauses for the owner."""
        course = course_or_404(name)
        form = request.form
        if environment.c_without_container(course):
            # Every draft would fail rule `safe` (C runs only in a container), so nothing
            # starts; `tutor.graph` stops there too, before any model is asked.
            return new_batch_page(course, {}, [C_NEEDS_CONTAINER], 409)
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

    @app.get("/c/<name>/plans/<label>/")
    def plan(name: str, label: str):
        return plan_page("plan.html", name, label)

    @app.get("/c/<name>/plans/<label>/live")
    def plan_live(name: str, label: str):
        """The part of the plan page that htmx fetches every second while a task runs."""
        return plan_page("_plan_live.html", name, label)

    @app.post("/c/<name>/plans/<label>/approve")
    def approve_plan(name: str, label: str):
        """Generate the batch from plan.json as it is now; this is what spends money."""
        course, view = plan_or_404(name, label)
        if not view.can_review:
            return redirect(url_for("plan", name=name, label=label) + "?notice=plan-moved",
                            code=303)
        try:
            selected = plan_selection.posted_selection(course, view, request.form)
        except (ValueError, OSError) as exc:
            # Stale pages require a fresh all-checked review; other errors keep the ticks.
            checked = (None if request.form.get("plan_digest") != view.plan_digest
                       else request.form.getlist("item"))
            return plan_page("plan.html", name, label, str(exc), checked), 400
        command = generation.resume_command(label, approve=True, selected=selected,
                                            plan_digest=view.plan_digest,
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
        return plan_task(name, label, view.can_review, f"Revise plan {label}",
                         generation.resume_command(label, feedback=feedback,
                                                  python=courses.task_python(course)),
                         generation.FEEDBACK, course)

    @app.post("/c/<name>/plans/<label>/continue")
    def continue_plan(name: str, label: str):
        course, view = plan_or_404(name, label)
        return plan_task(name, label, view.can_continue, f"Continue {label}",
                         generation.resume_command(label, python=courses.task_python(course)),
                         generation.CONTINUE, course, validate=True)

    @app.post("/c/<name>/plans/<label>/retry")
    def retry_item(name: str, label: str):
        """Generate one exercise that did not succeed again, then validate it (owner,
        2026-11-06): only one the plan page shows as discarded or failed, and only while no
        task of this plan runs; it costs money, as approving does. "Try again with a note"
        posts a note too, added to the exercise's notes for this try (owner, 2026-11-07, after
        a retry failed the same way, as nothing about it had changed). `cap` (owner,
        2026-11-10): the one larger output cap, plan_items.RETRY_CAP, offered when a round's
        reply was cut off at the batch's cap; any other value is refused, and it is taken
        only for an item whose rounds were cut off (the page offers it nowhere else)."""
        course, view = plan_or_404(name, label)
        exercise = request.form.get("item", "")
        back = url_for("plan", name=name, label=label)
        note = generation.clean_text(request.form.get("note", ""))
        if note is None:
            return redirect(f"{back}?notice=note-bad", code=303)
        cap = request.form.get("cap", "")
        if cap not in ("", str(plan_items.RETRY_CAP)):
            return redirect(f"{back}?notice=cap-bad", code=303)
        status = plan_items.item_statuses(course, view).get(exercise)
        running = tasks.running(course.workspace.root)
        if status is None or not status.can_retry or running or view.selection_error:
            return redirect(f"{back}?notice=plan-moved", code=303)
        if cap and not status.cut_off_rounds:
            return redirect(f"{back}?notice=plan-moved", code=303)
        if problem := courses.container_problem(course):
            return redirect(f"{back}?notice={problem}", code=303)
        commands = plan_items.prepare_retry(course, view, exercise, courses.task_python(course),
                                            note=note,
                                            max_tokens=plan_items.RETRY_CAP if cap else None)
        if commands is None:
            abort(404, "The plan has no such exercise.")
        title = (f"Generate {exercise} again" + (" with a note" if note else "")
                 + (f" and a {plan_items.RETRY_CAP:,} output cap" if cap else ""))
        return start_task(course, title, commands,
                          meta={"kind": generation.RETRY, "label": label, "item": exercise},
                          then=back)

    @app.post("/c/<name>/plans/<label>/retry-all")
    def retry_all(name: str, label: str):
        """Every exercise of the plan that did not come out, generated again one after
        another in one task, each then validated (owner, 2026-11-06)."""
        course, view = plan_or_404(name, label)
        back = url_for("plan", name=name, label=label)
        failed = [k for k, v in plan_items.item_statuses(course, view).items() if v.can_retry]
        if not failed or tasks.running(course.workspace.root) or view.selection_error:
            return redirect(f"{back}?notice=plan-moved", code=303)
        if problem := courses.container_problem(course):
            return redirect(f"{back}?notice={problem}", code=303)
        python = courses.task_python(course)
        commands = []
        for exercise in failed:
            commands += plan_items.prepare_retry(course, view, exercise, python) or []
        if not commands:
            abort(404, "The plan has none of these exercises.")
        return start_task(course, f"Generate {len(failed)} exercises again", commands,
                          meta={"kind": generation.RETRY, "label": label, "items": failed},
                          then=back)

    @app.post("/c/<name>/plans/<label>/generate-item")
    def generate_left_out(name: str, label: str):
        """Explicitly authorize one deferred row, only after the selected run completes."""
        course, view = plan_or_404(name, label)
        back = url_for("plan", name=name, label=label)
        exercise = request.form.get("item", "")
        status = plan_items.item_statuses(course, view).get(exercise)
        if (view.selection_error or not view.generation_complete or status is None
                or not status.can_generate or tasks.running(course.workspace.root)):
            return redirect(f"{back}?notice=plan-moved", code=303)
        if problem := courses.container_problem(course):
            return redirect(f"{back}?notice={problem}", code=303)
        # Status revalidates current schema/source/collisions against the approved snapshot.
        commands = plan_items.prepare_retry(course, view, exercise, courses.task_python(course))
        if commands is None:
            abort(404, "The approved plan has no such item.")
        return start_task(course, f"Generate {exercise}", commands,
                          meta={"kind": generation.RETRY, "label": label, "item": exercise},
                          then=back)

    @app.post("/c/<name>/plans/<label>/edit")
    def edit_plan(name: str, label: str):
        """plan.json in VS Code: the CLI approves the file as the owner left it."""
        course, view = plan_or_404(name, label)
        if not view.can_review:
            return redirect(url_for("plan", name=name, label=label) + "?notice=plan-moved",
                            code=303)
        if view.draft is None or not (view.draft / "plan.json").is_file():
            abort(404, "This plan has no plan.json yet.")
        return act(lambda: launcher.open_in_vscode(course.workspace.root,
                                                   view.draft / "plan.json"),
                   url_for("plan", name=name, label=label), done="opened-plan")
