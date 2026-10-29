"""The generation graph with a fake model and a fake validator: no network, no Spark."""

import json
import re

import pytest

from tutor.graph import nodes, prompts
from tutor.graph.nodes import Deps, DraftError, apply_mutant, write_draft, write_manifest
from tutor.graph.state import ExerciseDraft, MutantDraft, PlanItem
from tutor.llm import LLMConfig, Reply
from tutor.materials import cache
from tutor.schema import Manifest
from tutor.validation.checks import RuleResult, Status, ValidationReport

ITEM = PlanItem(id="sum-list", knowledge_point="summing a list", lecture="L1",
                slides="1.3-1.4", type="implementation", interface="function",
                framework="python")
REFERENCE = ('"""Reference: total of a list."""\n\n\n'
             "def total(xs):\n    # Step 1: add up.\n    return sum(xs)\n")


def draft(**changes) -> dict:
    data = {
        "metadata": {"id": "sum-list", "title": "Sum a list", "topic": "python/sum",
                     "type": "implementation", "interface": "function", "difficulty": "easy",
                     "framework": "python", "entry_point": ["total"],
                     "source": {"lecture": "L1", "slides": "1.3-1.4", "pdf_pages": "3-4"}},
        "student_py": '"""Sum a list."""\n\n\ndef total(xs):\n    raise NotImplementedError\n',
        "test_student_py": ("from student import total\n\n\n"
                            "def test_total():\n    assert total([1, 2]) == 3\n"),
        "hints_md": "## Useful functions\n",
        "data_files": {},
        "reference_py": REFERENCE,
        "oracle_py": "def total(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n",
        "test_oracle_py": "def test_same():\n    pass\n",
        "mutants": [
            {"name": f"m0{i}_wrong", "bug": f"bug {i}, wrong total",
             "replacements": [{"old": "sum(xs)", "new": f"sum(xs) + {i}"}]}
            for i in (1, 2, 3)
        ],
    }
    data.update(changes)
    return data


def report(*failing: str) -> ValidationReport:
    rules = ["0", "safe", "1", "2", "3", "4", "5", "api"]
    return ValidationReport("sum-list", [
        RuleResult(r, f"rule {r}", Status.FAIL if r in failing else Status.OK,
                   "3 of 3 mutants survived" if r in failing else "", 0.0)
        for r in rules])


class FakeModel:
    """Answers each call with the next reply; remembers what it was asked."""

    def __init__(self, *texts: str, finish_reason: str | None = None):
        self.texts, self.calls, self.finish_reason = list(texts), [], finish_reason

    def __call__(self, config, messages, *, purpose, log_path, max_tokens, response_schema,
                 thinking, temperature=0.0, **streaming):
        self.calls.append((purpose, messages, response_schema))
        self.configs = [*getattr(self, "configs", []), config.name]
        self.thinkings = [*getattr(self, "thinkings", []), thinking]
        self.max_tokens = [*getattr(self, "max_tokens", []), max_tokens]
        self.thinking = thinking
        self.temperatures = [*getattr(self, "temperatures", []), temperature]
        return Reply(self.texts.pop(0), 1000, 500, 0.1, self.finish_reason)


@pytest.fixture
def workspace(tmp_path):
    entries = [cache.Entry(n, f"1.{n}", "text", f"h{n}", f"page {n} text") for n in range(1, 7)]
    path = cache.cache_path(tmp_path, tmp_path / "lectures" / "L1.pdf")
    cache.save(path, cache.render("L1.pdf", entries))
    (tmp_path / "conventions.md").write_text("- Tokenizer: re.split on spaces.\n")
    return tmp_path


def deps(workspace, model, checks=(), **kw) -> Deps:
    results = list(checks)
    return Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-10-28_p3-test",
                config=LLMConfig("glm-flash", "glm", "http://x/v1", "glm-5.3-flash"),
                chat=model, check=lambda exercise, solution: results.pop(0), **kw)


def test_a_mutant_is_the_reference_with_one_change_and_its_bug_as_docstring():
    mutant = MutantDraft(name="m01_off_by_one", bug="adds one, every total is too big",
                         replacements=[{"old": "sum(xs)", "new": "sum(xs) + 1"}])
    code = apply_mutant(REFERENCE, mutant)
    assert code.startswith('"""Mutant: adds one, every total is too big."""\n')
    assert "return sum(xs) + 1" in code and "Reference" not in code
    assert code.count("\n") == REFERENCE.count("\n")  # a diff shows only the bug


@pytest.mark.parametrize(
    ("old", "expected"),
    [("xs", "occurs 2 times"), ("product(xs)", "occurs 0 times"), ("sum(xs)", "changes nothing")],
)
def test_a_mutant_that_cannot_be_applied_is_a_format_error(old, expected):
    new = old  # for the last case: a replacement that changes nothing
    mutant = MutantDraft(name="m01_x", bug="b", replacements=[{"old": old, "new": new}])
    with pytest.raises(DraftError, match=expected):
        apply_mutant(REFERENCE, mutant)


def test_write_draft_lays_out_a_batch_and_derives_the_skeleton(tmp_path):
    d = ExerciseDraft.model_validate(draft(data_files={"sample_input/a.txt": "1\n2"}))
    exercise, solution, dropped = write_draft(d, "sum-list", tmp_path / "round-0")
    assert dropped == {}
    assert (solution / "skeleton.py").read_text() == (exercise / "student.py").read_text()
    assert (exercise / "sample_input" / "a.txt").read_text() == "1\n2\n"
    assert sorted(p.name for p in (solution / "mutants").iterdir()) == [
        "m01_wrong.py", "m02_wrong.py", "m03_wrong.py"]
    assert json.loads((solution / "metadata.json").read_text())["id"] == "sum-list"


def broken_mutant(name: str) -> dict:
    return {"name": name, "bug": "b, wrong", "replacements": [
        {"old": "product(xs)", "new": "sum(xs)"}]}  # not in the reference


def test_a_mutant_that_cannot_be_applied_is_left_out_while_three_others_remain(tmp_path):
    good = draft()["mutants"]
    d = ExerciseDraft.model_validate(draft(mutants=[*good, broken_mutant("m04_invented")]))
    _, solution, dropped = write_draft(d, "sum-list", tmp_path / "round-0")
    assert sorted(p.stem for p in (solution / "mutants").iterdir()) == [
        "m01_wrong", "m02_wrong", "m03_wrong"]
    assert list(dropped) == ["m04_invented"] and "occurs 0 times" in dropped["m04_invented"]


