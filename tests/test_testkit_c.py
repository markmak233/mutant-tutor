"""tutor.testkit_c on this machine's C compiler: how a compile error, a warning, a crash, a
sanitizer report, a non-zero exit and a hang each show in a test, and where the course's
compiler flags come from. Needs `cc` (gcc or clang); skipped without one.
"""

import importlib.util
import json
import shutil
import sys
import warnings
from pathlib import Path

import pytest

from tutor import testkit_c
from tutor.testkit_c import (
    C_FLAGS_ENV,
    C_STRICT_ENV,
    DEFAULT_FLAGS,
    CompileError,
    CompilerWarning,
    LeaksNotCheckedWarning,
    compile_flags,
    course_flags,
)

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler (cc)")

HELLO = '#include <stdio.h>\nint main(void) { printf("hello\\n"); return 0; }\n'


def compiled(tmp_path: Path, source: str, *, extra: dict[str, str] | None = None):
    """Write `source` as p.c and a test module beside it that builds it at import, as an
    exercise's test_student.py does; import that module and return its program."""
    (tmp_path / "p.c").write_text(source, encoding="utf-8")
    for name, text in (extra or {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    sources = ", ".join(repr(n) for n in ["p.c", *(n for n in extra or {} if n.endswith(".c"))])
    module_file = tmp_path / "test_module.py"
    module_file.write_text(f"from tutor.testkit_c import build\nprogram = build({sources})\n",
                           encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"c_module_{id(tmp_path)}", module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.program


@pytest.fixture(autouse=True)
def _no_course_flags(monkeypatch):
    """Tests start without the validator's variables, as a learner's own run does."""
    monkeypatch.delenv(C_FLAGS_ENV, raising=False)
    monkeypatch.delenv(C_STRICT_ENV, raising=False)


def test_a_program_runs_with_its_input_and_arguments(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).parent)  # the sources are found beside the test file
    program = compiled(tmp_path, (
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        "    int a, b;\n"
        "    if (scanf(\"%d %d\", &a, &b) != 2) return 3;\n"
        "    printf(\"%d\\n%s\\n\", a + b, argc > 1 ? argv[1] : \"none\");\n"
        "    return 0;\n}\n"))

    run = program.run("word", stdin="2 40\n")

    assert (run.lines(), run.returncode, run.stderr) == (["42", "word"], 0, "")
    assert program.run(stdin="x", check=False).returncode == 3


def test_a_compile_error_raises_at_build_with_the_compilers_message(tmp_path):
    with pytest.raises(CompileError) as caught:
        compiled(tmp_path, "int main(void) { return 0 }\n")

    message = str(caught.value)
    assert message.startswith("p.c did not compile:")
    assert "p.c:1:" in message
    assert str(tmp_path) not in message  # file names, not this machine's paths


def test_a_call_to_an_undeclared_function_does_not_compile(tmp_path):
    """C99 would otherwise let code call any library function without its header, and the
    static check of headers counts on this."""
    with pytest.raises(CompileError, match="getpid"):
        compiled(tmp_path, "int main(void) { return getpid() > 0 ? 0 : 1; }\n")


def test_a_warning_is_listed_and_the_reference_must_compile_without_one(tmp_path, monkeypatch):
    source = "int main(void) { int unused; return 0; }\n"
    with pytest.warns(CompilerWarning, match="unused"):
        program = compiled(tmp_path, source)
    assert program.run().returncode == 0

    monkeypatch.setenv(C_STRICT_ENV, "1")
    with pytest.raises(CompileError, match="warnings count as errors here"):
        compiled(tmp_path, source)


def test_a_non_zero_exit_fails_unless_the_test_expects_it(tmp_path):
    program = compiled(tmp_path, '#include <stdio.h>\n'
                                 'int main(void) { fprintf(stderr, "bad input\\n"); return 1; }\n')

    with pytest.raises(AssertionError, match="exited with status 1; it printed to stderr:\nbad"):
        program.run()
    run = program.run(check=False)
    assert (run.returncode, run.stderr) == (1, "bad input\n")


def test_an_out_of_bounds_index_is_stopped_by_the_sanitizer(tmp_path):
    """Without the sanitizer this write past the array could go unnoticed."""
    program = compiled(tmp_path, (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int a[4], n;\n"
        "    if (scanf(\"%d\", &n) != 1) return 2;\n"
        "    for (int i = 0; i <= n; i++) a[i] = i;\n"
        "    printf(\"%d\\n\", a[0]);\n"
        "    return 0;\n}\n"))

    assert program.run(stdin="3").lines() == ["0"]
    with pytest.raises(AssertionError, match="the sanitizer stopped the program") as caught:
        program.run(stdin="4", check=False)  # a sanitizer's report fails even then
    assert "p.c:5" in str(caught.value)
    assert "Shadow bytes" not in str(caught.value)  # cut at the SUMMARY line


def test_signed_overflow_is_stopped_by_the_sanitizer(tmp_path):
    program = compiled(tmp_path, (
        "#include <limits.h>\n#include <stdio.h>\n"
        "int main(void) {\n"
        "    int n;\n"
        "    if (scanf(\"%d\", &n) != 1) return 2;\n"
        "    printf(\"%d\\n\", INT_MAX - 1 + n);\n"
        "    return 0;\n}\n"))

    assert program.run(stdin="1").lines() == [str(2**31 - 1)]
    with pytest.raises(AssertionError, match="runtime error"):
        program.run(stdin="2")


def test_a_program_that_never_ends_fails_its_test_at_the_time_limit(tmp_path):
    program = compiled(tmp_path, "int main(void) { for (;;) {} }\n")

    with pytest.raises(AssertionError, match="did not finish within 0.5 s"):
        program.run(timeout_s=0.5)


def test_a_program_killed_by_a_signal_names_it(tmp_path):
    program = compiled(tmp_path, "#include <stdlib.h>\nint main(void) { abort(); }\n")

    with pytest.raises(AssertionError, match="killed by SIGABRT"):
        program.run()


def test_several_sources_build_one_program(tmp_path):
    """A function exercise: student.c holds the function, the given driver.c the main."""
    program = compiled(tmp_path, "int twice(int x) { return 2 * x; }\n", extra={
        "driver.c": "#include <stdio.h>\nint twice(int x);\n"
                    "int main(void) { printf(\"%d\\n\", twice(21)); return 0; }\n"})

    assert program.run().lines() == ["42"]


LEAK = ("#include <stdlib.h>\n#include <stdio.h>\n"
        "int main(void) { int *p = malloc(64); p[0] = 1; printf(\"%d\\n\", p[0]); return 0; }\n")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="LeakSanitizer is Linux-only")
def test_a_leak_fails_only_a_run_that_asks_for_the_check(tmp_path):
    program = compiled(tmp_path, LEAK)

    assert program.run().lines() == ["1"]
    with pytest.raises(AssertionError, match="LeakSanitizer"):
        program.run(leaks=True)


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="LeakSanitizer exists here")
def test_without_leaksanitizer_a_leak_check_warns_that_it_did_not_check(tmp_path):
    program = compiled(tmp_path, LEAK)

    with pytest.warns(LeaksNotCheckedWarning, match="validation checks them"):
        assert program.run(leaks=True).lines() == ["1"]


