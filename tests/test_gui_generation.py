"""Generating from the GUI (step 4): the new-batch form's checks, the commands it builds, where
a planned batch stands by its files, and the whole flow through the real runner with a
stand-in for `python -m tutor.graph` (tests/fake_graph.py), so no model is ever called."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import courses, generation, launch, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
FAKE_GRAPH = Path(__file__).parent / "fake_graph.py"
WORKSPACE_TOML = """course = "demo"

[llm.roles]
plan = "cheap"
generate = "cheap"
oracle = "other"

[llm.profiles.cheap]
provider = "deepseek"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"

[llm.profiles.other]
provider = "openai"
model = "gpt-6.1-terra"

[generation]
mode = "review"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """A course with two lectures read into the cache and one PDF not read yet."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", WORKSPACE_TOML)
    for name in ("L1", "L2"):
        _write(course / "cache" / "lectures" / f"{name}.pdf.md", "## p.1 · slide 1.1\ntext\n")
    _write(course / "lectures" / "L3.pdf", "%PDF")
    return tmp_path / "courses"


@pytest.fixture
def course(root):
    return courses.get_course(root, "demo-course")


class FakeLauncher:
    LaunchError = launch.LaunchError

    def __init__(self):
        self.calls = []

    def open_folder(self, path):
        self.calls.append(("folder", path))

    def open_in_vscode(self, folder, file):
        self.calls.append(("vscode", folder, file))


def _browser(root, launcher=None):
    client = create_app(root, TOKEN, port=5000, launcher=launcher or FakeLauncher()).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def get(client, path):
    return client.get(path, base_url=BASE)


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


def _form(**changes):
    form = {"label": "w9-test", "lecture": ["L1"], "mode": "learn", "how": "auto",
            "max_count": "6", "count": "", "prefs": ""}
    return {**form, **changes}


def _check(course, **changes):
    form = {"label": "w9-test", "chosen": ["L1"], "mode": "learn", "how": "auto", "count": "",
            "max_count": "6", "prefs": "", "hide_answers": False, **changes}
    return generation.check_form(course, **form)


def _task(kind, label="w9-test", status=tasks.DONE, codes=(0,), **how_many):
    return tasks.Task("20260102-100000-abcdef", Path("/nowhere"), f"{kind} {label}", status,
                      "2026-01-02 10:00:00", None, [], list(codes), 1,
                      {"kind": kind, "label": label, **how_many})


def _draft(course, label="w9-test", plan=True, ids=("a", "b")):
    """A draft folder as `plan` leaves it at the pause."""
    draft = course.workspace.root / "batches" / ".drafts" / f"2026-01-02_p3-{label}"
    _write(draft / "checkpoint.sqlite", "")
    _write(draft / "run.json", json.dumps({"settings": {"mode": "review", "hide_answers": True}}))
    if plan:
        items = [{"id": i, "type": "implement", "lecture": "L1", "slides": "1.1"} for i in ids]
        _write(draft / "plan.json", json.dumps(items))
        _write(draft / "plan.model.json", json.dumps(items))
    return draft


# --- the form ---------------------------------------------------------------------------------


def test_only_lectures_read_into_the_cache_are_offered(course):
    assert generation.lectures(course) == (["L1", "L2"], ["L3"])


def test_the_form_starts_from_the_courses_generation_table(course):
    assert generation.defaults(course) == {"mode": "review", "hide_answers": False,
                                           "how": "auto", "max_count": 20}


def test_the_models_are_named_never_their_keys(course):
    shown = dict(generation.models(course))

    assert shown["plan"] == "cheap (deepseek: deepseek-v4-flash)"
    assert shown["oracle"] == "other (openai: gpt-6.1-terra)"
    assert shown["repair"].startswith("none")
    assert "DEEPSEEK_API_KEY" not in str(shown)


@pytest.mark.parametrize(("changes", "problem"), [
    ({"label": "W9"}, "lowercase"),
    ({"label": "-w9"}, "lowercase"),
    ({"label": "../w9"}, "lowercase"),
    ({"label": "x" * 41}, "lowercase"),
    ({"chosen": []}, "at least one lecture"),
    ({"chosen": ["L3"]}, "read into the cache"),  # a PDF not read yet
    ({"chosen": ["../../etc/passwd"]}, "read into the cache"),
    ({"mode": "exam"}, "learn or review"),
    ({"how": "exact", "count": "0"}, "1 to 20"),
    ({"how": "exact", "count": "21"}, "1 to 20"),
    ({"how": "exact", "count": "two"}, "1 to 20"),
    ({"how": "exact", "count": ""}, "1 to 20"),
    ({"max_count": "0"}, "1 to 20"),
    ({"max_count": ""}, "1 to 20"),
    ({"count": "4"}, "choose one"),  # a number typed under Exactly, but auto chosen
    ({"how": ""}, "Choose how many"),
    ({"prefs": "x" * 2001}, "2000 characters"),
    ({"prefs": "a\x00b"}, "plain text"),
])
def test_the_form_refuses_what_the_command_would_not_take(course, changes, problem):
    batch, problems = _check(course, **changes)

    assert batch is None and problem in " ".join(problems)


def test_a_taken_label_is_refused_before_anything_starts(course):
    (course.workspace.root / "batches" / "2026-01-01_p3-w9-test").mkdir(parents=True)

    assert "exists already" in _check(course)[1][0]


def test_the_plan_command_keeps_free_text_as_one_argument(course):
    batch, _ = _check(course, chosen=["L2", "L1"], how="exact", count="3",
                      prefs="-drop the\r\nlast one", hide_answers=True)
    command = generation.plan_command(batch)

    assert command[:4] == generation.GRAPH + ["plan"]
    assert command[4:7] == ["--lectures", "L1", "L2"]  # in the course's order
    assert "--batch=w9-test" in command and "--count=3" in command
    assert not any(part.startswith("--max-count") for part in command)
    assert ["--set", "hide_answers=true"] == command[command.index("--set"):][:2]
    # "=" joins the text to its flag: argparse cannot take "-drop..." for an option.
    assert command[-1] == "--prefs=-drop the\nlast one"


def test_when_the_model_decides_the_limit_goes_to_max_count(course):
    batch, _ = _check(course, max_count="9")
    command = generation.plan_command(batch)

    assert "--max-count=9" in command
    assert not any(part.startswith("--count") for part in command)


def test_resume_commands():
    assert generation.resume_command("w9")[-2:] == ["resume", "--batch=w9"]
    assert generation.resume_command("w9", approve=True)[-1] == "--approve"
    assert generation.resume_command("w9", feedback="-x")[-1] == "--feedback=-x"


# --- where a planned batch stands --------------------------------------------------------------


def test_a_plan_at_the_pause_waits_for_the_owner(course):
    _draft(course)
    view = generation.plan_view(course, "w9-test", [_task("plan")])

    assert view.state == generation.WAITING and view.can_approve
    assert [i["id"] for i in view.items] == ["a", "b"] and not view.edited
    assert view.settings == {"mode": "review", "hide_answers": True}


def test_an_edited_plan_json_is_flagged(course):
    draft = _draft(course)
    _write(draft / "plan.json", json.dumps([{"id": "a"}]))

    assert generation.plan_view(course, "w9-test", []).edited


def test_a_plan_that_stopped_at_the_limit_says_so(course):
    _draft(course)  # two items

    at_limit = generation.plan_view(course, "w9-test", [_task("plan", max_count=2)])
    below = generation.plan_view(course, "w9-test", [_task("plan", max_count=6)])
    exact = generation.plan_view(course, "w9-test", [_task("plan", count=2)])
    unknown = generation.plan_view(course, "w9-test", [])  # planned in a terminal

    assert at_limit.at_limit and at_limit.how_many == {"max_count": 2}
    assert not below.at_limit and not exact.at_limit and not unknown.at_limit


def test_the_limit_comes_from_the_task_that_made_the_plan(course):
    """A later feedback task does not carry it; the plan's own task does."""
    _draft(course)
    history = [_task("feedback"), _task("plan", max_count=2)]

    assert generation.plan_view(course, "w9-test", history).at_limit


