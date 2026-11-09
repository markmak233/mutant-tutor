"""Smoke selects shipped examples and cleans disposable copies without model calls."""

import sys
from pathlib import Path

import pytest
import test_doctor as support

from tutor import doctor, doctor_smoke, llm
from tutor.container import ContainerError
from tutor.execution import Outcome, RunResult
from tutor.validation import RuleResult, Status, ValidationReport, checks

rig = support.rig


@pytest.mark.parametrize("language,example", [("python", "station-longest-rise"),
                                             ("c", "station-above-average-c")])
def test_selects_language_example_and_writes_only_in_temp(tmp_path, rig, capsys, language, example):
    root = support.course(tmp_path, runner="container",
                          declarations="[languages.c]\n" if language == "c" else "")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    copies = []
    cwd = Path.cwd()

    def validator(exercise):
        copies.append(Path.cwd())
        assert Path.cwd() != root and exercise.exercise_id == example
        assert not (exercise.reference.parent / "validation.json").exists()
        copied = (Path.cwd() / "workspace.toml").read_bytes()
        assert copied == (root / "workspace.toml").read_bytes()
        return ValidationReport(example, [])

    assert doctor.main([str(root), "--smoke"], run=rig, validator=validator) == 0
    assert copies and all(not p.exists() for p in copies) and Path.cwd() == cwd
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert f"OK    smoke {example}: VALID" in capsys.readouterr().out


@pytest.mark.parametrize("outcome", ["invalid", "exception", "interrupt", "container"])
def test_temp_removed_and_cwd_restored_on_failure(tmp_path, rig, capsys, outcome):
    root = support.course(tmp_path)
    copies = []
    cwd = Path.cwd()

    def validator(exercise):
        copies.append(Path.cwd())
        if outcome == "exception":
            raise RuntimeError("validator broke")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "container":
            raise ContainerError("Docker stopped during smoke")
        return ValidationReport(exercise.exercise_id, [
            RuleResult("1", "reference", Status.FAIL, "synthetic failure", 0.0)])

    assert doctor.main([str(root), "--smoke"], run=rig, validator=validator) == (
        130 if outcome == "interrupt" else 1)
    assert copies and all(not p.exists() for p in copies) and Path.cwd() == cwd
    text = capsys.readouterr().out
    assert "Traceback" not in text
    if outcome == "invalid":
        assert "validator exit code 1" in text and "read the validator's lines above" in text
    if outcome == "container":
        assert "FAIL  smoke: Docker stopped" in text


def test_missing_examples_skips(tmp_path, rig, monkeypatch, capsys):
    root = support.course(tmp_path)
    monkeypatch.setattr(doctor_smoke, "examples_root", lambda: tmp_path / "no-examples")
    assert doctor.main([str(root), "--smoke"], run=rig) == 0
    assert "no shipped examples" in capsys.readouterr().out


def test_host_c_smoke_skips_on_macos(tmp_path, rig, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    root = support.course(tmp_path, declarations="[languages.c]\n")
    assert doctor.main([str(root), "--smoke"], run=rig) == 0
    assert "SKIP  smoke: C is validated only in a container" in capsys.readouterr().out


def test_c_validation_policy_does_not_fail_host_requirements_on_macos(tmp_path, rig,
                                                                    monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    root = support.course(tmp_path, declarations="[languages.c]\n")
    assert doctor.main([str(root)], run=rig) == 0
    text = capsys.readouterr().out
    assert "FAIL" not in text and "C runs in WSL" not in text


def test_actual_validate_path_never_calls_model_or_loads_env(tmp_path, rig, monkeypatch, capsys):
    """Keep the real validator, substituting only execution outcomes keyed by candidate."""
    root = support.course(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("smoke validation contacted a model or loaded .env")

    monkeypatch.setattr(llm, "chat", forbidden)
    monkeypatch.setattr(llm, "embed", forbidden)
    monkeypatch.setattr("tutor.validation.__main__.load_env", forbidden)

    def run(files, *args, **kwargs):
        candidate = files["student.py"].name
        calls.append(candidate)
        passed = candidate == "reference.py"
        return RunResult(Outcome.PASSED if passed else Outcome.FAILED,
                         0 if passed else 1, 0.0, "")

    monkeypatch.setattr(checks, "run_pytest", run)
    monkeypatch.setattr(checks, "run_script", run)
    assert doctor.main([str(root), "--smoke"], run=rig) == 0
    assert "reference.py" in calls and "skeleton.py" in calls
    assert "VALID" in capsys.readouterr().out
