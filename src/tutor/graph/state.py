"""What flows through the generation graph (docs/design.md, "Generation pipeline").

`ExerciseDraft` is the generate / repair nodes' structured output: its JSON Schema is sent
to the model, and the reply is parsed back into it. Files the pipeline can derive are not
asked for: skeleton.py is student.py, and a mutant is described as replacements in the
reference, so that every mutant differs from it by exactly the stated change.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from tutor.schema import Metadata

# Fewer planted mistakes make rule 3 a weak check; a draft keeps at least this many after
# leaving out mutants that cannot be applied (nodes.write_draft).
MIN_MUTANTS = 3


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanItem(_Strict):
    """One exercise to generate: written by the plan node (or by hand for one item)."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
    knowledge_point: str
    lecture: str  # the lecture's file name without .pdf, as in lectures/
    slides: str  # slide footers, e.g. "4.15-4.24"
    type: Literal["implementation", "completion", "debugging"]
    interface: Literal["function", "file"]
    framework: Literal["python", "mrjob", "pyspark-rdd", "pyspark-df"]
    notes: str = ""  # what the exercise must check, preferences
    reason: str = ""  # why the plan chose it: the gap it fills, the preference it follows


class BatchPlan(_Strict):
    items: Annotated[list[PlanItem], Field(min_length=1)]


class Replacement(_Strict):
    old: Annotated[str, Field(min_length=1)]  # must occur exactly once in reference.py
    new: str


class MutantDraft(_Strict):
    name: Annotated[str, Field(pattern=r"^m\d{2}_[a-z0-9_]+$")]  # file name without .py
    bug: Annotated[str, Field(min_length=1)]  # "<the bug>, <what goes wrong>"
    replacements: Annotated[list[Replacement], Field(min_length=1)]


class ExerciseDraft(_Strict):
    # First, so a model writes it before the files: a scratchpad that also works without
    # thinking (a repair without one tended to copy its previous answer). Never written
    # into the exercise; kept in generation.jsonl.
    diagnosis: str = Field(default="", description=(
        "Repairs only, before any file: which test or rule failed, the right result worked "
        "out by hand from the question, and which file is wrong (test, reference, question "
        "or mutant) and why. Empty for a first draft."))
    metadata: Metadata
    student_py: str  # the question in the module docstring, then the skeleton
    test_student_py: str
    hints_md: str
    data_files: dict[str, str] = {}  # path inside the exercise folder -> content
    reference_py: str
    # With an `oracle` role: only the interface (signatures, a docstring saying what goes in
    # and out, `raise NotImplementedError`); the oracle model writes the bodies. Without
    # one: the whole oracle, as hand drafting does.
    oracle_py: str
    test_oracle_py: str
    mutants: Annotated[list[MutantDraft], Field(min_length=MIN_MUTANTS, max_length=6)]


class OracleDraft(_Strict):
    """The oracle model's answer: oracle.py, written from the question alone."""

    oracle_py: str = Field(description=(
        "The whole oracle.py: the given interface with every function body written."))


class Attempt(TypedDict):
    round: int  # 0 = generate, 1-3 = repair
    failure: str | None  # the first failing rule, or a format error; None when VALID
    format_error: bool  # the reply did not parse or could not be written out
    dropped: list[str]  # mutants left out because they could not be applied
    unchanged: bool  # the repair reply repeated the previous draft word for word
    model: str  # the profile that wrote this round's draft
    diagnosis: str  # what that draft's author said was wrong (repairs)


class ItemState(TypedDict, total=False):
    """State of one plan item going through retrieve -> generate -> validate -> repair."""

    item: PlanItem
    context: str  # the course pages the model sees
    draft: ExerciseDraft | None  # None after a format error
    draft_json: str  # the last reply, shown to repair
    unchanged: bool  # that reply repeated the one before it (repair says so next time)
    model: str  # the profile that wrote the current draft (generate or repair role)
    round_dir: str  # where the current round's files were written
    failure: str | None  # what repair is asked to fix; None when VALID
    format_error: bool
    round: int
    attempts: list[Attempt]
    tokens: int  # input + output tokens spent on this item
    usage: dict[str, int]  # the same, split: input, cached (part of input), output
    outcome: Literal["saved", "discarded"]
    # The independent oracle (nodes.write_oracle): what it was written from (a hash of the
    # question, the interface and the sample data), its code, and what the node did.
    oracle_key: str
    oracle_py: str
    oracle_status: Literal["written", "kept", "failed", "skipped"]


class BatchState(TypedDict, total=False):
    """State of a whole batch: plan -> approve -> items in parallel -> collect. It is saved
    by the checkpointer, so it holds plain data (dicts), not models."""

    lectures: list[str]  # lecture file names without .pdf
    preferences: str
    count: int | None  # None: the plan chooses, up to max_count
    max_count: int
    plan: list[dict]  # PlanItem fields
    plan_problems: list[str]  # what was still wrong with the plan after the retries
    feedback: str  # the owner's answer when sending the plan back
    approved: bool
    # One summary per item; parallel branches each add theirs.
    results: Annotated[list[dict], operator.add]
