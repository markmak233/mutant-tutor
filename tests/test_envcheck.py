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
    assert version_matches(found, required) is expected


def test_parse_java_version_modern_and_legacy():
    assert parse_java_version('openjdk version "17.0.15" 2025-04-15') == "17.0.15"
    assert parse_java_version('java version "1.8.0_462"') == "8"
    assert parse_java_version("not java output") is None


def test_parse_hadoop_version_ignores_other_jars():
    names = ["hadoop-common-3.3.6-tests.jar", "hadoop-kms-3.3.6.jar", "hadoop-common-3.3.6.jar"]
    assert parse_hadoop_version(names) == "3.3.6"
    assert parse_hadoop_version(["hadoop-kms-3.3.6.jar"]) is None


def make_ws(requires: dict[str, str]) -> Workspace:
    return Workspace(root=Path("/ws"), course="demo", requires=requires, lecture_dir=Path("/ws"))


def test_python_requirement_uses_running_interpreter():
    major_minor = ".".join(platform.python_version_tuple()[:2])
    [result] = check_workspace(make_ws({"python": major_minor}))
    assert result.ok


def test_missing_package_fails_with_install_hint():
    [result] = check_workspace(make_ws({"no-such-package-xyz": "1.0"}))
    assert not result.ok
    assert result.found is None
    assert '".[demo]"' in result.hint


def test_java_without_java_home_fails(monkeypatch):
    monkeypatch.delenv("JAVA_HOME", raising=False)
    [result] = check_workspace(make_ws({"java": "17"}))
    assert not result.ok
    assert "JAVA_HOME" in result.hint
