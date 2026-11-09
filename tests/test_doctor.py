"""Doctor's 26 required scenarios use synthetic courses and content-keyed tool stand-ins.

Smoke lifecycle and language extensibility have their own test modules. No automatic
test reads an owner's course, loads .env, installs software or contacts a model.
"""

import builtins
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from tutor import __main__ as dispatcher
from tutor import (
    config,
    course_requirements,
    doctor,
    doctor_checks,
    doctor_languages,
    envcheck,
    llm,
)


class Tools:
    """Respond by command contents, never call order, and enforce bounded text probes."""

    def __init__(self):
        self.calls = []
        self.docker = "ready"
        self.venv = "ready"
        self.image = "ready"
        self.requirements = []  # the child interpreter's answer, never parent metadata

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        assert 0 < kwargs["timeout"] <= 60
        assert not {"build", "pull", "install", "sudo"}.intersection(command)
        if command[:2] == ["docker", "info"]:
            if self.docker == "missing":
                raise FileNotFoundError("docker")
            if self.docker == "timeout":
                raise subprocess.TimeoutExpired(command, 30)
            if self.docker == "interrupt":
                raise KeyboardInterrupt
            return subprocess.CompletedProcess(command, int(self.docker == "down"),
                                               "25.0" if self.docker == "ready" else "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, int(self.image != "ready"), "", "")
        if command[-2:] == ["-c", "import tutor"]:
            if self.venv == "missing":
                raise FileNotFoundError("removed interpreter")
            if self.venv == "timeout":
                raise subprocess.TimeoutExpired(command, 30)
            return subprocess.CompletedProcess(command, int(self.venv != "ready"), "", "")
        if command[-1] == course_requirements.PROBE:
            answer = {"results": [{**asdict(r), "ok": r.ok} for r in self.requirements]}
            return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")
        if command[0] == "gcc":
            return subprocess.CompletedProcess(command, 0, "12.2.0", "")
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, "", 'openjdk version "17.0.1"')
        raise AssertionError(f"unexpected probe: {command}")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Neutral tool metadata makes each test's deliberately broken check easy to see."""
    tools = Tools()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor_checks.metadata, "version", lambda name: "1.0")
    monkeypatch.setattr(doctor_checks.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(envcheck, "check_workspace",
                        lambda *a, **k: pytest.fail("doctor checked the tool interpreter"))
    # Linux hosts differ in mmap settings; dedicated language tests cover that probe.
    monkeypatch.setitem(doctor_languages.DOCTORS, "c",
                        replace(doctor_languages.DOCTORS["c"], machine_checks=lambda run: []))

    def forbidden(*args, **kwargs):
        pytest.fail("doctor called a model or loaded .env")

    monkeypatch.setattr(config, "load_env", forbidden)
    monkeypatch.setattr(envcheck, "load_env", forbidden)
    monkeypatch.setattr(llm, "chat", forbidden)
    return tools


def course(tmp_path, *, runner="host", declarations="", venv=True, script=True):
    """Build only metadata and empty executable markers, never run them directly."""
    root = tmp_path / "course"
    root.mkdir(exist_ok=True)
    text = 'course = "demo"\n' + declarations
    if runner is not None:
        text += f'\n[isolation]\nrunner = "{runner}"\n'
    (root / "workspace.toml").write_text(text, encoding="utf-8")
    if venv:
        executable = doctor_checks.workspace.venv_python(root / ".venv")
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        if script:
            executable.with_name("tutor.exe" if sys.platform == "win32" else "tutor").touch()
    return root


def test_missing_folder_runs_no_checks(tmp_path, rig, capsys):
    assert doctor.main([str(tmp_path / "absent")], run=rig) == 2
    out = capsys.readouterr()
    assert len(out.err.splitlines()) == 1 and out.out == "" and not rig.calls


def test_deep_folder_finds_nearest_course(tmp_path, rig, capsys):
    root = course(tmp_path)
    nested = root / "batches" / "demo" / "exercises" / "item"
    nested.mkdir(parents=True)
    assert doctor.main([str(nested)], run=rig) == 0
    assert "workspace.toml loads" in capsys.readouterr().out


def test_no_course_runs_tool_checks_and_exits_zero(rig, capsys):
    assert doctor.main([], run=rig) == 0
    text = capsys.readouterr().out
    assert "no course found" in text and "All checks passed" in text


def test_bad_toml_fails_load_and_skips_all_five_course_checks(tmp_path, rig, capsys):
    root = course(tmp_path)
    (root / "workspace.toml").write_text("[broken", encoding="utf-8")
    assert doctor.main([str(root), "--smoke"], run=rig) == 1
    text = capsys.readouterr().out
    assert text.count("workspace.toml did not load") == 5 and "Traceback" not in text


@pytest.mark.parametrize("mode,phrase", [("missing", "not installed"), ("down", "not running"),
                                        ("empty", "not running"), ("timeout", "did not answer")])
def test_docker_failures_warn_without_a_container_course(rig, capsys, mode, phrase):
    rig.docker = mode
    assert doctor.main([], run=rig) == 0
    text = capsys.readouterr().out
    assert "WARN  Docker" in text and phrase in text and "0 failed, 1 warnings" in text


def test_host_docker_problem_warns_and_image_skips(tmp_path, rig, capsys):
    root = course(tmp_path)
    rig.docker = "down"
    assert doctor.main([str(root)], run=rig) == 0
    text = capsys.readouterr().out
    assert "WARN  Docker" in text and "test image: this course runs" in text


@pytest.mark.parametrize("available", [True, False])
def test_container_checks_image_or_skips_without_reprobing(tmp_path, rig, capsys, available):
    root = course(tmp_path, runner="container")
    rig.docker = "ready" if available else "down"
    assert doctor.main([str(root), *([] if available else ["--smoke"])], run=rig) == (
        0 if available else 1)
    text = capsys.readouterr().out
    assert "tools on this machine: not needed" in text
    inspected = [c for c in rig.calls if c[:2] == ["docker", "image"]]
    assert bool(inspected) is available
    if not available:
        assert "FAIL  Docker" in text and "smoke: course image is not available" in text
        assert len([c for c in rig.calls if c[:2] == ["docker", "info"]]) == 1


def test_runner_missing_warns_and_checks_host(tmp_path, rig, capsys):
    root = course(tmp_path, runner=None)
    assert doctor.main([str(root)], run=rig) == 0
    assert any(c[-1] == course_requirements.PROBE for c in rig.calls)
    assert "runner not chosen" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["absent", "broken", "missing", "timeout", "no-script"])
