"""Where C cannot be built on the learner's machine, testkit_c says why in words, instead of
a linker's error (docs/design.md, "C": no native Windows, WSL instead). Needs no compiler:
native Windows is simulated, and a compiler without the sanitizers is a stand-in script.
"""

import os
import stat
import sys

import pytest
from test_testkit_c import compiled

from tutor import testkit_c
from tutor.testkit_c import NO_SANITIZER, WINDOWS, CompileError, host_problem


def test_native_windows_is_pointed_to_wsl(tmp_path, monkeypatch):
    monkeypatch.setattr(testkit_c.sys, "platform", "win32")

    assert host_problem() == WINDOWS
    with pytest.raises(CompileError, match="Open the course in WSL"):
        compiled(tmp_path, "int main(void) { return 0; }\n")


def test_a_missing_compiler_is_named(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("native Windows gets the WSL message instead")
    monkeypatch.delenv("CC", raising=False)

    assert "`no-such-cc-xyz` was not found" in host_problem("no-such-cc-xyz")


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in compiler is a shell script")
def test_a_compiler_without_the_sanitizer_libraries_says_so(tmp_path, monkeypatch):
    # What MinGW's gcc printed on GitHub's Windows runner (2026-11-07), from a stand-in.
    fake = tmp_path / "bin" / "fake-cc"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n"
                    "echo 'ld.exe: cannot find -lasan: No such file or directory' >&2\n"
                    "echo 'ld.exe: cannot find -lubsan: No such file or directory' >&2\n"
                    "exit 1\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CC", os.fspath(fake))
    exercise = tmp_path / "exercise"
    exercise.mkdir()

    with pytest.raises(CompileError) as caught:
        compiled(exercise, "int main(void) { return 0; }\n")

    assert str(caught.value).startswith(NO_SANITIZER)
    assert "cannot find -lasan" in str(caught.value)  # the linker's own words still follow
