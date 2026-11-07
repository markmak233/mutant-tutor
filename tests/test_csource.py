"""validation/csource.py: the C part of rules `safe` and `api`, on source text only.
Nothing here compiles or runs anything."""

from pathlib import Path

import pytest

from tutor.validation.csource import (
    C_HEADERS,
    defines,
    directives,
    find_hazards,
    find_violations,
    tokens,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
LOCAL = {"student.c", "student.h", "driver.c"}


def hazards(source: str, headers=C_HEADERS, local=LOCAL) -> list[str]:
    return find_hazards(source, headers, local)


# --- includes ---------------------------------------------------------------------------


def test_the_standard_headers_and_the_exercises_own_files_may_be_included():
    source = ('#include <stdio.h>\n#include <stdlib.h>\n# include <math.h>\n'
              '#include "student.h"\n')
    assert hazards(source) == []


def test_another_header_needs_the_course_to_allow_it():
    source = "#include <unistd.h>\n"
    assert hazards(source) == ["line 1: #include <unistd.h> is not allowed (allowed: the C "
                               "standard headers and the course's [safety] allow_headers)"]
    assert hazards(source, headers=C_HEADERS | {"unistd.h"}) == []


@pytest.mark.parametrize("name", ["../secret.h", "/etc/passwd", "sub/x.h", "other.h"])
def test_a_quoted_include_names_only_a_file_of_the_exercise(name):
    """The compiler reads what an include names, on the learner's machine too."""
    assert hazards(f'#include "{name}"\n') == [
        f'line 1: #include "{name}" is not allowed: only a file of the exercise, by its plain '
        "name"]


@pytest.mark.parametrize(("line", "message"), [
    ("#include HEADER", "#include HEADER is not allowed"),
    ("#embed \"/etc/passwd\"", "#embed is not allowed"),
    ("#import <stdio.h>", "#import is not allowed"),
    ("#include_next <stdio.h>", "#include_next is not allowed"),
])
def test_other_forms_of_include_are_refused(line, message):
    assert hazards(line + "\n")[0].startswith(f"line 1: {message}")


def test_an_include_continued_over_two_lines_is_read_whole():
    assert hazards("int x;\n#include \\\n  <unistd.h>\n") == [
        "line 2: #include <unistd.h> is not allowed (allowed: the C standard headers and the "
        "course's [safety] allow_headers)"]


def test_an_include_in_a_comment_is_not_one():
    assert hazards("/* #include <unistd.h> */\n// #include <sys/socket.h>\nint x;\n") == []


# --- names and paths --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["system", "popen", "fork", "execve", "socket", "dlopen",
                                  "syscall"])
def test_process_network_and_system_call_names_are_refused(name):
    found = hazards(f"#include <stdlib.h>\nint main(void) {{ {name}(\"x\"); return 0; }}\n")
    assert found == [f"line 2: {name} is not allowed (processes, network or system calls; if "
                     "it is a name of your own, rename it)"]


def test_a_function_declared_by_hand_or_taken_by_address_is_refused_too():
    """A header check alone misses these: C lets code declare a library function itself."""
    source = ("int system(const char *command);\n"
              "int (*run)(const char *) = system;\n")
    assert [h.split(":")[0] for h in hazards(source)] == ["line 1", "line 2"]


def test_names_in_comments_strings_or_longer_names_are_not_uses():
    source = ('/* system("ls") */\n'
              'int systematic = 0; // fork\n'
              'const char *s = "popen(1) and system";\n'
              "char c = 's';\n")
    assert hazards(source) == []


def test_inline_assembly_and_token_pasting_are_refused():
    found = hazards('int main(void) { __asm__("nop"); return 0; }\n#define CAT(a, b) a##b\n')
    assert found == [
        "line 1: inline assembly (__asm__) is not allowed",
        "line 2: token pasting (##) is not allowed: it builds names this check cannot see",
    ]


@pytest.mark.parametrize("call", ['fopen("/etc/passwd", "r")', 'fopen("../data.txt", "r")',
                                  'remove("~/notes")', 'rename("a.txt", "../b.txt")'])
def test_a_literal_path_outside_the_exercise_is_refused(call):
    found = hazards(f"#include <stdio.h>\nvoid f(void) {{ {call}; }}\n")
    assert len(found) == 1 and found[0].startswith("line 2: ")
    assert found[0].endswith("reaches outside the exercise")


def test_paths_inside_the_exercise_or_from_a_variable_are_allowed():
    source = ('#include <stdio.h>\n'
              'void f(const char *path) { fopen("data/in.txt", "r"); fopen(path, "w"); }\n')
    assert hazards(source) == []


def test_line_numbers_count_lines_inside_comments_and_strings():
    source = '/* one\n   two\n*/\nconst char *s = "a\\nb";\n\nint x = system;\n'
    assert hazards(source)[0].startswith("line 6: system")


def test_the_examples_c_files_pass():
    files = [*sorted(EXAMPLES.glob("*-c/exercise/*.[ch]")),
             *sorted(EXAMPLES.glob("*-c/solution/**/*.c"))]
    assert len(files) >= 10
    for path in files:
        root = next(folder for folder in path.parents if (folder / "exercise").is_dir())
        local = {p.name for p in root.glob("exercise/*")} | {"student.c"}
        assert hazards(path.read_text(encoding="utf-8"), local=local) == [], path


# --- rule api ---------------------------------------------------------------------------


def test_a_forbidden_call_is_found_with_its_line_but_not_in_comments_or_strings():
    source = ('#include <stdlib.h>\n'
              '/* qsort would be easier */\n'
              'void sort(int *a, int n) {\n'
              '    puts("no qsort here");\n'
              '    qsort(a, n, sizeof *a, 0);\n'
              '}\n')
    assert find_violations(source, {"forbidden_calls": ["qsort"]}) == ["line 5: uses qsort"]


def test_required_definitions_need_a_body_not_a_prototype():
    source = "int swap(int *a, int *b);\nstatic int helper(void) { return 1; }\n"
    assert find_violations(source, {"required_definitions": ["swap", "helper"]}) == [
        "does not define swap"]


def test_an_unknown_constraint_key_is_reported():
    assert find_violations("", {"forbidden_imports": ["x"]}) == [
        "unknown constraint key(s) for C: forbidden_imports"]


def test_defines_finds_functions_at_file_level_only():
    source = ("struct point { int x, y; };\n"
              "int table[] = { 1, 2 };\n"
              "#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"
              "int main(int argc, char **argv)\n{\n"
              "    if (argc > 1) { return 1; }\n"
              "    while (0) { }\n"
              "    return 0;\n}\n"
              "static int pick(int k) { return k; }\n")
    assert defines(source) == {"main", "pick"}


def test_tokens_and_directives_leave_comments_out():
    source = '#define A 1 /* note */\nint x = A; // trailing\n'
    assert [t.text for t in tokens(source)] == ["#", "define", "A", "1", "int", "x", "=", "A", ";"]
    assert directives(source) == [(1, "#define A 1")]
