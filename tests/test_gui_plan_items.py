"""GUI generation regression checks using synthetic courses and stand-in commands."""

import json

import pytest

pytest.importorskip("flask")

from gui_generation_helpers import (
    _browser,
    _draft,
    _generated,
    _record_starts,
    _task,
    _write,
    get,
    post,
)
from gui_generation_helpers import course as course  # noqa: E402
from gui_generation_helpers import root as root  # noqa: E402

from tutor.gui import courses, generation, plan_items, tasks  # noqa: E402


def test_each_exercise_of_a_generated_plan_has_its_status(course):
    _generated(course)
    view = generation.plan_view(course, "w9-test", history=[])
    statuses = plan_items.item_statuses(course, view)

    assert {k: v.state for k, v in statuses.items()} == {
        "a": "valid", "b": "saved, not validated", "c": "discarded", "d": "error"}
    assert statuses["c"].reason == "rule 1 (reference passes) FAIL, after 3 repairs"
    assert "42" not in statuses["c"].reason  # the check's details can quote expected values
    assert "stream ended" in statuses["d"].reason
    assert [k for k, v in statuses.items() if v.can_retry] == ["c", "d"]


def test_an_exercise_saved_before_the_manifest_exists_is_saved_not_discarded(course):
    # The manifest is written once every item has finished, so while a batch runs, or after
    # it stopped midway, a saved exercise is only in generation.jsonl.
    _, batch = _generated(course)
    (batch / "manifest.json").unlink()
    view = generation.plan_view(course, "w9-test", history=[])
    statuses = plan_items.item_statuses(course, view)

    assert {k: v.state for k, v in statuses.items()} == {
        "a": "saved, not validated", "b": "saved, not validated", "c": "discarded",
        "d": "error"}
    assert [k for k, v in statuses.items() if v.can_retry] == ["c", "d"]


def test_a_plan_not_approved_yet_shows_no_status(course):
    _draft(course)
    view = generation.plan_view(course, "w9-test", history=[])
    assert plan_items.item_statuses(course, view) == {}


def test_the_plan_page_shows_the_status_and_try_again_only_where_it_failed(root, course):
    _generated(course)
    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)

    assert "chip-valid" in page and "chip-discarded" in page and "chip-error" in page
    # Each of the two: "Try again", and "Try again with a note".
    assert page.count('name="item" value="c"') == 2 and page.count('name="item" value="d"') == 2
    assert page.count('name="item" value=') == 4
    assert 'href="/c/demo-course/b/2026-01-02_p3-w9-test/e/a/"' in page  # a saved one links


def test_try_again_generates_that_exercise_with_the_batchs_settings_then_validates_it(
        root, course, monkeypatch):
    draft, batch = _generated(course)
    started = []
    monkeypatch.setattr(tasks, "start", lambda course_root, title, commands, env, meta=None:
                        started.append((title, commands, meta)) or tasks.Task(
                            "20260102-100000-abcdef", course_root, title, "running", "", None,
                            commands, [], 1, meta or {}))
    monkeypatch.setattr(courses, "container_problem", lambda course: None)

    response = post(_browser(root), "/c/demo-course/plans/w9-test/retry", {"item": "c"})

    assert response.headers["Location"] == "/c/demo-course/plans/w9-test/"
    title, (graph, validate), meta = started[0]
    assert graph[2:5] == ["tutor.graph", "item", str(draft / "retry-c.json")]
    assert "--batch=w9-test" in graph and "--repair-profile=deepseek-pro" in graph
    assert "--set=mode=review" in graph and "--set=hide_answers=true" in graph
    assert not any("parallel" in a or "plan-profile" in a for a in graph)
    assert json.loads((draft / "retry-c.json").read_text(encoding="utf-8"))["id"] == "c"
    assert validate[2:] == ["tutor.validation", str(batch / "exercises" / "c"),
                            str(batch / "solutions" / "c")]
    assert meta == {"kind": "retry", "label": "w9-test", "item": "c"}


@pytest.mark.parametrize("exercise", ["a", "zz", "../x"])
def test_only_an_exercise_that_did_not_succeed_can_be_tried_again(root, course, exercise,
                                                                  monkeypatch):
    _generated(course)
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    response = post(_browser(root), "/c/demo-course/plans/w9-test/retry", {"item": exercise})
    assert "notice=plan-moved" in response.headers["Location"]


def test_while_a_task_runs_the_page_says_when_try_again_comes_back(root, course, monkeypatch):
    """The owner saw the button gone during another retry and could not tell why."""
    _generated(course)
    running = _task(generation.RETRY, status=tasks.RUNNING, codes=())
    monkeypatch.setattr(tasks, "list_tasks", lambda course_root: [running])
    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)
    assert 'name="item" value=' not in page
    assert page.count("comes back when the running task ends") == 2


def test_the_batch_page_points_to_the_plan_for_exercises_that_did_not_come_out(root, course):
    _, batch = _generated(course)
    # The batch page lists only what the manifest has; c and d live on the plan page.
    page = get(_browser(root), f"/c/demo-course/b/{batch.name}/").get_data(as_text=True)
    assert "2 exercises of the plan did not come out: c, d." in page
    assert 'href="/c/demo-course/plans/w9-test/">Open the plan</a>' in page


def test_a_batch_whose_exercises_all_came_out_says_nothing_of_it(root, course):
    _, batch = _generated(course)
    log = batch / "generation.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + "\n".join(json.dumps(
        {"exercise": e, "outcome": "saved", "rounds": 0, "attempts": []}) for e in "cd") + "\n",
        encoding="utf-8")
    manifest = batch / "manifest.json"
    manifest.write_text(json.dumps({"exercises": ["a", "b", "c", "d"]}), encoding="utf-8")
    page = get(_browser(root), f"/c/demo-course/b/{batch.name}/").get_data(as_text=True)
    assert "did not come out" not in page


