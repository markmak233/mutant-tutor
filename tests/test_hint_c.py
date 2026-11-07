"""Hints for C exercises (docs/design.md, "C", step 4b).

Layer 1 has C rules of its own, read from what the compiler and the sanitizers print (real
runs on this machine's `cc` where a test needs one, and gcc-shaped text for what only the
container's gcc prints); layer 2 reads a C mutant's `/* Mutant: ... */` line; layer 3 names
student.c to the model and reads C comments as comments when it checks for a leak.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from tutor.execution import run_pytest
from tutor.hint import llm_hint, mutants
from tutor.hint.__main__ import hint_for
from tutor.hint.rules import first_rule
from tutor.validation.exercise import load_exercise

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
needs_cc = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler (cc)")


def copy_example(tmp_path: Path, name: str) -> Path:
    """A copy, so the mutant signatures the hint command caches stay out of the repo."""
    root = tmp_path / name
    shutil.copytree(EXAMPLES / name, root, ignore=shutil.ignore_patterns("validation.json"))
    return root


def run_as_student(source: str) -> str:
    """The test output of the whole-program example with `source` as student.c."""
    root = EXAMPLES / "station-above-average-c"
    ex = load_exercise(root / "exercise", root / "solution")
    student = Path(tempfile.mkdtemp()) / "student.c"
    student.write_text(source, encoding="utf-8")
    return run_pytest(ex.run_files(student), 60, {}).output


def rule(source: str, output: str, mode: str = "learn", constraints: dict | None = None):
    return first_rule(source, output, constraints or {}, mode, "c")


# --- layer 1, on real runs ----------------------------------------------------------------

MAIN = "int main(void) {{\n{body}\n    return 0;\n}}\n"


@needs_cc
@pytest.mark.parametrize("source, key, says", [
    ("#include <stdio.h>\n" + MAIN.format(body="    printf(\"%d\\n\", 1)"),
     "c-compile", "line 3 of student.c: expected ';'"),
    (MAIN.format(body='    printf("hi\\n");'),
     "c-undeclared", "line 2 of student.c: `printf` is used without its header; add "
                     "#include <stdio.h>"),
    ("int helper(void);\n" + MAIN.format(body="    helper();"),
     "c-undefined", "`helper` is called but not defined anywhere"),
    ("#include <stdio.h>\n" + MAIN.format(body="    int *p = NULL;\n    printf(\"%d\\n\", *p);"),
     "c-bad-pointer", "line 4 of student.c, in main: a NULL or invalid pointer"),
    ("#include <stdio.h>\n" + MAIN.format(body="    volatile int z = 0;\n"
                                                "    printf(\"%d\\n\", 1 / z);"),
     "c-division", "line 4 of student.c, in main: a division by zero"),
    ("#include <stdlib.h>\n" + MAIN.format(body="    int *p = malloc(4);\n    free(p);\n"
                                                "    free(p);"),
     "c-double-free", "line 5 of student.c, in main: the same memory is freed twice"),
])
def test_a_c_mistake_gets_its_rule_with_the_line(source, key, says):
    hint = rule(source, run_as_student(source))

    assert (hint.layer, hint.key) == ("rule", key)
    assert says in hint.text


@needs_cc
def test_an_index_outside_an_array_names_the_students_line_not_the_c_library():
    """The report's first frame is scanf, inside the sanitizer's runtime; the hint names
    the first frame in student.c."""
    source = (EXAMPLES / "station-above-average-c" / "solution" / "mutants"
              / "m03_array_too_small.c").read_text(encoding="utf-8")

    output = run_as_student(source)
    learn, review = rule(source, output), rule(source, output, "review")

    assert learn.key == "c-bounds"
    assert learn.text.startswith("line 14 of student.c, in read_readings: an index outside")
    assert review.text == "an index outside an array"  # review: the kind only


# --- layer 1, on text only the container's gcc prints --------------------------------------

def test_gccs_missing_header_and_linker_messages_are_read():
    gcc = ("E   student.c:2:5: error: implicit declaration of function ‘strlen’ "
           "[-Werror=implicit-function-declaration]\n"
           "E   student.c:1:1: note: include ‘<string.h>’ or provide a declaration of "
           "‘strlen’\n")
    linker = ("E   /usr/bin/ld: /tmp/cc1.o: in function `main':\n"
              "E   undefined reference to `total'\n")

    assert rule("", gcc).text == ("line 2 of student.c: `strlen` is used without its header; "
                                  "add #include <string.h> at the top")
    assert rule("", linker).text.startswith("`total` is called but not defined anywhere")


def test_gccs_curly_quotes_read_as_straight_ones():
    """gcc in a UTF-8 locale (GitHub's Ubuntu runner) quotes with ‘ ’, in the C locale (the
    container) with ' ': the hint reads the same either way, as clang's does."""
    for quote_open, quote_close in (("‘", "’"), ("'", "'")):
        gcc = (f"E   student.c:3:22: error: expected {quote_open};{quote_close} before "
               f"{quote_open}return{quote_close}\n")

        assert rule("", gcc).text.startswith(
            "the code does not compile: line 3 of student.c: expected ';' before 'return'.")


def test_a_program_that_never_finishes_is_named():
    """testkit_c's message at its time limit (its own tests run a real endless loop; here
    that would wait 5 s for every test of the exercise)."""
    output = ("E       AssertionError: the program did not finish within 5 s: an endless "
              "loop, or waiting for input it was never given?\n")

    assert rule("", output).key == "c-timeout"
    assert rule("", output, "review").text == "a program that does not finish"


