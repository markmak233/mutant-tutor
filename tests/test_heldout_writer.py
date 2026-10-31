"""Model-written held-out candidates (tutor/heldout_writer.py), with a fake model: what the
model is shown, how each reply is checked, and what is written."""

import json

import pytest

from tutor import llm
from tutor.heldout_writer import (
    TASKS,
    HeldOutWriteError,
    Writer,
    code_problems,
    gather,
    unfence,
    user_prompt,
    write,
)
from tutor.materials import cache
from tutor.materials.slides import lecture_context, lecture_file, page_groups

SKELETON = '"""Add two numbers."""\n\ndef add(a, b):\n    raise NotImplementedError\n'
OWNER = "def add(a, b):\n    return a + b  # the owner's answer\n"
TESTS = "from student import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
CORRECT = "def add(a, b):\n    return b + a\n"
SLIDES = "def add(a, b):\n    return sum([a, b])\n"
WRONG = ["def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a * b\n",
         "def add(a, b):\n    return max(a, b)\n"]
UNSAFE = "import subprocess\n\ndef add(a, b):\n    return a + b\n"


def make_batch(tmp_path, pdf_pages="3-4"):
    """A workspace with a one-exercise batch and a cached lecture of 5 pages."""
    workspace = tmp_path / "ws"
    batch = workspace / "batches" / "b1"
    exercise, solution = batch / "exercises" / "demo", batch / "solutions" / "demo"
    (exercise / "sample_input").mkdir(parents=True)
    (exercise / "sample_output").mkdir()
    solution.mkdir(parents=True)
    (exercise / "question.md").write_text("# Add\n\nAdd `a` and `b`.\n")
    (exercise / "student.py").write_text(OWNER)
    (exercise / "test_student.py").write_text(TESTS)
    (exercise / "hints.md").write_text("# Hints\nthe generator's reading\n")
    (exercise / "sample_input" / "numbers.txt").write_text("2 3\n")
    (exercise / "sample_output" / "part-00000").write_text("5\n")
    (solution / "skeleton.py").write_text(SKELETON)
    (solution / "metadata.json").write_text(json.dumps({
        "id": "demo", "framework": "python", "entry_point": ["add"],
        "source": {"lecture": "L2.2", "slides": "2.3-2.4", "pdf_pages": pdf_pages}}))
    entries = [cache.Entry(n, f"2.{n}", "text", "h", f"page {n} body") for n in range(1, 6)]
    path = workspace / "cache" / "lectures" / "Lecture_2.2-Demo.pdf.md"
    path.parent.mkdir(parents=True)
    path.write_text(cache.render("Lecture_2.2-Demo.pdf", entries))
    return workspace, batch


class FakeModel:
    """Answers each kind of request (told apart by its task text) with the next reply
    listed for that kind, and records every request."""

    def __init__(self, correct=(), slides=(), wrong=()):
        self.replies = {"correct": list(correct), "slides": list(slides), "wrong": list(wrong)}
        self.calls = []

    def __call__(self, config, messages, **kwargs):
        kind = next(k for k, task in TASKS.items() if task in messages[1]["content"])
        self.calls.append((kind, messages))
        return llm.Reply(self.replies[kind].pop(0), 100, 50, 0.1)


def correct(code=CORRECT):
    return json.dumps({"code": code})


def slides(code=SLIDES, differs="the slides round first"):
    return json.dumps({"differs": differs, "code": code})


def wrong(code, mistake):
    return json.dumps({"mistake": mistake, "code": code})


MISTAKES = ["subtracts instead of adding", "multiplies the two numbers",
            "returns the larger number only"]


def three_wrong():
    return [wrong(code, mistake) for code, mistake in zip(WRONG, MISTAKES, strict=True)]


def every_kind():
    return FakeModel([correct()], [slides()], three_wrong())


def writer(model):
    return Writer(llm.LLMConfig("fake", "openai", "http://x", "fake-1"), chat=model)


