"""Read-only environment identity and cleanup with content-keyed metadata stand-ins."""

import json
import subprocess
from pathlib import Path

import pytest

from tutor import doctor_checks, pythons, workspace
from tutor.doctor_advice import facts


def course(tmp_path, runner="host"):
    """Synthetic private workspace; executable marker is never actually launched."""
    root = tmp_path / "demo"
    root.mkdir()
    (root / "workspace.toml").write_text(
        f'course = "demo"\n[requires]\ndemo = "1"\n[isolation]\nrunner = "{runner}"\n',
        encoding="utf-8")
    python = workspace.venv_python(root / ".venv")
    python.parent.mkdir(parents=True)
    python.touch()
    return doctor_checks.read_course(root)


class Probe:
    """All replies depend on the command, including partial Docker failure scenarios."""

    def __init__(self):
        self.calls = []
        self.mode = "ready"
        self.data = {"python": "3.13.2", "installed": [["Demo", "1.0rc1+local"]]}

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["timeout"] > 0
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert not {"build", "pull", "install"}.intersection(command)
        if command[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(command, int(self.mode == "down"),
                                               "25.0" if self.mode != "down" else "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, int(self.mode == "no-image"), "", "")
        if command[:2] == ["docker", "kill"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        assert facts.PROBE in command
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(command, 35)
        if self.mode == "interrupt":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, int(self.mode == "failed"),
                                           json.dumps(self.data), "private stderr not displayed")


def find(**kwargs):
    return [pythons.Found("3.12.3", Path("python312"))]


def test_host_probe_uses_course_python_and_strips_credentials(tmp_path, monkeypatch):
    ctx, run = course(tmp_path), Probe()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    result = facts.collect(ctx, run=run, find=find)
    command = run.calls[0][0]
    assert command[0] == str(workspace.venv_python(ctx.root / ".venv"))
    assert "-I" in command and "-B" in command
    assert result.source == "course venv" and result.installed["demo"] == "1.0rc1+local"
    assert result.context.available_pythons == frozenset({"3.12.3"})
    assert result.context.requires == {"demo": "1"}


def test_no_course_is_explicit_tool_environment():
    result = facts.collect(None, run=Probe(), find=find)
    assert result.source == "tool interpreter" and not result.context.requires


def test_container_probe_has_no_mount_pull_or_host_python_choices(tmp_path):
    ctx, run = course(tmp_path, "container"), Probe()
    result = facts.collect(ctx, run=run, find=lambda **kw: pytest.fail("host discovery"))
    command = next(c for c, kw in run.calls if facts.PROBE in c)
    assert command[:2] == ["docker", "run"] and "--pull" in command and "never" in command
    assert "-v" not in command and "--mount" not in command
    assert "--read-only" in command and "none" in command and "timeout" in command
    assert result.source == "course image" and result.context.python_locked
    assert not result.context.available_pythons


@pytest.mark.parametrize("runner", ["host", "container"])
def test_docker_context_is_preserved_without_granting_it_to_python_probes(tmp_path,
                                                                       monkeypatch, runner):
    home = str(tmp_path / "synthetic-docker-home")
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("DOCKER_CONTEXT", "synthetic-context")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "sk-test-not-a-real-key")
    run = Probe()
    facts.collect(course(tmp_path, runner), run=run, find=find)
    for command, kwargs in run.calls:
        assert "DOCKER_AUTH_CONFIG" not in kwargs["env"]
        if command[0] == "docker":
            assert kwargs["env"]["HOME"] == home
            assert kwargs["env"]["DOCKER_CONTEXT"] == "synthetic-context"
        else:
            assert "HOME" not in kwargs["env"] and "DOCKER_CONTEXT" not in kwargs["env"]


@pytest.mark.parametrize("mode", ["down", "no-image", "failed"])
def test_unavailable_image_never_falls_back(tmp_path, mode):
    run = Probe()
    run.mode = mode
    with pytest.raises(facts.FactsError):
        facts.collect(course(tmp_path, "container"), run=run, find=find)
    assert all(c[0] == "docker" for c, kw in run.calls)


@pytest.mark.parametrize("mode", ["timeout", "interrupt"])
def test_probe_interruption_cleans_only_its_container(tmp_path, mode):
    run = Probe()
    run.mode = mode
    error = KeyboardInterrupt if mode == "interrupt" else facts.FactsError
    with pytest.raises(error):
        facts.collect(course(tmp_path, "container"), run=run, find=find)
    command = next(c for c, kw in run.calls if facts.PROBE in c)
    name = command[command.index("--name") + 1]
    assert ["docker", "kill", name] in [c for c, kw in run.calls]


def test_missing_course_python_does_not_probe_tool_interpreter(tmp_path):
    ctx, run = course(tmp_path), Probe()
    workspace.venv_python(ctx.root / ".venv").unlink()
    with pytest.raises(facts.FactsError, match="missing"):
        facts.collect(ctx, run=run, find=find)
    assert not run.calls


@pytest.mark.parametrize("data", [[], {"python": "bad", "installed": []},
                                 {"python": "3.13", "installed": "not a list"},
                                 {"python": "3.13", "installed": [["demo", "1"], ["DEMO", "2"]]},
                                 {"python": "3.13", "installed": [["bad name", "1"]]}])
def test_probe_data_is_strict_and_errors_do_not_echo_stderr(data):
    run = Probe()
    run.data = data
    with pytest.raises(facts.FactsError) as exc:
        facts.collect(None, run=run, find=find)
    assert "private stderr" not in str(exc.value)
