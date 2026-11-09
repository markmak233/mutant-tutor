"""Deterministic doctor checks; reuse existing configuration and tool probes.

Only smoke writes, in its own temporary directory. Never load .env, install, change
configuration or call a model. The command isolates exceptions around each check.
"""

import importlib.util
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from tutor import (
    compat,
    config,
    container,
    course_requirements,
    doctor_languages,
    envcheck,
    languages,
    workspace,
)
from tutor.doctor_rows import Row


@dataclass
class Course:
    """Preload once to classify Docker severity, but print the load result in C1 order."""

    root: Path
    ws: config.Workspace | None = None
    data: dict = field(default_factory=dict)
    error: Exception | None = None
    # Within one doctor run, the venv health row and requirements share one snapshot.
    host_check: course_requirements.Probe | None = None

    @property
    def runner(self) -> str | None:
        return self.data.get("isolation", {}).get("runner")


def read_course(root: Path) -> Course:
    """Use the existing private-workspace loader without any ambient workspace or .env."""
    course = Course(root)
    try:
        course.ws = config.load_workspace({"WORKSPACE_DIR": str(root)})
        course.data = tomllib.loads((root / config.WORKSPACE_FILE).read_text(encoding="utf-8"))
        if not isinstance(course.data.get("isolation", {}), dict):
            raise config.WorkspaceError("[isolation] must be a table")
    except Exception as exc:
        course.error = exc
    return course


def tool_version() -> list[Row]:
    """Source checkout imports alone do not prove the installed entry point exists."""
    try:
        return [Row("OK", f"tutor {metadata.version('tutor')} is installed")]
    except metadata.PackageNotFoundError:
        return [Row("WARN", "running from a source tree without installing", "pip install -e .")]


def extra(name: str, module: str, purpose: str) -> list[Row]:
    """Locate optional packages without importing heavy frameworks."""
    if importlib.util.find_spec(module) is None:
        return [Row("WARN", f"the {name} extra is not installed: {purpose} need it",
                    f'pip install -e ".[{name}]" (in the tool\'s folder)')]
    return [Row("OK", f"{name} extra: {purpose} available")]


def docker(run: Callable, required: bool) -> list[Row]:
    """Observe the existing probe's answer for its version, without a second info call."""
    version = ""

    def observed(command, **kwargs):
        nonlocal version
        done = run(command, **kwargs)
        version = done.stdout.strip() if done.returncode == 0 else ""
        return done

    problem = container.docker_problem(observed)
    if problem:
        action = ("install Docker Desktop, start it, then run tutor doctor again"
                  if "not installed" in problem else problem)
        return [Row("FAIL" if required else "WARN", problem, action, "host-guide")]
    return [Row("OK", f"Docker {version} is running")]


def course_load(course: Course) -> list[Row]:
    """A failed load prevents every course-dependent check, but not tool diagnostics."""
    if course.error:
        if isinstance(course.error, (config.WorkspaceError, tomllib.TOMLDecodeError)):
            return [Row("FAIL", f"workspace.toml: {course.error}",
                        "correct workspace.toml, then run tutor doctor again")]
        raise course.error
    return [Row("OK", "workspace.toml loads")]


def declarations(course: Course) -> list[Row]:
    """Reuse both validators and deduplicate identical messages, not language lists."""
    problems = [*languages.problems(course.data),
                *envcheck.requires_problems(course.ws.effective_requires, allow_bounds=True)]
    return [Row("FAIL", problem,
                "this tool generates Python and C exercises only; remove it from "
                "workspace.toml or wait for support" if "not supported yet" in problem else
                "correct the declaration on the Environment page, step 1")
            for problem in dict.fromkeys(problems)]


def parts(course: Course | None) -> list[doctor_languages.LanguageDoctor]:
    """The language registry alone chooses membership and order; no language branches."""
    if course is None:
        return list(doctor_languages.DOCTORS.values())
    return [doctor_languages.DOCTORS[item.key]
            for item in languages.course_languages(course.data)]


def runner(course: Course) -> list[Row]:
    """Do not silently treat a misspelled runner as host execution."""
    if course.runner is None:
        return [Row("WARN", "runner not chosen; tests run on this machine",
                    "choose it on the Environment page or set [isolation] runner "
                    "in workspace.toml", "choose-runner")]
    if course.runner not in ("host", "container"):
        return [Row("FAIL", "runner is not host or container",
                    'set [isolation] runner to "host" or "container" in workspace.toml',
                    "choose-runner")]
    return [Row("OK", f"runner: {course.runner}")]


