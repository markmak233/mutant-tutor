"""Language registry completeness, Linux ASan settings and a stand-in third language."""

import json
import sys
from pathlib import Path

import pytest
import test_doctor as support

from tutor import doctor, doctor_languages, languages
from tutor.doctor_languages import c
from tutor.doctor_rows import Row

rig = support.rig


def test_registry_complete_and_examples_match_language():
    """A new exercise language must also bring a diagnostic part and a usable example."""
    assert set(doctor_languages.DOCTORS) == set(languages.LANGUAGES)
    examples = Path(__file__).resolve().parents[1] / "examples"
    for key, part in doctor_languages.DOCTORS.items():
        metadata = json.loads((examples / part.smoke_example / "solution" / "metadata.json")
                              .read_text(encoding="utf-8"))
        if key == "python":
            assert "language" not in metadata and metadata["framework"] == "python"
        else:
            assert metadata["language"] == key


@pytest.mark.parametrize("value,status", [("32", "WARN"), ("28", "OK"),
                                         (None, "SKIP"), ("invalid", "SKIP")])
def test_linux_mmap_bits_on_c_course(tmp_path, rig, monkeypatch, capsys, value, status):
    """Exercise the hook through the command, and only fake the one procfs read."""
    # The platform first: the course's venv marker goes where a Linux doctor looks for it
    # (.venv/bin/python), whatever the machine running the test (Windows CI, 2026-11-11).
    monkeypatch.setattr(sys, "platform", "linux")
    root = support.course(tmp_path, declarations="[languages.c]\n")
    monkeypatch.setitem(doctor_languages.DOCTORS, "c", c.DOCTOR)
    original = Path.read_text

    def read(path, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        if path == Path("/proc/sys/vm/mmap_rnd_bits"):
            if value is None:
                raise FileNotFoundError(path)
            return value
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert doctor.main([str(root)], run=rig) == 0
    text = capsys.readouterr().out
    assert f"{status:<5} C AddressSanitizer" in text
    assert ("sudo sysctl -w vm.mmap_rnd_bits=28" in text) == (status == "WARN")


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_mmap_has_no_row_elsewhere(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert c.machine_checks(lambda *a, **k: pytest.fail("unexpected process")) == []


def test_stand_in_third_language_needs_no_core_edit(tmp_path, rig, monkeypatch, capsys):
    """Membership, machine and host hooks all come from registries, not core branches."""
    language = languages.Language("demo", "Demo", "demo-compiler", "1", "synthetic")
    part = doctor_languages.LanguageDoctor(
        "demo", "synthetic", lambda run: [Row("WARN", "demo machine", "install demo runtime")],
        lambda platform, runner: "demo host unsupported")
    monkeypatch.setitem(languages.LANGUAGES, "demo", language)
    monkeypatch.setitem(doctor_languages.DOCTORS, "demo", part)
    original = languages.course_languages
    monkeypatch.setattr(languages, "course_languages", lambda data: [*original(data), language])
    root = support.course(tmp_path)
    assert doctor.main([str(root)], run=rig) == 1
    text = capsys.readouterr().out
    assert "WARN  demo machine" in text and "FAIL  demo host unsupported" in text


def test_native_windows_platform_limit_is_only_for_host(tmp_path, rig, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    root = support.course(tmp_path, declarations="[languages.c]\n")
    assert doctor.main([str(root), "--smoke"], run=rig) == 1
    text = capsys.readouterr().out
    assert "FAIL  C runs in WSL or in the container" in text
    assert "SKIP  smoke:" in text
    assert c.platform_problem("win32", "container") is None