def test_the_plan_page_shows_the_limit_notice(root, course):
    _draft(course)
    folder = course.workspace.root / tasks.TASKS_DIR / "20260102-100000-abcdef"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "Plan w9-test", "status": "done", "started": "2026-01-02 10:00:00",
        "commands": [], "exit_codes": [0], "pid": 1,
        "meta": {"kind": "plan", "label": "w9-test", "max_count": 2}}))
    _write(folder / tasks.LOG_FILE, "")

    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)

    assert "as many exercises as the limit (2)" in page and "at most 2" in page


def test_a_draft_folder_for_a_plan_item_means_generation_began(course):
    draft = _draft(course)
    (draft / "a" / "round-0").mkdir(parents=True)
    view = generation.plan_view(course, "w9-test", [_task("approve", status=tasks.STOPPED)])

    assert view.state == generation.STOPPED and view.can_continue and not view.can_approve


def test_a_manifest_means_generated(course):
    _draft(course)
    _write(course.workspace.root / "batches" / "2026-01-02_p3-w9-test" / "manifest.json", "{}")

    assert generation.plan_view(course, "w9-test", []).state == generation.GENERATED
    assert generation.open_plans(course, []) == []  # it is on the course page as a batch


def test_a_plan_sent_back_and_stopped_offers_continue_not_approve(course):
    """The old plan.json is still there, but approving would finish the new plan and approve
    it unseen."""
    _draft(course)

    for status in (tasks.STOPPED, tasks.FAILED, tasks.LOST):
        view = generation.plan_view(course, "w9-test", [_task("feedback", status=status)])
        assert view.state == generation.NO_PLAN and not view.can_approve and view.can_continue
        assert not view.shows_plan


