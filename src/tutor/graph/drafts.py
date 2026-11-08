"""A round's draft on disk and through the validator (validate_draft): the model's reply is
written out as an exercise and its solution (write_draft), each mutant applied to the
reference (apply_mutant), and the validator's failing rules become the failure text that
repair sees, with rule 4's verdict as a tie-breaker between the reference and the tests.
"""

from __future__ import annotations

import ast
import difflib
import io
import re
import shutil
import textwrap
import tokenize
from pathlib import Path, PurePosixPath

from tutor.graph.languages import PROFILES
from tutor.graph.nodes import Deps
from tutor.graph.state import MIN_MUTANTS, CExerciseDraft, ExerciseDraft, ItemState, MutantDraft
from tutor.question import QUESTION_FILE, question_markdown
from tutor.validation.checks import Status, ValidationReport


class DraftError(Exception):
    """The reply could not be turned into exercise files (a format failure, not a
    validation failure)."""


# Files of the exercise folder that data_files must not replace.
OWN_FILES = {"student.py", "test_student.py", "hints.md", "question.md"}


# --- validate ---------------------------------------------------------------------------

def validate_draft(state: ItemState, deps: Deps) -> ItemState:
    """Write the draft out and run the validator. The failure shown to repair lists the
    failing rules only, not the passing ones."""
    item, round_ = state["item"], state.get("round", 0)
    attempts = list(state.get("attempts", []))
    failure, format_error, round_dir = state.get("failure"), state.get("format_error", False), ""
    dropped: dict[str, str] = {}
    if state.get("draft") is not None:
        round_dir_path = deps.drafts / item.id / f"round-{round_}"
        try:
            exercise, solution, dropped = write_draft(state["draft"], item.id, round_dir_path,
                                                      deps.mode, item.lecture)
        except DraftError as e:
            failure, format_error = f"format: {e}", True
        else:
            round_dir = str(round_dir_path)
            report = deps.check(exercise, solution)
            (round_dir_path / "report.txt").write_text(_report_text(report), encoding="utf-8")
            # The failure text: each failing rule with pytest's excerpt, for repair.
            failure = None if report.valid else "\n".join(
                f"rule {r.rule} ({r.name}) FAIL: {r.detail}"
                + ("\n" + textwrap.indent(r.excerpt, "    ") if r.excerpt else "")
                for r in report.results if r.status is Status.FAIL)
            # Rule 4 as a tie-breaker: when it disagrees, repair sees the oracle's code;
            # when rule 1 fails but rule 4 agrees, the tests are the likely culprit.
            status = {r.rule: r.status for r in report.results}
            disagrees = any(r.rule == "4" and r.detail.startswith("reference disagrees")
                            for r in report.results)  # not a test that cannot run
            if deps.oracle_config is not None and disagrees:
                failure += ORACLE_DISAGREES + state["draft"].oracle_py
            elif status.get("1") is Status.FAIL and status.get("4") is Status.OK:
                failure += ORACLE_AGREES.format(
                    oracle="oracle.py, which another model wrote from the question alone"
                    if deps.oracle_config is not None else "oracle.py")
            if failure and dropped:
                # Not a failure by itself (a VALID draft is saved without them), but a
                # repair round may as well fix them.
                failure += ("\nLeft out, could not be applied (fix them too):\n"
                            + "\n".join(dropped.values()))
    attempts.append({"round": round_, "failure": failure, "format_error": format_error,
                     "dropped": sorted(dropped), "unchanged": state.get("unchanged", False),
                     "model": state.get("model", deps.config.name),
                     "diagnosis": state["draft"].diagnosis if state.get("draft") else ""})
    return {"failure": failure, "format_error": format_error, "attempts": attempts,
            "round_dir": round_dir}


ORACLE_DISAGREES = (
    "\nAbout rule 4: oracle.py was written by another model from your question, oracle "
    "interface and sample data alone; it never saw your reference. If the reference is "
    "right, the question is ambiguous or incomplete there: make it say exactly what it "
    "means (the oracle is then written again). If the reference is wrong, fix it. Keep "
    "oracle_py an interface: never copy this code into it. The oracle it wrote:\n")


ORACLE_AGREES = (
    "\nAbout rule 1: rule 4 ran anyway, and on random inputs the reference agrees with "
    "{oracle}. So the tests are the likely side to fix: their expected values (work them "
    "out again from the question on each test's data) or how they run.")