@pytest.mark.parametrize(("pdf_pages", "lectures", "expected"), [
    ("35-38, 43", ["2.2"], [("2.2", {35, 36, 37, 38, 43})]),
    ("p.13-p.15, p.19", ["4.2"], [("4.2", {13, 14, 15, 19})]),
    ("36–41", ["4.1"], [("4.1", set(range(36, 42)))]),  # an en dash
    ("L2.1 13-14; L2.2 18", ["2.1", "2.2"], [("2.1", {13, 14}), ("2.2", {18})]),
    ("13-14", ["2.1", "2.2"], []),  # two lectures, no label: whose pages is unknown
])
def test_page_groups_read_every_form_the_batches_use(pdf_pages, lectures, expected):
    assert page_groups(pdf_pages, lectures) == expected


def test_a_lecture_number_matches_its_cache_file_exactly(tmp_path):
    lectures = tmp_path / "cache" / "lectures"
    lectures.mkdir(parents=True)
    for name in ("Lecture_5-Spark_III.pdf.md", "Lecture_5.2-Spark_IV.pdf.md"):
        (lectures / name).write_text("")

    assert lecture_file(tmp_path, "5").name == "Lecture_5-Spark_III.pdf.md"
    assert lecture_file(tmp_path, "5.2").name == "Lecture_5.2-Spark_IV.pdf.md"
    assert lecture_file(tmp_path, "6") is None


def test_a_lecture_that_fits_goes_whole_with_the_exercises_pages_marked(tmp_path):
    workspace, _ = make_batch(tmp_path)
    source = {"lecture": "L2.2", "pdf_pages": "3-4"}

    whole = lecture_context(workspace, source)
    assert "page 1 body" in whole and "page 5 body" in whole
    assert "p.3 · slide 2.3 · this exercise's page" in whole
    assert "p.2 · slide 2.2\n" in whole  # not marked

    cut = lecture_context(workspace, source, limit=10)  # too long: own pages only
    assert "page 3 body" in cut and "page 4 body" in cut and "page 1 body" not in cut
    assert lecture_context(workspace, "synthetic") == ""


def test_the_model_sees_question_skeleton_data_and_slides_only(tmp_path):
    """Never the owner's student.py, the tests, the hints or a by-hand run's output."""
    workspace, batch = make_batch(tmp_path)
    inputs = gather(batch, "demo", workspace)
    prompt = user_prompt(inputs)

    assert "Add `a` and `b`." in prompt and "raise NotImplementedError" in prompt
    assert "page 3 body" in prompt
    assert inputs.data == {"sample_input/numbers.txt": "2 3\n"}
    for hidden in ("the owner's answer", "def test_add", "the generator's reading"):
        assert hidden not in prompt