def test_a_report_with_folders_before_the_file_name_names_the_file_alone():
    output = ("E   ==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x6020\n"
              "E       #0 0x55d1 in drop_below /tmp/tutor-run-x1/student.c:8\n"
              "E       #1 0x55d2 in main /tmp/tutor-run-x1/driver.c:28\n")

    assert rule("", output).text.startswith("line 8 of student.c, in drop_below:")


def test_full_width_punctuation_counts_only_in_the_code():
    """In the question (a comment) or a string it is fine; in the code the compiler stops."""
    in_comment = "/* 题目：数一数，然后打印。 */\nint main(void) { return 0; }\n"
    in_string = 'int main(void) { puts("你好，世界"); return 0; }\n'
    in_code = "int main(void) {\n    return 0；\n}\n"

    assert rule(in_comment, "") is None and rule(in_string, "") is None
    assert rule(in_code, "").text.startswith(
        "a full-width character where C needs ASCII (line 2: '；' should be ';')")


def test_a_forbidden_function_is_named_after_the_compilers_errors():
    source = "#include <stdlib.h>\nint main(void) { qsort(0, 0, 0, 0); return 0; }\n"

    hint = rule(source, "E   AssertionError: assert [] == ['1']",
                constraints={"forbidden_calls": ["qsort"]})

    assert (hint.key, hint.text) == ("forbidden-api",
                                     "this exercise does not allow it: line 2: uses qsort")


def test_python_rules_are_not_asked_about_c():
    """C code does not parse as Python: without the language it would read as a syntax
    error. With it, a plain wrong answer gets no rule."""
    source = "int main(void) { return 0; }\n"

    assert first_rule(source, "", {}, "learn").key == "syntax"
    assert rule(source, "E   AssertionError: assert ['1'] == ['2']") is None


# --- layer 2 and the whole command ------------------------------------------------------

def test_a_c_mutants_bug_is_read_from_its_comment(tmp_path):
    mutant = tmp_path / "m01.c"
    mutant.write_text("/* Mutant: integer division,\n   so the average loses its fraction. */"
                      "\n#include <stdio.h>\n", encoding="utf-8")

    assert mutants.bug_of(mutant) == ("integer division", "the average loses its fraction")


def test_two_runs_of_one_c_mistake_have_one_signature():
    """What differs between runs (process id, temp folder, addresses) is taken out."""
    first = ("_ test_x _\nE   ==101==ERROR: AddressSanitizer: SEGV on 0x0000\n"
             "E   #0 0x10 in main /tmp/tutor-run-a/student.c:4\n")
    second = ("_ test_x _\nE   ==202==ERROR: AddressSanitizer: SEGV on 0x0000\n"
              "E   #0 0x20 in main /tmp/tutor-run-b/student.c:4\n")

    assert mutants._normal("E   ==101== at /tmp/tutor-run-a/student.c:4") == (
        "==<pid>== at student.c:4")
    assert mutants.signature(first) == mutants.signature(second)


@needs_cc
def test_the_hint_command_on_a_c_exercise(tmp_path):
    """A logic mistake matches its mutant (layer 2, the bug from its comment); a memory
    mistake gets its rule (layer 1); the reference passes. The function example, so the
    given driver.c runs too (about 8 s: every mutant runs once for its signature)."""
    root = copy_example(tmp_path, "station-drop-low-c")
    exercise, solution = root / "exercise", root / "solution"
    found = {}
    for mutant in sorted((solution / "mutants").glob("*.c")):
        shutil.copy(mutant, exercise / "student.c")
        found[mutant.stem] = hint_for(exercise, say=lambda text: None)
    shutil.copy(solution / "reference.c", exercise / "student.c")

    m01, m02, m03 = (found[k] for k in sorted(found))
    assert (m01.layer, m02.layer, m03.layer) == ("mutant", "mutant", "rule")
    assert m03.key == "c-bounds"
    bug, _ = mutants.bug_of(sorted((solution / "mutants").glob("*.c"))[0])
    assert m01.text.startswith(f"your tests fail exactly as a known mistake: {bug}")
    assert hint_for(exercise, say=lambda text: None) is None


# --- layer 3 ------------------------------------------------------------------------------

def test_the_model_is_told_the_question_is_student_cs_opening_comment():
    python = llm_hint.user_prompt("def f(): ...", "", {}, "E   x")
    c = llm_hint.user_prompt("int f(void);", "", {}, "E   x", "c")

    # Python's prompt keeps the text it always had.
    assert python.startswith("## student.py (the question is its docstring; the rest is the "
                             "student's code)\n\ndef f(): ...")
    assert c.startswith("## student.c (the question is its opening comment; the rest is the "
                        "student's code)\n\nint f(void);")


def test_the_leak_check_reads_c_comments_as_comments():
    reference = ("/* # The question\n   int drop_below(int *readings, int n, int limit) */\n"
                 "    for (int i = 0; i < n; i++) {  // every reading\n"
                 "        if (readings[i] >= limit) readings[kept++] = readings[i];\n")
    copies_code = "Write `if (readings[i] >= limit) readings[kept++] = readings[i];` here."
    quotes_comment = "The question gives `int drop_below(int *readings, int n, int limit)`."

    assert llm_hint.leak(copies_code, reference, "", 0, "c")
    assert llm_hint.leak(quotes_comment, reference, "", 0, "c") is None
