"""The batch page's checks after generation (gui/after_checks.py, routes/checks.py), on a
made-up course in a temp folder: the judge and the held-out check start as tasks with the
commands the terminal takes, a course that names no model gets no button, and the table shows
verdicts only, the judge's once the owner has rated the exercise. No model is called."""

import json
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
BATCH = "2026-01-01_demo"
# A course that names both models, each a profile of its own file (the older layout).
WITH_MODELS = """course = "demo"

[llm.roles]
judge = "fair-judge"
heldout = "other-writer"

[llm.profiles.fair-judge]
provider = "deepseek"
model = "judge-model-1"

[llm.profiles.other-writer]
provider = "deepseek"
model = "writer-model-1"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_course(tmp_path: Path, toml: str = WITH_MODELS) -> Path:
    """Two exercises with their solution folders, as a generated batch has them."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", toml)
    batch = course / "batches" / BATCH
    _write(batch / "manifest.json", json.dumps({"mode": "learn", "exercises": ["one", "two"]}))
    for name in ("one", "two"):
        _write(batch / "exercises" / name / "question.md", f"# {name}\n")
        _write(batch / "exercises" / name / "student.py", "def f(): ...\n")
        _write(batch / "solutions" / name / "validation.json", json.dumps({"valid": True}))
    return course


def browser(course: Path):
    """A browser that has opened the printed link once, so it holds the cookie."""
    client = create_app(course.parent, TOKEN, port=5000).test_client()
    assert client.get(f"/?token={TOKEN}", base_url=BASE).status_code == 302
    return client


def page(client) -> str:
    return client.get(f"/c/demo-course/b/{BATCH}/", base_url=BASE).get_data(as_text=True)


def post(client, action: str):
    response = client.post(f"/c/demo-course/b/{BATCH}/{action}", base_url=BASE,
                           headers={"Origin": BASE})
    assert response.status_code == 303
    return response.headers["Location"]


@pytest.fixture
def started(monkeypatch):
    """What the pages start, in place of real tasks: (title, commands, meta) of each."""
    seen = []

    def fake_start(course_root, title, commands, env, meta=None):
        seen.append((title, commands, meta))
        return tasks.Task("20260102-100000-abcdef", course_root, title, "running", "", None,
                          commands, [], 1)

    monkeypatch.setattr(tasks, "start", fake_start)
    return seen


def rate(course: Path, exercise: str) -> None:
    """The owner's rating of one exercise, as the exercise page stores it."""
    line = {"batch": BATCH, "exercise": exercise, "clear": True, "fits": True, "taught": True,
            "targets": True, "date": "2026-01-03"}
    with (course / "ratings.jsonl").open("a", encoding="utf-8") as lines:
        lines.write(json.dumps(line) + "\n")


def test_each_button_starts_its_command_on_the_batch_folder(tmp_path, started):
    course = make_course(tmp_path)
    client = browser(course)
    batch = str((course / "batches" / BATCH).resolve())

    for action in ("judge", "heldout/write", "heldout/check"):
        assert post(client, action) == "/c/demo-course/tasks/20260102-100000-abcdef/"

    assert [(commands[0][1:], meta) for _title, commands, meta in started] == [
        (["-m", "tutor.judge", batch], {"kind": "judge", "batch": BATCH}),
        (["-m", "tutor.heldout_writer", batch], {"kind": "heldout-write", "batch": BATCH}),
        (["-m", "tutor.validation.heldout", batch], {"kind": "heldout-check", "batch": BATCH}),
    ]


def test_the_page_names_the_model_each_paid_button_uses(tmp_path):
    text = page(browser(make_course(tmp_path)))

    assert "fair-judge (judge-model-1)" in text and "other-writer (writer-model-1)" in text
    for action in ("judge", "heldout/write", "heldout/check"):
        assert f'action="/c/demo-course/b/{BATCH}/{action}"' in text


def test_a_course_that_names_no_model_gets_a_link_not_a_button(tmp_path, started):
    """The GUI does not read .env, so it cannot tell whether a fallback model exists: no
    button. The held-out check itself calls no model and stays."""
    course = make_course(tmp_path, 'course = "demo"\n')
    client = browser(course)
    text = page(client)

    assert f'action="/c/demo-course/b/{BATCH}/judge"' not in text
    assert f'action="/c/demo-course/b/{BATCH}/heldout/write"' not in text
    assert f'action="/c/demo-course/b/{BATCH}/heldout/check"' in text
    assert 'href="/c/demo-course/models/"' in text
    # A form sent anyway starts nothing and says why.
    assert post(client, "judge").endswith("?notice=no-judge-model")
    assert post(client, "heldout/write").endswith("?notice=no-heldout-model")
    assert started == []


