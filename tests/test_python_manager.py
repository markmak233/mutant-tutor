"""Optional interpreter management with command-keyed stand-ins, never real downloads."""

import json
import subprocess

import pytest

from tutor import python_manager as manager
from tutor import pythons
from tutor.config import load_workspace

IDENTIFIER = "cpython-3.12.10-macos-aarch64-none"


@pytest.fixture
def course(tmp_path):
    root = tmp_path / "courses" / "synthetic"
    root.mkdir(parents=True)
    (root / "lectures").mkdir()
    (root / "workspace.toml").write_text('course="synthetic"\n[requires]\npython="3.12"\n',
                                        encoding="utf-8")
    return load_workspace({"WORKSPACE_DIR": str(root)})


def fake_uv(tmp_path, calls):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs.get("env", {}).get("API_TOKEN") is None
        if "list" in command:
            installed = "--only-installed" in command
            rows = [{"key": IDENTIFIER, "path": str(tmp_path / "python") if installed else None},
                    {"key": "cpython-3.14.0+freethreaded-macos-aarch64-none"},
                    {"key": "pypy-3.11.11-macos-aarch64-none"}]
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        if "install" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1] == "-c":
            return subprocess.CompletedProcess(command, 0, "3.12.10\n" + command[0], "")
        pytest.fail("unexpected command")
    return run


def test_catalog_is_offline_read_only_and_filters_variants(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("API_TOKEN", "synthetic only")
    monkeypatch.setenv("UV_PYTHON_INSTALL_MIRROR", "https://invalid.example")
    rows = manager.catalog(which=lambda name: "uv", run=fake_uv(tmp_path, calls))
    assert [e.identifier for e in rows] == [IDENTIFIER]
    command, kwargs = calls[0]
    assert "--offline" in command and "--no-config" in command
    assert kwargs["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert "UV_PYTHON_INSTALL_MIRROR" not in kwargs["env"]


def test_missing_uv_gives_official_install_path():
    with pytest.raises(ValueError, match="docs.astral.sh"):
        manager.catalog(which=lambda name: None)


def test_explicit_download_rechecks_and_does_not_edit_course(course, tmp_path):
    calls = []
    before = (course.root / "workspace.toml").read_bytes()
    found = manager.install(course, IDENTIFIER, which=lambda name: "uv",
                            run=fake_uv(tmp_path, calls))
    assert found.path == tmp_path / "python"
    installs = [(command, kwargs) for command, kwargs in calls if "install" in command]
    assert len(installs) == 1 and installs[0][0][-2:] == ["--no-bin", IDENTIFIER]
    assert "UV_PYTHON_DOWNLOADS" not in installs[0][1]["env"]
    assert (course.root / "workspace.toml").read_bytes() == before
    assert not (course.root / ".venv").exists()


def test_download_refuses_unlisted_argument_before_install(course, tmp_path):
    calls = []
    with pytest.raises(ValueError, match="no longer offered"):
        manager.install(course, "https://invalid.example/python", which=lambda name: "uv",
                        run=fake_uv(tmp_path, calls))
    assert all("install" not in command for command, _ in calls)


def test_known_and_measured_conflicts_reject_incompatible_python():
    entry = manager.Entry("synthetic", "3.14.1", None)
    rows = [{"name": "pydub imports", "required": "audioop"}]
    reasons = manager.problems({"pyspark": "3.5"}, entry, rows)
    assert len(reasons) == 2 and "PySpark" in reasons[0] and "audioop" in reasons[1]


def test_managed_python_discovery_asks_interpreter_itself(tmp_path):
    found = pythons.find(which=lambda name: "uv" if name == "uv" else None,
                         run=fake_uv(tmp_path, []))
    assert [(f.version, f.path) for f in found] == [("3.12.10", tmp_path / "python")]


def test_cli_install_needs_explicit_confirmation(course, monkeypatch):
    monkeypatch.setattr(manager, "install", lambda *a, **k: pytest.fail("download without consent"))
    assert manager.main(["install", str(course.root), IDENTIFIER]) == 2


def test_gui_download_is_explicit_and_revalidates_identifier(course, monkeypatch):
    from tutor.gui import tasks
    from tutor.gui.app import create_app

    entries = [manager.Entry(IDENTIFIER, "3.12.10", None)]
    monkeypatch.setattr(manager, "catalog", lambda **k: entries)
    monkeypatch.setattr(manager, "course_results", lambda course: [])
    commands = []

    def start(root, title, command, env, meta):
        commands.extend(command)
        return tasks.Task("20260102-100000-abcdef", root, title, "running", "", None,
                          command, [], None, meta)

    monkeypatch.setattr(tasks, "start", start)
    client = create_app(course.root.parent, "synthetic", port=8000).test_client()
    base = "http://127.0.0.1:8000"
    client.get("/?token=synthetic", base_url=base)
    path = "/c/synthetic/environment/python-downloads"
    assert client.get(path, base_url=base).status_code == 200 and commands == []
    headers = {"Origin": base}
    assert client.post(path, base_url=base, headers=headers,
                       data={"identifier": IDENTIFIER}).status_code == 400
    assert client.post(path, base_url=base, headers=headers,
                       data={"identifier": "unlisted", "confirm": "yes"}).status_code == 400
    assert commands == []
    assert client.post(path, base_url=base, headers=headers,
                       data={"identifier": IDENTIFIER, "confirm": "yes"}).status_code == 303
    assert commands[0][-2:] == [IDENTIFIER, "--confirm"]
