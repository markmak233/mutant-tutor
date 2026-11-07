"""The Coverage page: coverage.md shown, a model's draft from stored answers (written here by
hand, no model), used as coverage.md only when there is none, and added to one only at its
end. Lecture text is made up; tasks.start and the launcher are stand-ins."""

import json
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import coverage, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402
from tutor.materials import cache  # noqa: E402
from tutor.materials import coverage as cov  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
TOML = ('course = "demo"\n[llm.profiles.cheap]\nprovider = "glm"\nmodel = "glm-5.3-flash"\n'
        '[llm.roles]\nplan = "cheap"\n')


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    _write(tmp_path / "demo" / "workspace.toml", TOML)
    return tmp_path


def _course_root(root) -> Path:
    return root / "demo"


def _lecture(root, name, answer=None):
    """A cached lecture, and the model's stored answer for it when given."""
    entries = [cache.Entry(1, "1.1", "text", "h1", "Course admin"),
               cache.Entry(2, "1.2", "text", "h2", "Word count with a combiner")]
    path = _course_root(root) / "cache" / "lectures" / f"{name}.md"
    _write(path, cache.render(name, entries))
    if answer is not None:
        _write(cov.result_path(_course_root(root), path), json.dumps({
            "hash": cov.lecture_hash({e.number: e for e in entries}), "model": "cheap: m",
            "points": answer}))


POINTS = [{"point": "Admin", "first_page": 1, "last_page": 1, "status": "n/a"},
          {"point": "Word count", "first_page": 2, "last_page": 2, "status": "gap-code",
           "check": "the counts with a combiner"}]


class FakeLauncher:
    from tutor.gui.launch import LaunchError

    def __init__(self):
        self.opened = []

    def open_folder(self, path):
        self.opened.append(path)

    def open_in_vscode(self, folder, file):
        self.opened.append(file)


def _browser(root, launcher=None):
    client = create_app(root, TOKEN, port=5000, launcher=launcher or FakeLauncher()
                        ).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


def _page(client) -> str:
    return client.get("/c/demo/coverage/", base_url=BASE).get_data(as_text=True)


@pytest.fixture
def started(monkeypatch):
    calls = []

    def fake_start(course_root, title, commands, env, meta=None):
        calls.append({"commands": commands, "meta": meta})
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    return calls


def test_a_lecture_not_drafted_offers_the_run_naming_the_model(root, started):
    _lecture(root, "L1.pdf")
    client = _browser(root)

    text = _page(client)
    assert "1 lecture not drafted yet" in text and "cheap (glm: glm-5.3-flash)" in text

    response = post(client, "/c/demo/coverage/draft")
    assert response.status_code == 303
    assert started[0]["commands"] == [coverage.draft_command()]
    assert started[0]["meta"] == {"kind": "coverage-draft"}


def test_the_draft_is_shown_and_used_as_coverage_only_once(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)

    text = _page(client)
    assert "gap-code [the counts with a combiner]" in text and "Use as coverage.md" in text

    response = post(client, "/c/demo/coverage/use")
    assert "notice=coverage-used" in response.location
    written = (_course_root(root) / "coverage.md").read_text(encoding="utf-8")
    assert written.startswith("# Knowledge-point coverage\n") and "## L1.pdf" in written

    (_course_root(root) / "coverage.md").write_text("# Mine, edited\n", encoding="utf-8")
    refused = post(client, "/c/demo/coverage/use")
    assert refused.status_code == 400 and "never written over" in refused.get_data(as_text=True)
    assert (_course_root(root) / "coverage.md").read_text(encoding="utf-8") == "# Mine, edited\n"


def test_a_new_lecture_is_added_at_the_end_and_nothing_else_changes(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)
    post(client, "/c/demo/coverage/use")
    path = _course_root(root) / "coverage.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nMy own note.\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    _lecture(root, "L2.pdf", POINTS)
    text = _page(client)
    assert 'name="lecture" value="L2.pdf" checked' in text  # named the drafts' way: ticked
    assert 'value="L1.pdf"' not in text  # coverage.md has it already

    response = post(client, "/c/demo/coverage/add", {"lecture": ["L2.pdf"]})
    assert "notice=coverage-added" in response.location
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before.rstrip("\n")) and after.count("## L2.pdf") == 1
    assert "Added from the draft by cheap: m" in after
    assert (_course_root(root) / "coverage.md.bak").read_text(encoding="utf-8") == before
    assert "has a section for every drafted lecture" in _page(client)


def test_a_hand_made_coverage_is_matched_by_nothing_so_nothing_is_ticked(root):
    _lecture(root, "L1.pdf", POINTS)
    _write(_course_root(root) / "coverage.md", "# Mine\n\n### L1.1 Course introduction\n")
    client = _browser(root)

    text = _page(client)
    assert 'name="lecture" value="L1.pdf">' in text  # offered, not ticked
    assert "names its lectures its own way" in text
    assert post(client, "/c/demo/coverage/add", {}).status_code == 400  # nothing ticked


def test_edit_opens_coverage_or_the_draft_in_vscode(root):
    _lecture(root, "L1.pdf", POINTS)
    launcher = FakeLauncher()
    client = _browser(root, launcher)
    assert post(client, "/c/demo/coverage/edit").status_code == 404  # no coverage.md yet
    _write(_course_root(root) / "coverage.md", "# Mine\n")
    _write(_course_root(root) / cov.DRAFT_FILE, "# draft\n")

    post(client, "/c/demo/coverage/edit")
    post(client, "/c/demo/coverage/edit", {"file": "draft"})

    assert [p.name for p in launcher.opened] == ["coverage.md", cov.DRAFT_FILE]


def test_a_finished_draft_sends_the_watching_page_back(root):
    folder = _course_root(root) / tasks.TASKS_DIR / "20260102-100000-0abcde"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "T", "status": "done", "started": "2026-01-02 10:00:00",
        "commands": [["python"]], "exit_codes": [0], "pid": None,
        "meta": {"kind": "coverage-draft"}}))
    _write(folder / tasks.LOG_FILE, "")
    polled = _browser(root).get(f"/c/demo/tasks/{folder.name}/log", base_url=BASE,
                                headers={"HX-Request": "true"})
    assert polled.headers["HX-Redirect"] == "/c/demo/coverage/?notice=coverage-drafted"


def test_the_course_page_links_to_it(root):
    text = _browser(root).get("/c/demo/", base_url=BASE).get_data(as_text=True)
    assert 'class="button" href="/c/demo/coverage/"' in text
    assert '<a href="/c/demo/coverage/">Coverage →</a>' in text
