"""A course's Models page (GUI step 6): profiles and roles in its workspace.toml's
[llm], the New model form filled from models.dev, and a check of every model.

Routes are registered on the app by `register` (see gui/routes/__init__.py)."""

from __future__ import annotations

import dataclasses
import os

from flask import Flask, abort, current_app, redirect, render_template, request, url_for

from tutor import llm
from tutor.gui import catalog, courses, keys, llm_settings
from tutor.gui.i18n import N_
from tutor.gui.routes.common import course_or_404, start_task


def models_page(course: courses.Course, problems: list[str], code: int = 200,
                unsaved: dict[str, str] | None = None):
    """The models and roles; `unsaved`: roles chosen but refused or held back by a
    warning, kept in the form with what stopped them just above Save."""
    others = [c for c in courses.find_courses(current_app.config["ROOT"])[0]
              if c.folder != course.folder and llm_settings.section(c).get("profiles")]
    saved = llm_settings.section(course).get("roles", {})
    roles = unsaved if unsaved is not None else saved
    found = catalog.load(current_app.config["ROOT"])
    warnings = llm_settings.role_warnings(course, roles, found)
    return render_template("models.html", course=course, problems=problems,
                           profiles=llm_settings.profiles(course), roles=roles,
                           warnings=[] if unsaved is not None else warnings,
                           check_warnings=warnings if unsaved is not None else [],
                           check_problems=problems, check_confirm=False,
                           makers=llm_settings.profile_makers(course, found),
                           unsaved=unsaved is not None,
                           groups=llm_settings.ROLE_GROUPS, others=others,
                           key_set=key_names(), measurable=llm_settings.measurable), code


def profile_page(course: courses.Course, original: str | None, values: dict,
                 problems: list[str], code: int = 200, shelf: dict | None = None):
    return render_template("profile.html", course=course, original=original,
                           values=values, problems=problems, fields=llm_settings.FIELDS,
                           providers=list(llm.PROVIDERS), shelf=shelf,
                           common=catalog.COMMON, local=catalog.LOCAL,
                           defaults=llm_settings.provider_defaults()), code


def applied() -> dict | None:
    """The form as Apply sent it back (a GET of every field, after the provider was
    changed), so the page shows the fields that provider needs; None without Apply."""
    if not request.args.get("apply"):
        return None
    values = {k: request.args.get(k, "") for k in llm_settings.FORM_FIELDS}
    # A provider's own address was shown for the provider before: the new one shows
    # its own instead. An address typed in (z.ai for glm, say) stays.
    if any(url and catalog.same_url(values["base_url"], url)
           for url, _ in llm.PROVIDERS.values()):
        values["base_url"] = ""
    return values


def key_names() -> set[str]:
    """The keys set, by name: in .env, or in the environment the GUI started from."""
    return set(keys.read(current_app.config["ENV_FILE"])) | set(os.environ)


