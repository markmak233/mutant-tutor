"""Locate the files of one exercise.

An exercise lives in two directories: the student-facing one (student.py, test_student.py,
hints.md, sample_input/) and the validator-only one (metadata.json, skeleton.py,
reference.py, mutants/, oracle.py, test_oracle.py). In examples/ they are exercise/ and
solution/; in a workspace batch they are exercises/<id>/ and solutions/<id>/, so both paths
are passed in.

Every check works the same way: copy the exercise's test files into a temp dir with one
candidate as student.py (the reference, the skeleton, a mutant, or for the held-out check
the owner's own code) and run pytest there. `ExerciseFiles` answers "which files does
that run need?".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tutor.execution import DEFAULT_TIMEOUT_S

# The file names the format fixes. Tests always import the code under test as `student`.
STUDENT_FILE = "student.py"
TEST_FILE = "test_student.py"
ORACLE_FILE = "oracle.py"
ORACLE_TEST_FILE = "test_oracle.py"

# Never copied into a test run: student.py is replaced by the code under test, and the
# rest is for people (hints) or left behind by running things by hand.
_NOT_COPIED = {STUDENT_FILE, "hints.md", "question.md", "sample_output", "__pycache__"}


# How heavy a framework's test runs are, for the executor's caps (execution.py). Light:
# plain Python in one process. Heavy: a JVM per run (Spark). An MRJob exercise on the
# Hadoop runner is heavy too. A framework in neither set counts as heavy, so a new one is
# slow rather than wrong until it is measured; tests check every framework the format
# allows is listed here.
LIGHT_FRAMEWORKS = frozenset({"python", "mrjob"})
HEAVY_FRAMEWORKS = frozenset({"pyspark-rdd", "pyspark-df"})


class ExerciseError(Exception):
    """A required file is missing or malformed. The message names it."""


@dataclass(frozen=True)
class ExerciseFiles:
    """Paths to one exercise's files, found by `load_exercise`. Nothing is read or run
    here except metadata.json, which `load_exercise` has already parsed."""

    exercise_id: str
    exercise_dir: Path  # the student-facing folder; its files are copied into every run
    reference: Path
    skeleton: Path
    mutants: tuple[Path, ...]  # sorted by name, so reports list them in a stable order
    metadata: dict = field(default_factory=dict)  # parsed metadata.json
    oracle: Path | None = None  # plain-Python specification
    oracle_test: Path | None = None  # hypothesis test comparing student.py with the oracle

    @property
    def timeout_s(self) -> float:
        """Per pytest run: metadata `timeout_s` (Hadoop exercises need longer), else the
        runner's default; the format allows null, which means the default."""
        value = self.metadata.get("timeout_s")
        return DEFAULT_TIMEOUT_S if value is None else float(value)

    @property
    def heavy(self) -> bool:
        """Its test runs start a JVM (Spark, or the Hadoop runner), or its framework is
        unknown: they run under the executor's lower cap."""
        return (self.metadata.get("framework") not in LIGHT_FRAMEWORKS
                or self.metadata.get("runner") == "hadoop")

    def run_files(self, candidate: Path) -> dict[str, Path]:
        """Everything the tests need (test file, sample data, ...), with `candidate`
        copied in as student.py. This is how one test file checks reference, skeleton
        and every mutant in turn."""
        # Name inside the temp dir -> source path. Hidden files (.pytest_cache, .DS_Store)
        # and the files in _NOT_COPIED stay behind.
        files = {
            path.name: path
            for path in sorted(self.exercise_dir.iterdir())
            if path.name not in _NOT_COPIED and not path.name.startswith(".")
        }
        files[STUDENT_FILE] = candidate
        return files

    def differential_files(self, candidate: Path | None = None) -> dict[str, Path]:
        """For rule 4: `candidate` (the reference unless given; the held-out check passes
        the owner's solution) as student.py with oracle.py and test_oracle.py, and without
        test_student.py, so pytest runs only the differential test."""
        if self.oracle is None or self.oracle_test is None:
            raise ExerciseError(f"{self.exercise_id} has no oracle.py / test_oracle.py")
        files = self.run_files(candidate or self.reference)
        files.pop(TEST_FILE, None)
        files[ORACLE_FILE] = self.oracle
        files[ORACLE_TEST_FILE] = self.oracle_test
        return files


def load_exercise(exercise_dir: Path, solution_dir: Path) -> ExerciseFiles:
    """Find an exercise's files; raise ExerciseError naming the first one missing, or a
    metadata.json without an `id`. Whether metadata fits the format is rule 0's job, so a
    bad field still loads and is reported there."""
    required = [
        exercise_dir / TEST_FILE,
        solution_dir / "metadata.json",
        solution_dir / "reference.py",
        solution_dir / "skeleton.py",
    ]
    for path in required:
        if not path.is_file():
            raise ExerciseError(f"missing {path}")

    try:
        metadata = json.loads((solution_dir / "metadata.json").read_text(encoding="utf-8"))
        exercise_id = metadata["id"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ExerciseError(f"{solution_dir / 'metadata.json'} needs an `id`: {e}") from e

    # No mutants/ folder loads as no mutants; rule 3 then fails with a clear reason.
    mutants_dir = solution_dir / "mutants"
    mutants = tuple(sorted(mutants_dir.glob("*.py"))) if mutants_dir.is_dir() else ()
    # Optional here; rule 4 reports their absence instead of loading failing.
    oracle = solution_dir / ORACLE_FILE
    oracle_test = solution_dir / ORACLE_TEST_FILE
    return ExerciseFiles(
        exercise_id=exercise_id,
        exercise_dir=exercise_dir,
        reference=solution_dir / "reference.py",
        skeleton=solution_dir / "skeleton.py",
        mutants=mutants,
        metadata=metadata,
        oracle=oracle if oracle.is_file() else None,
        oracle_test=oracle_test if oracle_test.is_file() else None,
    )
