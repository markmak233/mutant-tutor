"""The hint command: rules first, then the exercise's known mistakes (its mutants)."""

import json

import pytest

from tutor.execution import run_pytest
from tutor.hint import mutants
from tutor.hint.__main__ import hint_for, log_hint
from tutor.hint.rules import first_rule

TESTS = (
    "from student import total\n\n\n"
    "def test_example():\n    assert total([1, 2, 3]) == 6\n\n\n"
    "def test_empty():\n    assert total([]) == 0\n\n\n"
    "def test_negative():\n    assert total([-1, 1]) == 0\n"
)
MUTANTS = {
    "m01_skip_first": '"""Mutant: starts at the second element, so the first value is lost."""\n'
                      "def total(xs):\n    return sum(xs[1:])\n",
    "m02_plus_one": '"""Mutant: starts the running total at 1, every total is one too big."""\n'
                    "def total(xs):\n    return sum(xs) + 1\n",
}
# The first mutant's mistake, written the way a student would.
SKIPS_FIRST = "def total(xs):\n    t = 0\n    for x in xs[1:]:\n        t += x\n    return t\n"


def batch(tmp_path, student, mode="learn", constraints=None):
    """A batch with one exercise, laid out as the pipeline saves it."""
    root = tmp_path / "batches" / "2026-10-29_p3-demo"
    exercise, solution = root / "exercises" / "sum-list", root / "solutions" / "sum-list"
    (solution / "mutants").mkdir(parents=True)
    exercise.mkdir(parents=True)
    (exercise / "test_student.py").write_text(TESTS, encoding="utf-8")
    (exercise / "student.py").write_text(student, encoding="utf-8")
    (exercise / "hints.md").write_text("## Hint 1\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")
    (solution / "metadata.json").write_text(json.dumps(
        {"id": "sum-list", "constraints": constraints or {}}), encoding="utf-8")
    (solution / "reference.py").write_text("def total(xs):\n    return sum(xs)\n", encoding="utf-8")
    (solution / "skeleton.py").write_text("def total(xs):\n    raise NotImplementedError\n",
                                          encoding="utf-8")
    for name, code in MUTANTS.items():
        (solution / "mutants" / f"{name}.py").write_text(code, encoding="utf-8")
    return exercise


class CountingRuns:
    def __init__(self):
        self.calls = 0

    def __call__(self, files, timeout, env):
        self.calls += 1
        return run_pytest(files, timeout, env)


def test_a_student_who_makes_a_known_mistake_is_told_that_mistake(tmp_path):
    runs = CountingRuns()
    hint = hint_for(batch(tmp_path, SKIPS_FIRST), run_tests=runs, say=lambda _: None)
    assert (hint.layer, hint.key) == ("mutant", "m01_skip_first")
    assert hint.text == ("your tests fail exactly as a known mistake: starts at the second "
                         "element, so the first value is lost")
    assert runs.calls == 3  # the student, then each mutant once


def test_mutant_signatures_are_computed_once(tmp_path, symlink):
    exercise = batch(tmp_path, SKIPS_FIRST)
    hint_for(exercise, say=lambda _: None)
    runs = CountingRuns()
    assert hint_for(exercise, run_tests=runs, say=lambda _: None).key == "m01_skip_first"
    assert runs.calls == 1  # only the student's run; the mutants come from the cache
    (exercise.parent.parent / "solutions" / "sum-list" / "mutants" / "m02_plus_one.py"
     ).write_text(MUTANTS["m02_plus_one"].replace("+ 1", "+ 2"), encoding="utf-8")
    runs = CountingRuns()
    hint_for(exercise, run_tests=runs, say=lambda _: None)
    assert runs.calls == 2  # the changed mutant runs again
    (exercise / "data").mkdir()
    (exercise / "data" / "input.csv").write_text("1,2\n", encoding="utf-8")
    runs = CountingRuns()
    hint_for(exercise, run_tests=runs, say=lambda _: None)
    assert runs.calls == 3  # new test data: every mutant runs again
    (exercise / "data" / "input.csv").write_text("1,2,3\n", encoding="utf-8")
    runs = CountingRuns()
    hint_for(exercise, run_tests=runs, say=lambda _: None)
    assert runs.calls == 3  # changed test data too
    linked = tmp_path / "shared"
    linked.mkdir()
    (linked / "more.csv").write_text("4\n", encoding="utf-8")
    symlink(exercise / "data" / "shared", linked, target_is_directory=True)
    hint_for(exercise, say=lambda _: None)
    (linked / "more.csv").write_text("5\n", encoding="utf-8")
    runs = CountingRuns()
    hint_for(exercise, run_tests=runs, say=lambda _: None)
    assert runs.calls == 3  # data behind a link the run copies


