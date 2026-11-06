"""The GUI's pages, on a made-up course in a temp folder: who may open them, and that they
show the learner's side only, escaped."""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import __main__ as gui_main  # noqa: E402
from tutor.gui import courses, launch, tasks  # noqa: E402
from tutor.gui.app import COOKIE, create_app  # noqa: E402
from tutor.gui.rendering import question_html  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
BATCH = "2026-01-01_demo"
SECRET = "THE_REFERENCE_SOLUTION"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """Two exercises: one passed and valid, with a title that tries to be markup; one never
    started, marked skip, with an invalid report. solutions/ holds a reference no page may show."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", 'course = "demo"\n')
    batch = course / "batches" / BATCH
    _write(batch / "manifest.json", json.dumps({
        "mode": "learn", "exercises": ["ex-one", "ex-two"], "priority": {"skip": ["ex-two"]}}))
    _write(batch / "exercises" / "ex-one" / "question.md",
           "# Count <script>alert(1)</script> words\n\nTask text.\n")
    _write(batch / "exercises" / "ex-one" / "student.py", "def count(): ...\n")
    _write(batch / "exercises" / "ex-two" / "question.md", "# Second\n")
    _write(batch / "solutions" / "ex-one" / "validation.json", '{"valid": true}')
    _write(batch / "solutions" / "ex-one" / "reference.py", f"# {SECRET}\n")
    _write(batch / "solutions" / "ex-two" / "validation.json", '{"valid": false}')
    # A failed run's first_error can hold a hand-calculation answer: no page may show it.
    _write(course / "attempts" / "attempts.jsonl",
           json.dumps({"batch": BATCH, "exercise": "ex-one", "result": "failed",
                       "time": "2026-01-02T10:00:00", "first_error": SECRET}) + "\n"
           + json.dumps({"batch": BATCH, "exercise": "ex-one", "result": "passed",
                         "time": "2026-01-02T10:05:00"}) + "\n")
    _write(tmp_path / "courses" / "not-a-course" / "notes.txt", "x")
    return tmp_path / "courses"


@pytest.fixture
def client(root):
    """A browser that has opened the printed link once, so it holds the cookie."""
    client = create_app(root, TOKEN, port=5000).test_client()
    assert client.get(f"/?token={TOKEN}", base_url=BASE).status_code == 302
    return client


def get(client, path, **kwargs):
    return client.get(path, base_url=BASE, **kwargs)


def test_the_link_swaps_the_token_for_a_strict_cookie(root):
    client = create_app(root, TOKEN, port=5000).test_client()

    response = client.get(f"/c/demo-course/?token={TOKEN}", base_url=BASE)

    assert response.status_code == 302
    assert response.headers["Location"] == "/c/demo-course/"  # the token leaves the address bar
    cookie = response.headers["Set-Cookie"]
    assert f"{COOKIE}={TOKEN}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie


@pytest.mark.parametrize("token", [None, "wrong"])
def test_no_page_without_the_token(root, token):
    client = create_app(root, TOKEN, port=5000).test_client()
    query = f"?token={token}" if token else ""

    assert client.get(f"/{query}", base_url=BASE).status_code == 403
    assert client.get("/static/style.css", base_url=BASE).status_code == 403


@pytest.mark.parametrize("host", ["http://evil.example:5000", "http://127.0.0.1:5001",
                                  "http://localhost"])
def test_another_host_name_is_refused_even_with_the_cookie(client, host):
    """DNS rebinding: a hostile name that points at 127.0.0.1 still says its own name."""
    client.set_cookie(COOKIE, TOKEN, domain=host.split("//")[1].split(":")[0])

    assert client.get("/", base_url=host).status_code == 403


def test_pages_list_courses_batches_and_progress(client):
    home = get(client, "/").get_data(as_text=True)
    assert "demo" in home and "not-a-course" not in home
    assert "1 / 2" in home  # one of two exercises passed
    assert "Every must exercise has passed" in home  # ex-two is marked skip
    assert "2026-01-02 10:05" in home  # the latest run

    course = get(client, "/c/demo-course/").get_data(as_text=True)
    assert BATCH in course and "learn" in course

    batch = get(client, f"/c/demo-course/b/{BATCH}/").get_data(as_text=True)
    assert "passed" in batch and "not started" in batch and "invalid" in batch
    assert "priority-skip" in batch


def test_generated_text_is_escaped(client):
    page = get(client, f"/c/demo-course/b/{BATCH}/e/ex-one/").get_data(as_text=True)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_headers_forbid_inline_scripts_and_framing(client):
    headers = get(client, "/").headers

    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", [
    f"/c/demo-course/b/{BATCH}/e/..%2Fsolutions%2Fex-one/",
    f"/c/demo-course/b/{BATCH}/e/missing/",
    "/c/demo-course/b/.drafts/",
    "/c/demo-course/b/..%2F..%2F/",
    "/c/not-a-course/",
    "/c/..%2Fcourses/",
])
def test_names_outside_the_learners_side_are_not_found(client, path):
    assert get(client, path).status_code == 404


@pytest.mark.parametrize("name", ["..", ".", ".drafts", "a/b", "", "ex-one/../../.."])
def test_only_plain_visible_folder_names_are_followed(root, name):
    """The check itself, below Flask's routing, which already stops most of these: names also
    come from manifest.json, which the router never sees."""
    exercises = root / "demo-course" / "batches" / BATCH / "exercises"

    assert courses._child(exercises, name) is None
    assert courses._child(exercises, "ex-one") == (exercises / "ex-one").resolve()


def test_a_link_out_of_the_course_is_not_followed(root):
    outside = root.parent / "outside"
    outside.mkdir()
    exercises = root / "demo-course" / "batches" / BATCH / "exercises"
    try:
        (exercises / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as e:  # Windows without Developer Mode or admin rights (WinError 1314)
        pytest.skip(f"cannot create a symbolic link here: {e}")

    assert courses._child(exercises, "linked") is None


def test_no_page_shows_the_solutions_side(client):
    for path in ["/", "/c/demo-course/", f"/c/demo-course/b/{BATCH}/",
                 f"/c/demo-course/b/{BATCH}/e/ex-one/", f"/c/demo-course/b/{BATCH}/e/ex-two/"]:
        assert SECRET not in get(client, path).get_data(as_text=True)


def test_a_course_inside_a_git_repository_is_refused(client, root):
    """The same rule as any workspace: private material never sits inside a repository."""
    repo = root / "in-a-repo"
    _write(repo / "workspace.toml", 'course = "leaky"\n')
    (repo / ".git").mkdir()

    page = get(client, "/").get_data(as_text=True)

    assert "in-a-repo" in page and "git repository" in page  # under "Not shown"
    assert get(client, "/c/in-a-repo/").status_code == 404


def test_the_root_defaults_to_the_folder_above_the_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here to load
    (tmp_path / "courses" / "one").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "courses" / "one"))

    assert gui_main._root(None) == (tmp_path / "courses").resolve()


def test_no_root_and_no_workspace_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)

    assert gui_main.main(["--no-browser"]) == 2
    assert "--root" in capsys.readouterr().err


def _row(id, priority="must", status="not started"):
    return courses.ExerciseRow(id, id, priority, status, None, True)


def test_next_is_the_first_unpassed_must_exercise_of_the_newest_batch():
    newest = courses.BatchView("2026-02", "learn", [
        _row("a", status="passed"), _row("b", priority="optional"), _row("c"), _row("d")])
    older = courses.BatchView("2026-01", "learn", [_row("old")])
    view = courses.CourseView(None, [newest, older], [], [])

    assert view.next == (newest, newest.exercises[2])
    assert courses.CourseView(None, [older], [], []).next[1].id == "old"
    done = courses.BatchView("2026-03", "learn", [_row("x", status="passed")])
    assert courses.CourseView(None, [done], [], []).next is None


def test_markdown_headings_get_githubs_ids_and_tables_render():
    html = question_html("# Count words\n\n[Task](#task)\n\n## Task\n\n## Task\n\n"
                         "| a | b |\n|---|---|\n| 1 | 2 |\n")

    assert '<h1 id="count-words">' in html
    assert '<h2 id="task">' in html and '<h2 id="task-1">' in html
    assert '<a href="#task">' in html
    assert "<table>" in html


@pytest.mark.parametrize("text", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[click](javascript:alert(1))",
    "[click](data:text/html,<script>alert(1)</script>)",
])
def test_markdown_never_lets_markup_or_script_links_through(text):
    html = question_html(text)

    assert "<script" not in html and "<img" not in html
    assert 'href="javascript:' not in html and 'href="data:' not in html


@pytest.mark.parametrize("text", [
    "<details><summary>Steps</summary>\n\nFirst **step**.\n\n</details>\n",
    "<details>\n<summary>Steps</summary>\n\nFirst **step**.\n</details>\n",
])
def test_details_blocks_fold_in_both_shapes_questions_use(text):
    html = question_html(text)

    assert "<details><summary>Steps</summary>" in html
    assert "<strong>step</strong>" in html and "</details>" in html
    assert "&lt;details&gt;" not in html


def test_details_inside_a_code_block_stay_code():
    html = question_html("```\n<details>\n</details>\n```\n")

    assert "<details>" not in html and "&lt;details&gt;" in html


def test_a_summary_is_rendered_inline_and_escaped():
    html = question_html("<details><summary>The <b>sum</b> $x$</summary>\nbody\n</details>")

    assert "&lt;b&gt;sum&lt;/b&gt;" in html and "<math" in html


def test_formulas_become_mathml_and_money_stays_text():
    html = question_html("Cost $5 and $10.\n\n$$\\frac{a}{b}$$\n\nInline $p_1$ here.\n")

    assert "Cost $5 and $10." in html
    assert '<math display="block"><mrow><mfrac>' in html
    assert "<msub>" in html


@pytest.mark.parametrize("latex", [
    "\\text{<script>alert(1)</script>}",  # latex2mathml copies this out as a real tag
    "\\href{javascript:alert(1)}{x}",  # and this as a link attribute
])
def test_a_formula_that_is_not_plain_mathml_is_shown_as_source(latex):
    html = question_html(f"${latex}$")

    assert "<script" not in html and "href=" not in html
    assert '<code class="math-source">' in html


# --- step 2: Open folder and Open in VS Code ---------------------------------------------


class FakeLauncher:
    """Stands in for tutor.gui.launch: records what would be opened, or fails as asked."""

    LaunchError = launch.LaunchError

    def __init__(self, fail: str | None = None):
        self.calls: list[tuple] = []
        self.fail = fail

    def _call(self, *args):
        if self.fail:
            raise launch.LaunchError(self.fail)
        self.calls.append(args)

    def open_folder(self, path):
        self._call("folder", path)

    def open_in_vscode(self, folder, file):
        self._call("vscode", folder, file)


def _browser(root, launcher):
    client = create_app(root, TOKEN, port=5000, launcher=launcher).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, origin=BASE):
    headers = {"Origin": origin} if origin else {}
    return client.post(path, base_url=BASE, headers=headers)


def test_each_button_opens_its_own_folder(root):
    launcher = FakeLauncher()
    client = _browser(root, launcher)
    course = (root / "demo-course").resolve()
    exercises = course / "batches" / BATCH / "exercises"

    assert post(client, "/c/demo-course/open-folder").status_code == 303
    post(client, f"/c/demo-course/b/{BATCH}/open-folder")
    post(client, f"/c/demo-course/b/{BATCH}/e/ex-one/open-folder")
    response = post(client, f"/c/demo-course/b/{BATCH}/e/ex-one/open-ide")

    assert launcher.calls == [
        ("folder", course),
        ("folder", exercises),  # the batch's exercises/, not the folder beside solutions/
        ("folder", exercises / "ex-one"),
        ("vscode", course, exercises / "ex-one" / "student.py"),  # the course: its venv
    ]
    assert response.headers["Location"] == f"/c/demo-course/b/{BATCH}/e/ex-one/?notice=opened-ide"


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "http://127.0.0.1:5001"])
def test_an_action_needs_this_servers_origin(root, origin):
    launcher = FakeLauncher()
    client = _browser(root, launcher)

    assert post(client, "/c/demo-course/open-folder", origin=origin).status_code == 403
    assert launcher.calls == []


def test_an_action_is_post_only_and_needs_the_cookie(root):
    launcher = FakeLauncher()
    client = create_app(root, TOKEN, port=5000, launcher=launcher).test_client()

    assert post(client, "/c/demo-course/open-folder").status_code == 403  # no cookie yet
    client.get(f"/?token={TOKEN}", base_url=BASE)
    assert client.get("/c/demo-course/open-folder", base_url=BASE).status_code == 405
    assert launcher.calls == []


@pytest.mark.parametrize("path", [
    f"/c/demo-course/b/{BATCH}/e/missing/open-ide",
    f"/c/demo-course/b/{BATCH}/e/..%2F..%2Fsolutions/open-folder",
    "/c/demo-course/b/.drafts/open-folder",
    "/c/not-a-course/open-folder",
])
def test_an_action_on_an_unknown_name_opens_nothing(root, path):
    launcher = FakeLauncher()

    assert post(_browser(root, launcher), path).status_code == 404
    assert launcher.calls == []


def test_a_failure_comes_back_as_its_fixed_notice(root):
    client = _browser(root, FakeLauncher(fail="no-vscode"))

    response = post(client, f"/c/demo-course/b/{BATCH}/e/ex-one/open-ide")
    page = client.get(response.headers["Location"], base_url=BASE).get_data(as_text=True)

    assert response.headers["Location"].endswith("?notice=no-vscode")
    assert "VS Code was not found" in page


def test_an_unknown_notice_is_not_shown_or_echoed(client):
    page = get(client, "/?notice=%3Cb%3Eforged%3C/b%3E").get_data(as_text=True)

    assert "forged" not in page and 'class="notice' not in page


def test_open_folder_uses_the_platforms_opener(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(launch.sys, "platform", "darwin")

    launch.open_folder(tmp_path, start=lambda cmd, **kw: started.append((cmd, kw)))

    assert started[0][0] == ["open", str(tmp_path)]
    assert started[0][1]["start_new_session"] is True  # Ctrl+C on the GUI leaves it open


def test_no_opener_or_no_vscode_is_a_notice_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(launch.shutil, "which", lambda name: None)
    monkeypatch.setattr(launch, "MACOS_VSCODE", tmp_path / "missing")
    monkeypatch.setattr(launch.sys, "platform", "linux")

    with pytest.raises(launch.LaunchError, match="no-file-manager"):
        launch.open_folder(tmp_path)
    with pytest.raises(launch.LaunchError, match="no-vscode"):
        launch.open_in_vscode(tmp_path, None)


def test_a_program_that_cannot_start_is_a_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(launch.sys, "platform", "darwin")

    def broken(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(launch.LaunchError, match="failed"):
        launch.open_folder(tmp_path, start=broken)


def test_only_workspace_dir_is_read_from_env_file(tmp_path, monkeypatch):
    """The rest of .env (API keys) must not enter the GUI's environment, which VS Code and
    its terminals would inherit."""
    (tmp_path / "courses" / "one").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        f"OPENAI_API_KEY=sk-not-real\n# WORKSPACE_DIR=/elsewhere\n"
        f"WORKSPACE_DIR='{tmp_path / 'courses' / 'one'}'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert gui_main._root(None) == (tmp_path / "courses").resolve()
    assert "OPENAI_API_KEY" not in gui_main.os.environ


def test_the_referrer_policy_lets_our_own_buttons_send_their_origin(client):
    """Under "no-referrer" a browser sends `Origin: null` on a POST, and every button got 403
    (found by the owner's first click; Chrome confirmed it)."""
    assert get(client, "/").headers["Referrer-Policy"] == "same-origin"


# --- step 3: long tasks --------------------------------------------------------------------


def _task(root, status="done", log="", pid=None, codes=(0,)):
    """A task folder as the runner leaves it, without running anything."""
    folder = root / "demo-course" / tasks.TASKS_DIR / "20260102-100000-abcdef"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / tasks.TASK_FILE).write_text(json.dumps({
        "title": "Validate demo", "status": status, "started": "2026-01-02 10:00:00",
        "finished": None if status == "running" else "2026-01-02 10:01:00",
        "commands": [["python", "-m", "tutor.validation"]], "exit_codes": list(codes),
        "pid": pid}), encoding="utf-8")
    (folder / tasks.LOG_FILE).write_text(log, encoding="utf-8")
    return folder.name


