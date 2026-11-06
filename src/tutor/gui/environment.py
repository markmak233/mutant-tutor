"""A course's environment (GUI step 7a): what its tests need, whether this machine has it,
and the two installs the GUI may start (docs/design.md, "Local GUI", Environment page).

- The check is `python -m tutor.envcheck --json`, run with the course's own .venv: envcheck
  checks the interpreter that runs it, and the learner's tests run in that venv. It runs
  from the repository, as tasks do, so it reads .env itself (JAVA_HOME, HADOOP_HOME); the
  GUI never loads .env into its own environment.
- [requires] is edited here as a table, by envcheck's rules (plus a Hadoop release the
  image can build when tests run in the container), and written through toml_edit.
- Installs (owner, 2026-11-06): Python packages into the course's .venv, by running
  `tutor.workspace init` again, which installs [requires] too; and the test image, by
  `tutor.container build` with the course's venv, so the image's packages match the venv's.
  Both are tasks, each shown in full before its button. Never a system install: Java,
  Hadoop or another Python the user installs in their own terminal, then checks again.
- Where tests run: [isolation] runner, written only when the user chooses; "host" only with
  a tick saying that generated code then runs with their rights. Nothing switches by itself.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from tutor import container, workspace
from tutor.config import WORKSPACE_FILE
from tutor.envcheck import NOT_PIP, pip_requirements, requires_problems
from tutor.gui import tasks, toml_edit
from tutor.gui.courses import Course
from tutor.gui.toml_edit import put

CHECK_TIMEOUT_S = 60  # java -version, in a venv that may be cold; measured 0.2 s warm
RUNNERS = ("container", "host")
# What each special [requires] name stands for; any other name is a pip package.
KINDS = {"python": "Python version", "java": "JDK (Java)", "hadoop": "Hadoop"}
HADOOP_DOWNLOAD = "about 730 MB"  # measured 2026-11-05, see container.HADOOP_MIRRORS
# What an image weighs, for scale: the two built here on 2026-11-06.
IMAGE_SCALE = "a Python-only image is about 0.2 GB; one with Java, Hadoop and Spark about 1.7 GB"


@dataclass(frozen=True)
class Check:
    """envcheck's answer for the course's venv. `results`: its dicts (name, required, found,
    hint, ok), in [requires] order. `problem`: why there are none."""
    python: str | None
    results: list[dict] = field(default_factory=list)
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None and all(r.get("ok") for r in self.results)


@dataclass(frozen=True)
class DockerView:
    """The test image's state. `state`: "cannot-build" ([requires] has no recipe),
    "docker-missing", "docker-stopped", "image-missing" or "ready"; `detail` says more."""
    runner: str | None  # [isolation] runner, None when not chosen
    image: str | None
    state: str
    detail: str = ""
    size: int | None = None  # bytes, when ready
    recipe: str | None = None  # what Build would send; None without the course's venv
    downloads: list[tuple[str, str]] = field(default_factory=list)  # (what, from where)


def venv_python(course: Course) -> Path:
    return workspace.venv_python(course.workspace.root / ".venv")


def runner(course: Course) -> str | None:
    """[isolation] runner as written, None when not chosen (or the file cannot be read)."""
    return _data(course).get("isolation", {}).get("runner")


def check(course: Course, run: Callable = subprocess.run) -> Check:
    """Run envcheck in the course's venv. `run` is a parameter so tests need no venv."""
    python = venv_python(course)
    if not python.exists():
        return Check(None, problem="This course has no .venv yet: \"Install into .venv\" "
                                   "below creates it.")
    try:
        done = _run_in_venv(course, python, ["-m", "tutor.envcheck", "--json"], run)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check(str(python), problem=f"The check did not finish: {e}")
    try:
        data = json.loads(done.stdout)
        results = [r for r in data["results"] if isinstance(r, dict)]
    except (ValueError, KeyError, TypeError):
        return Check(str(python), problem="The check could not run in this course's .venv: "
                                          f"{_last_line(done.stderr)}")
    return Check(str(python), results)


def docker_view(course: Course, run: Callable = subprocess.run) -> DockerView:
    """Where the image stands, with Build's recipe and what it downloads. Asking Docker
    waits while a stopped VM wakes (container.image_problem), hence its own htmx part."""
    chosen = runner(course)
    requires = course.workspace.requires
    try:
        image = container.image_name(course.workspace.course, requires)
    except container.ContainerError as e:
        return DockerView(chosen, None, "cannot-build", str(e))
    python = venv_python(course)
    recipe = None
    if python.exists():
        try:
            done = _run_in_venv(course, python, ["-m", "tutor.container", "recipe"], run)
            recipe = done.stdout if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    parts = {"runner": chosen, "image": image, "recipe": recipe,
             "downloads": downloads(requires)}
    if shutil.which("docker") is None:
        return DockerView(state="docker-missing", **parts)
    if problem := container.docker_problem(run):
        return DockerView(state="docker-stopped", detail=problem, **parts)
    size = container.image_size(image, run)
    if size is None:
        return DockerView(state="image-missing", detail="Build it below.", **parts)
    return DockerView(state="ready", size=size, **parts)


