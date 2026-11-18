"""tools/release_notes.py: the part of CHANGELOG.md a GitHub Release page shows."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_notes.py"

# tools/ is not a package: the script is loaded from its file.
_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## Unreleased

- Not out yet.

## 0.2.0 (2027-01-05)

- **Second.** A thing.
  Its second line.

## 0.1.0 (2026-11-17)

The first release.

- One.
"""


def test_a_section_runs_from_its_heading_to_the_next():
    assert release_notes.section(CHANGELOG, "0.2.0") == "- **Second.** A thing.\n  Its second line."


def test_the_last_section_runs_to_the_end_of_the_file():
    assert release_notes.section(CHANGELOG, "0.1.0") == "The first release.\n\n- One."


def test_a_heading_without_a_date_is_found_too():
    assert release_notes.section("## 1.0.0\n\n- Out.\n", "1.0.0") == "- Out."


def test_a_version_is_matched_whole():
    # 0.1 is not 0.1.0, and 0.1.0 is not the start of 0.1.0rc1.
    assert release_notes.section(CHANGELOG, "0.1") is None
    assert release_notes.section("## 0.1.0rc1 (2026-11-01)\n\n- Early.\n", "0.1.0") is None


def test_a_missing_version_is_none():
    assert release_notes.section(CHANGELOG, "9.9.9") is None


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", check=False)


def test_the_command_prints_the_section(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    done = _run("0.1.0", cwd=tmp_path)
    assert done.returncode == 0
    assert done.stdout == "The first release.\n\n- One.\n"


def test_the_command_refuses_a_missing_or_empty_section(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("## 0.3.0\n\n## 0.2.0\n\n- Something.\n", encoding="utf-8")
    missing = _run("0.4.0", str(path), cwd=tmp_path)
    assert missing.returncode == 1 and "no section" in missing.stderr and missing.stdout == ""
    empty = _run("0.3.0", str(path), cwd=tmp_path)
    assert empty.returncode == 1 and "is empty" in empty.stderr and empty.stdout == ""


def test_the_command_refuses_a_file_it_cannot_read(tmp_path):
    done = _run("0.1.0", str(tmp_path / "nowhere.md"), cwd=tmp_path)
    assert done.returncode == 1 and "cannot read" in done.stderr


def test_the_repository_changelog_has_notes_for_the_current_version():
    # What the publish job would show for the version in pyproject.toml, once it is tagged:
    # either its own section, or none yet while the changes sit under "Unreleased".
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    found = release_notes.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    assert found is None or found.strip()
