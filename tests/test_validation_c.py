"""Validation of C exercises (metadata "language": "c") on this machine's compiler.

C is validated only in a container (rule `safe` refuses otherwise); `pytest -m docker` runs the
examples that way in tests/test_c_real.py. Here `checks.image_for` is patched to say a container
is set, so rule `safe` passes, while the runs themselves still go to this machine (execution.py
asks on its own), which keeps these in the fast suite. Needs `cc`.
"""

import json
import shutil
from pathlib import Path

import pytest

from tutor.schema import CMetadata, Metadata, language_of, metadata_model, problems
from tutor.testkit_c import C_FLAGS_ENV, DEFAULT_FLAGS
from tutor.validation import ExerciseError, checks, load_exercise, validate
from tutor.validation.__main__ import format_report
from tutor.validation.checks import (
    Status,
    check_api_constraints,
    check_main,
    check_mutants_killed,
    check_reference_passes,
    check_safety,
    check_skeleton_fails,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
C_EXAMPLES = ["station-above-average-c", "station-drop-low-c"]
needs_cc = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler (cc)")


@pytest.fixture
def in_container(monkeypatch):
    """Rule `safe` sees a container; the runs still happen here."""
    monkeypatch.setattr(checks, "image_for", lambda sources: "tutor-run-test:pretend")


def copy_example(tmp_path: Path, name: str, workspace: str | None = None):
    """A copy of an example under tmp_path (with a workspace.toml when given) and its files."""
    root = tmp_path / name
    shutil.copytree(EXAMPLES / name, root, ignore=shutil.ignore_patterns("validation.json"))
    if workspace is not None:
        (tmp_path / "workspace.toml").write_text(workspace, encoding="utf-8")
    return root, load_exercise(root / "exercise", root / "solution")


# --- format and files -------------------------------------------------------------------


def test_a_c_metadata_is_checked_against_its_own_model():
    data = json.loads((EXAMPLES / C_EXAMPLES[1] / "solution" / "metadata.json")
                      .read_text(encoding="utf-8"))
    assert metadata_model(data) is CMetadata and language_of(data) == "c"
    assert problems(CMetadata, data) == []
    assert metadata_model({"framework": "python"}) is Metadata

    python_only = {**data, "standalone": True, "partition_check": True}
    assert sorted(problems(CMetadata, python_only)) == [
        "partition_check: Extra inputs are not permitted",
        "standalone: Extra inputs are not permitted"]
    assert any("framework" in p for p in problems(CMetadata, {**data, "framework": "python"}))
    # Without "language": "c" it is a Python metadata, which has no C framework.
    without = {k: v for k, v in data.items() if k != "language"}
    assert any("framework" in p for p in problems(metadata_model(without), without))


def test_a_c_exercise_loads_its_c_files_and_runs_them_as_student_c():
    root = EXAMPLES / "station-drop-low-c"
    ex = load_exercise(root / "exercise", root / "solution")

    assert (ex.language, ex.student_file) == ("c", "student.c")
    assert ex.reference.name == "reference.c" and ex.skeleton.name == "skeleton.c"
    assert [m.name for m in ex.mutants] == ["m01_drops_the_limit.c", "m02_counts_without_moving.c",
                                            "m03_one_past_the_end.c"]
    files = ex.run_files(ex.reference)
    assert files["student.c"] == ex.reference
    assert {"driver.c", "student.h", "test_student.py"} <= set(files)
    assert "hints.md" not in files and "student.py" not in files
    assert not ex.heavy


def test_an_unknown_language_stops_loading(tmp_path):
    root, _ = copy_example(tmp_path, "station-drop-low-c")
    metadata = root / "solution" / "metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    metadata.write_text(json.dumps({**data, "language": "rust"}), encoding="utf-8")

    with pytest.raises(ExerciseError, match="language 'rust' is not one of python, c"):
        load_exercise(root / "exercise", root / "solution")


def test_every_run_gets_the_courses_flags_and_python_runs_get_nothing(tmp_path):
    _, ex = copy_example(tmp_path, "station-drop-low-c")
    assert ex.run_env == {C_FLAGS_ENV: json.dumps(list(DEFAULT_FLAGS))}

    _, ex = copy_example(tmp_path / "course", "station-drop-low-c",
                         'course = "x"\n[languages.c]\nflags = ["-Wall", "-std=c99"]\n')
    assert ex.run_env == {C_FLAGS_ENV: '["-Wall", "-std=c99"]'}

    python = EXAMPLES / "station-longest-rise"
    assert load_exercise(python / "exercise", python / "solution").run_env == {}


# --- rule safe --------------------------------------------------------------------------


def test_c_is_refused_unless_the_course_runs_its_tests_in_a_container():
    root = EXAMPLES / "station-drop-low-c"
    report = validate(load_exercise(root / "exercise", root / "solution"))

    safe = report.result("safe")
    assert safe.status is Status.FAIL
    assert safe.detail.startswith('C code runs only in a container: set [isolation] runner = '
                                  '"container"')
    assert all(r.status is Status.SKIPPED for r in report.results if r.rule not in ("0", "safe"))


def test_safe_reads_every_c_file_a_run_compiles(tmp_path, in_container):
    root, ex = copy_example(tmp_path, "station-drop-low-c")
    assert check_safety(ex).detail == "10 files checked"
    added = len(ex.skeleton.read_text(encoding="utf-8").splitlines()) + 1  # the line appended

    (root / "solution" / "skeleton.c").write_text(
        ex.skeleton.read_text(encoding="utf-8") + 'int run(void) { return system("ls"); }\n',
        encoding="utf-8")
    driver = root / "exercise" / "driver.c"
    driver.write_text("#include <unistd.h>\n" + driver.read_text(encoding="utf-8"),
                      encoding="utf-8")

    detail = check_safety(load_exercise(root / "exercise", root / "solution")).detail
    assert "driver.c line 1: #include <unistd.h> is not allowed" in detail
    assert f"solution/skeleton.c line {added}: system is not allowed" in detail


def test_a_course_may_allow_more_headers(tmp_path, in_container):
    root, _ = copy_example(tmp_path, "station-drop-low-c",
                           'course = "x"\n[safety]\nallow_headers = ["unistd.h"]\n')
    driver = root / "exercise" / "driver.c"
    driver.write_text("#include <unistd.h>\n" + driver.read_text(encoding="utf-8"),
                      encoding="utf-8")

    assert check_safety(load_exercise(root / "exercise", root / "solution")).status is Status.OK


def test_malformed_course_flags_stop_validation_at_safe(tmp_path, in_container):
    _, ex = copy_example(tmp_path, "station-drop-low-c",
                         'course = "x"\n[languages.c]\nflags = "-Wall"\n')

    assert check_safety(ex).detail.endswith('[languages.c] flags must be a list of compiler '
                                            'options, e.g. ["-Wall", "-std=c99"]')


# --- the rules that compile and run -------------------------------------------------------


@needs_cc
@pytest.mark.parametrize("name", C_EXAMPLES)
def test_the_c_examples_are_valid(name, in_container):
    report = validate(load_exercise(EXAMPLES / name / "exercise", EXAMPLES / name / "solution"))

    assert report.valid, format_report(report)
    assert report.result("5").status is Status.NOT_APPLICABLE


@needs_cc
def test_the_reference_must_compile_without_a_warning(tmp_path, in_container):
    """Under the course's flags, as the course asks of its students; others may warn."""
    root, ex = copy_example(tmp_path, "station-drop-low-c")
    warned = ex.reference.read_text(encoding="utf-8").replace("int kept = 0;",
                                                              "int kept = 0, unused;")
    ex.reference.write_text(warned, encoding="utf-8")
    # The same code as a candidate.
    (root / "solution" / "skeleton.c").write_text(warned, encoding="utf-8")

    reference = check_reference_passes(ex, 30)
    assert reference.status is Status.FAIL
    assert "warnings count as errors" in reference.excerpt and "unused" in reference.excerpt
    # Given as a candidate (an owner's own solution in the held-out check), it compiles.
    assert check_reference_passes(ex, 30, candidate=ex.skeleton).status is Status.OK


@needs_cc
def test_code_that_does_not_compile_is_an_error_not_a_failure(tmp_path, in_container):
    """A skeleton must fail its tests, not stop before them; a mutant that does not compile
    shows nothing, so it is "broken" rather than killed."""
    root, ex = copy_example(tmp_path, "station-drop-low-c")
    ex.skeleton.write_text("int drop_below(int *readings, int n, int limit) { return 0 }\n",
                           encoding="utf-8")
    (root / "solution" / "mutants" / "m04_typo.c").write_text("int drop_below( {\n",
                                                              encoding="utf-8")
    ex = load_exercise(root / "exercise", root / "solution")

    skeleton = check_skeleton_fails(ex, 30)
    assert skeleton.status is Status.FAIL
    assert skeleton.detail.startswith("skeleton errors before any test runs (does it compile?)")
    mutants = check_mutants_killed(ex, 30)
    assert mutants.detail == "3/4 killed; cannot run (not a useful mutant): m04_typo"


def test_api_reads_the_references_tokens(tmp_path):
    _, ex = copy_example(tmp_path, "station-drop-low-c")
    assert check_api_constraints(ex).status is Status.OK

    ex.reference.write_text(ex.reference.read_text(encoding="utf-8").replace(
        "int kept = 0;", "int kept = 0;\n    void *spare = malloc(4); /* malloc in a comment */"),
        encoding="utf-8")
    api = check_api_constraints(ex)
    assert (api.status, api.detail) == (Status.FAIL,
                                        "reference breaks its own constraints: line 7: uses malloc")


def test_main_asks_a_whole_program_for_its_main(tmp_path):
    _, ex = copy_example(tmp_path, "station-above-average-c")
    assert check_main(ex, 30).status is Status.OK

    ex.reference.write_text(ex.reference.read_text(encoding="utf-8")
                            .replace("int main(void)", "int run(void)"), encoding="utf-8")
    main = check_main(ex, 30)
    assert (main.status, main.detail) == (Status.FAIL,
                                          "reference.c define no main: a whole program needs one")

    _, function = copy_example(tmp_path / "f", "station-drop-low-c")
    assert check_main(function, 30).status is Status.NOT_APPLICABLE
