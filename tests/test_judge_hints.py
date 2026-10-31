"""The hint-quality judge (tutor/judge/hints.py), with fake models and fake test runs:
simulated students, what the hint model may see, and calibration against the owner."""

import json

from tutor import llm
from tutor.execution import Outcome, RunResult
from tutor.hint.llm_hint import HintModel
from tutor.judge.hints import (
    HINTS_FILE,
    LABELS_FILE,
    judged,
    report,
    scores,
    simulate,
    without_docstring,
)
from tutor.judge.teaching import Judge

TESTS = "from student import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
REFERENCE = "def add(a, b):\n    return a + b\n"
MUTANTS = {
    "m01_sub": '"""Mutant: subtracts instead of adding, so 2 + 3 gives -1."""\n\n'
               "def add(a, b):\n    return a - b\n",
    "m02_mul": '"""Mutant: multiplies, so add(2, 2) is right by luck."""\n\n'
               "def add(a, b):\n    return a * b\n",
    "m03_max": '"""Mutant: returns the larger number."""\n\n'
               "def add(a, b):\n    return max(a, b)\n",
}


def make_batch(tmp_path):
    workspace = tmp_path / "ws"
    batch = workspace / "batches" / "b1"
    exercise, solution = batch / "exercises" / "demo", batch / "solutions" / "demo"
    (solution / "mutants").mkdir(parents=True)
    exercise.mkdir(parents=True)
    (batch / "manifest.json").write_text(json.dumps({"mode": "learn"}))
    (exercise / "test_student.py").write_text(TESTS)
    (exercise / "hints.md").write_text("# Hints\n")
    (exercise / "student.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
    (solution / "metadata.json").write_text(json.dumps({"id": "demo", "framework": "python"}))
    (solution / "reference.py").write_text(REFERENCE)
    (solution / "skeleton.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
    for name, code in MUTANTS.items():
        (solution / "mutants" / f"{name}.py").write_text(code)
    return workspace, batch


def fake_tests(files, timeout, env, heavy=True):
    """m02 (a * b) passes this fake run, as a mutant caught only elsewhere would."""
    code = files["student.py"].read_text()
    if "a * b" in code:
        return RunResult(Outcome.PASSED, 0, 0.1, "1 passed")
    return RunResult(Outcome.FAILED, 1, 0.1,
                     "____ test_add ____\ntest_student.py:4: in test_add\nE   assert -1 == 5\n")


class FakeChat:
    """Answers with the given replies in turn: only for calls made one after another."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, config, messages, **kwargs):
        self.calls.append(messages)
        return llm.Reply(self.replies.pop(0), 100, 20, 0.1)


class KeyedChat:
    """Answers by what the request contains: simulate() and judged() call the model from
    several threads, so the order of the calls is not fixed (CI got them the other way
    round, 2026-10-31) and replies handed out in turn went to the wrong hint."""

    def __init__(self, replies: dict[str, str]):
        self.replies, self.calls = replies, []

    def __call__(self, config, messages, **kwargs):
        self.calls.append(messages)
        sent = json.dumps(messages)
        reply = next(text for key, text in self.replies.items() if key in sent)
        return llm.Reply(reply, 100, 20, 0.1)


def config(name="fake"):
    return llm.LLMConfig(name, "openai", "http://x", f"{name}-1")


def hint_reply(text="Look at how add combines its two numbers."):
    return json.dumps({"diagnosis": "subtracts", "hint": text})


def test_a_mutants_docstring_is_removed_and_nothing_else():
    assert without_docstring(MUTANTS["m01_sub"]) == "def add(a, b):\n    return a - b\n"
    assert without_docstring(REFERENCE) == REFERENCE


def test_mutants_play_students_and_the_hint_model_never_reads_their_bug(tmp_path):
    workspace, batch = make_batch(tmp_path)
    chat = KeyedChat({"a - b": hint_reply(),
                      "max(a, b)": hint_reply("Compare the result with the largest input.")})
    out = tmp_path / "out"

    found = simulate(batch, ["demo"], 3, HintModel(config(), chat=chat), out,
                     run_tests=fake_tests)

    assert [h["id"] for h in found] == ["demo/m01_sub@L1", "demo/m03_max@L1"]  # m02 passed
    assert found[0]["bug"] == "subtracts instead of adding"
    assert found[0]["effect"].startswith("2 + 3 gives -1")
    assert found[0]["hint"] == "Look at how add combines its two numbers."
    assert found[1]["hint"] == "Compare the result with the largest input."
    assert "assert -1 == 5" in found[0]["first_error"]
    sent = json.dumps(chat.calls)
    assert "Mutant:" not in sent and "subtracts instead" not in sent
    assert (out / "students" / "demo__m01_sub.py").read_text().startswith("def add")
    assert len((out / HINTS_FILE).read_text().splitlines()) == 2
    sheet = (out / "labels.md").read_text()
    assert "## 1. demo/m01_sub@L1" in sheet and "Real bug: subtracts instead of adding" in sheet


def test_a_hint_the_leak_guard_refuses_is_kept_as_refused(tmp_path):
    workspace, batch = make_batch(tmp_path)
    leaky = hint_reply("Write:\n```python\ndef add(a, b):\n    return a + b\n```")
    chat = FakeChat(leaky, leaky)  # rejected twice by the guard

    [h] = simulate(batch, ["demo"], 1, HintModel(config(), chat=chat), tmp_path / "out",
                   run_tests=fake_tests)

    assert h["hint"] == "" and h["refused"].startswith("blocked")


def verdict(leaks, direction):
    return json.dumps({"leaks": {"evidence": "quote", "yes": leaks},
                       "direction": {"evidence": "quote", "yes": direction}})


def test_the_judge_is_scored_against_the_owners_labels(tmp_path):
    workspace, batch = make_batch(tmp_path)
    out = tmp_path / "out"
    simulate(batch, ["demo"], 3, HintModel(config(), chat=KeyedChat({"def add": hint_reply()})),
             out, run_tests=fake_tests)
    (out / LABELS_FILE).write_text(
        json.dumps({"id": "demo/m01_sub@L1", "leaks": False, "direction": True}) + "\n"
        + json.dumps({"id": "demo/m03_max@L1", "leaks": True, "direction": False}) + "\n")
    chat = KeyedChat({"subtracts instead of adding": verdict(False, True),
                      "returns the larger number": verdict(False, False)})

    results = judged(out, workspace, Judge(config("judge"), chat=chat))

    by_question = {s.point: s for s in scores(results)}
    assert (by_question["leaks"].n_labels, by_question["leaks"].found) == (1, 0)  # missed
    assert (by_question["direction"].n_labels, by_question["direction"].found) == (1, 1)
    assert by_question["direction"].false_n == 0
    assert "demo/m03_max@L1: leaks: owner yes, judge no: quote" in report(results)
    assert "The real bug: returns the larger number" in json.dumps(chat.calls)  # the judge knows it
    again = judged(out, workspace, Judge(config("judge"), chat=FakeChat()))  # reused, no call
    assert [v.model_dump() for _, _, v in again] == [v.model_dump() for _, _, v in results]
