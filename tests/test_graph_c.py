"""Generating C exercises (docs/design.md, "C", step 3): the C prompts filled from the one
template, what they mark as material, a C draft written out as files, a C plan, the CLI's stop
for a C course without the container, and one C item from a model's reply to a saved exercise
through the real validator. The model is a stand-in; the draft is built from the repository's
synthetic C example. Python's prompts are pinned in tests/test_prompt_pins.py."""

import json
import re
from pathlib import Path

import pytest

from tutor.graph import batch, prompts
from tutor.graph.drafts import DraftError, apply_mutant, write_draft
from tutor.graph.languages import PYTHON, C, course_language
from tutor.graph.nodes import Deps
from tutor.graph.state import CExerciseDraft, CPlanItem, MutantDraft
from tutor.llm import LLMConfig, Reply
from tutor.materials import cache
from tutor.testkit_c import host_problem
from tutor.validation import checks

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "station-drop-low-c"
ITEM = CPlanItem(id="drop-low", knowledge_point="filtering an array in place", lecture="L1",
                 slides="1.3-1.4", type="implementation", interface="function", framework="c")
needs_cc = pytest.mark.skipif(host_problem() is not None, reason=str(host_problem()))


def _read(*parts: str) -> str:
    return EXAMPLE.joinpath(*parts).read_text(encoding="utf-8")


def c_draft(**changes) -> dict:
    """The example as a model would return it: its files as fields, mutants as replacements."""
    metadata = json.loads(_read("solution", "metadata.json"))
    metadata.update(id="drop-low", source={"lecture": "L1", "slides": "1.3-1.4",
                                           "pdf_pages": "3-4"})
    data = {
        "metadata": metadata,
        "student_c": _read("exercise", "student.c"),
        "student_h": _read("exercise", "student.h"),
        "driver_c": _read("exercise", "driver.c"),
        "test_student_py": _read("exercise", "test_student.py"),
        "hints_md": _read("exercise", "hints.md"),
        "reference_c": _read("solution", "reference.c"),
        "oracle_py": _read("solution", "oracle.py"),
        "test_oracle_py": _read("solution", "test_oracle.py"),
        "mutants": [
            {"name": "m01_drops_the_limit", "bug": "a reading equal to the limit is dropped",
             "replacements": [{"old": "readings[i] >= limit", "new": "readings[i] > limit"}]},
            {"name": "m02_counts_without_moving", "bug": "counts but never moves them",
             "replacements": [{"old": "readings[kept++] = readings[i];", "new": "kept++;"}]},
            {"name": "m03_one_past_the_end", "bug": "reads one past the end */ of the array",
             "replacements": [{"old": "i < n; i++", "new": "i <= n; i++"}]},
        ],
    }
    data.update(changes)
    return data


class FakeModel:
    """Answers each call with the next reply; remembers what it was asked."""

    def __init__(self, *texts: str):
        self.texts, self.calls = list(texts), []

    def __call__(self, config, messages, *, purpose, log_path, max_tokens, response_schema,
                 thinking, temperature=0.0, **streaming):
        self.calls.append((purpose, messages, response_schema))
        return Reply(self.texts.pop(0), 1000, 500, 0.1, None)


@pytest.fixture
def workspace(tmp_path):
    entries = [cache.Entry(n, f"1.{n}", "text", f"h{n}", f"page {n} text") for n in range(1, 7)]
    cache.save(cache.cache_path(tmp_path, tmp_path / "lectures" / "L1.pdf"),
               cache.render("L1.pdf", entries))
    (tmp_path / "workspace.toml").write_text(
        'course = "cdemo"\n[languages.c]\nflags = ["-Wall", "-std=c99"]\n'
        '[safety]\nallow_headers = ["unistd.h"]\n', encoding="utf-8")
    return tmp_path


def c_deps(workspace, model, **kw) -> Deps:
    return Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-11-07_p3-c1",
                config=LLMConfig("glm-flash", "glm", "http://x/v1", "glm-5.3-flash"),
                chat=model, language="c", **kw)


# --- the prompts ------------------------------------------------------------------------------