def test_too_few_mutants_left_is_still_a_format_error(tmp_path):
    good = draft()["mutants"][:2]
    d = ExerciseDraft.model_validate(draft(mutants=[*good, broken_mutant("m03_invented")]))
    with pytest.raises(DraftError, match="mutant m03_invented: 'product"):
        write_draft(d, "sum-list", tmp_path / "round-0")
    assert not (tmp_path / "round-0" / "exercises").exists()


def test_a_generated_exercise_is_always_under_rule_main(tmp_path):
    exercise, solution, _ = write_draft(ExerciseDraft.model_validate(draft()), "sum-list",
                                        tmp_path / "r")
    assert json.loads((solution / "metadata.json").read_text())["standalone"] is True
    framework_launched = draft()
    framework_launched["metadata"]["standalone"] = False  # the model's explicit choice
    _, solution, _ = write_draft(ExerciseDraft.model_validate(framework_launched), "sum-list",
                                 tmp_path / "r2")
    assert json.loads((solution / "metadata.json").read_text())["standalone"] is False
    assert 'if __name__ == "__main__":' in prompts.SPEC


def test_a_generated_exercise_must_cite_its_course_pages(tmp_path):
    synthetic = draft()
    synthetic["metadata"]["source"] = "synthetic"  # copied from the example exercise
    with pytest.raises(DraftError, match="give the lecture, slides and PDF pages"):
        write_draft(ExerciseDraft.model_validate(synthetic), "sum-list", tmp_path / "r")


def test_the_batch_mode_sets_how_much_a_debugging_question_explains():
    assert "may name the cause" in prompts.item_prompt(ITEM, "", "learn")
    assert "symptom only" in prompts.item_prompt(ITEM, "", "review")


def test_the_batch_mode_sets_how_much_one_planned_exercise_combines():
    def plan(mode):
        return prompts.plan_prompt(["L1"], "", "", [], "", None, mode=mode)
    assert "never merged" in plan("learn")
    assert "final exam" in plan("review")


def test_the_plan_model_sees_what_each_existing_exercise_does(tmp_path):
    batch = tmp_path / "batches" / "b1"
    for exercise_id in ("word-count", "bare"):
        (batch / "exercises" / exercise_id).mkdir(parents=True)
    (batch / "solutions" / "word-count").mkdir(parents=True)
    (batch / "solutions" / "word-count" / "metadata.json").write_text(
        json.dumps({"title": "Word count with MRJob", "topic": "mrjob/word-count"}))
    # an exercise without readable metadata still keeps its id from being reused
    assert nodes.existing_exercises(tmp_path) == [
        "bare", "word-count: Word count with MRJob [mrjob/word-count]"]


@pytest.mark.parametrize("name", ["../escape.txt", "/etc/x", "~/x", "student.py", "run.py",
                                  ".hidden/x", "./hints.md", "data//../hints.md"])
def test_data_files_stay_inside_the_exercise(tmp_path, name):
    d = ExerciseDraft.model_validate(draft(data_files={name: "x"}))
    with pytest.raises(DraftError, match="relative path inside the exercise"):
        write_draft(d, "sum-list", tmp_path / "round-0")


def test_two_names_for_one_data_file_are_refused(tmp_path):
    d = ExerciseDraft.model_validate(draft(data_files={"a.csv": "1", "./a.csv": "2"}))
    with pytest.raises(DraftError, match="given twice"):
        write_draft(d, "sum-list", tmp_path / "round-0")


def test_retrieve_context_takes_the_whole_lecture_or_the_item_slides(workspace):
    whole = nodes.retrieve_context({"item": ITEM}, deps(workspace, None))["context"]
    assert whole.startswith("## p.1 · slide 1.1\npage 1 text") and "page 6" in whole
    sliced = nodes.retrieve_context({"item": ITEM}, deps(workspace, None, context_chars=10))
    assert [line for line in sliced["context"].splitlines() if line.startswith("##")] == [
        "## p.3 · slide 1.3", "## p.4 · slide 1.4"]


@pytest.mark.parametrize("read", ["pending", "partial"])
def test_slides_not_fully_read_stop_the_item_and_other_unread_pages_are_left_out(
        workspace, read):
    path = cache.cache_path(workspace, workspace / "lectures" / "L1.pdf")
    entries = [cache.Entry(n, f"1.{n}", read if n in (4, 6) else "text", f"h{n}",
                           f"page {n} text") for n in range(1, 7)]
    cache.save(path, cache.render("L1.pdf", entries))
    with pytest.raises(ValueError, match=r"L1 pages \[4\] \(slides 1.3-1.4\) are not fully"):
        nodes.retrieve_context({"item": ITEM}, deps(workspace, None))
    item = ITEM.model_copy(update={"slides": "1.2-1.3"})
    context = nodes.retrieve_context({"item": item}, deps(workspace, None))["context"]
    assert "page 3 text" in context and "page 4" not in context and "page 6" not in context


@pytest.mark.parametrize("labels", [
    {3: "1.3", 5: "1.4"},  # page 4 has no footer but sits inside the item's slides
    {},  # no footers at all: any page may be the item's
])
def test_an_unread_page_without_a_footer_is_not_dropped_silently(workspace, labels):
    path = cache.cache_path(workspace, workspace / "lectures" / "L1.pdf")
    entries = [cache.Entry(n, labels.get(n), "pending" if n == 4 else "text", f"h{n}",
                           f"page {n} text") for n in range(1, 7)]
    cache.save(path, cache.render("L1.pdf", entries))
    with pytest.raises(ValueError, match=r"L1 pages \[4\]"):
        nodes.retrieve_context({"item": ITEM}, deps(workspace, None))


def test_slides_the_lecture_does_not_have_stop_the_item(workspace):
    item = ITEM.model_copy(update={"slides": "9.1-9.4"})
    with pytest.raises(ValueError, match="no page of L1 has a slide footer in 9.1-9.4"):
        nodes.retrieve_context({"item": item}, deps(workspace, None))