def test_the_judge_is_not_offered_for_a_c_course(tmp_path, started):
    course = make_course(tmp_path, WITH_MODELS + "\n[languages.c]\n")
    client = browser(course)

    assert f'action="/c/demo-course/b/{BATCH}/judge"' not in page(client)
    assert post(client, "judge").endswith("?notice=judge-python-only")
    assert started == []


def test_the_judges_letters_wait_for_the_owners_rating(tmp_path):
    """The owner's ratings are what the judge is checked against, so a verdict read first
    would only be agreed with."""
    course = make_course(tmp_path)
    solutions = course / "batches" / BATCH / "solutions"
    _write(solutions / "one" / "quality.json", json.dumps({
        "letters": "yyny", "points": {"taught": {"evidence": "SECRET-REASON", "ok": False}}}))
    client = browser(course)

    before = page(client)
    assert "judged; shown once you rate it" in before and "yyny" not in before
    assert "not judged" in before  # the other exercise

    rate(course, "one")
    after = page(client)
    assert "yyny" in after
    assert "SECRET-REASON" not in before + after  # a reason is never shown, only the letters


def test_the_held_out_column_shows_only_how_many_checks_were_flagged(tmp_path):
    course = make_course(tmp_path)
    solutions = course / "batches" / BATCH / "solutions"
    _write(solutions / "one" / "heldout.json", json.dumps({
        "flagged": 2, "results": [{"status": "FAIL", "detail": "SECRET-OUTPUT"}]}))
    _write(solutions / "two" / "heldout.json", json.dumps({"flagged": 0, "results": []}))

    text = page(browser(course))

    assert "2 flagged" in text and "nothing flagged" in text
    assert "SECRET-OUTPUT" not in text


@pytest.mark.parametrize("report", ["not json", "[]", '{"letters": "yes!"}', '{"letters": 4}',
                                    '{"flagged": "many"}', '{"flagged": true}'])
def test_a_report_that_cannot_be_read_counts_as_none(tmp_path, report):
    """A hand-edited or half-written file never breaks the page or shows its text."""
    course = make_course(tmp_path)
    solutions = course / "batches" / BATCH / "solutions"
    _write(solutions / "one" / "quality.json", report)
    _write(solutions / "one" / "heldout.json", report)
    rate(course, "one")

    text = page(browser(course))

    # Both exercises' cells, and the section's lines for a batch with nothing on file.
    assert text.count(">not judged<") == 2 and text.count(">not run<") == 2
    assert "No exercise of this batch has been judged yet." in text


def test_the_section_says_what_is_on_file_and_links_the_last_runs_log(tmp_path):
    """Owner, 2026-11-17: after a click nothing seemed to have happened. The section now
    counts what the batch has and links each check's last task, where the reasons are."""
    course = make_course(tmp_path)
    solutions = course / "batches" / BATCH / "solutions"
    client = browser(course)
    empty = page(client)
    assert "No exercise of this batch has been judged yet." in empty
    assert "The held-out check has not run on this batch yet." in empty

    _write(solutions / "one" / "quality.json", json.dumps({"letters": "nyyn"}))
    _write(solutions / "two" / "quality.json", json.dumps({"letters": "yyyy"}))
    _write(solutions / "one" / "heldout.json", json.dumps({"flagged": 3}))
    rate(course, "one")
    for task_id, kind in (("20260102-100000-aaaaaa", "judge"),
                          ("20260102-110000-bbbbbb", "heldout-check")):
        _write(course / "logs" / "gui-tasks" / task_id / "task.json", json.dumps({
            "title": kind, "status": "done", "started": "2026-01-02 10:00:00",
            "finished": "2026-01-02 10:01:00", "commands": [["python"]], "exit_codes": [0],
            "pid": 1, "meta": {"kind": kind, "batch": BATCH}}))
        _write(course / "logs" / "gui-tasks" / task_id / "output.log", "")
    text = page(client)

    assert "Judged: 2 of 2 exercises." in text and "shows 1 of them" in text
    assert "Checked: 1 of 2 exercises, 3 flagged in all." in text
    assert "no on:" in text and "clear, targets" in text  # the failed points by name
    assert 'href="/c/demo-course/tasks/20260102-100000-aaaaaa/"' in text
    assert 'href="/c/demo-course/tasks/20260102-110000-bbbbbb/"' in text
