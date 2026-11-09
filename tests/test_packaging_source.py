"""Controlled source/resources and noneditable wheel installs; no network or real course."""

import importlib.util
import zipfile
from pathlib import Path

import pytest
import test_workspace as support

from tutor import project_source


def builder():
    path = Path(__file__).resolve().parents[1] / "build_support.py"
    spec = importlib.util.spec_from_file_location("public_build_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def public_tree(root):
    files = {"pyproject.toml": "[project]\nname='demo'\n", "setup.py": "# synthetic\n",
             "src/tutor/demo.py": "VALUE = 1\n", "examples/demo/exercise/student.py": "VALUE = 1\n",
             "notes/private.md": "synthetic private note", ".env": "synthetic only",
             "src/tutor/.DS_Store": "synthetic", "src/tutor/__pycache__/ignored.py": "ignored"}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_public_bundle_excludes_private_roots_and_caches_and_contains_shipped_examples(tmp_path):
    root, package = tmp_path / "source", tmp_path / "installed" / "tutor"
    public_tree(root)
    builder().bundle(root, package)
    with zipfile.ZipFile(package / "_source.zip") as archive:
        names = set(archive.namelist())
    assert "src/tutor/demo.py" in names and "examples/demo/exercise/student.py" in names
    assert not any(n.startswith("notes/") or ".env" in n or "__pycache__" in n for n in names)
    assert (package / "_examples" / "demo" / "exercise" / "student.py").is_file()


def test_installed_source_is_noneditable_and_temporary(tmp_path, monkeypatch):
    root, package = tmp_path / "source", tmp_path / "installed" / "tutor"
    public_tree(root)
    builder().bundle(root, package)
    monkeypatch.setattr(project_source, "__file__", str(package / "project_source.py"))
    monkeypatch.setattr(project_source, "checkout", lambda: None)
    with project_source.source() as selected:
        assert not selected.editable and (selected.path / "src" / "tutor" / "demo.py").is_file()
        extracted = selected.path
    assert not extracted.exists() and (package / "_source.zip").is_file()


def test_source_archive_rejects_traversal_before_extracting(tmp_path, monkeypatch):
    package = tmp_path / "tutor"
    package.mkdir()
    with zipfile.ZipFile(package / "_source.zip", "w") as archive:
        archive.writestr("../outside.txt", "synthetic")
    monkeypatch.setattr(project_source, "__file__", str(package / "project_source.py"))
    monkeypatch.setattr(project_source, "checkout", lambda: None)
    with pytest.raises(ValueError, match="unsafe"), project_source.source():
        pytest.fail("unsafe archive yielded a source")
    assert not (tmp_path / "outside.txt").exists()


def test_packaged_course_install_keeps_no_editable_temporary_reference(tmp_path, monkeypatch):
    root, package = tmp_path / "source", tmp_path / "installed" / "tutor"
    public_tree(root)
    builder().bundle(root, package)
    monkeypatch.setattr(project_source, "__file__", str(package / "project_source.py"))
    monkeypatch.setattr(project_source, "checkout", lambda: None)
    fake = support.FakeInstall()
    support.make(tmp_path / "course", fake)
    command = fake.commands[0]
    assert "-e" not in command and command[5].endswith("[gen,dev]")
