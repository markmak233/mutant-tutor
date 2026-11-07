"""Helpers for the tests of a C exercise: compile the code under test once, run it the way the
course runs a program (standard input, arguments, files), and read what it printed
(docs/design.md, "Other languages").

A test file starts like this:

    from tutor.testkit_c import build

    program = build("student.c")            # a function exercise: build("student.c", "driver.c")

    def test_example_from_question():
        assert program.run(stdin="3\\n1 2 3\\n").lines() == ["6"]

How a broken program shows, which validation relies on:
- It does not compile: `build` raises CompileError while pytest imports the test file, so no
  test runs and pytest reports an error. That is the C form of a Python import error:
  validation calls a skeleton that does not compile an error, and a mutant "broken", not
  "killed".
- It compiles but crashes, trips a sanitizer, exits with a non-zero status or runs past its
  time limit: `run` raises AssertionError inside the test, so that test fails.

Every build adds AddressSanitizer and UndefinedBehaviorSanitizer, so an out-of-bounds index, a
use after free or a signed overflow stops the program with a report instead of passing by
luck, and makes a call to an undeclared function an error (C99 lets code call any library
function without including its header otherwise, and the static check of headers relies on
that).

Memory leaks are checked only in a run that asks, `program.run(..., leaks=True)`: a test of an
exercise about malloc and free. LeakSanitizer's check at exit took 0.72 s per run in the
validation container (Docker Desktop's Linux VM, 2026-11-07; the same with no limits at all,
while the run itself took 3 ms), which would make every test slow, and it exists only on Linux:
on macOS, where a learner's own runs compile with Apple's clang, such a run warns that leaks
were not checked, and validation checks them in the container.

This module imports only the standard library and tutor.config: it runs inside the test
container, where nothing else of tutor's dependencies is installed.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

from tutor.config import WORKSPACE_FILE

# The course's compiler flags as a JSON list. The validator sets it for every run, since a run
# in a container cannot see the course's workspace.toml.
C_FLAGS_ENV = "TUTOR_C_FLAGS"
# "1": warnings are errors. The validator sets it when it checks the reference, which must
# compile cleanly under the course's flags (the course asks the same of its students).
C_STRICT_ENV = "TUTOR_C_STRICT"
# Without a course (the repo's examples): a current standard with the usual warnings.
DEFAULT_FLAGS = ("-std=c11", "-Wall")
# Added to every build, after the course's flags.
ALWAYS_FLAGS = (
    "-g",  # line numbers in a sanitizer's report
    "-fsanitize=address,undefined",
    # UBSan prints and carries on by default; this makes it stop the program, so a test fails.
    "-fno-sanitize-recover=all",
    "-fno-omit-frame-pointer",  # readable stack traces in ASan's reports
    "-Werror=implicit-function-declaration",
)
LINK_FLAGS = ("-lm",)  # math.h's functions live in libm on Linux
COMPILE_TIMEOUT_S = 60.0
RUN_TIMEOUT_S = 5.0  # per program run; a test may give another
# LeakSanitizer runs with ASan on Linux only; asking for it on macOS stops the program.
LEAKS_CHECKED = sys.platform.startswith("linux")
_SHOWN_LINES = 30  # of a compiler's or sanitizer's message

# What a sanitizer writes when it stops a program. UBSan's line is "<file>:<line>:<col>:
# runtime error: ...", and gcc's UBSan prints no SUMMARY line, so both forms are looked for.
_SANITIZER = re.compile(
    r"ERROR: (AddressSanitizer|LeakSanitizer)|SUMMARY: UndefinedBehaviorSanitizer"
    r"|:\d+:\d+: runtime error: ")
_PROCESS_ID = re.compile(r"^==\d+==")


class CompileError(Exception):
    """The code did not compile (or link); the message is the compiler's."""


# C does not run natively on Windows (owner, 2026-11-07; docs/design.md, "C"): its compilers
# there (MinGW's gcc) have no AddressSanitizer or UBSan, and the alternatives would each need
# an install and hint rules of their own. WSL has Linux's gcc, as the course's container does.
WINDOWS = ("C exercises do not run natively on Windows: their tests need AddressSanitizer and "
           "UBSan, which compilers there lack. Open the course in WSL (Windows Subsystem for "
           "Linux), where `sudo apt install gcc` brings both")
# A compiler without the sanitizers' libraries (MinGW, a minimal Linux install) stops here.
_NO_SANITIZER = re.compile(r"cannot find -l(?:asan|ubsan)|library not found for -l(?:asan|ubsan)")
NO_SANITIZER = ("this C compiler lacks the AddressSanitizer or UBSan library the tests build "
                "with: install them with the compiler (Debian and Ubuntu: `sudo apt install "
                "gcc`), or run the tests in the course's container")