def test_an_approval_refused_still_waits(course):
    """`resume --approve` refuses a plan with blocking problems and changes nothing."""
    _draft(course)
    view = generation.plan_view(course, "w9-test", [_task("approve", status=tasks.FAILED)])

    assert view.state == generation.WAITING


def test_a_planning_command_that_stopped_before_the_plan(course):
    _draft(course, plan=False)

    assert generation.plan_view(course, "w9-test", []).state == generation.NO_PLAN


def test_assistant_mode_shows_as_waiting_for_the_assistant(course):
    _draft(course)
    view = generation.plan_view(course, "w9-test", [_task("plan", status=tasks.FAILED,
                                                          codes=(3,))])

    assert view.state == generation.ASSISTANT and view.can_continue


def test_a_running_task_says_what_it_does(course):
    _draft(course)

    for kind, state in [("plan", generation.MAKING_PLAN), ("feedback", generation.MAKING_PLAN),
                        ("approve", generation.GENERATING), ("continue", generation.CONTINUING)]:
        view = generation.plan_view(course, "w9-test", [_task(kind, status=tasks.RUNNING)])
        assert view.state == state and not view.can_approve


def test_a_plan_task_before_its_folder_exists(course):
    running = [_task("plan", label="w10", status=tasks.RUNNING)]

    assert generation.plan_view(course, "w10", running).state == generation.MAKING_PLAN
    assert [v.label for v in generation.open_plans(course, running)] == ["w10"]
    failed = [_task("plan", label="w10", status=tasks.FAILED, codes=(1,))]
    assert generation.plan_view(course, "w10", failed).state == generation.NOT_STARTED
    assert generation.open_plans(course, failed) == []


def test_drafts_not_made_by_plan_are_not_plans(course):
    """`item` runs (experiments) leave draft folders without a checkpoint."""
    (course.workspace.root / "batches" / ".drafts" / "2026-01-02_p3-pilot" / "x").mkdir(
        parents=True)

    assert generation.plan_view(course, "pilot", []) is None
    assert generation.open_plans(course, []) == []


@pytest.mark.parametrize("label", ["..", "../x", "-x", ".drafts", ""])
def test_odd_labels_are_no_plan(course, label):
    assert generation.plan_view(course, label, []) is None


def test_the_problems_are_the_ones_under_the_last_plan_shown(tmp_path):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / tasks.LOG_FILE).write_text(
        "1. a\n   ! old problem\n\nThe plan is in x; edit it\n"
        "1. a  [implement]\n   why: a gap\n   ! 3 items, 2 asked for\n   ! id 'a' is used twice\n"
        "\nThe plan is in x; edit it before approving if you like.\nPaused.\n", encoding="utf-8")
    task = tasks.Task("20260102-100000-abcdef", folder, "t", tasks.DONE, "", None, [], [0], 1)

    assert generation._problems(task) == ["3 items, 2 asked for", "id 'a' is used twice"]