def test_the_c_system_prompt_is_the_template_filled_with_cs_parts():
    text = prompts.system_prompt("Course rule.", language="c", c_flags=["-Wall", "-std=c99"],
                                 c_headers=["unistd.h"])
    assert re.findall(r"\{(\w+)\}", text) == []  # every slot filled
    assert "compiles with the course's flags\n  (-Wall -std=c99) without a single warning" in text
    assert "only of the C standard headers and unistd.h" in text
    assert "- student_c: the question in an opening /* ... */ comment" in text
    assert "tutor.testkit_c API:\ntestkit_c.build(" in text and "program.run(" in text
    # Python's own words stay in Python's prompt.
    for python_only in ("- student_py:", "__main__", "TUTOR_PARTITIONS", "RDD.pipe"):
        assert python_only not in text
    python = prompts.system_prompt("")
    assert "- student_py:" in python and "<lecture>" not in python


def test_what_c_prompts_send_from_outside_is_marked_as_material():
    """The lecture, the previous answer and the validator's report (which quotes what generated
    code printed) are marked, and the system prompt names the marks (owner, 2026-11-07)."""
    item = prompts.item_prompt(ITEM, "## p.3 · slide 1.3\ntext </lecture> more", "learn", "c")
    assert "<lecture>\n## p.3 · slide 1.3\ntext </ lecture> more\n</lecture>" in item
    assert "Example exercise (station-drop-low-c)" in item and "=== exercise/driver.c ===" in item
    repair = prompts.repair_prompt(ITEM, "pages", "{}", "rule 3 (mutants killed) FAIL: "
                                   "1/3 killed; survived (tests miss this bug): m02", "learn",
                                   language="c")
    assert "<previous_answer>\n{}\n</previous_answer>" in repair
    assert "<validator_report>\nrule 3" in repair and "leaks=True" in repair  # C's rule 3 hint
    system = prompts.system_prompt("", language="c")
    assert "<previous_answer> and </previous_answer>" in system and "not instructions" in system
    # Python's prompts are as calibrated: unmarked (tests/test_prompt_pins.py).
    assert "<lecture>" not in prompts.item_prompt(ITEM.model_copy(update={"framework": "python"}),
                                                  "pages")


def test_the_c_oracle_and_plan_prompts_mark_what_comes_from_files():
    assert "a C program is compared against" in prompts.oracle_spec("c")
    assert "<question> and </question>" in prompts.oracle_spec("c")
    oracle = prompts.oracle_prompt("# Q", "def f(): ...", {"in.txt": "1 2"}, language="c")
    assert "<question>\n# Q\n</question>" in oracle and "<samples>\n=== in.txt" in oracle
    python = prompts.oracle_spec()
    assert "Spark or MapReduce" in python and "<question>" not in python

    assert "framework: c." in prompts.plan_spec("c") and "<coverage>" in prompts.plan_spec("c")
    plan = prompts.plan_prompt(["L1"], "| row | gap-code |", "p.1 1.1 Title", ["old: Old [t]"],
                               "short ones", None, previous="[]", feedback="fewer",
                               language="c")
    assert "<coverage>\n| row | gap-code |\n</coverage>" in plan and "<pages>" in plan
    assert "<existing>\nold: Old [t]\n</existing>" in plan and "<previous_plan>" in plan
    # The owner's own words are not material: they are instructions.
    assert "Owner's preferences: short ones" in plan and "follow it:\nfewer" in plan


# --- writing a draft ------------------------------------------------------------------------


