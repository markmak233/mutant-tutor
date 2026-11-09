"""Read-only setup diagnosis: no installs, model requests, .env loading or course writes.

Only --smoke writes, in a temporary copy cleaned even on interruption. Subprocess probes
are bounded and injectable. Every check is isolated: a failure never hides later checks.
Language-specific policy lives in doctor_languages; this module only orchestrates rows.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tutor.doctor_rows import Row


def tool_python() -> list[Row]:
    """Diagnose an old interpreter before importing modules that require tomllib (3.11)."""
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 11):  # noqa: UP036 -- this is the bootstrap diagnostic
        return [Row("FAIL", f"Python {version} is too old for this tool",
                    "install Python 3.11+ from https://www.python.org/downloads/")]
    return [Row("OK", f"Python {version} runs this tool")]


def course_root(folder: Path) -> Path | None:
    """The nearest workspace wins, including when called from an exercise directory."""
    return next((p for p in (folder, *folder.parents) if (p / "workspace.toml").is_file()), None)


def main(argv: list[str] | None = None, *, run: Callable | None = None, validator=None) -> int:
    """Resolve injectable defaults at call time; tests never need a real course or Docker."""
    parser = argparse.ArgumentParser(prog="tutor doctor", description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", type=Path,
                        help="course folder or any folder inside it")
    parser.add_argument("--smoke", action="store_true", help="validate one shipped example (slow)")
    args = parser.parse_args(argv)
    run = subprocess.run if run is None else run
    rows: list[Row] = []

    def clean(text: str) -> str:
        # Exception messages can quote subprocess arguments. Redact environment data at
        # the output boundary too; never dump the environment or a probe's raw stderr.
        values = sorted({v for v in os.environ.values() if len(v) >= 8}, key=len, reverse=True)
        for value in values:
            text = text.replace(value, "[environment value redacted]")
        return text

    def say(text: str) -> None:
        for line in clean(text).splitlines():
            print(line, flush=True)

    def emit(row: Row) -> None:
        rows.append(row)
        # One row stays one line even when an external exception contains newlines.
        say(f"{row.status:<5} {' '.join(row.text.splitlines())}")
        if row.status in ("WARN", "FAIL"):
            say(f"      next: {' '.join(row.next_step.splitlines())}")

    def check(name: str, action: Callable) -> list[Row]:
        try:
            result = action()
        except Exception as exc:
            result = [Row("FAIL", f"{name}: the check itself failed: {type(exc).__name__}: {exc}",
                          "report this output to the owner")]
        for row in result:
            emit(row)
        return result

    try:
        folder = (args.folder or Path.cwd()).expanduser().resolve()
        if not folder.is_dir():
            print(clean("doctor: FOLDER does not exist or is not a directory"),
                  file=sys.stderr, flush=True)
            return 2
        interpreter = check("tool Python", tool_python)
        if any(row.status == "FAIL" for row in interpreter):
            emit(Row("SKIP", "remaining checks need Python 3.11+"))
            print("1 failed, 0 warnings", flush=True)
            return 1
        # Older interpreters must reach T1 without importing tomllib or the validator.
        from tutor import doctor_checks as checks

        root = course_root(folder)
        course = checks.read_course(root) if root else None
        # Configuration is read early only for severity, not printed out of check order.
        required = bool(course and not course.error and course.runner == "container")
        check("tool version", checks.tool_version)
        check("gen extra", lambda: checks.extra("gen", "langgraph", "generating exercises"))
        check("gui extra", lambda: checks.extra("gui", "flask", "the browser pages"))
        docker_rows = check("Docker", lambda: checks.docker(run, required))
        docker_ready = all(row.status == "OK" for row in docker_rows)
        selected = []

        def machine_checks():
            nonlocal selected
            selected = checks.parts(course if course and not course.error else None)
            # Isolate each language hook, so a broken third-language probe hides nothing.
            for part in selected:
                check(f"{part.key} machine", lambda part=part: part.machine_checks(run))
            return []

        check("language registry", machine_checks)
        if course is None:
            emit(Row("SKIP", "no course found; tool checks only"))
        else:
            loaded = check("workspace.toml", lambda: checks.course_load(course))
            if any(row.status == "FAIL" for row in loaded):
                for name in ("runner", ".venv", "host tools", "test image", "smoke"):
                    emit(Row("SKIP", f"{name}: workspace.toml did not load"))
            else:
                declared = check("declarations", lambda: checks.declarations(course))
                reported = {row.text for row in declared}
                check("runner", lambda: checks.runner(course))
                check("course .venv", lambda: checks.venv(course, run))
                blocked = set()
                if course.runner in (None, "host"):
                    for part in selected:
                        def platform_check(part=part):
                            problem = part.platform_problem(sys.platform, course.runner)
                            if problem:
                                blocked.add(part.key)
                                return [Row("FAIL", problem, "use WSL or choose the container "
                                            "on the Environment page")]
                            return []
                        check(f"{part.key} platform", platform_check)
                check("host tools", lambda: checks.requirements(course, run, reported))
                images = check("test image", lambda: checks.image(course, run, docker_ready))
                if args.smoke:
                    if course.runner not in (None, "host", "container"):
                        emit(Row("SKIP", "smoke: choose a valid runner first"))
                    elif course.runner == "container" and (
                            not docker_ready or any(r.status != "OK" for r in images)):
                        emit(Row("SKIP", "smoke: course image is not available"))
                    elif not selected:
                        emit(Row("SKIP", "smoke: no registered course language"))
                    elif (selected[-1].key in blocked
                          and not selected[-1].validation_needs_container):
                        emit(Row("SKIP", "smoke: this language cannot run on this platform"))
                    else:
                        from tutor import doctor_smoke

                        check("smoke", lambda: doctor_smoke.smoke(
                            course, selected[-1], validator=validator, say=say))
        failed = sum(row.status == "FAIL" for row in rows)
        warnings = sum(row.status == "WARN" for row in rows)
        summary = (f"{failed} failed, {warnings} warnings" if failed or warnings
                   else "All checks passed")
        print(summary, flush=True)
        return int(bool(failed))
    except KeyboardInterrupt:
        print("doctor: interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