def test_a_review_batch_hears_only_what_goes_wrong(tmp_path):
    hint = hint_for(batch(tmp_path, SKIPS_FIRST, mode="review"), say=lambda _: None)
    assert hint.text == ("your tests fail exactly as a known mistake, where the first value "
                         "is lost; look for the cause")
    assert "second element" not in hint.text


def test_rules_come_before_known_mistakes(tmp_path):
    hint = hint_for(batch(tmp_path, SKIPS_FIRST.replace("t += x", "t += x，")),
                    say=lambda _: None)
    assert (hint.layer, hint.key) == ("rule", "full-width")
    assert "line 4: '，' should be ','" in hint.text
    todo = hint_for(batch(tmp_path / "b", "def total(xs):\n    raise NotImplementedError\n"),
                    say=lambda _: None)
    assert todo.key == "todo-left"


def test_full_width_punctuation_in_comments_and_strings_is_not_a_mistake(tmp_path):
    commented = SKIPS_FIRST.replace("t = 0", "t = 0  # 总和，从零开始")
    commented = '"""求和：返回总数。"""\n' + commented
    assert hint_for(batch(tmp_path, commented), say=lambda _: None).key == "m01_skip_first"


def test_a_forbidden_api_is_named(tmp_path):
    hint = hint_for(batch(tmp_path, "def total(xs):\n    return sum(xs) - 1\n",
                          constraints={"forbidden_calls": ["sum"]}), say=lambda _: None)
    assert hint.key == "forbidden-api" and "uses sum" in hint.text


def test_passing_code_gets_no_hint_and_unknown_failures_show_the_first_error(tmp_path):
    assert hint_for(batch(tmp_path, "def total(xs):\n    return sum(xs)\n")) is None
    only_example = "def total(xs):\n    return 0 if len(xs) == 3 else sum(xs)\n"
    hint = hint_for(batch(tmp_path / "b", only_example), say=lambda _: None)
    assert hint.layer == "none" and "test_example" in hint.text and "hints.md" in hint.text
    # Failing the same tests as a mutant, with other values, is offered as a guess.
    guess = hint_for(batch(tmp_path / "c", "def total(xs):\n    return len(xs)\n"),
                     say=lambda _: None)
    assert guess.key == "m01_skip_first" and "it may be that one" in guess.text


@pytest.mark.parametrize(("output", "key"), [
    ("E   py4j.protocol.Py4JJavaError: ... PYTHON_VERSION_MISMATCH ...", "python-version"),
    ("E   TypeError: 'PipelinedRDD' object is not iterable", "rdd-on-driver"),
    ("E   ValueError: Cannot run multiple SparkContexts at once", "spark-context-left"),
    ("E     unexpected: [\"('a', 1)\"]", "tuple-output"),
    ("E   AssertionError: no output was saved to outputPath itself: ...", "not-saved"),
])
def test_rules_recognise_spark_and_output_mistakes(output, key):
    hint = first_rule("x = 1\n", output, {}, "learn")
    assert hint.key == key
    assert first_rule("x = 1\n", output, {}, "review").text != hint.text  # shorter


