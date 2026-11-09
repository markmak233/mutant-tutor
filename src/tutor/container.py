"""The image a course's test runs execute in: `python -m tutor.container build`, `status`, or
`recipe` (the Dockerfile a build would send, which the GUI shows before its Build button).

Isolation layer B (docs/design.md, "Code execution safety"): the host orchestrates and a
container only executes. This module holds what is specific to Docker: building the image
from its recipe (tutor.image_recipe, made from the course's [requires]), the limits every
container runs under, and whether the image is there to run in. It only calls Docker's command
line, so there is no new dependency.

Only this command builds, since a build needs the network and takes minutes. A test run that
finds no image refuses to start; it never builds and never falls back to the host.
"""

from __future__ import annotations

import argparse
import functools
import math
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from tutor import image_recipe
from tutor.config import WORKSPACE_FILE, Workspace, WorkspaceError, load_env, load_workspace
from tutor.image_recipe import (
    TUTOR_DIR,
    TUTOR_MOUNT,
    ContainerError,
    dockerfile,
    image_name,
    runner_uid,
)
from tutor.package_bounds import merge

BUILD_COMMAND = "python -m tutor.container build"
# What a run may use of the VM's CPUs: the same Spark runs took as long as on the host.
MAX_CPUS = 4
# Long enough for a stopped VM to wake (Docker's docs: 3-10 s) and for a slow disk.
WAKE_TIMEOUT_S = 60.0
_HOST_WARNING = ("warning: this course has not chosen where its tests run, so exercise code runs "
                 "on this machine with your rights. Choose in workspace.toml: [isolation] runner "
                 '= "container" (see `python -m tutor.container`) or "host".')
_warned_host = False  # once per process: a batch makes hundreds of runs



def limits(uid: int | None = None) -> list[str]:
    """What every container runs under (docs/design.md says why each one is there)."""
    return [
        "--init",  # reaps processes nobody waits for
        "--hostname", "localhost",  # with no network Hadoop cannot resolve the default name
        "--network", "none",
        "--read-only",
        # exec: a C exercise's tests compile the program into /tmp and run it there. Docker's
        # tmpfs is noexec by default; the run's own folder, a bind mount, never was, so this
        # lets nothing run that could not already.
        "--tmpfs", "/tmp:rw,exec,size=1g,mode=1777",
        "--user", str(runner_uid() if uid is None else uid),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        # One Spark run peaked at 393 processes and threads and 486 MB (measured 2026-11-05).
        "--pids-limit", "1024",
        "--memory", "2g",
        "--memory-swap", "2g",  # the same number: no swap
    ]


def run_command(image: str, name: str, workdir: Path, limit_s: float,
                variables: Mapping[str, str], command: list[str]) -> list[str]:
    """The `docker run` of one test run. Nothing of the host's environment goes in, so never
    `.env` or an API key: the fixed values are in the image, and `variables` are only what the
    caller set for this run. `--pull never`: an image that went missing must stop the run, not
    fetch whatever a public registry holds under that name. `timeout`: the container ends by
    itself even if the validator is killed and nothing is left to call `docker kill`."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", name, *limits(),
            "--cpus", _cpu_limit(),
            "-v", f"{workdir}:/work", "-w", "/work",
            "-v", f"{TUTOR_DIR}:{TUTOR_MOUNT}:ro",
            *[flag for key, value in variables.items() for flag in ("-e", f"{key}={value}")],
            image, "timeout", "-s", "KILL", str(math.ceil(limit_s) + 5), *command]


@functools.lru_cache(maxsize=1)
def _cpu_limit() -> str:
    """MAX_CPUS, or fewer if the VM has fewer, since Docker refuses more than it has. Once per
    process: the answer takes 0.7 s and a run's own check has already shown Docker is up."""
    try:
        count = subprocess.run(["docker", "info", "--format", "{{.NCPU}}"], capture_output=True,
                               text=True, timeout=WAKE_TIMEOUT_S).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return str(MAX_CPUS)
    return str(min(MAX_CPUS, int(count))) if count.isdigit() and int(count) > 0 else str(MAX_CPUS)


def image_for(sources: Iterable[Path]) -> str | None:
    """The image a run of these source files must use; None runs it on the host.

    Every workspace.toml above every source counts, and one asking for the container wins.
    So a folder holding its own workspace.toml cannot switch the container off, and neither
    can a student file kept outside the course, since the tests it runs with are inside.
    A workspace.toml that cannot be read stops the run: it may have asked for the container.
    """
    global _warned_host
    checked: set[Path] = set()
    never_chose = False
    for source in sources:
        for folder in Path(source).resolve().parents:
            if folder in checked:
                break  # and so was everything above it
            checked.add(folder)
            path = folder / WORKSPACE_FILE
            try:
                with path.open("rb") as f:
                    data = tomllib.load(f)
            except FileNotFoundError:
                continue
            except (OSError, tomllib.TOMLDecodeError) as e:
                raise ContainerError(f"{path}: {e}") from e
            runner = data.get("isolation", {}).get("runner")
            if runner == "container":
                return _image_of(path, data)
            if runner not in (None, "host"):
                raise ContainerError(f'{path}: [isolation] runner = {runner!r}: use "container" '
                                     'or "host"')
            never_chose = never_chose or runner is None
    if never_chose and not _warned_host:
        _warned_host = True
        print(_HOST_WARNING, file=sys.stderr)
    return None


def _image_of(path: Path, data: Mapping) -> str:
    if "course" not in data:
        raise ContainerError(f"{path} has no `course` entry")
    # str(), as load_workspace does: the same name whichever way the file is read.
    requires = {name: str(version) for name, version in data.get("requires", {}).items()}
    try:
        requires = merge(requires, data.get("package-bounds", {}))
    except ValueError as exc:
        raise ContainerError(str(exc)) from None
    return image_name(data["course"], requires)