def register(app: Flask) -> None:
    """Add this module's routes to `app`; their endpoint names are their function names."""

    @app.get("/c/<name>/models/")
    def models(name: str):
        return models_page(course_or_404(name), [])

    @app.get("/c/<name>/models/new")
    def new_model(name: str):
        """A blank form, with the models.dev catalog above it (catalog.py): `catalog` picks
        a provider, whose models are listed, `model` one of them, which fills the form, and
        `all` lists every model instead of the newest. A server on this machine (LOCAL) is
        asked for its own models, with no key, at its default address or the one typed
        in beside the choice (`at`: another port, say; owner, 2026-11-10), which "Use"
        then fills into the form."""
        course = course_or_404(name)
        found = catalog.load(app.config["ROOT"])
        chosen = request.args.get("catalog", "")
        provider = catalog.local_provider(chosen) or (found.provider(chosen) if found
                                                      else None)
        problem, at = "", " ".join(request.args.get("at", "").split()).rstrip("/")
        if request.args.get("at_for", chosen) != chosen:
            at = ""  # the address shown for another provider (the choice changed since)
        if provider and provider.id in catalog.LOCAL:
            if at and not at.startswith(("http://", "https://")):
                problem, at = f"The address starts with http://, not {at[:40]!r}.", ""
            if at:
                provider = dataclasses.replace(provider, base_url=at)
            if not problem:
                listed, problem = catalog.installed(provider.base_url)
                provider = dataclasses.replace(provider, models=listed)
        model = next((m for m in provider.models if m.id == request.args.get("model")),
                     None) if provider else None
        values: dict = {"name": ""}
        notes: list[str] = []
        if provider and model:
            taken = {p.name for p in llm_settings.profiles(course)}
            values, notes = catalog.fill(provider, model, taken)
        values = applied() or {**values, "timeout_s": values.get("timeout_s")
                               or str(llm_settings.NEW_TIMEOUT_S)}
        listed = list(provider.models) if provider else []
        shown = listed if request.args.get("all") else listed[:catalog.SHOWN]
        if model and model not in shown:
            shown.append(model)
        shelf = {"catalog": found, "stale": "" if found else catalog.stale(app.config["ROOT"]),
                 "provider": provider, "model": model, "notes": notes,
                 "models": shown, "hidden": len(listed) - len(shown), "problem": problem,
                 "at": at,  # the address typed in, carried by the page's links
                 "key_set": bool(provider and provider.key_env in key_names())}
        return profile_page(course, None, values, [], shelf=shelf)

    @app.get("/c/<name>/models/new/at")
    def local_address(name: str):
        """The address box beside the provider choice (_local_at.html), for the provider
        `catalog` names: htmx asks for it whenever the choice changes. Reads nothing."""
        course_or_404(name)
        provider = catalog.local_provider(request.args.get("catalog", ""))
        return render_template("_local_at.html", at_provider=provider, at="")

    @app.post("/c/<name>/models/catalog")
    def refresh_catalog(name: str):
        """Download models.dev's catalog again, then back to New model on the provider that
        was chosen; on a failure, the page says why and keeps the copy before."""
        course = course_or_404(name)
        root = app.config["ROOT"]
        if problem := catalog.refresh(root):
            shelf = {"catalog": catalog.load(root), "stale": catalog.stale(root),
                     "provider": None, "model": None, "problem": "",
                     "notes": [], "models": [], "hidden": 0, "key_set": False}
            return profile_page(course, None, {"name": ""}, [problem], 502, shelf=shelf)
        found = catalog.load(root)
        chosen = request.form.get("catalog", "")
        args = ({"catalog": chosen} if chosen in catalog.LOCAL
                or (found and found.provider(chosen)) else {})
        return redirect(url_for("new_model", name=name, notice="catalog-fetched", **args),
                        code=303)

    @app.get("/c/<name>/models/m/<profile>/")
    def edit_model(name: str, profile: str):
        course = course_or_404(name)
        data = llm_settings.section(course).get("profiles", {}).get(profile)
        if not isinstance(data, dict):
            abort(404, N_("No such model."))
        values = applied() or llm_settings.form_values(profile, data)
        return profile_page(course, profile, values, [])

    @app.post("/c/<name>/models/save")
    def save_model(name: str):
        course = course_or_404(name)
        form = {k: request.form.get(k, "") for k in llm_settings.FORM_FIELDS}
        original = request.form.get("original") or None
        profile = form["name"].strip()
        data, problems = llm_settings.check_profile(course, profile, form)
        if data is not None:
            # A key name used at another host, in any course, is refused (catalog.py: a
            # changed URL must not send a key elsewhere).
            problems = llm_settings.key_elsewhere(app.config["ROOT"], course, profile, data)
        if data is not None and not problems:
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
        roles = llm_settings.merged_roles(course, chosen)
        if problems := llm_settings.save_roles(course, chosen, dry_run=True):
            # The roles as chosen stay in the form (owner, 2026-11-07: they were reset to
            # the file's, which looked like the choices were wiped).
            return models_page(course, problems, 400, unsaved=roles)
        changed = roles != llm_settings.section(course).get("roles", {})
        if changed and not request.form.get("confirm") and llm_settings.role_warnings(
                course, roles, catalog.load(app.config["ROOT"])):
            # Asked before saving (owner, 2026-11-07): the roles as chosen come back with
            # the warning and "Save even so" above Save. With htmx the warning was shown
            # while choosing, and the box ticked saves at once.
            return models_page(course, [], 409, unsaved=roles)
        problems = llm_settings.save_roles(course, chosen)
        if problems:
            return models_page(course, problems, 400, unsaved=roles)
        return redirect(url_for("models", name=name) + "?notice=roles-saved", code=303)

    @app.get("/c/<name>/models/roles/check")
    def role_check(name: str):
        """The part above Save for the roles chosen in the form (_role_check.html): htmx
        asks for it whenever a role changes. Reads only; nothing is saved."""
        course = course_or_404(name)
        chosen = {role: request.args.get(role, "") for role in llm_settings.ROLES
                  if role in request.args}
        roles = llm_settings.merged_roles(course, chosen)
        same = roles == llm_settings.section(course).get("roles", {})
        warnings = [] if same else llm_settings.role_warnings(
            course, roles, catalog.load(app.config["ROOT"]))
        return render_template("_role_check.html", check_warnings=warnings,
                               check_problems=llm_settings.save_roles(course, chosen,
                                                                      dry_run=True),
                               check_confirm=bool(request.args.get("confirm")))

    @app.post("/c/<name>/models/copy")
    def copy_models(name: str):
        course = course_or_404(name)
        source = courses.get_course(app.config["ROOT"], request.form.get("source", ""))
        if source is None:
            abort(404, N_("No such course."))
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
            abort(404, N_("No such model."))
        title = f"Check {wanted}" if wanted else "Check the models"
        return start_task(course, title, [llm_settings.check_command(names)],
                          meta={"kind": "check"})

    @app.post("/c/<name>/models/timing")
    def time_model(name: str):
        """When the profile's provider can serve its cache to the next call (`python -m
        tutor.llm_timing`), as a task: about sixteen paid calls, so on this click only;
        the task's log ends with the suggested parallel_start, which nothing writes."""
        course = course_or_404(name)
        wanted = request.form.get("profile", "")
        found = next((p for p in llm_settings.profiles(course) if p.name == wanted), None)
        if found is None or not llm_settings.measurable(found):
            abort(404, N_("No such model."))
        return start_task(course, f"Measure how {wanted}'s cache starts",
                          [llm_settings.timing_command(wanted)], meta={"kind": "timing"})
