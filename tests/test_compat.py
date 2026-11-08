"""Known version conflicts (tutor.compat): what the venv install, the image, the [requires]
check and the environment check make of them. No pip, no Docker."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor import compat
from tutor.config import Workspace
from tutor.envcheck import check_workspace, pip_requirements, requires_problems
from tutor.image_recipe import ContainerError, dockerfile

SPARK = {"java": "17", "pyspark": "3.5", "mrjob": "0.7"}


def test_companions_by_python():
    assert compat.companions(SPARK, "3.11") == []
    assert compat.companions(SPARK, "3.12") == ["setuptools"]
    assert compat.companions(SPARK, "3.13.2") == ["setuptools", "standard-pipes"]
    assert compat.companions({"pyspark": "3.5"}, "3.13") == []  # only with mrjob


def test_companions_without_a_python_carry_markers_for_pip():
    assert compat.companions(SPARK) == ["setuptools; python_version >= '3.12'",
                                        "standard-pipes; python_version >= '3.13'"]


def test_the_venv_install_adds_the_companions_with_their_markers():
    assert pip_requirements(SPARK) == [
        "pyspark==3.5.*", "mrjob==0.7.*",
        "setuptools; python_version >= '3.12'", "standard-pipes; python_version >= '3.13'"]
    assert pip_requirements({"python": "3.11", "pyspark": "3.5"}) == ["pyspark==3.5.*"]


@pytest.mark.parametrize("python, refused", [("3.13", False), ("3.14", True), ("3.15", True)])
def test_pyspark_3_5_is_refused_from_python_3_14(python, refused):
    problems = requires_problems({"python": python, **SPARK})
    assert bool(problems) is refused
    if refused:
        assert "PySpark 3.5 cannot send functions to its workers" in problems[0]
    assert not requires_problems({"python": python, "java": "17", "pyspark": "4.0"})


def test_the_image_adds_companions_only_where_its_python_needs_them():
    old = dockerfile({"python": "3.11", **SPARK}, uid=501)
    assert "setuptools" not in old and "standard-pipes" not in old
    new = dockerfile({"python": "3.13", **SPARK}, uid=501)
    assert "'pyspark==3.5.*' 'mrjob==0.7.*' setuptools standard-pipes\n" in new


def test_the_image_refuses_a_conflict_on_the_hosts_python(monkeypatch):
    """No python in [requires]: the image takes this machine's version, and the rule holds."""
    monkeypatch.setattr("tutor.image_recipe.sys.version_info",
                        SimpleNamespace(major=3, minor=14))
    with pytest.raises(ContainerError, match="Python 3.14: PySpark 3.5"):
        dockerfile(SPARK, uid=501)


def _ws(requires):
    return Workspace(root=Path("ws"), course="demo", requires=requires, lecture_dir=Path("ws"))


def test_the_check_reports_a_missing_companion(monkeypatch):
    monkeypatch.setattr("tutor.envcheck.platform.python_version", lambda: "3.13.3")

    def versions(name):
        if name == "standard-pipes":
            raise __import__("importlib").metadata.PackageNotFoundError(name)
        return "1.0"

    monkeypatch.setattr("tutor.envcheck.metadata.version", versions)
    results = {r.name: r for r in check_workspace(_ws({"mrjob": "1.0"}))}
    assert results["setuptools"].ok
    assert not results["standard-pipes"].ok
    assert "mrjob imports `pipes`" in results["standard-pipes"].hint


def test_the_check_reports_a_conflict_with_the_running_python(monkeypatch):
    monkeypatch.setattr("tutor.envcheck.platform.python_version", lambda: "3.14.1")
    monkeypatch.setattr("tutor.envcheck.metadata.version", lambda name: "3.5.9")
    failed = [r for r in check_workspace(_ws({"pyspark": "3.5"})) if not r.ok]
    assert [r.name for r in failed] == ["python"]
    assert "use Python 3.13 or older" in failed[0].hint