def downloads(requires: dict[str, str]) -> list[tuple[str, str]]:
    """What a build fetches and from where, read off the same rules as the recipe."""
    python = requires.get("python") or f"{sys.version_info.major}.{sys.version_info.minor}"
    found = [(f"python:{python}-slim-bookworm", "Docker Hub, the official Python image")]
    if java := requires.get("java"):
        found.append((f"openjdk-{java.split('.')[0]}-jre-headless and procps",
                      "Debian's package mirrors (apt)"))
    if hadoop := requires.get("hadoop"):
        release = container.hadoop_release(hadoop)
        found.append((f"Hadoop {release}, {HADOOP_DOWNLOAD}",
                      f"{' or '.join(container.HADOOP_MIRRORS)}, checked against a fixed "
                      "SHA-512"))
    packages = [name for name in ("pytest", "hypothesis") if name not in requires]
    packages += [name for name in requires if name not in NOT_PIP]
    found.append((", ".join(packages), "PyPI (pip)"))
    return found


def install_shown(course: Course) -> str:
    """The pip command `init` runs, as the page shows it before its button."""
    command = workspace.install_command(course.workspace.root / ".venv",
                                        workspace.DEFAULT_EXTRAS,
                                        pip_requirements(course.workspace.requires))
    return shlex.join(command)


def install_command(course: Course) -> list[str]:
    """`tutor.workspace init` again: makes the .venv if missing, installs [requires]."""
    return [sys.executable, "-m", "tutor.workspace", "init", str(course.workspace.root)]


def build_command(course: Course) -> list[str]:
    """`tutor.container build` with the course's venv, whose versions the image takes."""
    return [str(venv_python(course)), "-m", "tutor.container", "build"]


def kind(name: str) -> str:
    return KINDS.get(name, "pip package")


def check_requires(names: list[str], versions: list[str], chosen: str | None
                   ) -> tuple[dict[str, str] | None, list[str]]:
    """The table from the form's rows (blank rows skipped), or the problems."""
    table: dict[str, str] = {}
    problems = []
    for raw_name, raw_version in zip(names, versions, strict=False):
        name, version = raw_name.strip(), raw_version.strip()
        if not name and not version:
            continue
        # python, java and hadoop are special only in lower case; pip ignores case.
        name = name.lower() if name.lower() in NOT_PIP else name
        if not name:
            problems.append(f"The version {version} has no name.")
        elif not version:
            problems.append(f"{name}: give a version prefix, such as 3.5 for any 3.5.x.")
        elif name.lower() in {n.lower() for n in table}:
            problems.append(f"{name} is listed twice.")
        else:
            table[name] = version
    problems += requires_problems(table)
    if not problems and chosen == "container" and "hadoop" in table:
        try:
            container.hadoop_release(table["hadoop"])
        except container.ContainerError as e:
            problems.append(f"{e}; the container's image could not be built.")
    return (None if problems else table), problems


def save_requires(course: Course, table: dict[str, str]) -> list[str]:
    """Write [requires] as `table`: changed values, new names after the last one, removed
    names gone, comments kept. Versions are written as strings, as they are read."""
    def change(doc) -> None:
        if "requires" not in doc:
            if not table:
                return
            doc["requires"] = tomlkit.table()
        section = doc["requires"]
        for name in list(section.keys()):
            if name not in table:
                del section[name]
        for name, version in table.items():
            if section.get(name) != version:
                put(section, name, version)

    return toml_edit.edit(course.workspace.root, "requires", change, table)


def save_runner(course: Course, chosen: str, understood: bool) -> list[str]:
    """[isolation] runner = chosen; the table's other keys stay."""
    if chosen not in RUNNERS:
        return ["Choose where tests run: container or host."]
    if chosen == "host" and not understood:
        return ["To run tests on this machine, tick that you understand generated code then "
                "runs with your rights."]
    old = _data(course).get("isolation", {})
    expected = {**old, "runner": chosen}

    def change(doc) -> None:
        if "isolation" not in doc:
            section = tomlkit.table()
            section.comment('"container" or "host", set on the GUI\'s Environment page')
            doc["isolation"] = section
        put(doc["isolation"], "runner", chosen)

    return toml_edit.edit(course.workspace.root, "isolation", change, expected)


def _run_in_venv(course: Course, python: Path, args: list[str], run: Callable):
    """A short command with the course's venv, from the repository (where .env is), with
    the course's paths set as tasks have them."""
    env = {**os.environ, "WORKSPACE_DIR": str(course.workspace.root),
           "LECTURE_DIR": str(course.workspace.lecture_dir)}
    return run([str(python), *args], capture_output=True, text=True,
               timeout=CHECK_TIMEOUT_S, cwd=tasks.REPO, env=env)


def _last_line(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def _data(course: Course) -> dict:
    try:
        with (course.workspace.root / WORKSPACE_FILE).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
