"""Check this machine against the current workspace's [requires] table.

Check, don't install: every failure carries a hint saying what to fix
(docs/design.md, "Environment"). Run with `python -m tutor.envcheck`.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from tutor.config import Workspace, WorkspaceError, load_env, load_workspace


@dataclass(frozen=True)
class CheckResult:
    name: str
    required: str
    found: str | None  # None means not found at all
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.found is not None and version_matches(self.found, self.required)


def version_matches(found: str, required: str) -> bool:
    """`required` is a prefix of dotted parts: "3.5" matches "3.5.9" but not "3.50.1"."""
    want = required.split(".")
    return found.split(".")[: len(want)] == want


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
    return [_check(name, required, ws.course) for name, required in ws.requires.items()]


def _check(name: str, required: str, course: str) -> CheckResult:
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
    try:
        found = metadata.version(name)
    except metadata.PackageNotFoundError:
        found = None
    return CheckResult(name, required, found, f'pip install -e ".[{course}]"')


def _java_version() -> str | None:
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
    return parse_java_version(proc.stderr + proc.stdout)


def _hadoop_version() -> str | None:
    hadoop_home = os.environ.get("HADOOP_HOME")
    if not hadoop_home:
        return None
    common = Path(hadoop_home) / "share" / "hadoop" / "common"
    if not common.is_dir():
        return None
    return parse_hadoop_version([p.name for p in common.iterdir()])


def main() -> int:
    load_env()
    try:
        ws = load_workspace()
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 1
    results = check_workspace(ws)
    print(f"course {ws.course}")
    for r in results:
        status = "ok  " if r.ok else "FAIL"
        print(f"  {status} {r.name:<8} required {r.required:<6} found {r.found or '-'}")
        if not r.ok:
            print(f"       -> {r.hint}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
