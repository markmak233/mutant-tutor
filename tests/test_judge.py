"""The teaching-quality judge (tutor/judge/), with a fake model: what it is shown, how its
verdicts are kept, and how calibration scores them against the owner's ratings."""

import json

import pytest

from tutor import llm
from tutor.judge.calibrate import (
    Item,
    blind_items,
    daily_items,
    defect_items,
    judge_flags,
    judged,
    owner_letters,
    precision,
    read_reviews,
    report,
    scores,
)
from tutor.judge.teaching import (
    POINTS,
    Judge,
    JudgeError,
    Verdict,
    gather,
    user_prompt,
    write,
)
from tutor.materials import cache
from tutor.materials.slides import lectures_taught


def make_workspace(tmp_path):
    """A workspace with three cached lectures and a review batch of one exercise."""
    workspace = tmp_path / "ws"
    lectures = workspace / "cache" / "lectures"
    lectures.mkdir(parents=True)
    for name in ("Lecture_2.1-Intro", "Lecture_2.2-Demo", "Lecture_3.1-Later"):
        entries = [cache.Entry(n, f"{name[8:11]}.{n}", "text", "h", f"{name} page {n}")
                   for n in range(1, 4)]
        (lectures / f"{name}.pdf.md").write_text(cache.render(f"{name}.pdf", entries))
    batch = workspace / "batches" / "b1"
    exercise, solution = batch / "exercises" / "demo", batch / "solutions" / "demo"
    exercise.mkdir(parents=True)
    solution.mkdir(parents=True)
    (batch / "manifest.json").write_text(json.dumps({"mode": "review"}))
    (exercise / "question.md").write_text("# Add\n\nAdd `a` and `b`.\n")
    (exercise / "student.py").write_text("def add(a, b):\n    return a + b  # the owner's\n")
    (exercise / "test_student.py").write_text("def test_add():\n    assert True\n")
    (exercise / "hints.md").write_text(
        "# Hints\n\n## Useful functions\n\n```python\nsum([1, 2])\n## not a heading in code\n"
        "```\n\n## Level 1\n\nthe hint text\n")
    (solution / "skeleton.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
    (solution / "reference.py").write_text("def add(a, b):\n    return sum([a, b])\n")
    (solution / "metadata.json").write_text(json.dumps({
        "id": "demo", "source": {"lecture": "L2.2", "pdf_pages": "2"}}))
    return workspace, batch


def verdict_json(letters="yyyy"):
    """"y" fine, "n" a serious flag, "m" a minor one."""
    return json.dumps({p: {"evidence": f"{p} evidence", "ok": letter == "y",
                           "serious": letter == "n"}
                       for p, letter in zip(POINTS, letters, strict=True)})


class FakeModel:
    """Answers with the given replies in turn; records the messages."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, config, messages, **kwargs):
        self.calls.append(messages)
        return llm.Reply(self.replies.pop(0), 1000, 100, 0.1)


def judge_with(model, name="fake"):
    return Judge(llm.LLMConfig(name, "openai", "http://x", f"{name}-1"), chat=model)


def test_the_judge_sees_what_the_student_reads_and_the_reference(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    prompt = user_prompt(gather(batch, "demo", workspace))

    assert "Add `a` and `b`." in prompt and "raise NotImplementedError" in prompt
    assert "return sum([a, b])" in prompt  # the reference, for "targets"
    assert "Batch mode: review" in prompt
    assert "taught so far, in order: Lecture_2.1-Intro, Lecture_2.2-Demo\n" in prompt
    assert "p.2 · slide 2.2.2 · this exercise's page" in prompt
    assert "## Useful functions" in prompt and "## not a heading in code" in prompt
    for hidden in ("the owner's", "def test_add", "the hint text", "Lecture_3.1-Later page"):
        assert hidden not in prompt


def test_a_rated_text_can_stand_in_for_question_md(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    assert gather(batch, "demo", workspace, question="the rated version").question == (
        "the rated version")


def test_lectures_taught_follow_the_course_order(tmp_path):
    lectures = tmp_path / "cache" / "lectures"
    lectures.mkdir(parents=True)
    for name in ("Lecture_10-X", "Lecture_5.2-B", "Lecture_5-A", "Lecture_9-C"):
        (lectures / f"{name}.pdf.md").write_text("")

    assert lectures_taught(tmp_path, {"lecture": "L9"}) == [
        "Lecture_5-A", "Lecture_5.2-B", "Lecture_9-C"]  # 10 is later, though "10" < "9"
    assert lectures_taught(tmp_path, "synthetic") == []


def test_a_verdict_is_saved_and_kept_while_nothing_changes(tmp_path):
    workspace, batch = make_workspace(tmp_path)

    letters, verdict = write(batch, "demo", workspace, judge_with(FakeModel(verdict_json("nyyy"))))

    assert letters == "nyyy" and verdict.clear.evidence == "clear evidence"
    data = json.loads((batch / "solutions/demo/quality.json").read_text())
    assert data["letters"] == "nyyy" and data["model"] == "fake: fake-1"
    assert data["points"]["clear"] == {"evidence": "clear evidence", "ok": False,
                                       "serious": True}
    assert data["serious"] == ["clear"]
    assert write(batch, "demo", workspace, judge_with(FakeModel())) == ("kept: nyyy", None)
    again = write(batch, "demo", workspace, judge_with(FakeModel(verdict_json())), force=True)
    assert again[0] == "yyyy"
    other = write(batch, "demo", workspace, judge_with(FakeModel(verdict_json()), "other"))
    assert other[0] == "yyyy"  # another model judges afresh


def test_a_reply_that_never_fits_the_schema_raises(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    model = FakeModel("not json", "{}", '{"clear": 1}')

    with pytest.raises(JudgeError, match="does not fit the JSON schema"):
        write(batch, "demo", workspace, judge_with(model))
    assert len(model.calls) == 3
    assert "That reply has problems" in model.calls[1][-1]["content"]


# --- calibration ------------------------------------------------------------------------


def item(name, owner, skip=()):
    labels = {p: letter == "y" for p, letter in zip(POINTS, owner, strict=True) if p not in skip}
    return Item(name, None, "demo", labels)


def verdict(letters):
    return Verdict.model_validate_json(verdict_json(letters))


def test_a_point_is_trusted_only_if_it_finds_the_owners_n_without_false_ones():
    pairs = [
        (item("a", "nyyy"), verdict("nyyy")),  # clear: found
        (item("b", "nyyy"), verdict("nyyy")),  # found
        (item("c", "yyyy"), verdict("ynyy")),  # fits: a false n
        (item("d", "yyyy"), verdict("yyyy")),
        (item("e", "yyyy"), verdict("yyyy")),
    ]
    by_point = {s.point: s for s in scores(pairs)}

    assert (by_point["clear"].n_labels, by_point["clear"].found) == (2, 2)
    assert by_point["clear"].status == "trusted"
    assert (by_point["fits"].y_labels, by_point["fits"].false_n) == (5, 1)
    assert by_point["fits"].status == "uncalibrated"  # the owner never said n: unchecked
    missed = scores([(item("a", "nyyy"), verdict("yyyy")), (item("b", "nyyy"), verdict("nyyy"))])
    assert missed[0].status == "not trusted"  # 1 of 2 found: below 80%
    too_many = scores([(item("a", "nyyy"), verdict("nyyy"))]
                      + [(item(str(i), "yyyy"), verdict("nyyy")) for i in range(3)])
    assert too_many[0].status == "not trusted"  # every owner's y marked n


def test_a_skipped_point_is_left_out_of_the_score_and_shown_as_a_dash():
    rated = item("w5", "nyyy", skip=("clear",))
    by_point = {s.point: s for s in scores([(rated, verdict("yyyy"))])}

    assert by_point["clear"].n_labels == 0 and by_point["clear"].y_labels == 0
    assert owner_letters(rated) == "-yyy"


def test_the_labels_come_from_both_rating_files(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    (workspace / "ratings.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"batch": "b1", "exercise": "demo", "clear": False, "fits": True, "taught": True,
         "targets": True, "skip_points": ["clear"]},
        {"batch": "gone", "exercise": "demo", "clear": True, "fits": True, "taught": True,
         "targets": True},  # its batch was deleted: left out
    ]) + "\n")
    blind = workspace / "experiments" / "blind"
    (blind / "1-demo").mkdir(parents=True)
    (blind / "1-demo" / "X.md").write_text("the X version")
    (blind / "arms.json").write_text(json.dumps({"low": "batches/b1"}))
    (blind / "ratings.jsonl").write_text(json.dumps(
        {"folder": "1-demo", "version": "X", "arm": "low", "clear": False, "fits": True,
         "taught": True, "targets": True}) + "\n")

    daily = daily_items(workspace)
    assert [(i.name, owner_letters(i)) for i in daily] == [("b1/demo", "-yyy")]
    [rated] = blind_items(workspace, blind)
    assert (rated.name, rated.batch_dir, rated.exercise_id, rated.question) == (
        "blind 1-demo X (low)", batch, "demo", "the X version")
    assert owner_letters(rated) == "nyyy"


def test_verdicts_are_reused_and_disagreements_listed(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    rated = Item("b1/demo", batch, "demo", {"clear": False, "fits": True})
    store = tmp_path / "store"

    first = judged([rated], workspace, judge_with(FakeModel(verdict_json("ynyy"))), store)
    again = judged([rated], workspace, judge_with(FakeModel()), store)  # no call: reused

    assert first[0][1].letters() == again[0][1].letters() == "ynyy"
    text = report(again)
    assert "missed (owner n, judge y):\n  b1/demo: clear: clear evidence" in text
    assert "1. [to review] [serious] b1/demo: fits: fits evidence" in text
    assert "2. [to review] b1/demo: taught: taught evidence" not in text  # judge said y


def test_the_owners_answers_on_flags_give_how_many_were_right(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    rated = Item("b1/demo", batch, "demo", {"clear": True, "fits": True},)
    unrated = Item("b1/other", batch, "demo", {"fits": True})  # "clear" skipped
    results = [(rated, verdict("nnyy"), ""), (unrated, verdict("nyyy"), "")]
    (tmp_path / "flag-reviews.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"name": "b1/demo", "point": "clear", "owner_says": "right"},
        {"name": "b1/demo", "point": "fits", "owner_says": "wrong"},
        {"name": "b1/other", "point": "clear", "owner_says": "right"},
    ]) + "\n")
    reviews = read_reviews(tmp_path)

    assert [(i.name, p) for i, p, _ in judge_flags(results)] == [
        ("b1/demo", "clear"), ("b1/demo", "fits"), ("b1/other", "clear")]
    assert precision(results, reviews)["clear"] == (2, 2)
    assert precision(results, reviews)["fits"] == (0, 1)
    text = report(results, reviews)
    assert "1. [right] [serious] b1/demo: clear" in text
    assert "2. [wrong] [serious] b1/demo: fits" in text
    assert "flags the owner found right: clear 2/2, fits 0/1" in text
    assert read_reviews(tmp_path / "none") == {}


def test_an_item_without_a_verdict_is_reported_not_scored(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    rated = Item("b1/demo", batch, "demo", {"clear": False})

    results = judged([rated], workspace, judge_with(FakeModel("x", "x", "x")), tmp_path / "s")

    assert results[0][1] is None
    assert "no verdict: b1/demo: the reply does not fit" in report(results)
    assert scores([])[0].n_labels == 0


def test_a_minor_flag_is_an_n_but_not_serious():
    rated = verdict("mnyy")  # clear minor, fits serious

    assert rated.letters() == "nnyy"  # compares with the owner's ratings as before
    assert rated.flags() == ["clear", "fits"]
    assert rated.flags(serious_only=True) == ["fits"]


def test_only_the_useful_functions_part_of_the_hints_is_shown():
    from tutor.judge.teaching import useful_functions

    assert useful_functions("# Hints\n\n## Level 1\nx\n") == ""
    hints = "## Useful functions\n### `f`\n```\n## in code\n```\n## Level 1\nsecret\n"
    assert useful_functions(hints) == "## Useful functions\n### `f`\n```\n## in code\n```"


def test_a_planted_defect_labels_only_its_point_and_is_scored_apart(tmp_path):
    workspace, batch = make_workspace(tmp_path)
    variant = tmp_path / "defects" / "demo-targets"
    variant.mkdir(parents=True)
    (variant / "question.md").write_text("Add them; any way will do.")
    (variant / "reference.py").write_text("def add(a, b):\n    return a.__add__(b)\n")
    (variant / "defect.json").write_text(json.dumps(
        {"batch": "b1", "exercise": "demo", "point": "targets", "change": "allows any way"}))
    (tmp_path / "defects" / "README.md").write_text("not a variant")

    [planted] = defect_items(workspace, tmp_path / "defects")
    assert (planted.name, planted.labels, planted.planted) == (
        "planted demo-targets", {"targets": False}, True)
    inputs = gather(batch, "demo", workspace, planted.question, planted.reference)
    assert "a.__add__(b)" in user_prompt(inputs) and "any way will do" in user_prompt(inputs)

    real = Item("b1/demo", batch, "demo", {"clear": False})
    text = report([(real, verdict("nyyy"), ""), (planted, verdict("yyyn"), "")])
    assert "real ratings:" in text and "planted defects (found / planted):\ntargets  1/1" in text
    real_clear = text.split("planted defects")[0]
    assert "targets        0" in real_clear  # the planted one is not in the real table
