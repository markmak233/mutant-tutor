import os
from pathlib import Path

import pytest

from tutor.config import WorkspaceError, load_env, load_workspace


def make_workspace(path: Path, toml: str = 'course = "demo"\n') -> Path:
    """Create a minimal workspace: a directory holding the given workspace.toml."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.toml").write_text(toml)
    return path


def test_loads_course_requires_and_default_lecture_dir(tmp_path):
    """Happy path: course and [requires] come from workspace.toml, and lectures/ and
    batches/ default to subfolders of the workspace."""
    root = make_workspace(tmp_path / "ws", 'course = "demo"\n[requires]\npython = "3.11"\n')

    ws = load_workspace({"WORKSPACE_DIR": str(root)})

    assert ws.course == "demo"
    assert ws.requires == {"python": "3.11"}
    assert ws.lecture_dir == root.resolve() / "lectures"
    assert ws.batches_dir == root.resolve() / "batches"


def test_lecture_dir_override(tmp_path):
    """LECTURE_DIR, when set, replaces the default <workspace>/lectures."""
    root = make_workspace(tmp_path / "ws")
    lectures = tmp_path / "elsewhere"
    lectures.mkdir()

    ws = load_workspace({"WORKSPACE_DIR": str(root), "LECTURE_DIR": str(lectures)})

    assert ws.lecture_dir == lectures.resolve()


def test_unset_workspace_dir():
    """No WORKSPACE_DIR at all (e.g. no .env yet): fail with a message saying how to fix it."""
    with pytest.raises(WorkspaceError, match="WORKSPACE_DIR is not set"):
        load_workspace({})


def test_missing_directory(tmp_path):
    """A typo in WORKSPACE_DIR fails loudly instead of silently creating an empty workspace."""
    with pytest.raises(WorkspaceError, match="does not exist"):
        load_workspace({"WORKSPACE_DIR": str(tmp_path / "nope")})


def test_directory_without_workspace_toml(tmp_path):
    """An existing folder without workspace.toml is not a workspace, so it is rejected."""
    with pytest.raises(WorkspaceError, match="workspace.toml"):
        load_workspace({"WORKSPACE_DIR": str(tmp_path)})


def test_refuses_workspace_inside_git_repo(tmp_path):
    """Safety rule: private material inside a git repo could get committed, so refuse it.
    The .git folder sits two levels up to show that every parent directory is checked."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    root = make_workspace(tmp_path / "repo" / "data" / "ws")

    with pytest.raises(WorkspaceError, match="inside the git repository"):
        load_workspace({"WORKSPACE_DIR": str(root)})


def test_refuses_lecture_dir_inside_git_repo(tmp_path):
    """The same safety rule applies to LECTURE_DIR, even when the workspace itself is fine."""
    root = make_workspace(tmp_path / "ws")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    lectures = tmp_path / "repo" / "lectures"
    lectures.mkdir()

    with pytest.raises(WorkspaceError, match="LECTURE_DIR is inside"):
        load_workspace({"WORKSPACE_DIR": str(root), "LECTURE_DIR": str(lectures)})


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    """.env fills in missing variables (comments skipped, quotes stripped) but never
    overrides a variable already exported in the shell."""
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nTUTOR_A="from file"\nTUTOR_B=from file\n')
    monkeypatch.delenv("TUTOR_A", raising=False)
    monkeypatch.setenv("TUTOR_B", "from shell")

    load_env(env_file)

    assert os.environ["TUTOR_A"] == "from file"
    assert os.environ["TUTOR_B"] == "from shell"
    monkeypatch.delenv("TUTOR_A")
