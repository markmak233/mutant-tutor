import os
from pathlib import Path

import pytest

from tutor.config import WorkspaceError, load_env, load_workspace


def make_workspace(path: Path, toml: str = 'course = "demo"\n') -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.toml").write_text(toml)
    return path


def test_loads_course_requires_and_default_lecture_dir(tmp_path):
    root = make_workspace(tmp_path / "ws", 'course = "demo"\n[requires]\npython = "3.11"\n')

    ws = load_workspace({"WORKSPACE_DIR": str(root)})

    assert ws.course == "demo"
    assert ws.requires == {"python": "3.11"}
    assert ws.lecture_dir == root.resolve() / "lectures"
    assert ws.batches_dir == root.resolve() / "batches"


def test_lecture_dir_override(tmp_path):
    root = make_workspace(tmp_path / "ws")
    lectures = tmp_path / "elsewhere"
    lectures.mkdir()

    ws = load_workspace({"WORKSPACE_DIR": str(root), "LECTURE_DIR": str(lectures)})

    assert ws.lecture_dir == lectures.resolve()


def test_unset_workspace_dir():
    with pytest.raises(WorkspaceError, match="WORKSPACE_DIR is not set"):
        load_workspace({})


def test_missing_directory(tmp_path):
    with pytest.raises(WorkspaceError, match="does not exist"):
        load_workspace({"WORKSPACE_DIR": str(tmp_path / "nope")})


def test_directory_without_workspace_toml(tmp_path):
    with pytest.raises(WorkspaceError, match="workspace.toml"):
        load_workspace({"WORKSPACE_DIR": str(tmp_path)})


def test_refuses_workspace_inside_git_repo(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    root = make_workspace(tmp_path / "repo" / "data" / "ws")

    with pytest.raises(WorkspaceError, match="inside the git repository"):
        load_workspace({"WORKSPACE_DIR": str(root)})


def test_refuses_lecture_dir_inside_git_repo(tmp_path):
    root = make_workspace(tmp_path / "ws")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    lectures = tmp_path / "repo" / "lectures"
    lectures.mkdir()

    with pytest.raises(WorkspaceError, match="LECTURE_DIR is inside"):
        load_workspace({"WORKSPACE_DIR": str(root), "LECTURE_DIR": str(lectures)})


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nTUTOR_A="from file"\nTUTOR_B=from file\n')
    monkeypatch.delenv("TUTOR_A", raising=False)
    monkeypatch.setenv("TUTOR_B", "from shell")

    load_env(env_file)

    assert os.environ["TUTOR_A"] == "from file"
    assert os.environ["TUTOR_B"] == "from shell"
    monkeypatch.delenv("TUTOR_A")
