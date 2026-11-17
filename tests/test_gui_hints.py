"""The exercise page's Hints tab (gui/hints_page.py), on a made-up course in a temp folder:
hints.md with each numbered hint closed, and a hint on the learner's code started as a task
with the command the terminal takes. No model is called and no test is run."""

import json
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
BATCH = "2026-01-01_demo"
HINTS = """# Hints

## Useful functions

### `sorted`: a new list in order

USEFUL-TEXT

## Hint 1

FIRST-HINT <script>alert(1)</script>

## Hint 2

SECOND-HINT
"""
WITH_HINT_MODEL = """course = "demo"

[llm.roles]
hint = "kind-helper"

[llm.profiles.kind-helper]
provider = "deepseek"
model = "helper-model-1"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_course(tmp_path: Path, toml: str = 'course = "demo"\n', mode: str = "learn",
                hints: str | None = HINTS) -> Path:
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", toml)
    batch = course / "batches" / BATCH
    _write(batch / "manifest.json", json.dumps({"mode": mode, "exercises": ["one"]}))
    _write(batch / "exercises" / "one" / "question.md", "# One\n\nQUESTION-TEXT\n")
    _write(batch / "exercises" / "one" / "student.py", "def f(): ...\n")
    if hints is not None:
        _write(batch / "exercises" / "one" / "hints.md", hints)
    return course


def browser(course: Path):
    client = create_app(course.parent, TOKEN, port=5000).test_client()
    assert client.get(f"/?token={TOKEN}", base_url=BASE).status_code == 302
    return client


URL = f"/c/demo-course/b/{BATCH}/e/one/"


def get(client, path: str = URL) -> str:
    return client.get(path, base_url=BASE).get_data(as_text=True)


def hint_task(course: Path, task_id: str, log: str, status: str = "done") -> None:
    """A finished hint task of the exercise, as the page's button leaves it."""
    folder = course / "logs" / "gui-tasks" / task_id
    _write(folder / "task.json", json.dumps({
        "title": "Hint: one", "status": status, "started": "2026-01-02 10:00:00",
        "finished": "2026-01-02 10:00:05", "commands": [["python", "-m", "tutor.hint"]],
        "exit_codes": [0], "pid": 1,
        "meta": {"kind": "hint", "batch": BATCH, "exercise": "one"}}))
    _write(folder / "output.log", log)


def test_the_two_tabs_are_two_pages_and_the_question_is_the_first(tmp_path):
    client = browser(make_course(tmp_path))
    question, hints = get(client), get(client, URL + "hints/")

    assert "QUESTION-TEXT" in question and "USEFUL-TEXT" not in question
    assert "USEFUL-TEXT" in hints and "QUESTION-TEXT" not in hints
    for text in (question, hints):  # each page links both
        assert f'href="{URL}"' in text and f'href="{URL}hints/"' in text
    assert 'class="current" aria-current="page">Question' in question
    assert 'class="current" aria-current="page">Hints' in hints


def test_useful_functions_show_and_each_numbered_hint_is_closed(tmp_path):
    text = get(browser(make_course(tmp_path)), URL + "hints/")

    assert "<h3>Useful functions</h3>" in text
    # Each hint is in a closed <details>: its text is in the page, behind a click.
    assert text.count('<details class="review hint"><summary>Hint') == 2
    assert "<details class=\"review hint\" open" not in text
    assert "FIRST-HINT" in text and "SECOND-HINT" in text


def test_a_hints_file_is_shown_as_text_never_as_markup(tmp_path):
    text = get(browser(make_course(tmp_path)), URL + "hints/")

    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_an_exercise_without_hints_says_so(tmp_path):
    text = get(browser(make_course(tmp_path, hints=None)), URL + "hints/")

    assert "This exercise has no hints.md." in text
    assert "Check my code" in text  # the hint on the code needs no hints.md


def test_a_review_batch_asks_to_try_first(tmp_path):
    learn = get(browser(make_course(tmp_path / "a")), URL + "hints/")
    review = get(browser(make_course(tmp_path / "b", mode="review")), URL + "hints/")

    assert "try on your own first" in review and "try on your own first" not in learn
    assert "says only what goes wrong" in review


@pytest.mark.parametrize("form, extra", [({}, []), ({"no_llm": "1"}, ["--no-llm"])])
def test_check_my_code_starts_the_hint_command_on_the_exercise_folder(tmp_path, monkeypatch,
                                                                      form, extra):
    course = make_course(tmp_path, WITH_HINT_MODEL)
    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append((commands, meta))
        return tasks.Task("20260102-100000-abcdef", course_root, title, "running", "", None,
                          commands, [], 1)

    monkeypatch.setattr(tasks, "start", fake_start)
    response = browser(course).post(URL + "hint", base_url=BASE, data=form,
                                    headers={"Origin": BASE})

    assert response.status_code == 303
    commands, meta = started[0]
    folder = str((course / "batches" / BATCH / "exercises" / "one").resolve())
    assert commands[0][1:] == ["-m", "tutor.hint", *extra, folder]
    assert meta == {"kind": "hint", "batch": BATCH, "exercise": "one"}


def test_the_page_says_where_the_code_goes(tmp_path):
    without = get(browser(make_course(tmp_path / "a")), URL + "hints/")
    named = get(browser(make_course(tmp_path / "b", WITH_HINT_MODEL)), URL + "hints/")

    assert "names no hint model, so your code stays on this machine" in without
    assert 'name="no_llm"' not in without
    assert "kind-helper (helper-model-1)" in named and 'name="no_llm"' in named


def test_the_latest_hint_is_shown_as_the_command_printed_it(tmp_path):
    course = make_course(tmp_path)
    hint_task(course, "20260102-100000-aaaaaa", "$ python -m tutor.hint x\nhint: OLDER\n[exit 0]\n")
    hint_task(course, "20260103-100000-bbbbbb",
              "$ python -m tutor.hint x\nhint: line 3 <b>NEWER</b>\n[exit 0]\n")

    text = get(browser(course), URL + "hints/")

    assert "hint: line 3 &lt;b&gt;NEWER&lt;/b&gt;" in text  # plain text, escaped
    assert "OLDER" not in text  # an earlier hint is a link to its log, not text on the page
    assert "$ python -m tutor.hint" not in text and "[exit 0]" not in text
    assert 'href="/c/demo-course/tasks/20260102-100000-aaaaaa/"' in text
    assert 'href="/c/demo-course/tasks/20260103-100000-bbbbbb/"' in text