def _report_text(report: ValidationReport) -> str:
    """The round's report.txt: one line per rule, then VALID or INVALID."""
    return "\n".join(f"{r.rule:>4} {r.status.value:<5} {r.name}: {r.detail}"
                     for r in report.results) + ("\nVALID\n" if report.valid else "\nINVALID\n")


def write_draft(draft: ExerciseDraft | CExerciseDraft, exercise_id: str, root: Path,
                mode: str | None = None, lecture: str | None = None
                ) -> tuple[Path, Path, dict[str, str]]:
    """root/exercises/<id> and root/solutions/<id>, laid out like a batch, and the mutants
    left out: one that cannot be applied is left out (name -> why) while MIN_MUTANTS others
    remain, so one copying slip does not cost the whole exercise; fewer is a format error.
    `mode`: the batch's, for the question's line under the title. `lecture`: the plan item's,
    the name of its cache file. A C draft (CExerciseDraft) writes .c files, and for a
    function exercise the given student.h and driver.c."""
    c = isinstance(draft, CExerciseDraft)
    if draft.metadata.id != exercise_id:
        raise DraftError(f"metadata.id is {draft.metadata.id!r}, the plan names {exercise_id!r}")
    if draft.metadata.source == "synthetic":
        # The example exercise says synthetic, and a model copies it; a generated exercise
        # always comes from course pages, which it must cite.
        raise DraftError('metadata.source is "synthetic": give the lecture, slides and PDF '
                         "pages the exercise comes from")
    if not c and escaped_docstring(draft.student_py):
        raise DraftError('student.py\'s docstring has a backslash but is not a raw string: '
                         'Python reads "\\frac" as a form feed and "\\neq" as a line break. '
                         'Write it as r"""...""" so the LaTeX stays as written')
    if c:
        _check_c_shape(draft)
    if root.exists():
        shutil.rmtree(root)
    exercise, solution = root / "exercises" / exercise_id, root / "solutions" / exercise_id
    metadata = draft.metadata
    if not c and metadata.standalone is None:
        # A generated exercise is always under rule "main"; only the model's explicit
        # false (a framework-launched program) is kept.
        metadata = metadata.model_copy(update={"standalone": True})
    if lecture is not None:
        # The exact file in place of the model's words: the judge and the held-out writer find
        # the pages by it, so a course may name its files anything, and a course code or a year
        # in free text cannot cost them their slides. Only the file written changes, so a repair
        # round's prompt stays as the model wrote it.
        source = metadata.source.model_copy(update={"lecture": lecture})
        metadata = metadata.model_copy(update={"source": source})
    student, reference, suffix = ((draft.student_c, draft.reference_c, ".c") if c
                                  else (draft.student_py, draft.reference_py, ".py"))
    files = {
        exercise / f"student{suffix}": student,
        exercise / "test_student.py": draft.test_student_py,
        exercise / "hints.md": draft.hints_md,
        solution / "metadata.json": metadata.model_dump_json(
            indent=2, exclude_unset=True, exclude_none=True) + "\n",
        solution / f"reference{suffix}": reference,
        solution / f"skeleton{suffix}": student,  # what the student starts from
        solution / "oracle.py": draft.oracle_py,
        solution / "test_oracle.py": draft.test_oracle_py,
    }
    if c and draft.driver_c:
        # Given to the student as they are; the tests compile driver.c with student.c.
        files[exercise / "driver.c"] = draft.driver_c
        files[exercise / "student.h"] = draft.student_h
    question = question_markdown(student, metadata.model_dump(mode="json"), mode,
                                 "c" if c else "python")
    if question:  # the question again as Markdown, for reading in a previewer
        files[exercise / QUESTION_FILE] = question
    for name, content in draft.data_files.items():
        target = exercise / _data_path(name)
        if target in files:  # "a.csv" and "./a.csv"
            raise DraftError(f"data file {name!r} is given twice")
        files[target] = content
    dropped = {}  # every mutant that cannot be applied, so one repair round fixes them all
    for mutant in draft.mutants:
        try:
            files[solution / "mutants" / f"{mutant.name}{suffix}"] = apply_mutant(
                reference, mutant, "c" if c else "python")
        except DraftError as e:
            dropped[mutant.name] = str(e)
    if dropped and len(draft.mutants) - len(dropped) < MIN_MUTANTS:
        raise DraftError("\n".join(dropped.values()))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return exercise, solution, dropped


