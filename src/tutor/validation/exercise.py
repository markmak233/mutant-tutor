"""Locate the files of one exercise.

An exercise lives in two directories: the student-facing one (student.py, test_student.py,
hints.md, sample_input/) and the validator-only one (metadata.json, skeleton.py,
reference.py, mutants/, oracle.py, test_oracle.py). In examples/ they are exercise/ and
solution/; in a workspace batch they are exercises/<id>/ and solutions/<id>/, so both paths
are passed in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tutor.execution import DEFAULT_TIMEOUT_S

STUDENT_FILE = "student.py"
TEST_FILE = "test_student.py"
ORACLE_FILE = "oracle.py"
ORACLE_TEST_FILE = "test_oracle.py"

# Never copied into a test run: student.py is replaced by the code under test, and the
# rest is for people (hints) or left behind by running things by hand.
_NOT_COPIED = {STUDENT_FILE, "hints.md", "sample_output", "__pycache__"}


class ExerciseError(Exception):
    """A required file is missing or malformed. The message names it."""


@dataclass(frozen=True)
class ExerciseFiles:
    exercise_id: str
    exercise_dir: Path
    reference: Path
    skeleton: Path
    mutants: tuple[Path, ...]
    metadata: dict = field(default_factory=dict)  # parsed metadata.json
    oracle: Path | None = None  # plain-Python specification
    oracle_test: Path | None = None  # hypothesis test comparing student.py with the oracle

    @property
    def timeout_s(self) -> float:
        """Per pytest run: metadata `timeout_s` (Hadoop exercises need longer), else the
        runner's default; the format allows null, which means the default."""
        value = self.metadata.get("timeout_s")
        return DEFAULT_TIMEOUT_S if value is None else float(value)

    def run_files(self, candidate: Path) -> dict[str, Path]:
        """Everything the tests need (test file, sample data, ...), with `candidate`
        copied in as student.py. This is how one test file checks reference, skeleton
        and every mutant in turn."""
        files = {
            path.name: path
            for path in sorted(self.exercise_dir.iterdir())
            if path.name not in _NOT_COPIED and not path.name.startswith(".")
        }
        files[STUDENT_FILE] = candidate
        return files

    def differential_files(self) -> dict[str, Path]:
        """For rule 4: the reference as student.py with oracle.py and test_oracle.py, and
        without test_student.py, so pytest runs only the differential test."""
        if self.oracle is None or self.oracle_test is None:
            raise ExerciseError(f"{self.exercise_id} has no oracle.py / test_oracle.py")
        files = self.run_files(self.reference)
        files.pop(TEST_FILE, None)
        files[ORACLE_FILE] = self.oracle
        files[ORACLE_TEST_FILE] = self.oracle_test
        return files


def load_exercise(exercise_dir: Path, solution_dir: Path) -> ExerciseFiles:
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
        metadata = json.loads((solution_dir / "metadata.json").read_text())
        exercise_id = metadata["id"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ExerciseError(f"{solution_dir / 'metadata.json'} needs an `id`: {e}") from e

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
