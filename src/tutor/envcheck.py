"""Check this machine against the current workspace's [requires] table.

Check, don't install: every failure carries a hint saying what to fix
(docs/design.md, "Environment"). Run with `python -m tutor.envcheck`, or with `--json` for
the GUI's Environment page, which runs it with the course's own .venv: the check is about
the interpreter that runs it, so that is the one the learner's tests use.

This module also owns what a [requires] entry may be (requires_problems) and pip's form of
its packages (pip_requirements), so the venv install (tutor.workspace), the image
(tutor.container) and the GUI agree.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

from tutor import languages
from tutor.config import Workspace, WorkspaceError, load_env, load_workspace

# The names checked by their own rule below; any other name is a pip package.
NOT_PIP = ("python", "java", "hadoop", "gcc")
NEEDS_JAVA = ("pyspark", "hadoop")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION = re.compile(r"^\d+(\.\d+)*$")  # a prefix of dotted numbers: "3.5" means 3.5.x


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one [requires] entry, e.g. name="java", required="17"."""

    name: str
    required: str
    found: str | None  # None means not found at all
    hint: str = ""  # what to do if the check fails

    @property
    def ok(self) -> bool:
        """Derived, not stored, so it can never disagree with `found` and `required`."""
        return self.found is not None and version_matches(self.found, self.required)


def version_matches(found: str, required: str) -> bool:
    """`required` is a prefix of dotted parts: "3.5" matches "3.5.9" but not "3.50.1"."""
    want = required.split(".")
    return found.split(".")[: len(want)] == want


def requires_problems(requires: Mapping[str, str]) -> list[str]:
    """What is wrong with a [requires] table, one line each; [] when nothing. A name or a
    version goes into a pip command and an image recipe, so only plain ones pass."""
    problems = []
    for name, version in requires.items():
        if toolchain := languages.toolchain_problem(name):
            problems.append(toolchain)  # not a pip package, whatever its name looks like
        elif not PACKAGE.match(name):
            problems.append(f"[requires] {name!r} is not a package name")
        elif not VERSION.match(version):
            problems.append(f"[requires] {name} = {version!r}: use a version prefix such as "
                            '"3.5"')
    problems += [f"[requires] has {name} but no java, and {name} needs a JVM"
                 for name in NEEDS_JAVA if name in requires and "java" not in requires]
    return problems


def pip_requirements(requires: Mapping[str, str]) -> list[str]:
    """The table's pip packages as pip takes them: pyspark = "3.5" is `pyspark==3.5.*`,
    which is what version_matches accepts. Call requires_problems first."""
    return [f"{name}=={prefix}.*" for name, prefix in requires.items() if name not in NOT_PIP]


def parse_java_version(output: str) -> str | None:
    """Version from `java -version` output; legacy "1.8.0_462" becomes "8"."""
    match = re.search(r'version "([^"]+)"', output)
    if not match:
        return None
    version = match.group(1)
    if version.startswith("1."):
        return version.split(".")[1]
    return version


def parse_hadoop_version(jar_names: list[str]) -> str | None:
    """Version from the hadoop-common jar name, so no JVM has to start."""
    for name in jar_names:
        match = re.fullmatch(r"hadoop-common-(\d+(?:\.\d+)*)\.jar", name)
        if match:
            return match.group(1)
    return None


def check_workspace(ws: Workspace) -> list[CheckResult]:
    """Check every [requires] entry of the workspace, in the order they are written, plus
    Hadoop's native files on Windows when the course needs Spark or Hadoop."""
    results = [_check(name, required, ws) for name, required in ws.requires.items()]
    if sys.platform == "win32" and ({"pyspark", "hadoop"} & set(ws.requires)):
        results.append(check_winutils(os.environ.get("HADOOP_HOME"), os.environ.get("PATH", "")))
    # A language not supported yet, or a malformed [languages.c], fails here like a missing
    # requirement, so the command line and the GUI both say so.
    results += [CheckResult("languages", "supported", None, problem)
                for problem in languages.problems({"languages": ws.languages})]
    return results