def test_the_model_gets_the_rules_conventions_pages_and_the_schema(workspace):
    model = FakeModel(json.dumps(draft()))
    update = nodes.generate({"item": ITEM, "context": "## p.3 · slide 1.3\npage 3 text"},
                            deps(workspace, model, thinking=False))
    purpose, messages, schema = model.calls[0]
    assert purpose == "generate" and schema == nodes.DRAFT_SCHEMA and model.thinking is False
    assert "Tokenizer: re.split on spaces." in messages[0]["content"]
    assert "testkit.run_mrjob(" in messages[0]["content"]
    assert "page 3 text" in messages[1]["content"]
    assert (update["round"], update["tokens"], update["failure"]) == (0, 1500, None)


def test_a_reply_that_does_not_parse_is_a_format_failure(workspace):
    model = FakeModel('{"metadata": {', finish_reason="length")
    update = nodes.generate({"item": ITEM, "context": ""}, deps(workspace, model))
    assert update["draft"] is None and update["format_error"]
    assert update["failure"].startswith("format:") and "cut off at max_tokens" in update["failure"]


def test_the_graph_repairs_a_failing_draft_then_saves_it(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(json.dumps(draft()), json.dumps(draft()))
    d = deps(workspace, model, checks=[report("3"), report()])
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved" and final["round"] == 1
    assert [purpose for purpose, _, _ in model.calls] == ["generate", "repair"]
    assert "rule 3 (rule 3) FAIL: 3 of 3 mutants survived" in model.calls[1][1][1]["content"]
    assert (d.batch_dir / "exercises" / "sum-list" / "student.py").is_file()
    assert not (d.batch_dir / "manifest.json").exists()  # written once per batch
    log = json.loads((d.batch_dir / "generation.jsonl").read_text())
    assert (log["outcome"], log["rounds"], log["tokens"]) == ("saved", 1, 3000)
    assert (d.drafts / "sum-list" / "round-0" / "report.txt").read_text().endswith("INVALID\n")


def test_the_graph_saves_a_valid_draft_without_its_broken_mutant(workspace):
    """GLM-5.3 lost three exercises in a night, each to one invented mutant it kept
    sending back through three repairs."""
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    four = draft(mutants=[*draft()["mutants"], broken_mutant("m04_invented")])
    model = FakeModel(json.dumps(four), json.dumps(four))
    d = deps(workspace, model, checks=[report("4"), report()])
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved" and final["round"] == 1
    repair = model.calls[1][1][1]["content"]
    assert "Left out, could not be applied (fix them too):\nmutant m04_invented" in repair
    assert [a["dropped"] for a in final["attempts"]] == [["m04_invented"], ["m04_invented"]]
    saved = d.batch_dir / "solutions" / "sum-list" / "mutants"
    assert sorted(p.stem for p in saved.iterdir()) == ["m01_wrong", "m02_wrong", "m03_wrong"]
    log = json.loads((d.batch_dir / "generation.jsonl").read_text())
    assert log["attempts"][-1]["dropped"] == ["m04_invented"]


def test_the_graph_discards_after_the_last_repair_round(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(*[json.dumps(draft())] * 2)
    d = deps(workspace, model, checks=[report("3")] * 2, max_rounds=1)
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "discarded"
    assert not (d.batch_dir / "exercises").exists()
    assert (d.drafts / "sum-list" / "round-1").is_dir()  # kept for a look at why


def test_the_system_prompt_lists_the_allowed_imports_from_the_safety_check():
    text = prompts.system_prompt("")
    assert "collections" in text and "{imports}" not in text and "(none recorded)" in text


# --- the batch: plan, approve, parallel items, collect -----------------------------------

def plan_json(*ids: str, lecture: str = "L1") -> str:
    return json.dumps({"items": [
        {"id": i, "knowledge_point": f"point {i}", "lecture": lecture, "slides": "1.3-1.4",
         "type": "implementation", "interface": "function", "framework": "python",
         "reason": "a gap"} for i in ids]})


class BatchModel:
    """Plans come from a queue; an exercise draft is made for whichever id is asked."""

    def __init__(self, *plans: str):
        self.plans, self.purposes = list(plans), []

    def __call__(self, config, messages, *, purpose, log_path, max_tokens, response_schema,
                 thinking, temperature=0.0, **streaming):
        self.purposes.append(purpose)
        self.thinking = thinking
        if purpose == "plan":
            self.last_plan_request = messages[1]["content"]
            return Reply(self.plans.pop(0), 800, 200, 0.1, "stop")
        exercise_id = re.search(r"Write the exercise `([^`]+)`", messages[1]["content"])[1]
        data = draft()
        data["metadata"]["id"] = exercise_id
        return Reply(json.dumps(data), 1000, 500, 0.1, "stop")


def test_plan_problems_are_found_by_code():
    items = [PlanItem.model_validate(i) for i in json.loads(
        plan_json("a", "a", "old-one", lecture="L9"))["items"]]
    found = nodes.plan_problems(items, ["L1"], 2, ["old-one"])
    assert "3 items, 2 asked for" in found
    assert "id 'a' is used twice" in found
    assert "id 'old-one' already exists in another batch" in found
    assert "a: lecture 'L9' is not one of ['L1']" in found


def test_a_plan_breaking_the_rules_is_asked_for_again_with_the_problems(workspace):
    model = BatchModel(plan_json("a", lecture="L9"), plan_json("a"))
    update = nodes.plan_batch({"lectures": ["L1"], "count": 1}, deps(workspace, model))
    assert model.thinking is True  # planning reasons first, unlike generating
    assert model.purposes == ["plan", "plan"]
    assert "lecture 'L9' is not one of ['L1']" in model.last_plan_request
    assert (update["plan"][0]["lecture"], update["plan_problems"]) == ("L1", [])


def test_page_titles_skip_the_footer_line(workspace):
    entries = [cache.Entry(13, "4.15", "text", "h", "4.15\nPassing Functions to RDD\n❖ Spark")]
    cache.save(cache.cache_path(workspace, workspace / "lectures" / "L4.pdf"),
               cache.render("L4.pdf", entries))
    assert nodes.page_titles(workspace, "L4").splitlines()[1] == (
        "  p.13 · slide 4.15: Passing Functions to RDD")


def test_the_owner_may_edit_the_plan_before_approving():
    plan = json.loads(plan_json("a"))["items"]
    state = {"plan": plan, "lectures": ["L1"]}
    edited = [{**plan[0], "id": "a-better-name"}]
    assert nodes.apply_decision(state, {"action": "approve", "plan": edited}, []) == {
        "plan": edited, "approved": True}
    assert nodes.apply_decision(state, {"action": "revise", "feedback": "harder"}, []) == {
        "feedback": "harder", "approved": False}


def test_a_plan_with_a_repeated_or_taken_id_is_never_approved():
    """Two items with one id would share a drafts folder, each clearing it first."""
    plan = json.loads(plan_json("a", "a"))["items"]
    with pytest.raises(ValueError, match="id 'a' is used twice"):
        nodes.apply_decision({"plan": plan, "lectures": ["L1"]}, {"action": "approve"}, [])
    with pytest.raises(ValueError, match="already exists in another batch"):
        nodes.apply_decision({"plan": plan[:1], "lectures": ["L1"]}, {"action": "approve"},
                             ["a"])
    # A count other than the one asked for is the owner's choice, not a blocking problem.
    items = [PlanItem.model_validate(i) for i in plan[:1]]
    assert nodes.blocking_problems(items, ["L1"], []) == []


def test_a_resume_keeps_the_owners_edits_to_the_plan_until_the_model_plans_again(tmp_path):
    from tutor.graph.__main__ import _owner_plan

    model_plan = json.loads(plan_json("a"))["items"]
    assert _owner_plan(tmp_path, model_plan) == (model_plan, False)  # first pause: written
    edited = [{**model_plan[0], "id": "a-better-name"}]
    (tmp_path / "plan.json").write_text(json.dumps(edited))
    assert _owner_plan(tmp_path, model_plan) == (edited, True)  # resume: edits kept
    replanned = json.loads(plan_json("b"))["items"]  # after feedback
    assert _owner_plan(tmp_path, replanned) == (replanned, False)
    assert json.loads((tmp_path / "plan.json").read_text()) == replanned


def test_a_batch_paused_before_the_model_plan_was_kept_keeps_the_owners_plan(tmp_path):
    from tutor.graph.__main__ import _owner_plan

    model_plan = json.loads(plan_json("a"))["items"]
    edited = [{**model_plan[0], "id": "a-better-name"}]
    (tmp_path / "plan.json").write_text(json.dumps(edited))  # no plan.model.json yet
    assert _owner_plan(tmp_path, model_plan) == (edited, True)
    assert _owner_plan(tmp_path, model_plan) == (edited, True)  # and on the next resume


def test_a_batch_is_planned_sent_back_approved_later_and_generated_in_parallel(workspace):
    pytest.importorskip("langgraph")
    sqlite = pytest.importorskip("langgraph.checkpoint.sqlite")
    from langgraph.types import Command

    from tutor.graph.build import build_batch

    model = BatchModel(plan_json("first-a", "first-b"), plan_json("sum-list", "sum-squares"))
    d = Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-10-28_p3-w1",
             config=LLMConfig("glm-flash", "glm", "http://x/v1", "glm-5.3-flash"),
             chat=model, max_rounds=1,
             check=lambda exercise, solution: report() if exercise.name == "sum-list"
             else report("3"))
    config = {"configurable": {"thread_id": "w1"}, "max_concurrency": 2}
    checkpoint = str(workspace / "checkpoint.sqlite")

    with sqlite.SqliteSaver.from_conn_string(checkpoint) as saver:
        app = build_batch(d, saver)
        app.invoke({"lectures": ["L1"], "count": 2, "preferences": "more debugging"}, config)
        assert app.get_state(config).next == ("approve",)  # paused for the owner
        app.invoke(Command(resume={"action": "revise", "feedback": "two items"}), config)
        assert "two items" in model.last_plan_request

    with sqlite.SqliteSaver.from_conn_string(checkpoint) as saver:  # another day
        app = build_batch(d, saver)
        plan = app.get_state(config).values["plan"]
        final = app.invoke(Command(resume={"action": "approve", "plan": plan}), config)

    outcomes = {r["id"]: r["outcome"] for r in final["results"]}
    assert outcomes == {"sum-list": "saved", "sum-squares": "discarded"}
    manifest = Manifest.model_validate_json((d.batch_dir / "manifest.json").read_text())
    assert manifest.exercises == ["sum-list"] and "more debugging" in manifest.notes
    proposal = (d.batch_dir / "coverage-proposal.md").read_text()
    assert "gap-code -> ex:sum-list (p3-w1)" in proposal and "sum-squares" not in proposal


def test_a_save_that_fails_midway_leaves_nothing_and_a_retry_saves(tmp_path, monkeypatch):
    d = deps(tmp_path, None)
    round_dir = tmp_path / "round-0"
    write_draft(ExerciseDraft.model_validate(draft()), "sum-list", round_dir)
    state = {"item": ITEM, "round_dir": str(round_dir)}
    copy = nodes.shutil.copytree
    calls = []

    def disk_full(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("No space left on device")
        return copy(source, target)

    monkeypatch.setattr(nodes.shutil, "copytree", disk_full)
    with pytest.raises(OSError):
        nodes.save(state, d)
    assert not (d.batch_dir / "exercises" / "sum-list").exists()
    monkeypatch.setattr(nodes.shutil, "copytree", copy)
    assert nodes.save(state, d) == {"outcome": "saved"}
    assert (d.batch_dir / "solutions" / "sum-list" / "reference.py").is_file()
    assert sorted(p.name for p in (d.batch_dir / "exercises").iterdir()) == ["sum-list"]


def test_an_item_saved_before_is_not_saved_again_nor_overwritten(tmp_path):
    """A resume may run an item whose save finished but was never recorded."""
    d = deps(tmp_path, None)
    round_dir = tmp_path / "round-0"
    write_draft(ExerciseDraft.model_validate(draft()), "sum-list", round_dir)
    state = {"item": ITEM, "round_dir": str(round_dir)}
    nodes.save(state, d)
    student = d.batch_dir / "exercises" / "sum-list" / "student.py"
    student.write_text("# the owner's answer\n")
    assert nodes.save(state, d) == {"outcome": "saved"}
    assert student.read_text() == "# the owner's answer\n"
    (d.batch_dir / "generation.jsonl").unlink()  # not saved by this pipeline: refuse
    with pytest.raises(FileExistsError, match="never overwrite"):
        nodes.save(state, d)


def test_listing_saved_items_again_leaves_the_manifest_unchanged(tmp_path):
    """collect may run twice when a batch resumes after writing the manifest."""
    d = deps(tmp_path, None)
    write_manifest(d, [ITEM])
    first = (d.batch_dir / "manifest.json").read_text()
    write_manifest(d, [ITEM])
    assert (d.batch_dir / "manifest.json").read_text() == first
    other = ITEM.model_copy(update={"id": "sum-squares"})
    write_manifest(d, [ITEM, other])
    manifest = Manifest.model_validate_json((d.batch_dir / "manifest.json").read_text())
    assert manifest.exercises == ["sum-list", "sum-squares"]
    assert [p.name for p in d.batch_dir.iterdir()] == ["manifest.json"]  # no .partial left


def test_without_a_count_the_plan_chooses_up_to_the_maximum():
    items = [PlanItem.model_validate(i) for i in json.loads(
        plan_json("a", "b", "c"))["items"]]
    assert nodes.plan_problems(items, ["L1"], None, [], max_count=3) == []
    assert nodes.plan_problems(items, ["L1"], None, [], max_count=2) == [
        "3 items, at most 2: merge or drop the least important"]
    assert "at most 4" in prompts.plan_prompt(["L1"], "", "", [], "", None, max_count=4)


def test_a_run_stopped_midway_continues_without_redoing_finished_items(workspace):
    pytest.importorskip("langgraph")
    sqlite = pytest.importorskip("langgraph.checkpoint.sqlite")
    from langgraph.types import Command

    from tutor.graph.build import build_batch

    checked: list[str] = []
    stop = {"once": True}

    def check(exercise, solution):
        checked.append(exercise.name)
        if exercise.name == "sum-squares" and stop.pop("once", False):
            raise KeyboardInterrupt  # not an Exception: it stops the whole run
        return report()

    model = BatchModel(plan_json("sum-list", "sum-squares"))
    d = Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-10-28_p3-w1",
             config=LLMConfig("glm-flash", "glm", "http://x/v1", "glm-5.3-flash"),
             chat=model, check=check)
    config = {"configurable": {"thread_id": "w1"}, "max_concurrency": 1}
    with sqlite.SqliteSaver.from_conn_string(str(workspace / "c.sqlite")) as saver:
        app = build_batch(d, saver)
        app.invoke({"lectures": ["L1"], "count": 2}, config)
        plan = app.get_state(config).values["plan"]
        with pytest.raises(KeyboardInterrupt):
            app.invoke(Command(resume={"action": "approve", "plan": plan}), config)
        assert app.get_state(config).next  # stopped, not finished
        final = app.invoke(None, config)  # what `resume` does for a stopped run

    assert {r["id"]: r["outcome"] for r in final["results"]} == {
        "sum-list": "saved", "sum-squares": "saved"}
    assert checked.count("sum-list") == 1  # finished before the stop, not run again


def test_an_exception_in_one_item_is_reported_and_the_others_finish(workspace):
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from tutor.graph.build import build_batch

    def check(exercise, solution):
        if exercise.name == "sum-squares":
            raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte")
        return report()

    model = BatchModel(plan_json("sum-list", "sum-squares"))
    d = Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-10-28_p3-w1",
             config=LLMConfig("glm-flash", "glm", "http://x/v1", "glm-5.3-flash"),
             chat=model, check=check)
    config = {"configurable": {"thread_id": "w1"}}
    app = build_batch(d, MemorySaver())
    app.invoke({"lectures": ["L1"], "count": 2}, config)
    final = app.invoke(Command(resume={"action": "approve",
                                       "plan": app.get_state(config).values["plan"]}), config)
    outcomes = {r["id"]: r["outcome"] for r in final["results"]}
    assert outcomes == {"sum-list": "saved", "sum-squares": "error"}
    assert (d.batch_dir / "manifest.json").is_file()  # collect still ran
    log = [json.loads(x) for x in d.generation_log.read_text().splitlines()]
    assert any(x["outcome"] == "error" and "UnicodeDecodeError" in x["error"] for x in log)