def test_venv_states_have_specific_repair_paths(tmp_path, rig, capsys, mode):
    root = course(tmp_path, venv=mode != "absent", script=mode != "no-script")
    rig.venv = mode if mode not in ("absent", "no-script") else "ready"
    assert doctor.main([str(root)], run=rig) == (0 if mode == "no-script" else 1)
    text = capsys.readouterr().out
    assert "python -m tutor.workspace init" in text
    if mode not in ("absent", "no-script"):
        assert "--replace-venv" in text


def test_windows_venv_paths(tmp_path, rig, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    root = course(tmp_path)
    executable = root / ".venv" / "Scripts" / "python.exe"
    assert doctor.main([str(root)], run=rig) == 0
    assert [str(executable), "-c", "import tutor"] in rig.calls
    assert "has the tutor command" in capsys.readouterr().out


def test_empty_requirement_hint_falls_back(tmp_path, rig, capsys):
    root = course(tmp_path)
    rig.requirements = [envcheck.CheckResult("missing", "1", None)]
    assert doctor.main([str(root)], run=rig) == 1
    assert "next: see the Environment page, step 2" in capsys.readouterr().out


def test_host_packages_and_python_come_from_course_venv(tmp_path, rig, monkeypatch, capsys):
    """The tool lacks this synthetic package; the subprocess's installed version wins."""
    root = course(tmp_path, declarations='[requires]\npython="3.12"\nexamplelib="1"\n')

    def absent(name):
        if name == "tutor":
            return "1.0"
        raise envcheck.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(envcheck.metadata, "version", absent)
    rig.requirements = [envcheck.CheckResult("python", "3.12", "3.12.9"),
                        envcheck.CheckResult("examplelib", "1", "1.4")]
    assert doctor.main([str(root)], run=rig) == 0
    output = capsys.readouterr().out
    assert "OK    host examplelib: required 1, found 1.4" in output
    assert "OK    host python: required 3.12, found 3.12.9" in output
    probes = [c for c in rig.calls if c[-1] == course_requirements.PROBE]
    expected = doctor_checks.workspace.venv_python(root / ".venv")
    assert len(probes) == 1 and probes[0][0] == str(expected)


@pytest.mark.parametrize("state", ["absent", "broken", "missing", "timeout"])
def test_unavailable_venv_skips_host_requirements_without_duplicate_failure(
        tmp_path, rig, capsys, state):
    root = course(tmp_path, venv=state != "absent")
    rig.venv = state if state != "absent" else "ready"
    assert doctor.main([str(root)], run=rig) == 1
    output = capsys.readouterr().out
    assert len([line for line in output.splitlines() if line.startswith("FAIL ")]) == 1
    assert "SKIP  tools on this machine: see the .venv row above" in output
    assert not any(c[-1] == course_requirements.PROBE for c in rig.calls)


def test_unexpected_error_is_one_failure_and_later_checks_run(tmp_path, rig, monkeypatch, capsys):
    root = course(tmp_path)

    def broken():
        raise RuntimeError("unexpected problem")

    monkeypatch.setattr(doctor_checks, "tool_version", broken)
    assert doctor.main([str(root)], run=rig) == 1
    text = capsys.readouterr().out
    assert "the check itself failed: RuntimeError: unexpected problem" in text
    assert "test image:" in text and "Traceback" not in text


def test_ctrl_c_is_130_without_traceback(rig, capsys):
    rig.docker = "interrupt"
    assert doctor.main([], run=rig) == 130
    assert capsys.readouterr().err == "doctor: interrupted\n"


def test_output_redacts_environment_values_even_from_exceptions(rig, monkeypatch, capsys):
    sentinel = "sk-test-not-a-real-key"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)

    def bad():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(doctor_checks, "tool_version", bad)
    assert doctor.main([], run=rig) == 1
    assert sentinel not in capsys.readouterr().out