def check_winutils(hadoop_home: str | None, path: str) -> CheckResult:
    """Spark on Windows reads and writes files through Hadoop's native library: it needs
    winutils.exe and hadoop.dll in $HADOOP_HOME/bin, and that folder on PATH (without
    PATH every file-level exercise failed with UnsatisfiedLinkError, 2026-11-01).
    Not a version, so `required` and `found` are both "present" when it is ok."""
    name, required = "winutils", "present"
    if not hadoop_home:
        return CheckResult(name, required, None, "set HADOOP_HOME in .env (docs/setup/bigdata.md)")
    bin_dir = Path(hadoop_home) / "bin"
    missing = [f for f in ("winutils.exe", "hadoop.dll") if not (bin_dir / f).is_file()]
    if missing:
        return CheckResult(name, required, None,
                           f"put {' and '.join(missing)} in {bin_dir} (docs/setup/bigdata.md)")
    want = os.path.normcase(os.path.normpath(bin_dir))
    if not any(os.path.normcase(os.path.normpath(p)) == want for p in path.split(os.pathsep) if p):
        return CheckResult(name, required, None, f"add {bin_dir} to PATH, then open a new shell")
    return CheckResult(name, required, "present")


def _check(name: str, required: str, ws: Workspace) -> CheckResult:
    """Dispatch on the requirement name.

    python, java and hadoop are special; any other name is a pip package, so a new
    course can require a new library without changing this code. A missing package is
    installed into the course's own .venv by `tutor.workspace init`, which installs the
    [requires] packages too.
    """
    if toolchain := languages.toolchain_problem(name):
        return CheckResult(name, required, None, toolchain)  # not something pip can install
    if name == "python":
        return CheckResult(
            name, required, platform.python_version(),
            f"recreate .venv with Python {required}",
        )
    if name == "java":
        return CheckResult(
            name, required, _java_version(), f"set JAVA_HOME in .env to a JDK {required}"
        )
    if name == "hadoop":
        return CheckResult(
            name, required, _hadoop_version(),
            f"set HADOOP_HOME in .env to an unpacked Hadoop {required}.x",
        )
    if name == "gcc":
        return CheckResult(
            name, required, _gcc_version(),
            f"install gcc {required}; C exercises are validated in the course's container, "
            "where it is, and a learner's own runs here compile with this machine's `cc`",
        )
    try:
        found = metadata.version(name)
    except metadata.PackageNotFoundError:
        found = None
    return CheckResult(name, required, found,
                       f"install it into the course's .venv: python -m tutor.workspace init "
                       f"{ws.root}")


def _java_version() -> str | None:
    """Ask $JAVA_HOME/bin/java for its version. JAVA_HOME, not PATH, because that is
    the JDK Spark and Hadoop will actually use."""
    java_home = os.environ.get("JAVA_HOME")
    if not java_home:
        return None
    try:
        proc = subprocess.run(
            [str(Path(java_home) / "bin" / "java"), "-version"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # `java -version` writes to stderr, not stdout.
    return parse_java_version(proc.stderr + proc.stdout)


def _gcc_version() -> str | None:
    """`gcc`'s version, e.g. "12.2.0"; "clang-21.0.0" where `gcc` is clang (macOS), which
    then does not match a gcc version; None when there is none."""
    try:
        first = subprocess.run(["gcc", "--version"], capture_output=True, text=True,
                               timeout=10).stdout.splitlines()[:1]
        # -dumpfullversion: gcc 7 and later print the whole version; clang does not know it.
        flag = "-dumpversion" if first and "clang" in first[0] else "-dumpfullversion"
        version = subprocess.run(["gcc", flag], capture_output=True, text=True,
                                 timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not version:
        return None
    return f"clang-{version}" if flag == "-dumpversion" else version


def _hadoop_version() -> str | None:
    """Read the version of the Hadoop unpacked at $HADOOP_HOME from its jar names."""
    hadoop_home = os.environ.get("HADOOP_HOME")
    if not hadoop_home:
        return None
    common = Path(hadoop_home) / "share" / "hadoop" / "common"
    if not common.is_dir():
        return None
    return parse_hadoop_version([p.name for p in common.iterdir()])


def main(argv: list[str] | None = None) -> int:
    """Print one line per requirement, or with --json one object (the course, the
    interpreter checked, and each result with `ok`); exit code 0 only if everything is ok."""
    parser = argparse.ArgumentParser(prog="python -m tutor.envcheck",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)
    load_env()
    try:
        ws = load_workspace()
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 1
    results = check_workspace(ws)
    if args.json:
        print(json.dumps({"course": ws.course, "python": sys.executable,
                          "results": [{**asdict(r), "ok": r.ok} for r in results]}))
        return 0 if all(r.ok for r in results) else 1
    print(f"course {ws.course}")
    for r in results:
        status = "ok  " if r.ok else "FAIL"
        print(f"  {status} {r.name:<8} required {r.required:<6} found {r.found or '-'}")
        if not r.ok:
            print(f"       -> {r.hint}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
