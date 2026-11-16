"""The exercise page's teaching-quality rating (gui/ratings.py), on a made-up course in a temp
folder: offered once an exercise has passed, refused otherwise, one line per exercise that a
second rating replaces, and the stored rating filled back into the form."""

import datetime
import json
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.eval import report  # noqa: E402
from tutor.gui import ratings  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
BATCH = "2026-01-01_demo"
ALL_YES = {"clear": "y", "fits": "y", "taught": "y", "targets": "y"}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def course(tmp_path):
    """Two exercises: `passed` has a logged passing run, `started` only a failing one."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", 'course = "demo"\n')
    batch = course / "batches" / BATCH
    _write(batch / "manifest.json", json.dumps({"mode": "learn",
                                                "exercises": ["passed", "started"]}))
    for name in ("passed", "started"):
        _write(batch / "exercises" / name / "question.md", f"# {name}\n")
        _write(batch / "exercises" / name / "student.py", "def f(): ...\n")
    runs = [{"batch": BATCH, "exercise": "passed", "result": "passed",
             "time": "2026-01-02T10:05:00"},
            {"batch": BATCH, "exercise": "started", "result": "failed",
             "time": "2026-01-02T10:00:00"}]
    _write(course / "attempts" / "attempts.jsonl",
           "".join(json.dumps(run) + "\n" for run in runs))
    return course


@pytest.fixture
def client(course):
    """A browser that has opened the printed link once, so it holds the cookie."""
    client = create_app(course.parent, TOKEN, port=5000).test_client()
    assert client.get(f"/?token={TOKEN}", base_url=BASE).status_code == 302
    return client


def page(client, exercise: str) -> str:
    return client.get(f"/c/demo-course/b/{BATCH}/e/{exercise}/",
                      base_url=BASE).get_data(as_text=True)


def rate(client, exercise: str, form: dict) -> str:
    """Send the rating form as the page does; returns the notice it goes back with."""
    response = client.post(f"/c/demo-course/b/{BATCH}/e/{exercise}/rate", base_url=BASE,
                           data=form, headers={"Origin": BASE})
    assert response.status_code == 303
    return response.headers["Location"].split("notice=")[1]


def rows(course: Path) -> list[dict]:
    path = course / ratings.FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_form_shows_only_once_the_exercise_has_passed(client, course):
    assert 'action="/c/demo-course/b/2026-01-01_demo/e/passed/rate"' in page(client, "passed")
    assert "/rate" not in page(client, "started")
    # The server refuses it too, whatever the page showed.
    assert rate(client, "started", ALL_YES) == "rating-not-passed"
    assert not (course / ratings.FILE).exists()


def test_a_rating_is_one_line_and_rating_again_replaces_it(client, course):
    assert rate(client, "passed", {**ALL_YES, "remark": "  too\neasy  "}) == "rating-saved"
    first = rows(course)
    assert len(first) == 1
    assert first[0]["remark"] == "too easy"  # one line, whatever the text box held
    assert list(first[0]) == ["batch", "exercise", "date", "clear", "fits", "taught",
                              "targets", "remark"]  # the fixed format calibrate reads

    assert rate(client, "passed", {**ALL_YES, "clear": "n", "remark": ""}) == "rating-saved"
    again = rows(course)
    assert len(again) == 1 and again[0]["clear"] is False and again[0]["remark"] == ""
    # The file before is kept, and the report counts the exercise as rated once.
    assert "too easy" in (course / "ratings.jsonl.bak").read_text(encoding="utf-8")
    assert report.collect(course).ratings == again


def test_the_stored_rating_is_filled_into_the_form(client, course):
    rate(client, "passed", {**ALL_YES, "taught": "n", "remark": "unclear <input> format"})
    html = page(client, "passed")
    assert '<details class="review rating">' in html  # closed: the summary shows the rating
    assert 'name="taught" value="n" checked' in html
    assert 'name="clear" value="y" required checked' in html
    assert 'value="unclear &lt;input&gt; format"' in html  # escaped, never markup


def test_a_point_left_out_saves_nothing(client, course):
    assert rate(client, "passed", {"clear": "y", "fits": "y", "taught": "y"}) \
        == "rating-incomplete"
    assert rate(client, "passed", {**ALL_YES, "fits": "maybe"}) == "rating-incomplete"
    assert not (course / ratings.FILE).exists()


def test_replacing_keeps_other_lines_and_keys_the_page_does_not_set(course):
    # A hand-written file: another exercise, a broken line, and this one with extra keys
    # (`note`, `skip_points`) and a second line for it that would count it twice.
    lines = [json.dumps({"batch": BATCH, "exercise": "other", "clear": True}),
             "not json",
             json.dumps({"batch": BATCH, "exercise": "passed", "date": "2026-01-01",
                         "clear": True, "fits": True, "taught": True, "targets": True,
                         "remark": "old", "note": "kept", "skip_points": ["clear"]}),
             json.dumps({"batch": BATCH, "exercise": "passed", "clear": True})]
    _write(course / ratings.FILE, "\n".join(lines) + "\n")
    points = {point: False for point in report.RATING_POINTS}
    ratings.save(course, BATCH, "passed", points, "new", today=datetime.date(2026, 2, 3))

    text = (course / ratings.FILE).read_text(encoding="utf-8").splitlines()
    assert text[:2] == lines[:2]  # untouched, the broken line included
    assert len(text) == 3  # the duplicate is gone
    replaced = json.loads(text[2])
    assert replaced["note"] == "kept" and replaced["skip_points"] == ["clear"]
    assert replaced["date"] == "2026-02-03" and replaced["remark"] == "new"
    assert ratings.find(course, BATCH, "passed") == replaced


def test_a_stored_rating_can_be_changed_even_without_a_logged_pass(client, course):
    # Rated before the attempt log existed: the page still offers to change it.
    _write(course / ratings.FILE, json.dumps({"batch": BATCH, "exercise": "started",
                                              "clear": True}) + "\n")
    assert "/rate" in page(client, "started")
    assert rate(client, "started", ALL_YES) == "rating-saved"