def host_problem(compiler: str | None = None) -> str | None:
    """Why C programs cannot be built on this machine, or None: native Windows, or no
    compiler on PATH. The same reasons `build` gives, for a caller (a test) to check first."""
    if sys.platform == "win32":
        return WINDOWS
    compiler = compiler or os.environ.get("CC") or "cc"
    if shutil.which(compiler) is None:
        return f"no C compiler: `{compiler}` was not found (install gcc or clang, or set CC)"
    return None


class CompilerWarning(UserWarning):
    """The compiler warned; pytest lists it under "warnings summary"."""


class LeaksNotCheckedWarning(UserWarning):
    """A run asked for a leak check where LeakSanitizer does not exist."""


@dataclass(frozen=True)
class Run:
    """What one run of the program gave back."""

    stdout: str
    stderr: str
    returncode: int

    def lines(self) -> list[str]:
        """Standard output split into lines, without their line endings."""
        return self.stdout.splitlines()


@dataclass(frozen=True)
class Program:
    """A compiled program, ready to run as often as the tests like."""

    path: Path

    def run(self, *args: object, stdin: str = "", cwd: str | Path | None = None,
            timeout_s: float = RUN_TIMEOUT_S, check: bool = True, leaks: bool = False) -> Run:
        """Run it with `args` as its command-line arguments and `stdin` as its standard input.

        AssertionError, so the calling test fails, when it runs past `timeout_s`, when a
        sanitizer stops it, and, with `check` (the default), when it exits with a non-zero
        status or is killed by a signal. A test that expects a failing exit passes
        check=False and looks at `returncode` itself; a sanitizer's report fails it anyway.
        `leaks`: memory still allocated at exit fails it too (Linux only; see above).
        """
        # pytest leaves this function out of a failure's traceback: the learner sees the line
        # of their test and the message, not this module's code.
        __tracebackhide__ = True
        if leaks and not LEAKS_CHECKED:
            warnings.warn(LeaksNotCheckedWarning(
                "memory leaks are not checked on this machine (LeakSanitizer runs on Linux "
                "only); validation checks them in the course's container"), stacklevel=2)
        # Set whole, so the learner's own ASAN_OPTIONS cannot change what a test checks.
        # UBSan's stack trace shows which call led to the fault.
        env = {**os.environ,
               "ASAN_OPTIONS": f"detect_leaks={int(leaks and LEAKS_CHECKED)}",
               "UBSAN_OPTIONS": "print_stacktrace=1"}
        try:
            proc = subprocess.run([str(self.path), *map(str, args)],
                                  input=stdin.encode("utf-8"), capture_output=True,
                                  timeout=timeout_s, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"the program did not finish within {timeout_s:g} s: an endless loop, or "
                "waiting for input it was never given?") from None
        result = Run(proc.stdout.decode("utf-8", "replace"),
                     proc.stderr.decode("utf-8", "replace"), proc.returncode)
        report = _SANITIZER.search(result.stderr)
        if report:
            # From the start of the report's line: UBSan's begins "student.c:4:20: runtime
            # error", and the match starts after the file name, which the learner needs.
            # ASan's begins "==1234==ERROR": the process id means nothing and differs every
            # run, so it is left out.
            start = result.stderr.rfind("\n", 0, report.start()) + 1
            text = _PROCESS_ID.sub("", result.stderr[start:], count=1)
            raise AssertionError("the sanitizer stopped the program (a memory error or "
                                 "undefined behaviour):\n" + _excerpt(_up_to_summary(text)))
        if check and result.returncode != 0:
            raise AssertionError(f"the program {_exit_text(result.returncode)}"
                                 + (f"; it printed to stderr:\n{_excerpt(result.stderr)}"
                                    if result.stderr.strip() else ""))
        return result


