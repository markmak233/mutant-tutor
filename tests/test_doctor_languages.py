"""Language registry completeness, Linux ASan settings and a stand-in third language."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_doctor as support

from tutor import container, doctor, doctor_languages, languages
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


def _course(tmp_path, runner, gcc="12"):
    """The two things the ASan row reads from doctor's course: [requires] and the runner."""
    requires = {"python": "3.11", **({"gcc": gcc} if gcc else {})}
    return SimpleNamespace(root=tmp_path, runner=runner,
                           ws=SimpleNamespace(effective_requires=requires))


def _no_process(*args, **kwargs):
    pytest.fail("unexpected process")


@pytest.mark.parametrize("value,status", [("32", "WARN"), ("28", "OK"), (None, "SKIP"),
                                         ("invalid", "SKIP"), (PermissionError, "SKIP")])
def test_linux_mmap_bits_on_a_gcc_12_host_course(tmp_path, rig, monkeypatch, capsys, value,
                                                  status):
    """Exercise the hook through the command, and only fake the one procfs read."""
    # The platform first: the course's venv marker goes where a Linux doctor looks for it
    # (.venv/bin/python), whatever the machine running the test (Windows CI, 2026-11-11).
    monkeypatch.setattr(sys, "platform", "linux")
    root = support.course(tmp_path, declarations='[requires]\ngcc = "12"\n[languages.c]\n')
    monkeypatch.setitem(doctor_languages.DOCTORS, "c", c.DOCTOR)
    original = Path.read_text

    def read(path, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        if path == Path("/proc/sys/vm/mmap_rnd_bits"):
            if value is None:
                raise FileNotFoundError(path)
            if value is PermissionError:
                raise PermissionError(path)  # root-only, as on the owner's Ubuntu VM
            return value
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert doctor.main([str(root)], run=rig) == 0
    text = capsys.readouterr().out
    assert f"{status:<5} C AddressSanitizer" in text
    assert ("sudo sysctl -w vm.mmap_rnd_bits=28" in text) == (status == "WARN")
    assert ('gcc = "14"' in text) == (status == "WARN")
    assert ("sudo cat /proc/sys/vm/mmap_rnd_bits" in text) == (value is PermissionError)


@pytest.mark.parametrize("gcc", ["14", None])
def test_only_gcc_12_gets_the_row(tmp_path, monkeypatch, gcc):
    """gcc 14 does not hang at 32 bits (2026-11-16), and without gcc there is no C image."""
    monkeypatch.setattr(sys, "platform", "linux")
    for runner in ("host", "container"):
        assert c.machine_checks(_no_process, _course(tmp_path, runner, gcc)) == []
    assert c.machine_checks(_no_process, None) == []  # no course: no gcc to judge


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_host_c_elsewhere_has_no_row(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert c.machine_checks(_no_process, _course(tmp_path, "host")) == []


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
@pytest.mark.parametrize("answer,status", [((0, "32\n"), "WARN"), ((0, "18\n"), "OK"),
                                          ((125, ""), "SKIP"), (OSError, "SKIP")])
def test_a_container_course_reads_the_value_in_its_own_image(tmp_path, monkeypatch, platform,
                                                             answer, status):
    """Docker's VM is the kernel that counts on macOS and Windows; the host cannot read it.
    One fixed read-only command in the built image: nothing pulled, no network, no exercise."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(container, "image_for", lambda sources: "tutor-run-demo-0")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] <= 60 and kwargs["capture_output"] is True
        if answer is OSError:
            raise FileNotFoundError("docker")
        return subprocess.CompletedProcess(command, answer[0], answer[1], "")

    [row] = c.machine_checks(run, _course(tmp_path, "container"))
    assert row.status == status
    assert calls == [["docker", "run", "--rm", "--pull", "never", "--network", "none",
                      "--user", "0", "--entrypoint", "cat", "tutor-run-demo-0",
                      "/proc/sys/vm/mmap_rnd_bits"]]
    if status == "WARN":
        assert 'gcc = "14"' in row.next_step and "build the test image again" in row.next_step
        assert "sysctl" not in row.next_step  # a Docker Desktop VM forgets it on restart
    if answer == (125, ""):
        assert "build the test image first" in row.text


def test_stand_in_third_language_needs_no_core_edit(tmp_path, rig, monkeypatch, capsys):
    """Membership, machine and host hooks all come from registries, not core branches."""
    language = languages.Language("demo", "Demo", "demo-compiler", "1", "synthetic")
    part = doctor_languages.LanguageDoctor(
        "demo", "synthetic",
        lambda run, course: [Row("WARN", "demo machine", "install demo runtime")],
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