# --- the pages -------------------------------------------------------------------------------


def test_the_form_page_lists_lectures_models_and_the_courses_mode(root):
    page = get(_browser(root), "/c/demo-course/new-batch").get_data(as_text=True)

    assert 'value="L1"' in page and 'value="L2"' in page and 'value="L3"' not in page
    assert "Not read yet, so not offered: L3" in page
    assert "deepseek-v4-flash" in page
    assert 'value="review" checked' in page


def test_a_refused_form_comes_back_with_its_values_escaped(root, monkeypatch):
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))

    response = post(_browser(root), "/c/demo-course/new-batch",
                    _form(label="<b>x</b>", prefs="<script>alert(1)</script>"))
    page = response.get_data(as_text=True)

    assert response.status_code == 400 and "lowercase letters" in page
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page


def test_the_form_starts_plan_as_a_task_and_goes_to_the_plan_page(root, monkeypatch):
    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append((title, commands, env, meta))
        return tasks.Task("20260102-100000-abcdef", course_root, title, tasks.RUNNING, "", None,
                          commands, [], 1, meta)

    monkeypatch.setattr(tasks, "start", fake_start)
    response = post(_browser(root), "/c/demo-course/new-batch",
                    _form(lecture=["L1", "L2"], prefs="more joins", hide_answers="on"))

    title, commands, env, meta = started[0]
    assert response.headers["Location"] == "/c/demo-course/plans/w9-test/"
    assert title == "Plan w9-test"
    assert commands[0][4:7] == ["--lectures", "L1", "L2"] and "--prefs=more joins" in commands[0]
    assert meta == {"kind": "plan", "label": "w9-test", "max_count": 6}
    assert "hide_answers=true" in commands[0]
    assert env["WORKSPACE_DIR"] == str((root / "demo-course").resolve())


def test_no_plan_starts_without_docker_and_the_form_keeps_its_values(root, monkeypatch):
    monkeypatch.setattr(courses, "container_problem", lambda course: "docker-missing")
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))

    response = post(_browser(root), "/c/demo-course/new-batch", _form(prefs="keep me"))
    page = response.get_data(as_text=True)

    assert response.status_code == 409 and "Install Docker Desktop" in page
    assert "keep me" in page and 'value="w9-test"' in page


@pytest.mark.parametrize("action", ["approve", "feedback", "continue"])
def test_no_resume_starts_without_docker(root, course, monkeypatch, action):
    monkeypatch.setattr(courses, "container_problem", lambda course: "docker-stopped")
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    _draft(course, plan=action != "continue")  # continue: a plan that did not finish

    response = post(_browser(root), f"/c/demo-course/plans/w9-test/{action}", {"feedback": "x"})

    assert response.headers["Location"].endswith("?notice=docker-stopped")


def test_buttons_for_another_step_start_nothing(root, course, monkeypatch):
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    draft = _draft(course)
    (draft / "a").mkdir()  # generation began: no approving or sending back any more
    client = _browser(root)

    for action in ("approve", "feedback"):
        response = post(client, f"/c/demo-course/plans/w9-test/{action}", {"feedback": "x"})
        assert response.headers["Location"].endswith("?notice=plan-moved")


def test_continue_is_refused_while_the_plan_waits(root, course, monkeypatch):
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    _draft(course)

    response = post(_browser(root), "/c/demo-course/plans/w9-test/continue")

    assert response.headers["Location"].endswith("?notice=plan-moved")


def test_empty_feedback_is_sent_back_to_the_owner(root, course, monkeypatch):
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))
    _draft(course)

    response = post(_browser(root), "/c/demo-course/plans/w9-test/feedback", {"feedback": " "})

    assert response.headers["Location"].endswith("?notice=feedback-empty")


@pytest.mark.parametrize("path", ["/c/demo-course/plans/nothing/",
                                  "/c/demo-course/plans/..%2F..%2Fx/",
                                  "/c/demo-course/plans/.drafts/"])
