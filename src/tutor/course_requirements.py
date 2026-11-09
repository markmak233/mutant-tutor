"""One read-only course-venv requirements probe for doctor and the GUI.

Doctor runs trusted envcheck functions without its .env-loading CLI. The GUI retains its
existing child-only settings lookup. Both use the course Python and the same JSON decoder;
neither loads keys into the GUI server or falls back to the tool's interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from tutor import runtime_env, workspace

# This fixed script works with course installations predating this helper module.
# No generated/student code or input-supplied script is executed.
PROBE = """from dataclasses import asdict
import json, sys
from tutor.config import load_workspace
from tutor.envcheck import check_workspace
results = check_workspace(load_workspace())
print(json.dumps({"python": sys.executable,
                  "results": [{**asdict(r), "ok": r.ok} for r in results]}))
"""


@dataclass(frozen=True)
class Probe:
    """Result dictionaries retain envcheck's field names for the existing Python switch."""

    python: str | None
    results: list[dict] = field(default_factory=list)
    problem: str | None = None


def environment(course) -> dict[str, str]:
    """Keep explicit host tool locations while stripping model/Python injection settings."""
    env = runtime_env.environment()
    env.update({k: v for k, v in os.environ.items() if k.upper() in {"JAVA_HOME", "HADOOP_HOME"}})
    env.update(WORKSPACE_DIR=str(course.root), LECTURE_DIR=str(course.lecture_dir),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def check(course, *, run=None, settings_dir=None) -> Probe:
    """An unavailable/broken venv never falls back to the tool's interpreter."""
    python = workspace.venv_python(course.root / ".venv")
    if not python.is_file():
        return Probe(None, problem='This course has no .venv yet: "Install into .venv" creates it.')
    # Keep the existing GUI's explicit child settings context (JAVA_HOME/HADOOP_HOME).
    # Default doctor never supplies one, so its fixed worker never reads .env.
    command = ([str(python), "-m", "tutor.envcheck", "--json"] if settings_dir is not None
               else [str(python), "-I", "-B", "-c", PROBE])
    try:
        done = (run or subprocess.run)(command,
                                      capture_output=True, text=True, encoding="utf-8",
                                      timeout=60, cwd=settings_dir or course.root,
                                      env=environment(course))
    except (OSError, subprocess.TimeoutExpired):
        return Probe(str(python), problem="The check did not finish in this course's .venv.")
    try:
        if done.returncode or len(done.stdout) > 1024 * 1024:
            raise ValueError
        data = json.loads(done.stdout)
        rows = data["results"]
        if not isinstance(rows, list):
            raise ValueError
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("ok")) is not bool
                    or any(not isinstance(row.get(k), str) for k in ("name", "required", "hint"))
                    or row.get("found") is not None and not isinstance(row["found"], str)):
                raise ValueError
        return Probe(str(python), rows)
    except (ValueError, KeyError, TypeError):
        # Raw stderr can include private paths/credentials. The .venv repair row is the
        # recovery path; do not echo a child's traceback into browser or CLI diagnostics.
        return Probe(str(python), problem="The check could not run in this course's .venv.")