def test_a_mutant_that_misses_the_reference_is_shown_the_closest_line():
    mutant = MutantDraft(name="m01_x", bug="b",
                         replacements=[{"old": "return  sum(xs)", "new": "return 0"}])
    with pytest.raises(DraftError, match=r"closest line in reference.py is, exactly: "
                                         r"'    return sum\(xs\)'"):
        apply_mutant(REFERENCE, mutant)


def test_the_example_leaves_out_hidden_and_binary_files(tmp_path, monkeypatch):
    root = tmp_path / prompts.DEFAULT_EXAMPLE
    (root / "exercise").mkdir(parents=True)
    (root / "exercise" / "student.py").write_text("def f(): ...\n")
    (root / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\x80")  # what macOS leaves
    (root / "exercise" / "data.bin").write_bytes(b"\x80\x81")
    monkeypatch.setattr(prompts, "EXAMPLES", tmp_path)
    text = prompts.example("python", "function")
    assert "=== exercise/student.py ===" in text
    assert ".DS_Store" not in text and "data.bin" not in text


def test_the_item_graph_keeps_no_checkpoints_of_its_own(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    assert build_item(deps(workspace, None)).checkpointer is False


def test_the_draft_schema_tells_the_model_what_each_constraint_means():
    """A model put API calls into required_any: the meaning must reach it in the schema."""
    fields = nodes.DRAFT_SCHEMA["$defs"]["Constraints"]["properties"]
    assert set(fields) == {"forbidden_calls", "forbidden_imports", "required_definitions",
                           "required_any", "count_uses"}
    assert all(field.get("description") for field in fields.values())
    assert "Not API calls" in fields["required_any"]["description"]


def test_the_progress_line_names_mutants_left_out(capsys):
    from tutor.graph.__main__ import _progress

    _progress("sum-list", "validate", {"failure": None, "attempts": [
        {"round": 0, "failure": None, "format_error": False, "dropped": ["m04_invented"]}]})
    assert capsys.readouterr().out == (
        "  [sum-list] validate: VALID (left out, could not be applied: m04_invented)\n")


# --- the oracle, written by another model from the question alone -----------------------

ORACLE = LLMConfig("gpt-terra", "openai", "https://api.openai.com/v1", "gpt-6.1-terra")
INTERFACE = ('"""Oracle for total."""\n\n\n'
             'def total(xs: list[int]) -> int:\n    """The sum of xs."""\n'
             "    raise NotImplementedError\n")
FILLED = ('"""Oracle for total."""\n\n\n'
          'def total(xs: list[int]) -> int:\n    """The sum of xs."""\n'
          "    t = 0\n    for x in xs:\n        t += x\n    return t\n")


def with_interface(**changes) -> str:
    return json.dumps(draft(oracle_py=INTERFACE, **changes))


def test_without_an_oracle_role_the_generating_model_writes_the_whole_oracle(workspace):
    model = FakeModel()
    assert nodes.write_oracle({"draft": ExerciseDraft.model_validate(draft())},
                              deps(workspace, model)) == {}
    assert "only the interface" not in prompts.system_prompt("")
    assert "only the interface" in prompts.system_prompt("", oracle_interface=True)


def test_the_oracle_model_sees_the_question_and_interface_never_the_reference(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(with_interface(), json.dumps({"oracle_py": FILLED}))
    d = deps(workspace, model, checks=[report()], oracle_config=ORACLE)
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved" and final["oracle_status"] == "written"
    assert [purpose for purpose, _, _ in model.calls] == ["generate", "oracle"]
    asked = model.calls[1][1][1]["content"]
    assert "Sum a list." in asked and "raise NotImplementedError" in asked
    assert "Reference" not in asked and "sum(xs)" not in asked and "m01_wrong" not in asked
    assert "only the interface" in model.calls[0][1][0]["content"]  # the generating model
    saved = d.batch_dir / "solutions" / "sum-list" / "oracle.py"
    assert saved.read_text() == FILLED
    log = json.loads((d.batch_dir / "generation.jsonl").read_text())
    assert log["oracle_model"] == "gpt-terra: gpt-6.1-terra"


def test_a_repair_that_keeps_the_question_keeps_the_oracle(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(with_interface(), json.dumps({"oracle_py": FILLED}), with_interface())
    d = deps(workspace, model, checks=[report("3"), report()], oracle_config=ORACLE)
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved" and final["oracle_status"] == "kept"
    assert [purpose for purpose, _, _ in model.calls] == ["generate", "oracle", "repair"]


def test_when_the_oracle_disagrees_the_repair_is_told_the_question_may_be_unclear(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    asked_again = with_interface(student_py='"""Sum a list of ints; [] gives 0."""\n')
    model = FakeModel(with_interface(), json.dumps({"oracle_py": FILLED}), asked_again,
                      json.dumps({"oracle_py": FILLED}))
    disagrees = ValidationReport("sum-list", [
        RuleResult(r.rule, r.name, r.status, "reference disagrees with the oracle; "
                   "Failing test case: test_same(xs=[])" if r.rule == "4" else r.detail, 0.0)
        for r in report("4").results])
    d = deps(workspace, model, checks=[disagrees, report()], oracle_config=ORACLE)
    final = build_item(d).invoke({"item": ITEM})

    repair = model.calls[2][1][1]["content"]
    assert "it never saw your reference" in repair and "t += x" in repair
    assert "never copy this code into it" in repair  # a repair once pasted it into oracle_py
    # The question changed, so the oracle was written again from the new one.
    assert [purpose for purpose, _, _ in model.calls] == ["generate", "oracle", "repair",
                                                          "oracle"]
    assert final["outcome"] == "saved" and final["oracle_status"] == "written"


def test_a_whole_oracle_becomes_an_interface_before_the_oracle_model_sees_it(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(json.dumps(draft()), json.dumps({"oracle_py": FILLED}))
    d = deps(workspace, model, checks=[report()], oracle_config=ORACLE)
    final = build_item(d).invoke({"item": ITEM})

    assert [purpose for purpose, _, _ in model.calls] == ["generate", "oracle"]
    asked = model.calls[1][1][1]["content"]
    assert "def total(xs):\n    raise NotImplementedError" in asked and "t += x" not in asked
    assert final["outcome"] == "saved" and final["round"] == 0


def test_an_oracle_left_unwritten_is_a_failure_of_the_round(workspace):
    unwritten = json.dumps({"oracle_py": INTERFACE})
    d = deps(workspace, FakeModel(unwritten, unwritten), oracle_config=ORACLE)
    state = {"item": ITEM, "draft": ExerciseDraft.model_validate_json(with_interface())}
    update = nodes.write_oracle(state, d)
    assert update["oracle_status"] == "failed" and update["format_error"] is True
    assert "it left total unwritten" in update["failure"]
    assert nodes.after_oracle({**state, **update, "round": 0}, d) == "repair"


def test_the_oracle_must_come_from_another_provider(workspace):
    from tutor.graph.__main__ import _deps

    (workspace / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.glm-flash]\nprovider = "glm"\nmodel = "glm-5.3-flash"\n'
        '[llm.profiles.glm-full]\nprovider = "glm"\nmodel = "glm-5.3"\n'
        '[llm.profiles.gpt]\nprovider = "openai"\nmodel = "gpt-6.1-terra"\n'
        '[llm.roles]\ngenerate = "glm-flash"\noracle = "glm-full"\n')
    with pytest.raises(ValueError, match="come from one provider"):
        _deps(workspace, workspace / "batches" / "b", {})
    d = _deps(workspace, workspace / "batches" / "b", {"oracle_profile": "gpt"})
    assert d.oracle_config.name == "gpt"
    d = _deps(workspace, workspace / "batches" / "b",
              {"oracle_profile": "gpt", "plan_profile": "glm-full"})
    assert d.plan_config.name == "glm-full"


def test_the_progress_line_says_what_the_oracle_node_did(capsys):
    from tutor.graph.__main__ import _progress

    _progress("sum-list", "oracle", {})  # no oracle role: nothing to say
    _progress("sum-list", "oracle", {"oracle_status": "kept"})
    assert capsys.readouterr().out == "  [sum-list] oracle: kept\n"


# --- what a repair round is told ---------------------------------------------------------

def test_repair_sees_the_failing_tests_and_asks_a_little_warmer_each_round(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    failing = report("1")
    failing = ValidationReport("sum-list", [
        RuleResult(r.rule, r.name, r.status, r.detail, 0.0,
                   "test_total\ntest_student.py:5:\nE   assert 4 == 3" if r.rule == "1" else "")
        for r in failing.results])
    model = FakeModel(json.dumps(draft()), json.dumps(draft()), json.dumps(draft()))
    d = deps(workspace, model, checks=[failing, failing, report()])
    final = build_item(d).invoke({"item": ITEM})

    first_repair = model.calls[1][1][1]["content"]
    assert "rule 1 (rule 1) FAIL: 3 of 3 mutants survived\n    test_total\n" in first_repair
    assert "    E   assert 4 == 3" in first_repair
    assert model.temperatures == [0.0, 0.2, 0.4]
    # The first repair repeated the draft word for word: the second is told so.
    assert "identical to the one before" not in first_repair
    assert "identical to the one before" in model.calls[2][1][1]["content"]
    assert [a["unchanged"] for a in final["attempts"]] == [False, True, True]


def test_a_rule_1_failure_reminds_repair_that_the_test_may_be_wrong():
    rule_1 = prompts.repair_prompt(ITEM, "", "{}", "rule 1 (reference passes) FAIL: ...")
    rule_3 = prompts.repair_prompt(ITEM, "", "{}", "rule 3 (mutants killed) FAIL: ...")
    assert "if the test is wrong, fix the test" in rule_1
    assert "if the test is wrong" not in rule_3


def test_shared_parts_come_first_so_providers_can_cache_them():
    """System: the same for the whole course. User: pages (one lecture), the example (one
    kind of exercise), then the item."""
    other = ITEM.model_copy(update={"id": "other", "knowledge_point": "another point"})
    assert "Example exercise" not in prompts.system_prompt("conventions")
    first, second = (prompts.item_prompt(i, "PAGES", "learn") for i in (ITEM, other))
    shared = first[:first.index("Write the exercise")]
    assert second.startswith(shared) and shared.index("PAGES") < shared.index("Example exercise")


def test_usage_counts_the_cached_input_of_every_call_of_an_item(workspace, capsys):
    from tutor.graph.__main__ import _summary, _usage_text

    state = {"usage": {"input": 1000, "cached": 0, "output": 500}}
    spent = nodes._spent(state, Reply("{}", 2000, 300, 0.1, "stop", cached_tokens=1500))
    assert spent == {"tokens": 3800, "usage": {"input": 3000, "cached": 1500, "output": 800}}
    assert _usage_text(spent["usage"]) == (
        "input 3,000 tokens (1,500 from the provider's cache, 50%), output 800")
    results = [{"id": i, "outcome": "saved", "rounds": 0, "tokens": 3800,
                "usage": spent["usage"], "last_failure": None} for i in ("a", "b")]
    _summary(results, workspace / "batches" / "b", workspace)
    assert "all items: input 6,000 tokens (3,000 from the provider's cache, 50%)" in (
        capsys.readouterr().out)


def test_a_surviving_mutant_reminds_repair_how_to_catch_a_how_bug():
    survived = "rule 3 (mutants killed) FAIL: 5/6 killed; survived (tests miss this bug): m01_x"
    broken = "rule 3 (mutants killed) FAIL: 5/6 killed; cannot run (not a useful mutant): m01"
    assert "a spy or monkeypatch" in prompts.repair_prompt(ITEM, "", "{}", survived)
    assert "a spy or monkeypatch" not in prompts.repair_prompt(ITEM, "", "{}", broken)


def test_exercise_replies_are_streamed_with_progress_by_exercise(workspace, capsys):
    from tutor.graph.__main__ import _receiving

    seen = {}

    def model(config, messages, **kw):
        seen.update(kw)
        kw["on_progress"](45_000)
        return Reply(json.dumps(draft()), 1000, 500, 0.1, "stop")

    nodes.generate({"item": ITEM, "context": ""}, deps(workspace, model, on_stream=_receiving))
    assert seen["stream"] is True
    assert capsys.readouterr().out == "  [sum-list] receiving: 45,000 characters\n"


def test_when_the_reference_agrees_with_the_oracle_repair_suspects_the_tests(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(json.dumps(draft()), json.dumps(draft()))
    d = deps(workspace, model, checks=[report("1"), report()])  # rule 4 passed
    build_item(d).invoke({"item": ITEM})
    repair = model.calls[1][1][1]["content"]
    assert "the reference agrees with oracle.py. So the tests are the likely side" in repair
    model = FakeModel(json.dumps(draft()), json.dumps(draft()))
    d = deps(workspace, model, checks=[report("1", "4")] * 2, max_rounds=1)  # discarded
    build_item(d).invoke({"item": ITEM})
    assert "the reference agrees" not in model.calls[1][1][1]["content"]


PRO = LLMConfig("deepseek-pro", "deepseek", "https://api.deepseek.com", "deepseek-v4-pro")


def test_the_repair_role_takes_over_from_the_second_repair(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    model = FakeModel(*[json.dumps(draft())] * 4)
    d = deps(workspace, model, checks=[report("1")] * 3 + [report()], repair_config=PRO)
    final = build_item(d).invoke({"item": ITEM})

    assert final["outcome"] == "saved"
    assert model.configs == ["glm-flash", "glm-flash", "deepseek-pro", "deepseek-pro"]
    assert model.thinkings == [False, False, True, True]  # the repair model reasons
    assert model.max_tokens == [16_000, 16_000, 32_000, 32_000]  # room for its reasoning
    assert [a["model"] for a in final["attempts"]] == model.configs
    log = json.loads((d.batch_dir / "generation.jsonl").read_text())
    assert log["repair_model"] == "deepseek-pro: deepseek-v4-pro"


def test_the_oracle_must_differ_from_the_repair_model_too(workspace):
    from tutor.graph.__main__ import _deps

    (workspace / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.ds]\nprovider = "deepseek"\nmodel = "f"\n'
        '[llm.profiles.gpt]\nprovider = "openai"\nmodel = "g"\n'
        '[llm.roles]\ngenerate = "ds"\nrepair = "gpt"\noracle = "gpt"\n')
    with pytest.raises(ValueError, match="the oracle \\(gpt\\) and gpt, which writes"):
        _deps(workspace, workspace / "batches" / "b", {})


def test_a_repair_writes_its_diagnosis_first_and_it_is_logged(workspace):
    pytest.importorskip("langgraph")
    from tutor.graph.build import build_item

    assert next(iter(nodes.DRAFT_SCHEMA["properties"])) == "diagnosis"
    fixed = draft(diagnosis="test_total expects 4; by hand 1 + 2 = 3, so the test is wrong.")
    model = FakeModel(json.dumps(draft()), json.dumps(fixed))
    d = deps(workspace, model, checks=[report("1"), report()])
    final = build_item(d).invoke({"item": ITEM})

    assert "Start with `diagnosis`" in model.calls[1][1][1]["content"]
    assert [a["diagnosis"] for a in final["attempts"]] == ["", fixed["diagnosis"]]
    saved = d.batch_dir / "solutions" / "sum-list"
    assert not any("by hand" in p.read_text() for p in saved.rglob("*") if p.is_file())


def test_the_oracle_model_fixes_its_own_unsafe_import(workspace):
    """GPT imported io in an oracle; the generating model, told to fix it, could not."""
    unsafe = json.dumps({"oracle_py": "import socket\n" + FILLED})
    model = FakeModel(unsafe, json.dumps({"oracle_py": FILLED}))
    d = deps(workspace, model, oracle_config=ORACLE)
    state = {"item": ITEM, "draft": ExerciseDraft.model_validate_json(with_interface())}
    update = nodes.write_oracle(state, d)

    assert update["oracle_status"] == "written" and update["oracle_py"] == FILLED
    retry = model.calls[1][1]
    assert retry[2]["role"] == "assistant"
    assert "import of 'socket' is not allowed" in retry[3]["content"]
    assert "imports only from" in model.calls[0][1][0]["content"] and "collections" in (
        model.calls[0][1][0]["content"])


def test_a_differential_test_that_cannot_run_is_not_called_a_disagreement(workspace):
    cannot_run = ValidationReport("sum-list", [
        RuleResult(r.rule, r.name, r.status,
                   "differential test cannot run: 1 error" if r.rule == "4" else r.detail, 0.0)
        for r in report("4").results])
    d = deps(workspace, FakeModel(json.dumps({"oracle_py": FILLED})), checks=[cannot_run],
             oracle_config=ORACLE)
    state = {"item": ITEM, "draft": ExerciseDraft.model_validate_json(with_interface())}
    state.update(nodes.write_oracle(state, d))
    failure = nodes.validate_draft(state, d)["failure"]
    assert "cannot run" in failure and "it never saw your reference" not in failure


# --- assistant mode: the batch pauses for answers and replays -------------------------------

def answer_waiting(queue, replies: dict) -> list[str]:
    """Act as the assistant: answer every waiting request by its purpose."""
    from tutor.exchange import store_answer

    answered = []
    for request in queue.waiting():
        purpose = re.search(r"- for: (\w+)", request.read_text())[1]
        store_answer(queue, request.stem, replies[purpose])
        answered.append(purpose)
    return answered


def test_an_assistant_batch_pauses_for_answers_and_replays_to_the_end(workspace):
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from tutor.exchange import AwaitingAnswer, Queue
    from tutor.graph.build import build_batch

    queue = Queue(workspace / ".exchange" / "cc")
    checks = []
    d = Deps(workspace=workspace, batch_dir=workspace / "batches" / "2026-10-29_p3-a1",
             config=LLMConfig("cc", "assistant", "assistant:cc", "cc", queue=queue.root),
             max_rounds=1,
             check=nodes.reusing_reports(lambda e, s: checks.append(e) or report(),
                                         workspace / "checks"))
    app, config = build_batch(d, MemorySaver()), {"configurable": {"thread_id": "a1"}}
    with pytest.raises(AwaitingAnswer):  # the plan is the first request
        app.invoke({"lectures": ["L1"], "count": 1}, config)
    assert answer_waiting(queue, {"plan": plan_json("sum-list")}) == ["plan"]
    app.invoke(None, config)  # replays the plan call, then pauses for approval
    plan = app.get_state(config).values["plan"]
    app.invoke(Command(resume={"action": "approve", "plan": plan}), config)
    [pause] = [i.value for t in app.get_state(config).tasks for i in t.interrupts]
    assert len(pause["waiting"]) == 1  # the item stopped at its draft request

    assert answer_waiting(queue, {"generate": json.dumps(draft())}) == ["generate"]
    final = app.invoke(Command(resume={"answered": True}), config)
    assert [r["outcome"] for r in nodes.latest_results(final["results"])] == ["saved"]
    assert (d.batch_dir / "exercises" / "sum-list" / "student.py").is_file()
    assert len(checks) == 1
    # Who wrote it is on record: the manifest and the log name the assistant as such.
    manifest = Manifest.model_validate_json((d.batch_dir / "manifest.json").read_text())
    assert manifest.model == "cc: cc (assistant)"
    log = json.loads((d.batch_dir / "generation.jsonl").read_text().splitlines()[-1])
    assert log["model"] == "cc: cc (assistant)"


def test_a_validation_report_is_reused_for_the_same_files(tmp_path):
    exercise, solution = tmp_path / "e", tmp_path / "s"
    for folder in (exercise, solution):
        folder.mkdir()
        (folder / "a.py").write_text("x = 1\n")
    runs = []
    check = nodes.reusing_reports(lambda e, s: runs.append(1) or report("3"), tmp_path / "c")
    first, again = check(exercise, solution), check(exercise, solution)
    assert again == first and again.result("3").status is Status.FAIL and len(runs) == 1
    (solution / "a.py").write_text("x = 2\n")
    check(exercise, solution)
    assert len(runs) == 2  # other files: validated again


def test_one_assistant_may_write_the_oracle_through_a_second_profile(workspace, capsys):
    from tutor.graph.__main__ import _deps

    (workspace / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.cc]\nprovider = "assistant"\n'
        '[llm.profiles.cc-oracle]\nprovider = "assistant"\nmodel = "cc"\n'
        '[llm.roles]\ngenerate = "cc"\noracle = "cc-oracle"\n')
    d = _deps(workspace, workspace / "batches" / "b", {})
    assert d.oracle_config.name == "cc-oracle"
    assert "fresh context" in capsys.readouterr().out  # allowed, with a reminder
    with pytest.raises(ValueError, match="through a second profile"):
        _deps(workspace, workspace / "batches" / "b", {"oracle_profile": "cc"})
