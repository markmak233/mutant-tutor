"""The image a course's test runs execute in: `python -m tutor.container build`, `status`, or
`recipe` (the Dockerfile a build would send, which the GUI shows before its Build button).

Isolation layer B (docs/design.md, "Code execution safety"): the host orchestrates and a
container only executes. This module holds what is specific to Docker: the image, made from
the course's [requires], and the limits every container runs under. It only calls Docker's
command line, so there is no new dependency.

Only this command builds, since a build needs the network and takes minutes. A test run that
finds no image refuses to start; it never builds and never falls back to the host.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import math
import os
import re
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from tutor.config import WORKSPACE_FILE, Workspace, WorkspaceError, load_env, load_workspace
from tutor.envcheck import NOT_PIP, requires_problems, version_matches

IMAGE_PREFIX = "tutor-run-"
BUILD_COMMAND = "python -m tutor.container build"
# This package, mounted read-only: exercise tests import tutor.testkit. PYTHONPATH in the image
# points at the parent. A mount and not a copy, so an image never holds an older testkit.
TUTOR_DIR = Path(__file__).resolve().parent
TUTOR_MOUNT = "/opt/tutor/tutor"
# What a run may use of the VM's CPUs: the same Spark runs took as long as on the host.
MAX_CPUS = 4
# Long enough for a stopped VM to wake (Docker's docs: 3-10 s) and for a slow disk.
WAKE_TIMEOUT_S = 60.0
_HOST_WARNING = ("warning: this course has not chosen where its tests run, so exercise code runs "
                 "on this machine with your rights. Choose in workspace.toml: [isolation] runner "
                 '= "container" (see `python -m tutor.container`) or "host".')
_warned_host = False  # once per process: a batch makes hundreds of runs

# Fixed in the code: only these releases can be built, each checked against Apache's own
# checksum. The documentation is half a gigabyte no run reads, and is removed after unpacking.
HADOOP_RELEASES = {
    "3.3.6": "de3eaca2e0517e4b569a88b63c89fae19cb8ac6c01ff990f1ff8f0cc0f3128c8"
             "e8a23db01577ca562a0e0bb1b4a3889f8c74384e609cd55e537aada3dcaa9f8a",
}
# Measured 2026-11-05: 3.3 MB/s and 450 KB/s for the same 730 MB. The first drops old releases
# in time and the second keeps them all; the checksum covers both.
HADOOP_MIRRORS = ("https://downloads.apache.org/hadoop/common",
                  "https://archive.apache.org/dist/hadoop/common")

# Every image has these: the validator's rules run pytest, and oracle tests use hypothesis.
_TEST_PACKAGES = ("pytest", "hypothesis")


class ContainerError(Exception):
    """Docker or the image cannot be used. The message says how to fix it."""


def runner_uid() -> int:
    """The uid the image's `runner` user gets: the host user's, so the host can delete what
    a run wrote in its folder (on native Linux the numbers must match). Never root, and 1000
    where there are no uids (Windows)."""
    getuid = getattr(os, "getuid", None)
    return (getuid() if getuid else 0) or 1000


def dockerfile(requires: Mapping[str, str], uid: int | None = None,
               installed: Callable[[str], str | None] = lambda name: None) -> str:
    """The image's recipe, from a course's [requires]: `python` picks the base image, `java`
    the JDK, `hadoop` Apache's tarball, and any other name is a pip package.

    `installed` gives a package's version on this machine, or None. A build passes the real
    one so that the image tests with what the host tests with, as long as it fits [requires].
    Leaving it out gives the recipe image_name hashes, which must not depend on the venv.

    Slow, rarely changing steps come first, so a changed package does not download Hadoop
    again.
    """
    _check_requires(requires)
    python = requires.get("python") or f"{sys.version_info.major}.{sys.version_info.minor}"
    steps = [f"FROM python:{python}-slim-bookworm"]
    env = {
        # The root file system is read-only and /tmp is the only writable place besides a
        # run's own folder, so Python, Spark and MRJob all keep their files there.
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(TUTOR_MOUNT).parent.as_posix()),
        # A pipe's encoding, so the host can decode what the run prints.
        "PYTHONIOENCODING": "utf-8",
        # Spark workers otherwise start the `python3` on PATH.
        "PYSPARK_PYTHON": "/usr/local/bin/python",
        "SPARK_LOCAL_IP": "127.0.0.1",
    }
    if java := requires.get("java"):
        major = java.split(".")[0]
        # procps: Hadoop's and Spark's launch scripts call `ps`.
        steps.append("RUN apt-get update \\\n"
                     f" && apt-get install -y --no-install-recommends openjdk-{major}-jre-headless"
                     " procps \\\n"
                     " && rm -rf /var/lib/apt/lists/*")
        # The JDK's folder name holds the architecture; one JAVA_HOME for arm64 and amd64.
        steps.append(f'RUN ln -s "/usr/lib/jvm/java-{major}-openjdk-'
                     '$(dpkg --print-architecture)" /opt/java')
        env["JAVA_HOME"] = "/opt/java"
    if hadoop := requires.get("hadoop"):
        steps.append(_hadoop_step(hadoop))
        env["HADOOP_HOME"] = "/opt/hadoop"
    packages = [_pip_spec(name, None, installed) for name in _TEST_PACKAGES
                if name not in requires]
    packages += [_pip_spec(name, prefix, installed) for name, prefix in requires.items()
                 if name not in NOT_PIP]
    steps.append("RUN pip install --no-cache-dir " + " ".join(shlex.quote(p) for p in packages))
    # --no-log-init: useradd's log file grows with the uid, and a large one fills the disk.
    steps.append(f"RUN useradd --create-home --no-log-init --uid {_uid(uid)} runner")
    steps.append("ENV " + " ".join(f"{key}={value}" for key, value in env.items()))
    steps.append("USER runner")
    return "\n".join(steps) + "\n"


def _uid(uid: int | None) -> int:
    return runner_uid() if uid is None else uid


def _check_requires(requires: Mapping[str, str]) -> None:
    """Refuse what would fail late, or change the recipe's meaning, inside a build. [requires]
    is the owner's and generation never writes it, so this catches typing errors. The rules
    are envcheck's, shared with the venv install and the GUI; a build adds a known Hadoop."""
    if problems := requires_problems(requires):
        raise ContainerError(problems[0])
    if "hadoop" in requires:
        hadoop_release(requires["hadoop"])


