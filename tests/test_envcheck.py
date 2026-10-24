import platform
from pathlib import Path

import pytest

from tutor.config import Workspace
from tutor.envcheck import (
    check_workspace,
    parse_hadoop_version,
    parse_java_version,
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
    the course's extra to install."""
    [result] = check_workspace(make_ws({"no-such-package-xyz": "1.0"}))
    assert not result.ok
    assert result.found is None
    assert '".[demo]"' in result.hint


def test_java_without_java_home_fails(monkeypatch):
    """Without JAVA_HOME there is no java to ask, so the check fails and points at .env."""
    monkeypatch.delenv("JAVA_HOME", raising=False)
    [result] = check_workspace(make_ws({"java": "17"}))
    assert not result.ok
    assert "JAVA_HOME" in result.hint