def build(*sources: str) -> Program:
    """Compile `sources`, file names in the calling test file's folder, into one program.

    Called while pytest imports the test file, so it runs once per test run, before any test.
    Raises CompileError with the compiler's message; warnings go to pytest's warnings summary.
    The program goes to a temporary folder deleted when the run ends, never next to the
    sources (the learner's exercise folder).
    """
    __tracebackhide__ = True  # a compile error shows the test file's line and the message
    folder = _caller_folder()
    if sys.platform == "win32":
        raise CompileError(WINDOWS)
    compiler = os.environ.get("CC") or "cc"
    strict = os.environ.get(C_STRICT_ENV) == "1"
    out_dir = Path(tempfile.mkdtemp(prefix="tutor-c-"))
    atexit.register(shutil.rmtree, out_dir, True)
    program = out_dir / "program"
    # File names, not paths, with the folder as the working directory: the compiler's messages
    # then name "student.c:12:5" and nothing of this machine's paths.
    command = [compiler, *compile_flags(folder), *ALWAYS_FLAGS, *(["-Werror"] if strict else []),
               *sources, "-o", str(program), *LINK_FLAGS]
    try:
        proc = subprocess.run(command, cwd=folder, capture_output=True, text=True,
                              timeout=COMPILE_TIMEOUT_S)
    except FileNotFoundError:
        raise CompileError(f"no C compiler: `{compiler}` was not found (install gcc or clang, "
                           "or set CC)") from None
    except subprocess.TimeoutExpired:
        raise CompileError(f"compiling took over {COMPILE_TIMEOUT_S:g} s") from None
    message = (proc.stderr + proc.stdout).strip()
    if proc.returncode != 0:
        if _NO_SANITIZER.search(message):
            raise CompileError(f"{NO_SANITIZER}:\n{_excerpt(message)}")
        note = " (warnings count as errors here: the reference must compile cleanly)" \
            if strict else ""
        raise CompileError(f"{' '.join(sources)} did not compile{note}:\n{_excerpt(message)}")
    if message:
        warnings.warn(CompilerWarning(f"the compiler warned:\n{_excerpt(message)}"),
                      stacklevel=2)
    return Program(program)


def compile_flags(folder: Path) -> list[str]:
    """The course's flags: C_FLAGS_ENV when set (by the validator), else `[languages.c]
    flags` of the nearest workspace.toml above `folder` (a learner's own run), else
    DEFAULT_FLAGS."""
    given = os.environ.get(C_FLAGS_ENV)
    if given is not None:
        flags = json.loads(given)
        check_flags(flags, C_FLAGS_ENV)
        return flags
    found = course_flags(folder)
    return list(DEFAULT_FLAGS) if found is None else found


def course_flags(start: Path) -> list[str] | None:
    """`[languages.c] flags` of the nearest workspace.toml above `start`; None when there is
    no workspace.toml or it sets none. Only the owner writes workspace.toml, and generation
    never does, so generated code cannot choose its own flags. ValueError when malformed."""
    for folder in [start, *start.parents]:
        path = folder / WORKSPACE_FILE
        if path.is_file():
            with path.open("rb") as f:
                flags = tomllib.load(f).get("languages", {}).get("c", {}).get("flags")
            if flags is None:
                return None
            check_flags(flags, f"{path}: [languages.c] flags")
            return flags
    return None


def check_flags(flags: object, where: str) -> None:
    """ValueError unless `flags` is a list of compiler options, each its own string. They go
    to the compiler as a list, never through a shell; a file name in it would be compiled into
    every program."""
    if not (isinstance(flags, list)
            and all(isinstance(f, str) and f.startswith("-") and f.strip() == f and f != "-"
                    for f in flags)):
        raise ValueError(f'{where} must be a list of compiler options, e.g. ["-Wall", '
                         '"-std=c99"]')


def _caller_folder() -> Path:
    """The folder of the file that called build(): the test file, whose neighbours are the
    sources, wherever pytest was started from."""
    caller = sys._getframe(2).f_globals.get("__file__")
    return Path(caller).resolve().parent if caller else Path.cwd()


def _exit_text(returncode: int) -> str:
    """"exited with status 3", or "was killed by SIGSEGV (segmentation fault)"."""
    if returncode >= 0:
        return f"exited with status {returncode}"
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        return f"was killed by signal {-returncode}"
    meaning = {"SIGSEGV": " (segmentation fault: a bad pointer or index)",
               "SIGABRT": " (abort: an assert failed, or the C library found a fault)",
               "SIGFPE": " (arithmetic error: division by zero?)"}.get(name, "")
    return f"was killed by {name}{meaning}"


def _up_to_summary(report: str) -> str:
    """A sanitizer's report up to its SUMMARY line: ASan's map of shadow memory after it means
    nothing to a learner."""
    lines = report.splitlines()
    ends = [i for i, line in enumerate(lines) if line.startswith("SUMMARY:")]
    return "\n".join(lines[:ends[0] + 1]) if ends else report


def _excerpt(text: str) -> str:
    lines = text.strip().splitlines()
    shown = lines[:_SHOWN_LINES]
    if len(lines) > _SHOWN_LINES:
        shown.append(f"... ({len(lines) - _SHOWN_LINES} more lines)")
    return "\n".join(shown)
