"""Doctor's redacted rows displayed in Environment, without parsing CLI output.

Only deterministic reads run here. Fixed identifiers select existing form routes; no
model call, task, install or configuration write is started by this view.
"""

from tutor.gui import doctor_page, environment, python_switch

STATUS_TEXT = {"OK": "OK", "WARN": "Needs attention", "FAIL": "Problem", "SKIP": "Skipped"}


def rows(course):
    """One displayed observation/problem even if a diagnostic hook repeats it."""
    found = {}
    for row in doctor_page.report(course):
        found.setdefault((row.status, row.text), row)
    return list(found.values())


def switch_options(course, check):
    """Reused for the visible choices and fresh POST-time authorization of a switch."""
    if check.problem:
        return python_switch.options(course.workspace.effective_requires, [], None)
    if not python_switch.related(check.results):
        return []
    # envcheck's Python row is already measured in the course interpreter.
    current = next((r.get("found") for r in check.results if r.get("name") == "python"), None)
    if current is None:
        from tutor import pythons

        measured = pythons.ask(environment.venv_python(course))
        current = measured.version if measured else None
    return python_switch.options(course.workspace.effective_requires, check.results, current)


def switches(course, found) -> list[python_switch.Option] | None:
    """None means no Python recovery is needed; [] means discovery found no candidates.

    A package install or missing tutor entry point can share install-venv's action without
    needing another Python. Do not mistake that unneeded search for no suitable Python.
    """
    if not any(r.fix in {"switch-python", "install-venv"} for r in found):
        return None
    check = environment.check(course)
    if not check.problem and not python_switch.related(check.results):
        return None
    return switch_options(course, check)


def guide_fragment(row):
    """Fixed anchors on the packaged guide; never turn diagnostic text into a URL."""
    text = row.text.lower()
    for prefix, fragment in (("docker", "docker"), ("host java:", "java"),
                             ("host hadoop:", "hadoop"), ("host gcc:", "c"),
                             ("host winutils", "windows-spark"), ("c runs", "c")):
        if text.startswith(prefix):
            return fragment
    return "python"