def test_an_unknown_plan_is_not_found(root, path):
    assert get(_browser(root), path).status_code == 404


def test_actions_on_an_unknown_plan_start_nothing(root, monkeypatch):
    monkeypatch.setattr(tasks, "start", lambda *a, **k: pytest.fail("nothing may start"))

    assert post(_browser(root), "/c/demo-course/plans/nothing/approve").status_code == 404


def test_edit_opens_plan_json_in_vscode_from_the_course_folder(root, course):
    draft = _draft(course)
    launcher = FakeLauncher()

    response = post(_browser(root, launcher), "/c/demo-course/plans/w9-test/edit")

    assert launcher.calls == [("vscode", course.workspace.root, draft.resolve() / "plan.json")]
    assert response.headers["Location"].endswith("?notice=opened-plan")


def test_the_plan_page_shows_plan_json_escaped(root, course):
    draft = _draft(course)
    _write(draft / "plan.json", json.dumps([{"id": "<script>alert(1)</script>", "slides": 3}]))

    page = get(_browser(root), "/c/demo-course/plans/w9-test/").get_data(as_text=True)

    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
    assert "plan.json was edited" in page and "Approve and generate" in page


def test_a_running_plan_polls_and_offers_no_decision(root, course):
    _draft(course)
    folder = course.workspace.root / tasks.TASKS_DIR / "20260102-100000-abcdef"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "Plan w9-test", "status": "running", "started": "2026-01-02 10:00:00",
        "commands": [], "exit_codes": [], "pid": os.getpid(),
        "meta": {"kind": "plan", "label": "w9-test"}}))
    _write(folder / tasks.LOG_FILE, "")

    page = get(_browser(root), "/c/demo-course/plans/w9-test/live").get_data(as_text=True)

    assert 'hx-trigger="every 1s"' in page and "Stop" in page
    assert "Approve" not in page and "Send back" not in page


def test_the_course_page_links_new_batch_and_open_plans(root, course):
    _draft(course)

    page = get(_browser(root), "/c/demo-course/").get_data(as_text=True)

    assert "/c/demo-course/new-batch" in page
    assert "/c/demo-course/plans/w9-test/" in page and "waiting for you" in page


# --- the whole flow, through the real runner -------------------------------------------------


def _wait_idle(course_root: Path, count: int, within_s: float = 30) -> tasks.Task:
    """The course's `count`-th task, once it has ended."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        found = tasks.list_tasks(course_root)
        if len(found) == count and not found[0].active:
            return found[0]
        time.sleep(0.1)
    raise AssertionError("the task did not end")


def test_plan_send_back_and_approve_with_a_stand_in_for_the_graph(root, course, monkeypatch):
    monkeypatch.setattr(generation, "GRAPH", [sys.executable, str(FAKE_GRAPH)])
    client = _browser(root)
    home = course.workspace.root
    plan_page = "/c/demo-course/plans/w9-test/"

    assert post(client, "/c/demo-course/new-batch",
                _form(prefs="a problem please")).headers["Location"] == plan_page
    assert _wait_idle(home, 1).status == tasks.DONE
    page = get(client, plan_page).get_data(as_text=True)
    assert "waiting for you" in page and "w9-test-ex1" in page
    assert "2 items, 3 asked for" in page  # the "!" line, from the log

    post(client, f"{plan_page}feedback", {"feedback": "-add one more"})
    assert _wait_idle(home, 2).status == tasks.DONE
    assert "w9-test-extra" in get(client, plan_page).get_data(as_text=True)

    post(client, f"{plan_page}approve")
    assert _wait_idle(home, 3).status == tasks.DONE
    page = get(client, plan_page).get_data(as_text=True)
    assert "generated" in page and "Approve" not in page

    calls = [json.loads(line) for line in
             (home / "fake-graph-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [c[0] for c in calls] == ["plan", "resume", "resume"]
    assert calls[1][-1] == "--feedback=-add one more" and calls[2][-1] == "--approve"
    course_page = get(client, "/c/demo-course/").get_data(as_text=True)
    assert "_p3-w9-test" in course_page and "waiting for you" not in course_page
