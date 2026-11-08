"""What the generation graph does differently by a course's language (docs/design.md, "C").

One profile per language: the plan and draft models the plan and generate models answer in,
their JSON Schemas, and where a draft's question is. The prompts are one template filled per
language (graph/prompts.py, from graph/prompt_parts/). Python's profile is the pipeline as it was,
calibrated, and stays byte for byte as it was (tests/test_prompt_pins.py); a new language
adds a profile and its prompt parts, not a new version of the prompts.

A course's language is what its workspace.toml declares ([languages.c]; tutor.languages);
every exercise of a C course's batch is C.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from tutor import languages
from tutor.config import WORKSPACE_FILE
from tutor.graph.state import (
    BatchPlan,
    CBatchPlan,
    CExerciseDraft,
    CPlanItem,
    ExerciseDraft,
    PlanItem,
)
from tutor.question import question_source


@dataclass(frozen=True)
class Profile:
    """What the graph needs to know of one language."""

    name: str  # "python" or "c", as prompts.LANGUAGES names it
    plan_item: type[PlanItem]
    batch_plan: type[BaseModel]
    draft: type[BaseModel]
    plan_schema: dict
    draft_schema: dict
    student_field: str  # the draft's field that holds student.<suffix>, with the question
    suffix: str  # of the code files: student<suffix>, reference<suffix>, mutants

    def question(self, draft: BaseModel) -> str:
        """The question as the draft writes it: student.py's docstring, student.c's opening
        comment; "" when there is none."""
        return question_source(getattr(draft, self.student_field), self.name)


PYTHON = Profile("python", PlanItem, BatchPlan, ExerciseDraft,
                 BatchPlan.model_json_schema(), ExerciseDraft.model_json_schema(),
                 "student_py", ".py")
C = Profile("c", CPlanItem, CBatchPlan, CExerciseDraft,
            CBatchPlan.model_json_schema(), CExerciseDraft.model_json_schema(),
            "student_c", ".c")
PROFILES = {profile.name: profile for profile in (PYTHON, C)}


def course_language(workspace: Path) -> str:
    """"c" when the course's workspace.toml declares C, else "python" (also when it cannot
    be read: the commands that need it read it again and say why)."""
    try:
        with (workspace / WORKSPACE_FILE).open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return "python"
    return "c" if languages.is_c(data) else "python"


def profile_of(workspace: Path) -> Profile:
    return PROFILES[course_language(workspace)]
