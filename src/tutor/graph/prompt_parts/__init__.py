"""Each language's parts of the generation prompts: graph/prompts.py holds one template per
prompt (generate and repair, oracle, plan) and fills it with the parts of the course's language
(owner, 2026-11-07: one version of each prompt, not one per language). A new language adds a
module here with its LanguagePrompts, and a Profile in graph/languages.py; prompts.py does not
change.

Python's parts are its prompts as calibrated and stay byte for byte as they are
(tests/test_prompt_pins.py); a new language's are not calibrated until batches measure them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguagePrompts:
    """What one language puts into the prompt templates."""

    # The generate / repair system prompt's {slots} (prompts.SPEC): the validator's rules,
    # each file of the reply with its rules, test data, "how", the code rules.
    spec: dict[str, str]
    # The two ways oracle_py is written (SPEC's {oracle}): whole, by the generating model,
    # or as an interface whose bodies another model writes from the question alone, so
    # rule 4 compares two independent readings of the question (docs/design.md,
    # "Independent checks").
    oracle_whole: str
    oracle_interface: str
    # The oracle model's own prompt (prompts.ORACLE_TEMPLATE): what the oracle is compared
    # with, and the sentence naming its marked material ("" for none).
    oracle_compared: str
    oracle_boundary: str
    # The plan model's prompt (prompts.PLAN_TEMPLATE): the kinds of exercise a plan may
    # choose, and the sentence naming its marked material ("" for none).
    plan_kinds: str
    plan_boundary: str
    # Added to a repair prompt when a mutant survived: how this language's tests catch one.
    rule_3_hint: str
    # Where the question is written, as the oracle's prompt names it.
    question_place: str
    # Whether the user messages put text from outside (the lectures, an earlier answer, the
    # validator's report, the exercise sent to the oracle, the files sent to the plan model)
    # between tags (prompts.material). Every new language does (AGENTS.md, "Prompts");
    # Python's calibrated prompts do not.
    marks_material: bool
    # The example exercise shown for each (framework, interface) of this language: the
    # closest hand-made, validated one in examples/.
    examples: dict[tuple[str, str], str]
    # The test helpers' API as the system prompt lists it, read from the module itself so
    # the prompt never drifts from the code.
    api: Callable[[], str]