def test_validate_all_starts_one_validation_per_listed_exercise(root, monkeypatch):
    started = []

    def fake_start(course_root, title, commands, env):
        started.append((course_root, title, commands, env))
        return tasks.Task("20260102-100000-abcdef", course_root, title, "running", "", None,
                          commands, [], 1)

    monkeypatch.setattr(tasks, "start", fake_start)
    response = post(_browser(root, FakeLauncher()), f"/c/demo-course/b/{BATCH}/validate")

    course_root, title, commands, env = started[0]
    batch = (root / "demo-course" / "batches" / BATCH).resolve()
    assert [c[2:] for c in commands] == [
        ["tutor.validation", str(batch / "exercises" / e), str(batch / "solutions" / e)]
        for e in ("ex-one", "ex-two")]
    assert env["WORKSPACE_DIR"] == str(course_root)  # the course's, not .env's
    assert response.headers["Location"] == "/c/demo-course/tasks/20260102-100000-abcdef/"


def test_a_second_task_goes_to_the_running_one(root, monkeypatch):
    running = tasks.Task("20260102-100000-abcdef", root, "Validate", "running", "", None, [],
                         [], 1)

    def busy(*args, **kwargs):
        raise tasks.TaskBusy(running)

    monkeypatch.setattr(tasks, "start", busy)
    response = post(_browser(root, FakeLauncher()), f"/c/demo-course/b/{BATCH}/validate")

    assert response.headers["Location"].endswith("/tasks/20260102-100000-abcdef/?notice=busy")