def test_one_call_per_file_and_each_saved_with_its_note(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = every_kind()

    outcome = write(batch, "demo", workspace, writer(model))

    out = batch / "solutions" / "demo" / "heldout" / "model"
    assert outcome == "written: correct, slides, 3 wrong"
    assert [kind for kind, _ in model.calls] == ["correct", "slides", "wrong", "wrong", "wrong"]
    assert sorted(p.name for p in out.glob("*.py")) == [
        "correct.py", "slides.py", "wrong_01.py", "wrong_02.py", "wrong_03.py"]
    assert (out / "wrong_02.py").read_text() == WRONG[1]
    meta = json.loads((out / "meta.json").read_text())
    assert meta["notes"] == {"slides.py": "the slides round first", "wrong_01.py": MISTAKES[0],
                             "wrong_02.py": MISTAKES[1], "wrong_03.py": MISTAKES[2]}
    assert meta["model"] == "fake: fake-1" and meta["dropped"] == {}
    assert meta["tokens"] == {"input": 500, "output": 250}


def test_each_wrong_one_is_told_the_mistakes_before_it(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = every_kind()
    write(batch, "demo", workspace, writer(model))

    asked = [messages[1]["content"] for kind, messages in model.calls if kind == "wrong"]
    assert "already written" not in asked[0]
    assert asked[2].endswith(f"make a different one: {MISTAKES[0]}; {MISTAKES[1]}")
    # Everything before the task is the same in every call: one cached prefix.
    prefixes = {messages[1]["content"].split("Your task:")[0] for _, messages in model.calls}
    assert len(prefixes) == 1 and len({m[0]["content"] for _, m in model.calls}) == 1


def test_slides_without_a_procedure_write_no_file(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = FakeModel([correct()], [slides(code="", differs="")],
                      three_wrong())

    assert write(batch, "demo", workspace, writer(model)) == "written: correct, 3 wrong"


def test_an_unsafe_file_is_sent_back_and_dropped_if_it_stays_unsafe(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = FakeModel([correct()], [slides()],
                      [wrong(WRONG[0], "m1"), *[wrong(UNSAFE, "m2")] * 3, wrong(WRONG[2], "m3")])

    outcome = write(batch, "demo", workspace, writer(model))

    second = [messages for kind, messages in model.calls if kind == "wrong"][2]
    assert "That reply has problems: line 1: import of 'subprocess'" in second[-1]["content"]
    assert outcome == "written: correct, slides, 2 wrong; dropped wrong_02.py"
    meta = json.loads((batch / "solutions/demo/heldout/model/meta.json").read_text())
    assert "import of 'subprocess'" in meta["dropped"]["wrong_02.py"]
    # The next file is numbered by its place in the plan, and told only the kept mistakes.
    assert (batch / "solutions/demo/heldout/model/wrong_03.py").read_text() == WRONG[2]


def test_a_fixed_reply_replaces_the_unsafe_one(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = FakeModel([correct(UNSAFE), correct()], [slides()],
                      three_wrong())

    assert write(batch, "demo", workspace, writer(model)) == "written: correct, slides, 3 wrong"
    assert [kind for kind, _ in model.calls][:2] == ["correct", "correct"]


def test_unchanged_inputs_keep_the_files_without_a_call(tmp_path):
    workspace, batch = make_batch(tmp_path)
    write(batch, "demo", workspace, writer(every_kind()))

    no_calls = FakeModel()  # would fail on any call: it has no replies
    assert write(batch, "demo", workspace, writer(no_calls)) == "kept: inputs unchanged"
    assert write(batch, "demo", workspace, writer(every_kind()), force=True).startswith("written")
    other = Writer(llm.LLMConfig("other", "openai", "http://x", "other-1"), chat=every_kind())
    assert write(batch, "demo", workspace, other).startswith("written")  # another model


def test_nothing_usable_writes_nothing(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = FakeModel(["not json"] * 3, ["{}"] * 3, ["[]"] * 9)

    with pytest.raises(HeldOutWriteError, match="does not fit the JSON schema"):
        write(batch, "demo", workspace, writer(model))
    assert not (batch / "solutions/demo/heldout").exists()


def test_a_candidate_must_define_what_the_tests_import(tmp_path):
    workspace, batch = make_batch(tmp_path)
    inputs = gather(batch, "demo", workspace)

    assert code_problems("def total(a, b):\n    return a + b\n", inputs, frozenset()) == [
        "does not define add"]
    assert code_problems("def add(a, b:\n", inputs, frozenset())[0].startswith("syntax error")


@pytest.mark.parametrize("reply", [CORRECT, f"```python\n{CORRECT}```", f"```\n{CORRECT}\n```\n"])
def test_a_markdown_fence_around_the_code_is_taken_off(reply):
    assert unfence(reply).strip() == CORRECT.strip()


@pytest.mark.parametrize("code", [
    "def add(a, b):\n    ...\n",
    "def add(a, b):\n    \"\"\"Add.\"\"\"\n    pass\n",
    "def add(a, b):\n    raise NotImplementedError\n",
    # Newlines lost, as GLM-5.3 once did: the body is the name `n`, the rest a comment.
    "def add(a, b) -> int:n    # add themn    return a + bn\n",
])
def test_a_function_that_does_nothing_is_a_problem(tmp_path, code):
    workspace, batch = make_batch(tmp_path)
    inputs = gather(batch, "demo", workspace)

    assert code_problems(code, inputs, frozenset()) == ["add has no body: write it out in full"]
    assert code_problems(CORRECT, inputs, frozenset()) == []


def test_the_same_mistake_again_is_sent_back(tmp_path):
    workspace, batch = make_batch(tmp_path)
    model = FakeModel([correct()], [slides()],
                      [wrong(WRONG[0], "subtracts instead of adding, wrong total"),
                       wrong(WRONG[1], "Subtracts instead of adding, wrong total."),
                       wrong(WRONG[1], "multiplies, wrong total"),
                       wrong(WRONG[2], "takes the larger one, wrong total")])

    assert write(batch, "demo", workspace, writer(model)) == "written: correct, slides, 3 wrong"
    retry = [messages for kind, messages in model.calls if kind == "wrong"][2]
    assert "the same mistake as one already written" in retry[-1]["content"]