def check_course(root: Path, run: Callable = subprocess.run) -> None:
    """For a command about to spend something: raises if the course's tests could not run,
    so generation stops before its first LLM call and not after paying for a draft."""
    image = image_for([root / WORKSPACE_FILE])
    if image and (problem := image_problem(image, run)):
        raise ContainerError(problem)


def image_problem(image: str, run: Callable = subprocess.run) -> str | None:
    """Why `image` cannot run now, or None. This waits while a stopped VM wakes (Resource
    Saver; measured 2.5 s, then 0.07 s per call), so call it before a time limit starts."""
    try:
        found = run(["docker", "image", "inspect", image], capture_output=True, text=True,
                    timeout=WAKE_TIMEOUT_S)
        if found.returncode == 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # docker_problem says which
    return docker_problem(run) or (f"image {image} is missing or out of date: "
                                   f"run `{BUILD_COMMAND}`")


def docker_problem(run: Callable = subprocess.run) -> str | None:
    """Why Docker cannot be used right now, or None."""
    try:
        done = run(["docker", "info", "--format", "{{.ServerVersion}}"],
                   capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "Docker is not installed (no `docker` command on PATH)"
    except subprocess.TimeoutExpired:
        return "Docker did not answer in 30 s: restart Docker Desktop"
    # The server's version stays empty when only the client answers.
    if done.returncode != 0 or not done.stdout.strip():
        return "Docker is not running: start Docker Desktop, then try again"
    return None


def image_size(image: str, run: Callable = subprocess.run) -> int | None:
    """The image's size in bytes, as Docker reports it; None when it is missing or Docker
    does not answer. Call docker_problem first to tell those apart."""
    try:
        done = run(["docker", "image", "inspect", "--format", "{{.Size}}", image],
                   capture_output=True, text=True, timeout=WAKE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    size = done.stdout.strip()
    return int(size) if done.returncode == 0 and size.isdigit() else None


@dataclass(frozen=True)
class ImageStatus:
    image: str
    problem: str | None  # None: ready to run tests

    @property
    def ready(self) -> bool:
        return self.problem is None


def image_status(workspace: Workspace, run: Callable = subprocess.run) -> ImageStatus:
    """Whether this course's tests can run in a container now, and if not, how to fix it."""
    image = image_name(workspace.course, workspace.effective_requires)
    return ImageStatus(image, image_problem(image, run))


def _say(text: str) -> None:
    print(text, flush=True)  # before Docker's own output, also when it goes to a log file


def build(workspace: Workspace, run: Callable = subprocess.run,
          say: Callable[[str], None] = _say, *, keep_older: bool = False,
          installed: Callable[[str], str | None] | None = None) -> str:
    """Build this course's image, check it from inside, drop its older versions; returns its
    name. `run` is a parameter so tests need no Docker. Raises ContainerError."""
    if problem := docker_problem(run):
        raise ContainerError(problem)
    image = image_name(workspace.course, workspace.effective_requires)
    lookup = image_recipe.installed_version if installed is None else installed
    recipe = dockerfile(workspace.effective_requires, installed=lookup)
    say(f"building {image}\n{recipe}")
    # "-": the recipe comes from stdin with no build context, so no file of this machine
    # reaches the image. Docker's output is not captured: a first build takes minutes.
    if run(["docker", "build", "--tag", image, "-"], input=recipe, text=True).returncode != 0:
        raise ContainerError("docker build failed, see its output above")
    _check_inside(workspace, image, run)
    if not keep_older:  # doctor retains the previous image for explicit rollback
        _remove_older(image, run)
    say(f"ready: {image}")
    return image


def _check_inside(workspace: Workspace, image: str, run: Callable) -> None:
    """Envcheck inside the image against the same workspace.toml, and the imports a test run
    needs, under the limits a run has. An image that fails is removed, so that `status` cannot
    call it ready."""
    check = ("python -m tutor.envcheck && python -c 'import tutor.testkit, pytest, hypothesis'")
    done = run(["docker", "run", "--rm", *limits(),
                "-v", f"{TUTOR_DIR}:{TUTOR_MOUNT}:ro",
                "-v", f"{workspace.root / WORKSPACE_FILE}:/workspace/{WORKSPACE_FILE}:ro",
                "-e", "WORKSPACE_DIR=/workspace", image, "sh", "-c", check])
    if done.returncode != 0:
        run(["docker", "image", "rm", image], capture_output=True, text=True)
        raise ContainerError(f"{image} does not meet [requires], see above; it was removed")


def _remove_older(image: str, run: Callable) -> None:
    """Older versions of this course's image, which may be 2 GB each. Docker refuses to remove
    one in use, and that is fine: the next build tries again."""
    repository = image.split(":")[0]
    listed = run(["docker", "image", "ls", repository, "--format", "{{.Repository}}:{{.Tag}}"],
                 capture_output=True, text=True)
    for older in listed.stdout.split():
        if older != image:
            run(["docker", "image", "rm", older], capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.container",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("build", "status", "recipe"))
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace()
        if args.command == "build":
            build(workspace)
            return 0
        if args.command == "recipe":
            # What build would send, with this interpreter's versions; no Docker needed.
            print(f"# {image_name(workspace.course, workspace.effective_requires)}")
            print(dockerfile(workspace.effective_requires,
                             installed=image_recipe.installed_version), end="")
            return 0
        status = image_status(workspace)
    except (WorkspaceError, ContainerError) as e:
        print(f"container: {e}", file=sys.stderr)
        return 1
    print(f"course {workspace.course}: {status.image}: {status.problem or 'ready'}")
    return 0 if status.ready else 1


if __name__ == "__main__":
    sys.exit(main())
