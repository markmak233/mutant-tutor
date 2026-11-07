import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

from tutor import envcheck
from tutor.config import Workspace
from tutor.envcheck import (
    check_winutils,
    check_workspace,
    main,
    parse_hadoop_version,
    parse_java_version,
    pip_requirements,
    requires_problems,
    version_matches,
)


@pytest.mark.parametrize(
    ("found", "required", "expected"),
    [
        ("3.5.9", "3.5", True),
        ("3.50.1", "3.5", False),
        ("3.11.17", "3.11", True),
        ("3.12.0", "3.11", False),
        ("17.0.15", "17", True),
        ("0.7.4", "0.7", True),
    ],
)
def test_version_matches(found, required, expected):
    """The required version is a prefix of dotted parts: "3.5" accepts "3.5.9" but not
    "3.50.1", which a plain string-prefix check would wrongly accept."""
    assert version_matches(found, required) is expected


def test_parse_java_version_modern_and_legacy():
    """Java 9+ prints e.g. "17.0.15"; Java 8 prints "1.8.0_462", which must read as 8.
    Output without a version string gives None."""
    assert parse_java_version('openjdk version "17.0.15" 2025-04-15') == "17.0.15"
    assert parse_java_version('java version "1.8.0_462"') == "8"
    assert parse_java_version("not java output") is None


def test_parse_hadoop_version_ignores_other_jars():
    """The version comes from the hadoop-common jar name only; the -tests jar and jars of
    other Hadoop modules must not match."""
    names = ["hadoop-common-3.3.6-tests.jar", "hadoop-kms-3.3.6.jar", "hadoop-common-3.3.6.jar"]
    assert parse_hadoop_version(names) == "3.3.6"
    assert parse_hadoop_version(["hadoop-kms-3.3.6.jar"]) is None


def make_ws(requires: dict[str, str]) -> Workspace:
    """An in-memory workspace; check_workspace only reads `requires` and `course`."""
    return Workspace(root=Path("/ws"), course="demo", requires=requires, lecture_dir=Path("/ws"))


def test_python_requirement_uses_running_interpreter():
    """`python` is checked against the interpreter running the tests, so requiring that
    interpreter's own major.minor passes on any machine."""
    major_minor = ".".join(platform.python_version_tuple()[:2])
    [result] = check_workspace(make_ws({"python": major_minor}))
    assert result.ok


def test_missing_package_fails_with_install_hint():
    """Any other name is treated as a pip package. A missing one fails, and the hint names
    the command that installs [requires] into the course's own venv."""
    [result] = check_workspace(make_ws({"no-such-package-xyz": "1.0"}))
    assert not result.ok
    assert result.found is None
    assert "python -m tutor.workspace init /ws" in result.hint


def test_pip_gets_each_package_as_the_prefix_envcheck_accepts():
    requires = {"python": "3.11", "java": "17", "hadoop": "3.3", "pyspark": "3.5", "torch": "2",
                "gcc": "12"}
    assert pip_requirements(requires) == ["pyspark==3.5.*", "torch==2.*"]


def _fake_gcc(monkeypatch, version_line: str, dumped: str) -> list[list[str]]:
    """`gcc` answering as gcc or as macOS's clang does; returns the commands it was given."""
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        out = version_line if command[1] == "--version" else dumped
        return subprocess.CompletedProcess(command, 0, stdout=out + "\n", stderr="")

    monkeypatch.setattr(envcheck.subprocess, "run", run)
    return calls


def test_gcc_is_checked_by_its_full_version(monkeypatch):
    calls = _fake_gcc(monkeypatch, "gcc (Debian 12.2.0-14+deb12u1) 12.2.0", "12.2.0")
    [result] = check_workspace(make_ws({"gcc": "12"}))
    assert (result.found, result.ok) == ("12.2.0", True)
    assert calls[1] == ["gcc", "-dumpfullversion"]


def test_clang_answering_as_gcc_does_not_pass_for_gcc(monkeypatch):
    """On macOS `gcc` is Apple's clang: the learner's runs use it, and validation uses the
    container's gcc, so the hint says so instead of asking for an install only."""
    _fake_gcc(monkeypatch, "Apple clang version 21.0.0 (clang-2100.4.1.1)", "21.0.0")
    [result] = check_workspace(make_ws({"gcc": "12"}))
    assert (result.found, result.ok) == ("clang-21.0.0", False)
    assert "validated in the course's container" in result.hint


def test_no_gcc_is_not_found(monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(envcheck.subprocess, "run", missing)
    assert check_workspace(make_ws({"gcc": "12"}))[0].found is None


@pytest.mark.parametrize("requires, says", [
    ({"pyspark": "3.5"}, "needs a JVM"),
    ({"java": "17; rm -rf /"}, "version prefix"),
    ({"java": "17", "pyspark": ">=3.5"}, "version prefix"),
    ({"bad name": "1"}, "not a package name"),
    ({"-e": "1"}, "not a package name"),  # would be an option to pip
])
def test_a_requirement_that_could_change_a_command_is_refused(requires, says):
    problems = requires_problems(requires)
    assert len(problems) == 1 and says in problems[0]


def test_a_plain_table_has_no_problems():
    assert requires_problems({"python": "3.11", "java": "17", "pyspark": "3.5"}) == []


def test_json_output_gives_each_result_with_ok(tmp_path, monkeypatch, capsys):
    """The GUI reads this; it runs in the course's venv, so `python` says which one."""
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[requires]\nno-such-package-xyz = "1"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    assert main(["--json"]) == 1

    out = json.loads(capsys.readouterr().out)
    assert out["course"] == "demo" and out["python"]
    assert out["results"] == [{"name": "no-such-package-xyz", "required": "1", "found": None,
                               "hint": out["results"][0]["hint"], "ok": False}]


def test_java_without_java_home_fails(monkeypatch):
    """Without JAVA_HOME there is no java to ask, so the check fails and points at .env."""
    monkeypatch.delenv("JAVA_HOME", raising=False)
    [result] = check_workspace(make_ws({"java": "17"}))
    assert not result.ok
    assert "JAVA_HOME" in result.hint


def _hadoop_home(tmp_path, files=("winutils.exe", "hadoop.dll")):
    bin_dir = tmp_path / "hadoop" / "bin"
    bin_dir.mkdir(parents=True)
    for name in files:
        (bin_dir / name).write_bytes(b"")
    return str(tmp_path / "hadoop"), bin_dir


def test_winutils_ok_when_files_present_and_bin_on_path(tmp_path):
    home, bin_dir = _hadoop_home(tmp_path)
    path = os.pathsep.join(["/somewhere/else", str(bin_dir)])

    assert check_winutils(home, path).ok


@pytest.mark.parametrize(
    ("files", "on_path", "hint"),
    [
        (("winutils.exe",), True, "hadoop.dll"),
        ((), True, "winutils.exe and hadoop.dll"),
        (("winutils.exe", "hadoop.dll"), False, "to PATH"),
    ],
)
def test_winutils_names_what_is_missing(tmp_path, files, on_path, hint):
    """Each gap is a different fix, so the hint names the one that applies; without PATH
    every file-level Spark exercise failed with UnsatisfiedLinkError."""
    home, bin_dir = _hadoop_home(tmp_path, files)
    path = str(bin_dir) if on_path else "/somewhere/else"

    result = check_winutils(home, path)

    assert not result.ok
    assert hint in result.hint


def test_winutils_without_hadoop_home():
    result = check_winutils(None, "")
    assert not result.ok and "HADOOP_HOME" in result.hint