@pytest.mark.parametrize("entry", ["installed", "module"])
def test_help_uses_command_parser(entry):
    # Both installed and module entry points resolve to this dispatcher in pyproject.toml.
    with pytest.raises(SystemExit) as exc:
        dispatcher.main(["doctor", "--help"])
    assert exc.value.code == 0


def test_dispatch_preserves_test_and_unknown_commands(monkeypatch):
    from tutor import test_command
    monkeypatch.setattr(test_command, "main", lambda args: 7 if args == ["--help"] else 9)
    assert dispatcher.main(["test", "--help"]) == 7
    with pytest.raises(SystemExit) as exc:
        dispatcher.main(["unknown-command"])
    assert exc.value.code == 2


@pytest.mark.parametrize("declaration", ['[languages.java]\n', '[requires]\njavac = "17"\n'])
def test_unsupported_declarations_are_reported_once(tmp_path, rig, capsys, monkeypatch,
                                                  declaration):
    root = course(tmp_path, declarations=declaration)
    # Include envcheck's duplicate so the diagnostic must deduplicate across C1 and C4 too.
    problem = ("[languages.java]: java is not supported yet (exercises can be in Python or C)"
               if "languages" in declaration else envcheck.requires_problems({"javac": "17"})[0])
    rig.requirements = [envcheck.CheckResult("unsupported", "17", None, problem)]
    assert doctor.main([str(root)], run=rig) == 1
    assert capsys.readouterr().out.count(problem) == 1


def test_python_only_has_no_c_rows(tmp_path, rig, capsys, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    root = course(tmp_path)
    assert doctor.main([str(root)], run=rig) == 0
    text = capsys.readouterr().out
    assert "mmap" not in text and "C runs" not in text


def test_core_has_no_language_branches():
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert not any(token in source for token in ('"c"', "'c'", "gcc", "mmap"))


def test_rows_have_next_steps_only_for_warn_and_fail(tmp_path, rig, capsys):
    root = course(tmp_path, runner=None, venv=False)
    rig.docker = "missing"
    assert doctor.main([str(root)], run=rig) == 1
    lines = capsys.readouterr().out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("OK   ", "SKIP ", "WARN ", "FAIL ")):
            has_next = i + 1 < len(lines) and lines[i+1].startswith("      next:")
            assert has_next == line.startswith(("WARN ", "FAIL "))


def test_optional_extras_and_source_tree_warn(rig, monkeypatch, capsys):
    def absent(name):
        raise doctor_checks.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(doctor_checks.metadata, "version", absent)
    monkeypatch.setattr(doctor_checks.importlib.util, "find_spec", lambda name: None)
    assert doctor.main([], run=rig) == 0
    text = capsys.readouterr().out
    assert "source tree without installing" in text and "0 failed, 3 warnings" in text


def test_old_tool_python_fails(rig, monkeypatch, capsys):
    # A real old interpreter lacks tomllib: the bootstrap must diagnose before importing
    # project checks/validation, not merely pass a test after those modules were loaded.
    original = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        assert not (name == "tutor" and {"doctor_checks", "doctor_smoke"}.intersection(fromlist))
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(sys, "version_info", (3, 10, 1))
    assert doctor.main([], run=rig) == 1
    assert "install Python 3.11+" in capsys.readouterr().out