def test_try_all_generates_every_exercise_that_did_not_come_out_in_one_task(
        root, course, monkeypatch):
    draft, batch = _generated(course)
    started = []
    monkeypatch.setattr(tasks, "start", lambda course_root, title, commands, env, meta=None:
                        started.append((title, commands, meta)) or tasks.Task(
                            "20260102-100000-abcdef", course_root, title, "running", "", None,
                            commands, [], 1, meta or {}))
    monkeypatch.setattr(courses, "container_problem", lambda course: None)
    client = _browser(root)
    assert "Try all 2 again" in get(client, "/c/demo-course/plans/w9-test/").get_data(
        as_text=True)

    post(client, "/c/demo-course/plans/w9-test/retry-all")

    title, commands, meta = started[0]
    assert title == "Generate 2 exercises again" and len(commands) == 4  # each: graph, validate
    assert [c[4] for c in commands[::2]] == [str(draft / "retry-c.json"),
                                             str(draft / "retry-d.json")]
    assert meta == {"kind": "retry", "label": "w9-test", "items": ["c", "d"]}


# --- trying again with a note (owner, 2026-11-07) ----------------------------------------------




def test_the_last_rounds_failure_is_shown_unless_the_batch_hides_answers(root, course):
    _generated(course, hide_answers=False)
    view = generation.plan_view(course, "w9-test", history=[])
    statuses = plan_items.item_statuses(course, view)
    assert statuses["c"].detail == "rule 1 (reference passes) FAIL: expected 42, got 41"
    assert statuses["d"].detail == ""  # an error has its reason line, and no check output
    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)
    assert "expected 42, got 41" in page and "what the last round failed on" in page


def test_a_batch_that_hides_answers_shows_no_check_output(root, course):
    _generated(course)  # hide_answers: hand-calculation exercises
    view = generation.plan_view(course, "w9-test", history=[])
    assert plan_items.item_statuses(course, view)["c"].detail == ""
    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)
    assert "expected 42" not in page and "this batch hides answers" in page


def test_a_note_is_added_to_the_items_notes_for_that_try_only(root, course, monkeypatch):
    draft, _ = _generated(course)
    plan = (draft / "plan.json").read_text(encoding="utf-8")
    started = _record_starts(monkeypatch)
    note = "Sum the list from the left.\nThe tests and the reference must agree."

    response = post(_browser(root), "/c/demo-course/plans/w9-test/retry",
                    {"item": "c", "note": note.replace("\n", "\r\n")})

    assert response.headers["Location"] == "/c/demo-course/plans/w9-test/"
    item = json.loads((draft / "retry-c.json").read_text(encoding="utf-8"))
    assert item["notes"] == plan_items.NOTE_PREFIX + note  # the plan's item had no notes
    assert (draft / "plan.json").read_text(encoding="utf-8") == plan  # the plan as approved
    assert started[0][0] == "Generate c again with a note"
    # The next page starts the next note from this one.
    view = generation.plan_view(course, "w9-test", history=[])
    assert plan_items.item_statuses(course, view)["c"].note == note
    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)
    assert "Sum the list from the left." in page


def test_a_note_follows_the_notes_the_plan_gave(course):
    draft, _ = _generated(course)
    items = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    items[2]["notes"] = "check an empty list"
    _write(draft / "plan.json", json.dumps(items))
    view = generation.plan_view(course, "w9-test", history=[])
    plan_items.prepare_retry(course, view, "c", note="keep the order")
    item = json.loads((draft / "retry-c.json").read_text(encoding="utf-8"))
    assert item["notes"] == "check an empty list\n" + plan_items.NOTE_PREFIX + "keep the order"


@pytest.mark.parametrize("note", ["x" * 2001, "a\x00b", "a\x1bb"])
def test_a_note_that_is_not_plain_text_starts_nothing(root, course, note, monkeypatch):
    draft, _ = _generated(course)
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    response = post(_browser(root), "/c/demo-course/plans/w9-test/retry",
                    {"item": "c", "note": note})
    assert "notice=note-bad" in response.headers["Location"]
    assert not (draft / "retry-c.note.txt").exists()


def test_try_again_without_a_note_and_try_all_leave_the_notes_as_planned(root, course,
                                                                       monkeypatch):
    draft, _ = _generated(course)
    _write(draft / "retry-c.note.txt", "an earlier note")
    _record_starts(monkeypatch)
    client = _browser(root)
    for path, form in (("retry", {"item": "c"}), ("retry-all", {})):
        post(client, f"/c/demo-course/plans/w9-test/{path}", form)
        item = json.loads((draft / "retry-c.json").read_text(encoding="utf-8"))
        assert "notes" not in item  # the plan's item, unchanged
    # The earlier note stays, to start the next note from.
    assert (draft / "retry-c.note.txt").read_text(encoding="utf-8") == "an earlier note"


def test_the_batch_page_links_back_to_the_plan_it_came_from(course):
    """Only while the label's newest draft is this batch's own: a newer plan that took the
    label would show on the plan page instead of the one the batch was made from."""
    draft, batch = _generated(course)
    assert generation.plan_label(course, batch.name) == "w9-test"
    newer = draft.parent / "2026-02-03_p3-w9-test"
    newer.mkdir()
    (newer / "checkpoint.sqlite").write_bytes(b"")
    assert generation.plan_label(course, batch.name) is None
    assert generation.plan_label(course, "not-a-batch-name") is None