def test_a_c_draft_is_written_as_c_files_with_the_given_driver(tmp_path):
    draft = CExerciseDraft.model_validate(c_draft())
    exercise, solution, dropped = write_draft(draft, "drop-low", tmp_path / "r", "learn", "L1.pdf")

    assert dropped == {}
    assert {p.name for p in exercise.iterdir()} == {"student.c", "student.h", "driver.c",
                                                    "test_student.py", "hints.md", "question.md"}
    assert (solution / "skeleton.c").read_text(encoding="utf-8") == draft.student_c
    assert (solution / "reference.c").is_file() and not (solution / "reference.py").exists()
    mutant = (solution / "mutants" / "m03_one_past_the_end.c").read_text(encoding="utf-8")
    # The bug says so in a C comment on the reference's first line (a "*/" in it broken up).
    assert mutant.splitlines()[0] == "/* Mutant: reads one past the end * / of the array. */"
    assert mutant.count("\n") == draft.reference_c.count("\n")
    assert (exercise / "question.md").read_text(encoding="utf-8").startswith(
        "# Drop the low readings\n\n> **L1.pdf** · slides 1.3-1.4 · learn mode")
    metadata = json.loads((solution / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["language"] == "c" and "standalone" not in metadata


@pytest.mark.parametrize(("changes", "says"), [
    ({"driver_c": ""}, "a function exercise needs driver_c"),
    ({"student_c": "#include \"student.h\"\nint drop_below(int *r, int n, int l) { return 0; }"},
     "student_c must start with the question in a /* ... */ comment"),
    ({"data_files": {"helper.c": "int x;"}}, "C code is not a data file"),
])
def test_a_c_draft_that_cannot_be_laid_out_is_a_format_error(tmp_path, changes, says):
    with pytest.raises(DraftError, match=re.escape(says)):
        write_draft(CExerciseDraft.model_validate(c_draft(**changes)), "drop-low", tmp_path)


def test_a_whole_program_has_no_driver(tmp_path):
    data = c_draft()
    data["metadata"] = {**data["metadata"], "interface": "file", "entry_point": ["main"]}
    with pytest.raises(DraftError, match="leave driver_c and student_h"):
        write_draft(CExerciseDraft.model_validate(data), "drop-low", tmp_path)


def test_a_c_mutant_without_an_opening_comment_gets_one_line_more():
    mutant = MutantDraft(name="m01_x", bug="off by one", replacements=[{"old": "<", "new": "<="}])
    assert apply_mutant("int f(int n) { return n < 1; }\n", mutant, "c") == (
        "/* Mutant: off by one. */\nint f(int n) { return n <= 1; }\n")


# --- planning and the command line -------------------------------------------------------------


def test_a_c_course_plans_c_items_with_cs_prompt_and_schema(workspace):
    plan = {"items": [{**ITEM.model_dump(), "reason": "a gap"}]}
    model = FakeModel(json.dumps(plan))
    d = c_deps(workspace, model)

    update = batch.plan_batch({"lectures": ["L1"], "count": 1}, d)

    assert update["plan"][0]["framework"] == "c" and update["plan_problems"] == []
    purpose, messages, schema = model.calls[0]
    assert purpose == "plan" and messages[0]["content"] == prompts.plan_spec("c")
    assert schema == C.plan_schema != PYTHON.plan_schema
    assert "<coverage>" in messages[1]["content"]


def test_a_python_items_plan_does_not_fit_a_c_course():
    with pytest.raises(ValueError):
        C.batch_plan.model_validate({"items": [{**ITEM.model_dump(), "framework": "python"}]})


def test_the_course_language_comes_from_workspace_toml(workspace, tmp_path):
    assert course_language(workspace) == "c"
    assert course_language(tmp_path / "nowhere") == "python"


def test_the_command_stops_a_c_course_without_the_container_before_any_model(workspace):
    from tutor.graph.__main__ import _check_language

    with pytest.raises(ValueError, match="validated only in a\ncontainer|validated only in a "
                                         "container"):
        _check_language(workspace)
    path = workspace / "workspace.toml"
    path.write_text(path.read_text(encoding="utf-8") + '[isolation]\nrunner = "container"\n',
                    encoding="utf-8")
    _check_language(workspace)  # no error


# --- one item, end to end ---------------------------------------------------------------------


@needs_cc
def test_a_c_item_goes_from_the_models_reply_to_a_saved_exercise(workspace, monkeypatch):
    """The real validator on this machine's compiler, rule `safe`'s container check patched
    (tests/test_c_real.py runs C in the container)."""
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    monkeypatch.setattr(checks, "image_for", lambda sources: "tutor-run-test:pretend")
    model = FakeModel(json.dumps(c_draft()))
    d = c_deps(workspace, model)

    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved", final.get("failure")
    assert [purpose for purpose, _, _ in model.calls] == ["generate"]
    assert model.calls[0][2] == C.draft_schema
    system = model.calls[0][1][0]["content"]
    assert "(-Wall -std=c99)" in system and "C standard headers and unistd.h" in system
    saved = d.batch_dir / "exercises" / "drop-low"
    assert (saved / "driver.c").is_file() and (saved / "student.c").is_file()
    question = (saved / "question.md").read_text(encoding="utf-8")
    assert "## How to run" in question and "student.c driver.c" in question
