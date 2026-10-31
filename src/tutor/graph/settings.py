"""Generation settings in one place (docs/design.md, "Generation settings").

Every value of the pipeline worth tuning is a field below, with its default, a description
and its range. Three sources fill them, later ones winning:
1. the defaults here: the values the pipeline was measured with;
2. the workspace's `[generation]` table in workspace.toml, which the owner edits by hand
   now and a GUI later (the pipeline only reads workspace.toml, never writes it);
3. one run's command line: `--set name=value`, or the older named flags (`--thinking on`).

The settings a batch resolved are saved with it (run.json), so `resume` keeps them, and
logged with every item (generation.jsonl), so a model comparison knows what each run used.
`settings_schema()` is the JSON Schema a GUI can build its form from: each field's
description, type and bounds.

Model choices (which profile per role) and per-model values (`reasoning_effort`,
`timeout_s`) are not here: they belong to `[llm]` (see tutor.llm).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tutor.config import WORKSPACE_FILE
from tutor.schema import problems

TABLE = "generation"  # the workspace.toml table that holds the owner's values


class GenerationSettings(BaseModel):
    # An unknown name (a typo such as `max_round`) is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    # --- repair -------------------------------------------------------------------------
    max_rounds: int = Field(3, ge=0, le=5, description=(
        "Repair rounds after the first draft; an item still invalid after them is "
        "discarded."))
    stronger_repair_from: int = Field(2, ge=1, le=5, description=(
        "The repair round from which the `repair` role's model takes over; the generating "
        "model repairs before it. 1: the larger model does every repair."))
    # At temperature 0 an unchanged prompt got the identical draft back, round after round
    # (2026-10-29), so each repair round asks a little warmer.
    repair_temperature_step: float = Field(0.2, ge=0, le=0.4, description=(
        "Repair round N asks at temperature N x step (at most 0.8); the first draft asks "
        "at 0."))

    # --- thinking -----------------------------------------------------------------------
    # Off by default: with thinking on, GLM-5.3 spent the whole 32k output on reasoning
    # and was cut off (2026-10-28), and DeepSeek Flash was cut off in 5 of 10 calls at
    # twice the cost (2026-10-29).
    thinking: bool = Field(False, description=(
        "The generating model reasons at length before writing the exercise."))
    # Deciding which side of a failure is wrong is what the cheap model cannot do, and
    # DeepSeek-V4-Pro without thinking did not do it either (2026-10-29).
    repair_thinking: bool = Field(True, description=(
        "The `repair` role's model reasons first."))
    # One short reply per batch, so thinking costs little; without it DeepSeek-V4-Pro wrote
    # vague checks and ignored a preference (2026-10-29).
    plan_thinking: bool = Field(True, description="The planning model reasons first.")
    # The oracle fills in a few function bodies from the question, which needs no long
    # reasoning. It followed `thinking` until 2026-10-30; with week 7's generation thinking
    # on, a GLM-5.3 oracle would think too, and GLM spent its whole output on reasoning when
    # it did (2026-10-28); without, it wrote all 19 week-7 oracles in 9 s each.
    oracle_thinking: bool = Field(False, description="The oracle's model reasons first.")

    # --- the question -----------------------------------------------------------------
    # A learn question's Concept is the knowledge point's condensed lecture, so the student
    # need not go back to the slides (owner, 2026-10-30). It was 3 lines after three week 5
    # questions were rated "explanations too long", but the trouble there was reading a
    # docstring without layout, not the length: week 1-2 questions were longer and rated
    # clear, and question.md now renders the sections.
    concept_lines: int = Field(50, ge=3, le=120, description=(
        "The most lines of a learn-mode question's Concept section."))

    # --- size limits --------------------------------------------------------------------
    max_tokens: int = Field(32_000, ge=2_000, le=128_000, description=(
        "Output tokens per reply when the model thinks (its reasoning counts as output)."))
    # A draft without thinking is 4-7k tokens; a reply that ran away filled all 32k
    # (2026-10-29), so a lower cap halves what such a reply costs.
    max_tokens_no_thinking: int = Field(16_000, ge=2_000, le=128_000, description=(
        "Output tokens per reply when the model does not think."))
    item_token_budget: int = Field(400_000, ge=20_000, description=(
        "Input and output tokens per item over all rounds; repairs stop once it is spent."))
    context_chars: int = Field(120_000, ge=10_000, description=(
        "Characters of course pages a prompt may hold; a longer lecture is cut to the "
        "item's slides."))

    # --- the batch ----------------------------------------------------------------------
    max_count: int = Field(6, ge=1, le=20, description=(
        "At most this many exercises when the plan chooses how many."))
    parallel: int | None = Field(None, ge=1, le=8, description=(
        "Exercises generated at once; empty: one per four CPU cores, 1 to 4 (each "
        "validation runs Spark on every core)."))
    mode: Literal["learn", "review"] = Field("learn", description=(
        "learn: questions may explain the cause of a bug; review: only its symptom."))


DEFAULTS = GenerationSettings()

# The fields that are also Deps fields (graph/nodes.py): passed to it, and logged with each
# item. max_count and parallel act on the whole batch instead.
DEPS_FIELDS = ("max_rounds", "stronger_repair_from", "repair_temperature_step", "thinking",
               "repair_thinking", "plan_thinking", "oracle_thinking", "concept_lines", "max_tokens",
               "max_tokens_no_thinking", "item_token_budget", "context_chars", "mode")


def workspace_table(workspace: Path) -> dict:
    """The workspace's [generation] table ({} when there is none)."""
    path = workspace / WORKSPACE_FILE
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return dict(tomllib.load(f).get(TABLE, {}))


def resolve(workspace: Path, given: dict, base: GenerationSettings | None = None
            ) -> GenerationSettings:
    """`base` (default: the defaults with the workspace's [generation] table on top), with
    `given` on top: the values set on this run's command line. A wrong name or value is a
    ValueError naming it."""
    data = {**(base.model_dump() if base else workspace_table(workspace)), **given}
    if found := problems(GenerationSettings, data):
        raise ValueError(f"generation settings ([{TABLE}] in {WORKSPACE_FILE} or --set): "
                         + "; ".join(found))
    return GenerationSettings.model_validate(data)


def parse_set(pairs: list[str] | None) -> dict:
    """`--set name=value` pairs as a dict of strings; GenerationSettings turns "3", "on"
    or "0.1" into the field's type when it validates them."""
    found = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--set {pair!r}: write name=value, e.g. --set max_rounds=2")
        found[name.strip()] = value.strip()
    return found


def settings_schema() -> dict:
    """The fields as JSON Schema (descriptions, types, bounds), for a settings form."""
    return GenerationSettings.model_json_schema()
