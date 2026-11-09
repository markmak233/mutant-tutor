"""Setup diagnosis with explicit advice, verification and repair actions.

Default checks never load .env or call a model. Advice requires a selected failure log;
only an explicit --env-file is read into a private map. Advice alone installs or fixes nothing.
Explicit --verify-advice installs only in disposable environments. --smoke writes only
in its temporary copy. Subprocess probes
are bounded and injectable. Every check is isolated: a failure never hides later checks.
Language-specific policy lives in doctor_languages; this module only orchestrates rows.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tutor.doctor_rows import Row

# Text shaped like an API key, whatever variable it came from: "sk-..." (OpenAI, DeepSeek,
# Anthropic) and GLM's "<32 hex>.<secret>" (llm.KEY_LIKE's two shapes, anywhere in a line).
_KEY_TEXT = re.compile(r"\bsk-[\w-]{16,}|\b[0-9a-f]{32}\.\w{8,}")


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


def main(argv: list[str] | None = None, *, run: Callable | None = None, validator=None,
         output: Callable[[str], None] | None = None,
         on_row: Callable[[Row], None] | None = None) -> int:
    """Resolve injectable defaults at call time; tests never need a real course or Docker."""
    parser = argparse.ArgumentParser(prog="tutor doctor", description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", type=Path,
                        help="course folder or any folder inside it")
    from tutor.doctor_advice import cli as advice_cli
    from tutor.doctor_advice import redaction

    advice_cli.add_arguments(parser)
    args = parser.parse_args(argv)
    if problem := advice_cli.argument_problem(args):
        parser.error(problem)
    run = subprocess.run if run is None else run
    rows: list[Row] = []

    def clean(text: str) -> str:
        # Exception messages can quote subprocess arguments, so credentials are removed at
        # the output boundary too: the values of variables named like one (KEY, TOKEN,
        # SECRET, PASSWORD, ...; the redactor's rule) and any text shaped like an API key.
        # Not every environment value (owner, 2026-11-09): hiding HOME or TMPDIR broke the
        # paths in the commands a row advises, e.g. `init "../private[...]course"`.
        values = sorted({v for k, v in os.environ.items()
                         if len(v) >= 8 and redaction.secret_name(k)}, key=len, reverse=True)
        for value in values:
            text = text.replace(value, "[environment value redacted]")
        return _KEY_TEXT.sub("[credential redacted]", text)

    def say(text: str) -> None:
        for line in clean(text).splitlines():
            if output is None:
                print(line, flush=True)
            else:
                output(line)

    def emit(row: Row) -> None:
        rows.append(row)
        if on_row is not None:
            # GUI gets the same environment redaction as CLI, without parsing human logs.
            on_row(Row(row.status, clean(row.text), clean(row.next_step), row.fix))
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
            say("1 failed, 0 warnings")
            return 1
        failure = None
        if args.advise or args.preview_advice or args.verify_advice or args.review_new_package:
            from tutor.doctor_advice.service import AdviceInputError, read_failure

            try:
                failure = read_failure(args.failure)
            except AdviceInputError as exc:
                print(f"doctor: {exc}", file=sys.stderr, flush=True)
                return 2
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
        image_ready = None
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
                                            "on the Environment page", "host-guide")]
                            return []
                        check(f"{part.key} platform", platform_check)
                check("host tools", lambda: checks.requirements(course, run, reported))
                images = check("test image", lambda: checks.image(course, run, docker_ready))
                image_ready = all(row.status == "OK" for row in images)
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
        if failure is not None:
            try:
                advice = advice_cli.perform(args, course, failure, run=run,
                                            docker_ready=docker_ready, image_ready=image_ready)
                for row in advice:
                    emit(row)
            except AdviceInputError as exc:
                print(f"doctor: {exc}", file=sys.stderr, flush=True)
                return 2
        if args.preview_repair or args.apply_repair or args.undo_repair or args.finish_repair:
            from tutor.doctor_advice import repair_cli
            from tutor.doctor_advice.service import AdviceInputError

            try:
                for row in repair_cli.perform(args, course, run=run, docker_ready=docker_ready,
                                              image_ready=image_ready):
                    emit(row)
            except AdviceInputError as exc:
                print(f"doctor: {exc}", file=sys.stderr, flush=True)
                return 2
        failed = sum(row.status == "FAIL" for row in rows)
        warnings = sum(row.status == "WARN" for row in rows)
        summary = (f"{failed} failed, {warnings} warnings" if failed or warnings
                   else "All checks passed")
        say(summary)
        return int(bool(failed))
    except KeyboardInterrupt:
        print("doctor: interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