def test_a_running_tasks_log_polls_and_a_finished_one_stops(client, root):
    task_id = _task(root, status="running", pid=os.getpid(), codes=())
    running = get(client, f"/c/demo-course/tasks/{task_id}/log").get_data(as_text=True)
    assert 'hx-trigger="every 1s"' in running and "Stop" in running

    _task(root, status="done")
    done = get(client, f"/c/demo-course/tasks/{task_id}/log").get_data(as_text=True)
    assert "hx-trigger" not in done and "Stop" not in done


def test_a_log_is_shown_escaped(client, root):
    task_id = _task(root, log="<script>alert(1)</script>\n")

    page = get(client, f"/c/demo-course/tasks/{task_id}/").get_data(as_text=True)

    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page


def test_stop_is_post_only_and_says_when_nothing_runs(root):
    task_id = _task(root, status="done")
    client = _browser(root, FakeLauncher())

    assert client.get(f"/c/demo-course/tasks/{task_id}/stop", base_url=BASE).status_code == 405
    response = post(client, f"/c/demo-course/tasks/{task_id}/stop")
    assert response.headers["Location"].endswith("?notice=not-running")
    assert post(client, "/c/demo-course/tasks/..%2F..%2Fx/stop").status_code == 404


def test_the_course_page_lists_its_tasks(client, root):
    _task(root, status="failed", codes=(0, 1))

    page = get(client, "/c/demo-course/").get_data(as_text=True)

    assert "Validate demo" in page and "chip-failed" in page


def test_pages_load_htmx_from_this_server_with_eval_off(client):
    page = get(client, "/").get_data(as_text=True)

    assert '/static/htmx-2.0.11.min.js' in page and '"allowEval": false' in page


def test_the_favicon_request_gets_no_content(client):
    assert get(client, "/favicon.ico").status_code == 204
