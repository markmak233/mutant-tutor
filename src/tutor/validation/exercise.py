"""Locate the files of one exercise.

An exercise lives in two directories: the student-facing one (student.py, test_student.py,
hints.md, sample_input/) and the validator-only one (metadata.json, skeleton.py,
reference.py, mutants/). In examples/ they are exercise/ and solution/; in a workspace batch
they are exercises/<id>/ and solutions/<id>/, so both paths are passed in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STUDENT_FILE = "student.py"
TEST_FILE = "test_student.py"

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
        exercise_id = json.loads((solution_dir / "metadata.json").read_text())["id"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ExerciseError(f"{solution_dir / 'metadata.json'} needs an `id`: {e}") from e

    mutants_dir = solution_dir / "mutants"
    mutants = tuple(sorted(mutants_dir.glob("*.py"))) if mutants_dir.is_dir() else ()
    return ExerciseFiles(
        exercise_id=exercise_id,
        exercise_dir=exercise_dir,
        reference=solution_dir / "reference.py",
        skeleton=solution_dir / "skeleton.py",
        mutants=mutants,
    )