def test_mutants_the_tests_cannot_tell_apart_are_reported():
    same = {"test_a": ["assert 1 == 2"]}
    assert mutants.indistinguishable({"m01": same, "m02": dict(same), "m03": {}}) == [
        ("m01", "m02")]


def test_signatures_ignore_addresses_and_timings():
    one = "___ test_a ___\nE   AssertionError: <obj at 0x10a2b3> took 1.25s\n"
    two = "___ test_a ___\nE   AssertionError: <obj at 0x99ffee> took 3.50s\n"
    assert mutants.signature(one) == mutants.signature(two) == {
        "test_a": ["AssertionError: <obj at <addr>> took <s>"]}


def test_a_hint_in_a_workspace_batch_is_logged(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    exercise = batch(tmp_path, SKIPS_FIRST)
    log_hint(exercise, hint_for(exercise, say=lambda _: None))
    line = json.loads((tmp_path / "attempts" / "attempts.jsonl").read_text(encoding="utf-8"))
    assert (line["event"], line["layer"], line["key"], line["exercise"]) == (
        "hint", "mutant", "m01_skip_first", "sum-list")


def test_mistakes_the_tests_cannot_tell_apart_are_all_named(tmp_path):
    exercise = batch(tmp_path, SKIPS_FIRST)
    twin = exercise.parent.parent / "solutions" / "sum-list" / "mutants" / "m03_drop_head.py"
    twin.write_text('"""Mutant: drops the head of the list, so the first value is lost."""\n'
                    "def total(xs):\n    return sum(xs[1:])\n", encoding="utf-8")
    hint = hint_for(exercise, say=lambda _: None)
    assert hint.key == "m01_skip_first|m03_drop_head"
    assert "one of: starts at the second element; drops the head of the list" in hint.text


def test_a_guess_prefers_the_mutant_whose_failures_read_most_alike():
    student = {"test_a": ["assert ['b', 'a'] == ['a', 'b']"]}
    known = {"m01_far": {"test_a": ["KeyError: 'x'"]},
             "m02_near": {"test_a": ["assert ['a', 'b', 'c'] == ['a', 'b']"]}}
    assert [m.mutant for m in mutants.matches(student, known)] == ["m02_near", "m01_far"]


# --- layer 3: a model's hint ----------------------------------------------------------------

# Fails test_example only, which no mutant does: layers 1 and 2 have nothing to say.
UNKNOWN = "def total(xs):\n    return 0 if len(xs) == 3 else sum(xs)\n"


class HintChat:
    """Replies from a queue; keeps every request's messages."""

    def __init__(self, *hints: str):
        self.replies = [json.dumps({"diagnosis": "returns 0 for three items", "hint": h})
                        for h in hints]
        self.calls = []

    def __call__(self, config, messages, *, purpose, log_path, max_tokens, response_schema):
        from tutor.llm import Reply
        self.calls.append(list(messages))
        return Reply(self.replies.pop(0), 900, 80, 0.1, "stop")


def model_of(chat):
    from tutor.hint.llm_hint import HintModel
    from tutor.llm import LLMConfig
    return HintModel(LLMConfig("deepseek", "deepseek", "http://x", "deepseek-v4-flash"), chat)


def test_a_model_hint_sees_what_the_student_sees_and_never_the_solution(tmp_path):
    exercise = batch(tmp_path, UNKNOWN)
    chat = HintChat("Check what your function does for a list of exactly three numbers.")
    hint = hint_for(exercise, say=lambda _: None, model=model_of(chat))
    assert (hint.layer, hint.key) == ("llm", "level-1")
    assert hint.text == "Check what your function does for a list of exactly three numbers."
    assert "returns 0" not in hint.text  # the diagnosis is never shown
    system, user = (m["content"] for m in chat.calls[0])
    assert "Never write the solution" in system and "No code blocks at all" in system
    assert "0 if len(xs) == 3" in user and "## Hint 1" in user and "test_example" in user
    assert "return sum(xs)\n" not in user.replace(UNKNOWN, "")  # the reference stays out


def test_a_hint_that_gives_code_away_is_asked_for_again_with_the_reason(tmp_path):
    chat = HintChat("Use this:\n```python\ndef total(xs):\n    return sum(xs)\n```",
                    "A special case for three items hides the general rule.")
    hint = hint_for(batch(tmp_path, UNKNOWN), say=lambda _: None, model=model_of(chat))
    assert hint.text == "A special case for three items hides the general rule."
    assert "code block of 2 lines" in chat.calls[1][-1]["content"]


def test_a_hint_that_gives_code_away_twice_falls_back_to_the_first_error(tmp_path):
    sneaky = "Like this:\n```python\nt = 0\nfor x in xs:\n    t += x\n```"
    chat = HintChat(sneaky, sneaky)
    hint = hint_for(batch(tmp_path, UNKNOWN), say=lambda _: None, model=model_of(chat))
    assert (hint.layer, hint.key) == ("none", "llm-blocked")
    assert "test_example" in hint.text and "t += x" not in hint.text


def test_code_close_to_the_solution_is_caught_even_with_other_names():
    from tutor.hint.llm_hint import leak
    reference = "def top(rdd):\n    return rdd.reduceByKey(lambda a, b: a + b).collect()\n"
    hint = "Try `rdd.reduceByKey(lambda x, y: x + y).collect()`."
    assert "close to the solution" in leak(hint, reference, visible="", allowed=0)
    assert leak("Think about `reduceByKey` versus `groupByKey`.", reference, "", 0) is None


@pytest.mark.parametrize(("mode", "level", "allowed"), [
    ("learn", 3, True), ("learn", 2, False), ("review", 3, False)])
def test_only_level_3_of_a_learn_batch_may_show_a_corrected_line(mode, level, allowed):
    from tutor.hint.llm_hint import allowed_lines, leak, system_prompt
    one_line = "Fix the format:\n```python\nprint(f'{k}\\t{v}')\n```"
    reason = leak(one_line, "def total(xs):\n    return sum(xs)\n", "", allowed_lines(mode,
                                                                                    level))
    assert (reason is None) is allowed
    assert ("Never show code" in system_prompt(mode, level)) is (mode == "review")


def test_a_line_the_student_already_has_may_be_quoted():
    from tutor.hint.llm_hint import leak
    reference = "def total(xs):\n    result = sorted(xs)[::-1]\n    return result\n"
    hint = "Your line `result = sorted(xs)[::-1]` is fine; look at what you return."
    assert leak(hint, reference, visible=reference, allowed=0) is None
    assert "repeats a line" in leak(hint, reference, visible="", allowed=0)


def test_java_stack_frames_are_left_out_of_the_error():
    from tutor.hint.llm_hint import first_error
    output = ("___ test_a ___\nE   py4j.Py4JJavaError: boom\n"
              "E   \tat org.apache.Spark.run(X.java:1)\nE   ... 12 more\n")
    assert first_error(output).splitlines() == ["test_a", "E   py4j.Py4JJavaError: boom"]


def test_asking_again_with_the_same_code_raises_the_level(tmp_path, monkeypatch):
    from tutor.hint.__main__ import model_hints_given
    (tmp_path / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    exercise = batch(tmp_path, UNKNOWN)
    systems = []
    for n in (1, 2):
        chat = HintChat(f"hint {n}")
        level = 1 + model_hints_given(exercise)
        log_hint(exercise, hint_for(exercise, say=lambda _: None, model=model_of(chat),
                                    level=level))
        systems.append(chat.calls[0][0]["content"])
    assert "Level 1:" in systems[0] and "Level 2" in systems[1]
    assert model_hints_given(exercise) == 2
    (exercise / "student.py").write_text(UNKNOWN + "# tried something\n", encoding="utf-8")
    assert model_hints_given(exercise) == 0  # changed code starts again at level 1
