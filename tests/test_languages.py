"""A course's languages (tutor.languages): what workspace.toml declares, what is not supported
yet, and the compiler flags the lectures show. The lecture lines here are made up."""

from pathlib import Path

import pytest

from tutor import languages
from tutor.config import Workspace, load_workspace
from tutor.envcheck import check_workspace, requires_problems
from tutor.materials import cache


def test_python_needs_nothing_declared_and_c_is_a_table_of_its_own():
    assert languages.declared({}) == [] and not languages.is_c({})
    data = {"languages": {"c": {"flags": ["-Wall", "-std=c99"]}}}
    assert languages.declared(data) == ["c"] and languages.is_c(data)
    assert languages.problems(data) == []


@pytest.mark.parametrize(("languages_table", "says"), [
    ({"rust": {}}, "[languages.rust]: rust is not supported yet (exercises can be in Python or C)"),
    ({"c": {"compiler": "clang"}}, "[languages.c]: unknown key compiler (known: flags)"),
    ({"c": {"flags": "-Wall"}}, "[languages.c] flags must be a list of compiler options"),
    ({"c": "yes"}, "[languages.c] must be a table"),
    ("c", "[languages] must be a table of languages"),
])
def test_what_is_wrong_with_languages_is_said(languages_table, says):
    found = languages.problems({"languages": languages_table})
    assert len(found) == 1 and found[0].startswith(says)


@pytest.mark.parametrize("name", ["javac", "rustc", "g++", "node", "Cargo"])
def test_a_toolchain_of_a_language_not_supported_is_not_taken_for_a_pip_package(name):
    """g++ would be "not a package name", javac a pip install that fails: neither says why."""
    [problem] = requires_problems({name: "1"})
    assert "not supported yet (exercises can be in Python or C)" in problem
    assert languages.toolchain_problem("pyspark") is None


def test_the_check_reports_a_language_not_supported_and_a_toolchain_entry(tmp_path):
    ws = Workspace(root=tmp_path, course="x", requires={"javac": "17"}, lecture_dir=tmp_path,
                   languages={"java": {}})
    javac, language = check_workspace(ws)
    assert (javac.ok, javac.found) == (False, None)
    assert javac.hint.startswith("[requires] javac is a Java toolchain")
    assert (language.name, language.ok) == ("languages", False)
    assert "java is not supported yet" in language.hint


def test_the_workspace_keeps_its_languages_table(tmp_path):
    (tmp_path / "workspace.toml").write_text(
        'course = "x"\n[languages.c]\nflags = ["-Wall"]\n', encoding="utf-8")
    ws = load_workspace({"WORKSPACE_DIR": str(tmp_path)})
    assert ws.languages == {"c": {"flags": ["-Wall"]}}
    assert check_workspace(ws) == []


# --- the flags the lectures compile with ------------------------------------------------------


def test_the_flags_most_compile_lines_share_are_suggested_with_the_first_such_line():
    pages = [
        ("lecture02", "p.4", "gcc -Wall -std=c99 celsius.c -o f2c\nint main(void)"),
        ("lecture02", "p.5", "  gcc -std=c99 -Wall -c utility.c"),  # the same set, reordered
        ("lecture05", "p.1", "CFLAGS = -Wall -std=c99 -ggdb\ngcc -O2 -std=c11 x.c"),
        ("lecture07", "p.2", "cc -std=c99 -Wall -DNUM=100 define.c -o program"),
        ("lecture08", "p.3", "The access rights: -Wall in a sentence, not a compile line"),
    ]
    found = languages.suggested_c_flags(pages)
    assert found.flags == ["-Wall", "-std=c99"]
    assert (found.lecture, found.page, found.count, found.lines) == ("lecture02", "p.4", 3, 4)
    assert found.line == "gcc -Wall -std=c99 celsius.c -o f2c"


def test_no_compile_line_with_flags_suggests_nothing():
    pages = [("l1", "p.1", "gcc hello.c\nmake all"), ("l2", "p.1", "Use the gcc compiler.")]
    assert languages.suggested_c_flags(pages) is None


def test_lecture_pages_reads_the_cached_lectures(tmp_path):
    entries = [cache.Entry(1, None, "text", "h1", "gcc -Wall -std=c99 a.c"),
               cache.Entry(2, None, "text", "h2", "no command")]
    path = tmp_path / "cache" / "lectures" / "lecture02.html.md"
    path.parent.mkdir(parents=True)
    path.write_text(cache.render("lecture02.html", entries), encoding="utf-8")

    pages = languages.lecture_pages(Path(tmp_path))
    assert pages == [("lecture02.html", "p.1", "gcc -Wall -std=c99 a.c"),
                     ("lecture02.html", "p.2", "no command")]
    assert languages.suggested_c_flags(pages).flags == ["-Wall", "-std=c99"]