def hadoop_release(required: str) -> str:
    """The newest release in HADOOP_RELEASES that fits, or ContainerError."""
    releases = [r for r in HADOOP_RELEASES if version_matches(r, required)]
    if not releases:
        raise ContainerError(f"[requires] hadoop = {required!r}: no known release. Known: "
                             f"{', '.join(HADOOP_RELEASES)} (container.HADOOP_RELEASES)")
    return releases[-1]


def _hadoop_step(required: str) -> str:
    release = hadoop_release(required)
    urls = " ".join(f"{mirror}/hadoop-{release}/hadoop-{release}.tar.gz"
                    for mirror in HADOOP_MIRRORS)
    # Python downloads, as the slim image has no curl. A failed mirror leaves no file, or a
    # partial one, and the checksum stops the build either way.
    fetch = ("python -c \"import sys, urllib.request; "
             "urllib.request.urlretrieve(sys.argv[1], '/tmp/hadoop.tgz')\" \"$url\"")
    return (f"RUN for url in {urls}; do {fetch} && break; done \\\n"
            f' && echo "{HADOOP_RELEASES[release]}  /tmp/hadoop.tgz" | sha512sum -c - \\\n'
            " && mkdir /opt/hadoop \\\n"
            " && tar -xzf /tmp/hadoop.tgz -C /opt/hadoop --strip-components=1 \\\n"
            " && rm -rf /tmp/hadoop.tgz /opt/hadoop/share/doc")


def _pip_spec(name: str, prefix: str | None, installed: Callable[[str], str | None]) -> str:
    """`pyspark==3.5.9` when this machine has a version that fits, else `pyspark==3.5.*`."""
    found = installed(name)
    if found and (prefix is None or version_matches(found, prefix)):
        return f"{name}=={found}"
    return f"{name}=={prefix}.*" if prefix else name


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def image_name(course: str, requires: Mapping[str, str], uid: int | None = None) -> str:
    """`tutor-run-<course>:<hash of the recipe>`, so a changed [requires] or a changed recipe
    means a missing image and a clear "rebuild". The hash leaves out the versions on this
    machine: two venvs of one course hold different pytest versions (measured), and each
    would otherwise call the image out of date."""
    digest = hashlib.sha256(dockerfile(requires, uid).encode("utf-8")).hexdigest()[:12]
    # Docker takes lower case only, and a separator must stand between two alphanumerics.
    return f"{IMAGE_PREFIX}{re.sub(r'[^a-z0-9]+', '-', course.lower()).strip('-')}:{digest}"


def limits(uid: int | None = None) -> list[str]:
    """What every container runs under (docs/design.md says why each one is there)."""
    return [
        "--init",  # reaps processes nobody waits for
        "--hostname", "localhost",  # with no network Hadoop cannot resolve the default name
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=1g,mode=1777",
        "--user", str(_uid(uid)),
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
    image = image_name(workspace.course, workspace.requires)
    return ImageStatus(image, image_problem(image, run))


def _say(text: str) -> None:
    print(text, flush=True)  # before Docker's own output, also when it goes to a log file


def build(workspace: Workspace, run: Callable = subprocess.run,
          say: Callable[[str], None] = _say) -> str:
    """Build this course's image, check it from inside, drop its older versions; returns its
    name. `run` is a parameter so tests need no Docker. Raises ContainerError."""
    if problem := docker_problem(run):
        raise ContainerError(problem)
    image = image_name(workspace.course, workspace.requires)
    recipe = dockerfile(workspace.requires, installed=_installed_version)
    say(f"building {image}\n{recipe}")
    # "-": the recipe comes from stdin with no build context, so no file of this machine
    # reaches the image. Docker's output is not captured: a first build takes minutes.
    if run(["docker", "build", "--tag", image, "-"], input=recipe, text=True).returncode != 0:
        raise ContainerError("docker build failed, see its output above")
    _check_inside(workspace, image, run)
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
            print(f"# {image_name(workspace.course, workspace.requires)}")
            print(dockerfile(workspace.requires, installed=_installed_version), end="")
            return 0
        status = image_status(workspace)
    except (WorkspaceError, ContainerError) as e:
        print(f"container: {e}", file=sys.stderr)
        return 1
    print(f"course {workspace.course}: {status.image}: {status.problem or 'ready'}")
    return 0 if status.ready else 1


if __name__ == "__main__":
    sys.exit(main())
