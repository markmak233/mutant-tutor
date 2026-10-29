"""Static safety check (isolation layer A): one case per rule, the patterns exercises
really use, the workspace's extra modules, and the repo's examples as a whole."""

import json
from pathlib import Path

import pytest

from tutor.validation.safety import (
    allowed_imports,
    config_files,
    find_hazards,
    workspace_allow_imports,
)

MRJOB = allowed_imports("mrjob")
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # 1 imports
        ("import socket", "import of 'socket' is not allowed"),
        ("import urllib.request", "import of 'urllib.request' is not allowed"),
        ("from subprocess import run", "import of 'subprocess' is not allowed"),
        ("import pyspark", "import of 'pyspark' is not allowed"),  # not this framework
        ("from . import helper", "relative imports are not allowed"),
        # 2 os / sys
        ("import os\nos.system('ls')", "os.system is not allowed"),
        ("import os as o\no.remove('x')", "os.remove is not allowed"),
        ("import os.path\nos.system('ls')", "os.system is not allowed"),
        ("from os import system", "os.system is not allowed"),
        ("import sys\nsys.modules.clear()", "sys.modules is not allowed"),
        # 3 builtins
        ("eval('1 + 1')", "eval is not allowed"),
        ("exec('x = 1')", "exec is not allowed"),
        ("__import__('socket')", "__import__ is not allowed"),
        # 4 dunders and getattr
        ("().__class__.__base__.__subclasses__()", "attribute __subclasses__ is not allowed"),
        ("f.__globals__", "attribute __globals__ is not allowed"),
        ("__builtins__", "__builtins__ is not allowed"),
        ("getattr(obj, name)", "getattr needs a literal attribute name"),
        ("getattr(f, '__globals__')", "getattr(..., '__globals__') is not allowed"),
        # 5 paths
        ("open('/etc/passwd')", "open('/etc/passwd') reaches outside the exercise"),
        ("open('~/.ssh/id_rsa')", "reaches outside the exercise"),
        ("from pathlib import Path\nPath('../../secret')", "reaches outside the exercise"),
        ("sc.textFile('/srv/data')", "textFile('/srv/data') reaches outside"),
        ("from pathlib import Path\nPath.home()", "home() is not allowed"),
        ("import os\nos.path.expanduser('~')", "expanduser() is not allowed"),
        # 6 shell commands through the frameworks
        ("rdd.pipe('cat /etc/passwd')", "pipe is not allowed (it runs a shell command)"),
        ("class J(MRJob):\n    def mapper_cmd(self):\n        return 'cat'",
         "defining mapper_cmd is not allowed"),
        ("MRStep(reducer_cmd='sort')", "reducer_cmd= is not allowed"),
    ],
)
def test_each_rule_reports_the_line_and_the_reason(source, expected):
    found = find_hazards(source, MRJOB)
    assert any(expected in line for line in found), found
    assert all(line.startswith("line ") for line in found)


def test_what_exercises_really_use_passes():
    """Taken from the patterns of the existing exercises: test helpers, environment
    variables, sys.argv, tmp paths, the entry-point guard, a literal getattr."""
    source = (
        "from __future__ import annotations\n"
        "import os, re, sys, tempfile\n"
        "from collections import Counter\n"
        "from pathlib import Path\n"
        "from mrjob.job import MRJob\n"
        "from tutor.testkit import run_mrjob, write_inputs\n"
        "import student\n"
        "os.environ.setdefault('PYSPARK_PYTHON', sys.executable)\n"
        "def test_it(tmp_path):\n"
        "    with open(tmp_path / 'in.txt', 'w') as f:\n"
        "        f.write('/home,100')\n"
        "    assert getattr(student.job, 'func', None) is not None\n"
        "    assert student.Job.__name__ == 'Job' and Path('data/a.txt').name\n"
        "class Job(MRJob):\n"
        "    def mapper(self, _, line):\n"
        "        yield line, 1\n"
        "if __name__ == '__main__':\n"
        "    Job.run(sys.argv[1:])\n"
    )
    assert find_hazards(source, MRJOB) == []


def test_a_local_name_shadowing_a_builtin_is_not_the_builtin():
    assert find_hazards("def compile(x):\n    return x\ncompile(1)", MRJOB) == []


def test_frameworks_add_their_own_modules():
    assert "pyspark" in allowed_imports("pyspark-df")
    assert "mrjob" not in allowed_imports("pyspark-rdd")
    assert allowed_imports("python") == allowed_imports("unknown-framework")


def test_workspace_toml_adds_course_modules(tmp_path):
    (tmp_path / "workspace.toml").write_text(
        'course = "x"\n[safety]\nallow_imports = ["numpy", "pandas"]\n')
    exercise = tmp_path / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True)

    extra = workspace_allow_imports(exercise)
    assert extra == {"numpy", "pandas"}
    assert find_hazards("import numpy", allowed_imports("python", extra)) == []
    assert find_hazards("import numpy", allowed_imports("python")) != []


def test_outside_a_workspace_there_are_no_extra_modules(tmp_path):
    assert workspace_allow_imports(tmp_path) == frozenset()


def test_a_malformed_allow_list_is_an_error(tmp_path):
    (tmp_path / "workspace.toml").write_text('course = "x"\n[safety]\nallow_imports = "numpy"\n')
    with pytest.raises(ValueError, match="allow_imports must be a list"):
        workspace_allow_imports(tmp_path)


def test_pytest_config_files_are_reported_in_subfolders_too():
    names = ["conftest.py", "pytest.ini", "test_student.py", "data/conftest.py", "data/a.csv"]
    assert config_files(names) == ["conftest.py", "data/conftest.py", "pytest.ini"]


def test_the_examples_pass():
    """Every .py file of the repo's examples, under its own framework's allowlist."""
    for solution in sorted(EXAMPLES.glob("*/solution")):
        framework = json.loads((solution / "metadata.json").read_text())["framework"]
        allowed = allowed_imports(framework)
        files = [*solution.parent.glob("exercise/*.py"), *solution.glob("*.py"),
                 *solution.glob("mutants/*.py")]
        for path in files:
            assert find_hazards(path.read_text(), allowed) == [], path
