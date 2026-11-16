"""The release's per-OS install instructions (packaging/INSTALL-*.txt) against the code they
describe: the version and wheel name, the extras the install command names, and the courses
folder an installed tool uses. A version bump or a moved folder then fails here instead of
leaving a user with a command that cannot work."""

import sys
import tomllib
from pathlib import Path

import pytest

from tutor import locations

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"
SYSTEMS = {"macOS": "darwin", "Linux": "linux", "Windows": "win32"}


def _project() -> dict:
    with (REPO / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]


def _text(system: str) -> str:
    return (PACKAGING / f"INSTALL-{system}.txt").read_text(encoding="utf-8")


def test_there_is_one_instruction_file_per_system():
    assert sorted(p.name for p in PACKAGING.glob("INSTALL-*.txt")) == sorted(
        f"INSTALL-{system}.txt" for system in SYSTEMS)


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_install_command_names_this_version_and_existing_extras(system):
    project = _project()
    wheel = f"tutor-{project['version']}-py3-none-any.whl"
    text = _text(system)
    assert text.startswith(f"Tutor {project['version']} on {system}: how to install it")
    assert f'uv tool install --python 3.11 "{wheel}[gen,gui,doctor]"' in text
    assert {"gen", "gui", "doctor"} <= set(project["optional-dependencies"])


@pytest.mark.parametrize("system", SYSTEMS)
def test_plain_ascii_so_any_editor_or_browser_shows_it(system):
    # Opened in Notepad, a browser or a terminal on any of the three systems.
    (PACKAGING / f"INSTALL-{system}.txt").read_bytes().decode("ascii")


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_courses_folder_is_where_an_installed_tool_keeps_them(system, monkeypatch, tmp_path):
    # locations.home() decides by sys.platform and the environment when it is called, so
    # each system's answer can be computed here on any one machine.
    monkeypatch.delenv("TUTOR_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", SYSTEMS[system])
    courses = locations.home() / "courses"  # the default `tutor gui` uses (gui/__main__.py)
    if system == "Windows":
        written = "%LOCALAPPDATA%\\" + "\\".join(courses.relative_to(tmp_path / "Local").parts)
    else:
        written = "~/" + "/".join(courses.relative_to(tmp_path).parts)
    assert written in _text(system)


def test_windows_on_arm_is_said_to_be_unsupported():
    # sqlite-vec (through langgraph-checkpoint-sqlite) has no Windows ARM64 wheel, and
    # PyYAML none for Python 3.11 there (docs/design/packaging.md, "Packaging and release").
    assert "Windows on ARM\nis not supported yet" in _text("Windows")
