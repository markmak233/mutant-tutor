"""The image's recipe and the build's steps, with Docker's command line faked: no Docker here."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from tutor import container
from tutor.config import Workspace
from tutor.container import (
    ContainerError,
    build,
    desktop_app,
    docker_problem,
    docker_state,
    image_size,
    image_status,
    limits,
    main,
    start_docker_desktop,
)
from tutor.image_recipe import HADOOP_MIRRORS, HADOOP_RELEASES, dockerfile, image_name, runner_uid

SPARK_COURSE = {"python": "3.11", "java": "17", "pyspark": "3.5", "mrjob": "0.7", "hadoop": "3.3"}
PLAIN_COURSE = {"python": "3.11"}


class FakeDocker:
    """Stands in for subprocess.run: records each docker command and answers by its verb.
    `fail` holds the verbs that exit 1, and `images` what `docker image ls` lists."""

    def __init__(self, fail=(), images=""):
        self.fail = set(fail)
        self.images = images
        self.commands: list[list[str]] = []
        self.stdin: list[str] = []
        self.manifests = {}

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if "input" in kwargs:
            self.stdin.append(kwargs["input"])
        verb = " ".join(command[1:3]) if command[1] == "image" else command[1]
        out = {"info": "24.0.6\n", "image ls": self.images}.get(verb, "")
        if verb == "build" and verb not in self.fail:
            tag = command[command.index("--tag") + 1]
            raw_labels = [command[i + 1] for i, p in enumerate(command) if p == "--label"]
            labels = dict(p.split("=", 1) for p in raw_labels)
            identity = "base" if tag.startswith("tutor-base-") else "built"
            self.manifests[tag] = {"Id": "sha256:" + hashlib.sha256(identity.encode()).hexdigest(),
                                   "RepoDigests": [], "Config": {"Labels": labels}}
        if verb == "image inspect":
            data = self.manifests.get(command[-1], {
                "Id": "sha256:" + hashlib.sha256(b"built").hexdigest(), "Config": {"Labels": {}}})
            out = json.dumps([data])
        if verb == "run" and any("tutor-image-inventory-v1" in part for part in command):
            out = json.dumps({"python": "3.11.17", "packages": [["pytest", "9.1.1"]],
                              "java": None, "gcc": None, "hadoop": None})
        return subprocess.CompletedProcess(command, 1 if verb in self.fail else 0, out, "")

    def verbs(self):
        return [" ".join(c[1:3]) if c[1] == "image" else c[1] for c in self.commands]


def workspace(tmp_path, requires=None, course="demo"):
    return Workspace(root=tmp_path, course=course, requires=requires or PLAIN_COURSE,
                     lecture_dir=tmp_path / "lectures")


def test_a_plain_course_gets_python_and_the_test_packages_only():
    recipe = dockerfile(PLAIN_COURSE, uid=501)
    assert recipe.startswith("FROM python:3.11-slim-bookworm\n")
    assert "pip install --no-cache-dir pytest hypothesis\n" in recipe
    assert "--uid 501 runner" in recipe
    assert recipe.endswith("USER runner\n")  # nothing after it runs as root
    for absent in ("openjdk", "hadoop", "JAVA_HOME", "pyspark"):
        assert absent not in recipe


def test_a_course_with_java_and_hadoop_gets_both_and_any_other_name_is_a_pip_package():
    recipe = dockerfile(SPARK_COURSE, uid=501)
    assert "openjdk-17-jre-headless" in recipe
    assert "JAVA_HOME=/opt/java" in recipe
    assert "HADOOP_HOME=/opt/hadoop" in recipe
    # Quoted, or the shell would treat the star as a file pattern.
    assert "'pyspark==3.5.*'" in recipe and "'mrjob==0.7.*'" in recipe


def test_a_c_course_gets_gcc_in_a_step_of_its_own():
    """A step of its own, so adding C leaves every other course's recipe, and image name, as
    it was; gcc is not a pip package."""
    recipe = dockerfile({**PLAIN_COURSE, "gcc": "12"}, uid=501)
    assert ("RUN apt-get update \\\n && apt-get install -y --no-install-recommends gcc "
            "libc6-dev \\\n && rm -rf /var/lib/apt/lists/*\n") in recipe
    assert "pip install --no-cache-dir pytest hypothesis\n" in recipe
    without = dockerfile(PLAIN_COURSE, uid=501)
    assert without == recipe.replace(recipe[recipe.index("RUN apt-get"):
                                            recipe.index("RUN pip")], "")


@pytest.mark.parametrize(("gcc", "base"), [("12", "bookworm"), ("12.2", "bookworm"),
                                           ("14", "trixie")])
def test_gcc_picks_the_debian_release_that_packages_it(gcc, base):
    """gcc 14 needs trixie (gcc 12's ASan hangs at 32 bits of mmap randomisation); gcc 12 and
    no gcc keep bookworm, so those recipes and image names stay as they were."""
    recipe = dockerfile({**PLAIN_COURSE, "gcc": gcc}, uid=501)
    assert recipe.startswith(f"FROM python:3.11-slim-{base}\n")


@pytest.mark.parametrize(("extra", "words"), [
    ({"gcc": "13"}, "gcc 12 (Debian bookworm), 14 (Debian trixie)"),
    ({"gcc": "14", "java": "17"}, "has only Java 21"),
])
def test_a_gcc_or_java_no_release_has_is_refused_before_a_build(extra, words):
    with pytest.raises(ContainerError, match=re.escape(words)):
        dockerfile({**PLAIN_COURSE, **extra}, uid=501)


def test_trixie_takes_its_own_jdk():
    recipe = dockerfile({**PLAIN_COURSE, "gcc": "14", "java": "21"}, uid=501)
    assert recipe.startswith("FROM python:3.11-slim-trixie\n")
    assert "openjdk-21-jre-headless" in recipe


def test_hadoop_is_downloaded_from_every_mirror_and_checked_against_a_fixed_checksum():
    recipe = dockerfile(SPARK_COURSE, uid=501)
    for mirror in HADOOP_MIRRORS:
        assert f"{mirror}/hadoop-3.3.6/hadoop-3.3.6.tar.gz" in recipe
    assert f'"{HADOOP_RELEASES["3.3.6"]}  /tmp/hadoop.tgz" | sha512sum -c -' in recipe
    # Before pip, so a changed package does not download it again.
    assert recipe.index("sha512sum") < recipe.index("pip install")


def test_a_build_pins_this_machines_versions_where_they_fit():
    versions = {"pyspark": "3.5.9", "mrjob": "0.8.0", "pytest": "9.1.1"}
    recipe = dockerfile(SPARK_COURSE, uid=501, installed=versions.get)
    assert "pyspark==3.5.9" in recipe
    assert "'mrjob==0.7.*'" in recipe  # 0.8.0 does not fit [requires]: the course decides
    assert "pytest==9.1.1" in recipe
    assert " hypothesis " in recipe  # not on this machine, so whatever is current


def test_a_test_package_named_in_requires_is_listed_once():
    recipe = dockerfile({"python": "3.11", "pytest": "9"}, uid=501)
    assert recipe.count("pytest") == 1
    assert "'pytest==9.*'" in recipe


@pytest.mark.parametrize("requires, says", [
    ({"pyspark": "3.5"}, "needs a JVM"),
    ({"hadoop": "3.3"}, "needs a JVM"),
    ({"java": "17", "hadoop": "2.7"}, "no known release"),
    ({"java": "17; rm -rf /"}, "version prefix"),
    ({"bad name": "1"}, "not a package name"),
])
def test_a_requirement_that_cannot_be_built_is_refused_before_docker(requires, says):
    with pytest.raises(ContainerError, match=says):
        dockerfile(requires, uid=501)


def test_the_image_name_follows_the_recipe_and_not_this_machines_versions(monkeypatch):
    first = image_name("Demo Course_1", SPARK_COURSE, uid=501)
    assert first.startswith("tutor-run-demo-course-1:")
    assert image_name("Demo Course_1", SPARK_COURSE, uid=501) == first
    assert image_name("Demo Course_1", {**SPARK_COURSE, "pyspark": "3.4"}, uid=501) != first
    assert image_name("Demo Course_1", SPARK_COURSE, uid=1000) != first  # another user's image
    # Two venvs of one course hold different pytest versions; neither may call it out of date.
    monkeypatch.setattr("tutor.image_recipe.installed_version", lambda name: "1.2.3")
    assert image_name("Demo Course_1", SPARK_COURSE, uid=501) == first


def test_limits_keep_the_container_off_the_network_and_out_of_root():
    flags = limits(uid=501)
    for pair in (["--network", "none"], ["--user", "501"], ["--cap-drop", "ALL"],
                 ["--hostname", "localhost"], ["--pids-limit", "1024"], ["--memory-swap", "2g"]):
        assert any(flags[i:i + 2] == pair for i in range(len(flags)))
    assert "--read-only" in flags and "--init" in flags
    # exec: C tests compile into /tmp and run the program there (the run folder allows it too).
    assert ["--tmpfs", "/tmp:rw,exec,size=1g,mode=1777"] == flags[flags.index("--tmpfs"):][:2]


def test_the_runner_is_never_root(monkeypatch):
    monkeypatch.setattr("os.getuid", lambda: 0, raising=False)
    assert runner_uid() == 1000
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    assert runner_uid() == 501
    monkeypatch.delattr("os.getuid", raising=False)  # Windows has none
    assert runner_uid() == 1000


def _answer(stderr: str):
    """`docker info` exiting 1 with this on stderr, as the CLI does when it cannot connect."""
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", stderr)
    return run


def test_docker_state_tells_each_case_apart(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", 30)

    # Never this machine's own Docker Desktop: each case says which app it finds.
    monkeypatch.setattr(container, "desktop_app", lambda **kwargs: None)
    assert docker_state(missing).kind == container.MISSING
    assert "not installed" in docker_problem(missing)
    # Installed after this terminal opened (Windows, 2026-11-15): not "install it again".
    monkeypatch.setattr(container, "desktop_app", lambda **kwargs: Path("Docker Desktop.exe"))
    found = docker_state(missing)
    assert found.kind == container.NOT_FOUND and "not installed" not in found.message
    assert "open a new terminal" in found.fix
    assert docker_state(slow).kind == container.SLOW and "did not answer" in docker_problem(slow)
    assert docker_problem(FakeDocker()) is None and docker_state(FakeDocker()).kind is None
    # Not running: Docker Desktop on macOS and Windows, Desktop or Engine on Linux.
    assert docker_problem(FakeDocker(fail={"info"})) is not None
    for platform, words in (("darwin", "start Docker Desktop, then"),
                            ("win32", "start Docker Desktop, then"),
                            ("linux", "sudo systemctl start docker")):
        state = docker_state(_answer("Cannot connect to the Docker daemon"), platform=platform)
        assert state.kind == container.STOPPED and "not running" in state.problem
        assert words in state.fix
    # Running but not this user's (Ubuntu with Docker Engine, 2026-11-15): never "not running".
    denied = docker_state(_answer("permission denied while trying to connect to the docker "
                                  "API at unix:///var/run/docker.sock"), platform="linux")
    assert denied.kind == container.DENIED and "not running" not in denied.message
    assert "usermod -aG docker" in denied.fix and "log out" in denied.fix
    windows = docker_state(_answer("open //./pipe/docker_engine: Access is denied."),
                           platform="win32")
    assert windows.kind == container.DENIED and "docker-users" in windows.fix


def test_desktop_app_finds_either_windows_install(tmp_path, monkeypatch):
    program_files, local = tmp_path / "Program Files", tmp_path / "Local"
    env = {"ProgramFiles": str(program_files), "LOCALAPPDATA": str(local)}

    def app(root):
        exe = root / "Docker Desktop.exe"
        (root / "resources" / "bin").mkdir(parents=True)
        (root / "resources" / "bin" / "docker.exe").write_bytes(b"")
        exe.write_bytes(b"")
        return exe

    def nowhere(name):
        return None

    assert desktop_app(platform="win32", which=nowhere, environ=env) is None
    assert desktop_app(platform="linux", which=nowhere, environ=env) is None
    # For one user, with no admin (seen in Windows Sandbox): under AppData\Local\Programs.
    per_user = app(local / "Programs" / "DockerDesktop")
    assert desktop_app(platform="win32", which=nowhere, environ=env) == per_user
    # Wherever it is, the docker command it put on PATH leads to it (two folders up).
    elsewhere = app(tmp_path / "D" / "Docker")
    assert desktop_app(platform="win32", environ=env,
                       which=lambda name: str(elsewhere.parent / "resources" / "bin" /
                                              "docker.exe")) == elsewhere.resolve()


def test_start_docker_desktop_asks_the_installed_app_and_never_installs(tmp_path, monkeypatch):
    # macOS and Linux go through `run`, keyed by the command: `open` finds the app, and this
    # Linux has no docker-desktop user service (systemctl exits 5), so nothing is started.
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0 if command[0] == "open" else 5, "", "")

    assert start_docker_desktop(run=run, platform="darwin") == "open -a Docker"
    assert start_docker_desktop(run=run, platform="linux") is None

    def no_command(command, **kwargs):
        raise FileNotFoundError(command[0])

    assert start_docker_desktop(run=no_command, platform="linux") is None

    # Windows starts the exe desktop_app finds (Program Files or the user's own install),
    # detached, only if it is there; this machine's own docker command is kept out.
    monkeypatch.setattr(container.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    started = []

    def popen(command, **kwargs):
        started.append(command)

    assert start_docker_desktop(popen=popen, platform="win32") is None
    exe = tmp_path / "Local" / "Programs" / "DockerDesktop" / "Docker Desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert start_docker_desktop(popen=popen, platform="win32") == str(exe)
    assert started == [[str(exe)]]


def test_the_status_says_how_to_get_ready(tmp_path):
    ws = workspace(tmp_path)
    assert image_status(ws, FakeDocker()).ready
    missing = image_status(ws, FakeDocker(fail={"image inspect"}))
    assert not missing.ready and "tutor.container build" in missing.problem
    stopped = image_status(ws, FakeDocker(fail={"info", "image inspect"}))
    assert not stopped.ready and "not running" in stopped.problem


def test_a_build_sends_the_recipe_with_no_context_checks_inside_and_drops_older_images(tmp_path):
    ws = workspace(tmp_path)
    image = image_name(ws.course, ws.requires)
    older = "tutor-run-demo:0123456789ab"
    docker = FakeDocker(images=f"{image}\n{older}\n")
    assert build(ws, docker, say=lambda text: None) == image
    builds = [c for c in docker.commands if c[1] == "build"]
    assert len(builds) == 2 and all(c[-1] == "-" for c in builds)  # no build context/files
    assert docker.stdin[0] == "FROM python:3.11-slim-bookworm\n"
    assert docker.stdin[1].startswith("FROM tutor-base-")
    assert docker.commands[-1] == ["docker", "image", "rm", older]
    check = next(c for c in docker.commands if c[1] == "run" and "tutor.envcheck" in c[-1])
    assert "--network" in check and "tutor.envcheck" in check[-1]
    mounts = [check[i + 1] for i, flag in enumerate(check) if flag == "-v"]
    assert all(mount.endswith(":ro") for mount in mounts)
    assert len(mounts) == 2  # this package and workspace.toml, nothing else of the workspace


def test_a_failed_build_stops_there_and_keeps_the_older_image(tmp_path):
    docker = FakeDocker(fail={"build"}, images="tutor-run-demo:0123456789ab\n")
    with pytest.raises(ContainerError, match="build failed"):
        build(workspace(tmp_path), docker, say=lambda text: None)
    assert "image ls" not in docker.verbs()
    removed = [c[-1] for c in docker.commands if c[1:3] == ["image", "rm"]]
    assert all(name.startswith("tutor-base-") for name in removed)


def test_an_image_that_fails_its_check_is_removed(tmp_path):
    ws = workspace(tmp_path)
    docker = FakeDocker(fail={"run"})
    with pytest.raises(ContainerError, match="was removed"):
        build(ws, docker, say=lambda text: None)
    assert ["docker", "image", "rm", image_name(ws.course, ws.requires)] in docker.commands


def test_build_refuses_when_docker_is_not_running(tmp_path):
    docker = FakeDocker(fail={"info"})
    with pytest.raises(ContainerError, match="not running"):
        build(workspace(tmp_path), docker, say=lambda text: None)
    assert docker.verbs() == ["info"]


def test_the_command_reports_a_bad_workspace_and_exits_1(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    assert main(["status"]) == 1
    assert "WORKSPACE_DIR" in capsys.readouterr().err


def test_the_recipe_command_prints_what_a_build_would_send(monkeypatch, capsys, tmp_path):
    """The GUI shows this before its Build button, run with the course's own venv."""
    (tmp_path / "workspace.toml").write_text('course = "demo"\n[requires]\npython = "3.11"\n',
                                             encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr("tutor.image_recipe.installed_version",
                        {"pytest": "9.1.1"}.get)

    assert main(["recipe"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"# {image_name('demo', PLAIN_COURSE)}\nFROM python:3.11-slim")
    assert "pytest==9.1.1" in out  # this interpreter's version, as a build pins it


def test_image_size_reads_docker_and_says_none_when_missing():
    class Sized(FakeDocker):
        def __call__(self, command, **kwargs):
            done = super().__call__(command, **kwargs)
            return subprocess.CompletedProcess(command, done.returncode, "1729000000\n", "")

    assert image_size("tutor-run-demo:abc", Sized()) == 1_729_000_000
    assert image_size("tutor-run-demo:abc", Sized(fail={"image inspect"})) is None

    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    assert image_size("tutor-run-demo:abc", missing) is None


def test_the_module_only_mounts_the_package_itself():
    # Exercise tests import tutor.testkit from this mount; the repo's .env sits two levels up
    # and must stay out of reach.
    from tutor.container import TUTOR_DIR

    assert TUTOR_DIR.name == "tutor" and not (TUTOR_DIR / ".env").exists()
    assert TUTOR_DIR == Path(__file__).resolve().parents[1] / "src" / "tutor"