def venv(course: Course, run: Callable) -> list[Row]:
    """Probe importability, then the sibling script; never repair an old or broken venv."""
    executable = workspace.venv_python(course.root / ".venv")
    target, context = course_target(course)
    install = f'python -m tutor.workspace init {target}' + context
    if not executable.is_file():
        course.host_check = course_requirements.Probe(None, problem="missing .venv")
        return [Row("FAIL", "course .venv is missing", install, "install-venv")]
    try:
        done = run([str(executable), "-c", "import tutor"], timeout=30,
                   capture_output=True, text=True,
                   env=course_requirements.environment(course.ws))
        good = done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        good = False
    if good and course.runner in (None, "host"):
        course.host_check = course_requirements.check(course.ws, run=run)
        good = course.host_check.problem is None
    if not good:
        course.host_check = course_requirements.Probe(str(executable), problem="broken .venv")
        return [Row("FAIL", "course .venv Python cannot import tutor",
                    install + " --python <a Python 3.11+> --replace-venv", "install-venv")]
    script = executable.with_name("tutor.exe" if sys.platform == "win32" else "tutor")
    if not script.is_file():
        return [Row("WARN", ".venv made before the tutor command existed", install, "install-venv")]
    return [Row("OK", "course .venv imports tutor and has the tutor command")]


def requirements(course: Course, run: Callable, reported: set[str]) -> list[Row]:
    """Course-venv facts only; container practice needs no host toolchain."""
    if course.runner == "container":
        return [Row("SKIP", "tools on this machine: not needed, tests run in the course image "
                    "(`tutor test`)")]
    if course.runner not in (None, "host"):
        return [Row("SKIP", "tools on this machine: choose a valid runner first")]
    rows = []
    snapshot = course.host_check or course_requirements.check(course.ws, run=run)
    if snapshot.problem:
        return [Row("SKIP", "tools on this machine: see the .venv row above")]
    target, context = course_target(course)
    for result in snapshot.results:
        # envcheck also reports declaration errors. The same text belongs in one row only.
        if not result["ok"] and result["hint"] in reported:
            continue
        hint = result["hint"].replace(str(course.root), target)
        if target in hint:
            hint += context
        rows.append(Row("OK" if result["ok"] else "FAIL",
                        f"host {result['name']}: required {result['required']}, "
                        f"found {result['found'] or 'not found'}",
                        "" if result["ok"] else hint or "see the Environment page, step 2",
                        None if result["ok"] else requirement_fix(result)))
    return rows


def requirement_fix(result: dict) -> str | None:
    """Map known checks to existing actions, leaving unsupported declarations as text."""
    name = result["name"]
    if name == "python" or (name.endswith(" imports")
                             and result["required"] in compat.REMOVED_MODULES):
        return "switch-python"
    if name in ("java", "hadoop", "gcc") or name.startswith("winutils"):
        return "host-guide"
    if name.endswith(" imports"):
        return "ask-model"  # an imports failure with no known deterministic remedy
    if envcheck.requires_problems({name: result["required"]}, allow_bounds=True):
        return None
    return "install-venv"


def course_target(course: Course) -> tuple[str, str]:
    """Printable commands use relative paths, avoiding values such as the home directory.

    Windows cannot express a relative path across drives; in that case tell the user to
    run the command from the course folder. These are instructions, never executed here.
    """
    try:
        return f'"{os.path.relpath(course.root)}"', ""
    except ValueError:
        return ".", " (run from the course folder)"


def image(course: Course, run: Callable, docker_ready: bool) -> list[Row]:
    """An unavailable Docker result suppresses all image calls; no build or pull path."""
    if course.runner in (None, "host"):
        return [Row("SKIP", "test image: this course runs its tests on this machine")]
    if course.runner != "container":
        return [Row("SKIP", "test image: choose a valid runner first")]
    if not docker_ready:
        return [Row("SKIP", "test image: Docker is not available")]
    name = container.image_for([course.root / config.WORKSPACE_FILE])
    problem = container.image_problem(name, run)
    return ([Row("FAIL", f"test image: {problem}", problem, "build-image")] if problem else
            [Row("OK", f"test image: {name}")])