def _check_c_shape(draft: CExerciseDraft) -> None:
    """What a C draft's files must be for its interface, before anything is written: the
    question in student.c's opening comment; a function exercise's given driver.c and
    student.h; none for a whole program, whose main is student.c's."""
    if not PROFILES["c"].question(draft):
        raise DraftError("student_c must start with the question in a /* ... */ comment, "
                         "with no */ inside it")
    if draft.metadata.interface == "function" and not (draft.driver_c.strip()
                                                       and draft.student_h.strip()):
        raise DraftError("a function exercise needs driver_c (the main that calls the "
                         "student's functions) and student_h (their prototypes)")
    if draft.metadata.interface == "file" and (draft.driver_c.strip()
                                               or draft.student_h.strip()):
        raise DraftError("a whole program (file interface) has its main in student_c: leave "
                         'driver_c and student_h ""')


def escaped_docstring(code: str) -> bool:
    """The module docstring holds a backslash but is not a raw string. In a plain string
    Python turns "\\frac" into a form feed and "\\neq" into a line break, quietly; the
    Markdown question's LaTeX needs r\"\"\"...\"\"\" (docs/design.md, "question.md format")."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, SyntaxError):
        return False  # code that does not tokenize fails elsewhere, with a clearer message
    for token in tokens:
        if token.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING):
            continue  # comments and blank lines may come before the docstring
        if token.type != tokenize.STRING:
            return False  # the module has no docstring
        prefix = re.match(r"[A-Za-z]*", token.string).group(0).lower()
        return "\\" in token.string and "r" not in prefix
    return False


def _data_path(name: str) -> PurePosixPath:
    """A data file's path inside the exercise folder, or DraftError: no absolute path,
    no "..", no hidden part, no Python file and none of the exercise's own files."""
    # Checked after PurePosixPath has normalised it: "./hints.md" is hints.md.
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or not path.parts or name.startswith("~")
            or any(part.startswith(".") for part in path.parts) or str(path) in OWN_FILES
            or path.suffix == ".py"):
        raise DraftError(f"data file {name!r}: give a relative path inside the exercise "
                         "folder, not a Python file or one of student.py / test_student.py "
                         "/ hints.md / question.md")
    if path.suffix in (".c", ".h"):
        # C code goes in its own fields (student_c, driver_c, student_h), where the checks
        # and the question see it, never in a data file.
        raise DraftError(f"data file {name!r}: C code is not a data file; give it in "
                         "student_c, driver_c or student_h")
    return path


def _closest(code: str, old: str) -> str:
    """The reference line most like the first line of `old`, copied exactly: a model's
    `old` often differs only in quotes or indentation, and repair needs the real text."""
    lines = [line for line in code.splitlines() if line.strip()]
    first = old.strip().splitlines()[0] if old.strip() else old
    match = difflib.get_close_matches(first.strip(), [line.strip() for line in lines], n=1,
                                      cutoff=0.5)
    if not match:
        return ""
    exact = next(line for line in lines if line.strip() == match[0])
    return f"; the closest line in reference.py is, exactly: {exact!r}"


def apply_mutant(reference: str, mutant: MutantDraft, language: str = "python") -> str:
    """The reference with the mutant's replacements, and its docstring (in C its opening
    comment) saying the bug."""
    code = reference
    suffix = ".c" if language == "c" else ".py"
    # Each replacement must hit exactly one place, so the mutant is the change it states.
    for r in mutant.replacements:
        count = code.count(r.old)
        if count != 1:
            raise DraftError(f"mutant {mutant.name}: {r.old!r} occurs {count} times in "
                             f"reference{suffix}; `old` must be exact text that occurs once"
                             + _closest(code, r.old))
        code = code.replace(r.old, r.new)
    if code == reference:
        raise DraftError(f"mutant {mutant.name} changes nothing")
    if language == "c":
        # A comment, not a docstring, which would not compile; one line in place of the
        # reference's opening one-line comment, so a diff shows only the bug.
        note = f"/* Mutant: {mutant.bug.rstrip('.').replace('*/', '* /')}. */"
        lines = code.splitlines()
        if lines and lines[0].lstrip().startswith("/*") and lines[0].rstrip().endswith("*/"):
            return "\n".join([note, *lines[1:]]) + "\n"
        return f"{note}\n{code}"
    doc = f'"""Mutant: {mutant.bug.rstrip(".")}."""'
    try:
        first = ast.parse(code).body[0]
    except (SyntaxError, IndexError):
        return f"{doc}\n{code}"
    lines = code.splitlines()
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        lines[first.lineno - 1:first.end_lineno] = [doc]  # replace the reference's docstring
        return "\n".join(lines) + "\n"
    return f"{doc}\n{code}"
