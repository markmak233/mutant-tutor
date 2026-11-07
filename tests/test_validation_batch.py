"""`python -m tutor.validation.batch <label>`: the command the GUI chains after generating a
batch. It adds no rule of its own; these check that it validates what the manifest lists and
writes each report, on a copy of the repo's plain-Python example (no JVM: fast suite)."""

import json
import shutil
from pathlib import Path

import pytest

from tutor.validation.batch import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "station-longest-rise"
NAME = "station-longest-rise"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[isolation]\nrunner = "host"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def _batch(workspace: Path, ids: list[str]) -> Path:
    batch = workspace / "batches" / "2026-11-06_p3-demo"
    shutil.copytree(EXAMPLE / "exercise", batch / "exercises" / NAME)
    shutil.copytree(EXAMPLE / "solution", batch / "solutions" / NAME)
    # A report from a local run of the example (git-ignored, so absent in a fresh clone).
    (batch / "solutions" / NAME / "validation.json").unlink(missing_ok=True)
    (batch / "manifest.json").write_text(json.dumps({"exercises": ids}), encoding="utf-8")
    return batch


def test_every_listed_exercise_is_validated_and_its_report_written(workspace, capsys):
    batch = _batch(workspace, [NAME])

    assert main(["demo"]) == 0

    report = json.loads((batch / "solutions" / NAME / "validation.json").read_text(
        encoding="utf-8"))
    assert report["valid"] is True
    assert "2026-11-06_p3-demo: 1 of 1 valid" in capsys.readouterr().out


def test_one_that_does_not_validate_fails_the_run(workspace, capsys):
    _batch(workspace, [NAME, "not-there"])

    assert main(["demo"]) != 0
    assert "1 of 2 valid" in capsys.readouterr().out


def test_a_batch_with_no_manifest_yet_has_nothing_to_validate(workspace, capsys):
    """A run that stopped midway: its own exit code says so, not this one."""
    assert main(["demo"]) == 0
    assert "nothing to validate" in capsys.readouterr().out