def test_the_learners_own_asan_options_do_not_change_what_a_test_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("ASAN_OPTIONS", "halt_on_error=0:detect_stack_use_after_return=0")
    program = compiled(tmp_path, HELLO)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no warning for an ordinary run
        assert program.run().lines() == ["hello"]


# --- where the flags come from ------------------------------------------------------------


def test_flags_come_from_the_validator_then_the_course_then_the_default(tmp_path, monkeypatch):
    course = tmp_path / "course"
    exercise = course / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True)
    assert compile_flags(exercise) == list(DEFAULT_FLAGS)

    (course / "workspace.toml").write_text('course = "x"\n[languages.c]\nflags = ["-Wall", '
                                           '"-std=c99"]\n', encoding="utf-8")
    assert course_flags(exercise) == compile_flags(exercise) == ["-Wall", "-std=c99"]

    monkeypatch.setenv(C_FLAGS_ENV, json.dumps(["-std=c11"]))
    assert compile_flags(exercise) == ["-std=c11"]


def test_a_course_without_c_flags_gets_the_default(tmp_path):
    (tmp_path / "workspace.toml").write_text('course = "x"\n', encoding="utf-8")

    assert course_flags(tmp_path) is None
    assert compile_flags(tmp_path) == list(DEFAULT_FLAGS)


@pytest.mark.parametrize("flags", ['"-Wall"', '["-Wall", "extra.c"]', '["-Wall", " -O2"]',
                                   '[1]', '["-"]'])
def test_malformed_flags_are_refused(tmp_path, flags):
    (tmp_path / "workspace.toml").write_text(f'course = "x"\n[languages.c]\nflags = {flags}\n',
                                             encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[languages.c\] flags must be a list"):
        course_flags(tmp_path)


def test_the_module_imports_only_what_the_test_container_has():
    """Exercise tests import it inside the container, where only pytest and hypothesis are
    installed next to the standard library and the mounted tutor package."""
    source = Path(testkit_c.__file__).read_text(encoding="utf-8")
    imported = {line.split()[1].split(".")[0] for line in source.splitlines()
                if line.startswith(("import ", "from "))}
    assert imported <= set(sys.stdlib_module_names) | {"tutor", "__future__"}
