"""The Coverage page on coverage.json: a model's draft from stored answers (written here by
hand, no model) becomes the data; a hand-kept coverage.md is converted once; points are
edited, added, removed and kept. Lecture text is made up; tasks.start and the launcher are
stand-ins."""

import json
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import coverage, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402
from tutor.materials import cache  # noqa: E402
from tutor.materials import coverage as cov  # noqa: E402
from tutor.materials import coverage_store as cs  # noqa: E402

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
    assert started[0]["commands"] == [coverage.first_draft_command()]  # no data yet
    assert started[0]["meta"] == {"kind": "coverage-draft"}


def test_the_draft_is_shown_and_becomes_the_data_once(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)

    text = _page(client)
    assert "gap-code [the counts with a combiner]" in text and "Use as coverage" in text

    response = post(client, "/c/demo/coverage/use")
    assert "notice=coverage-used" in response.location
    store = cs.load(_course_root(root))
    assert list(store.lectures) == ["L1"]
    points = store.lectures["L1"].points
    assert [(p.slides, p.status, p.check) for p in points] == [
        ("1.1", "n/a", ""), ("1.2", "gap-code", "the counts with a combiner")]
    assert store.lectures["L1"].model == "cheap: m" and store.lectures["L1"].source_hash
    written = (_course_root(root) / "coverage.md").read_text(encoding="utf-8")
    assert written == cs.render(store) and "## L1\n" in written

    refused = post(client, "/c/demo/coverage/use")
    assert refused.status_code == 400 and "exists already" in refused.get_data(as_text=True)


def test_with_data_the_page_lists_each_lecture_and_drafts_only_new_or_changed_ones(root, started):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)
    post(client, "/c/demo/coverage/use")
    _lecture(root, "L2.pdf")  # read, not drafted

    text = _page(client)

    assert "1 lecture, 2 points in the data" in text
    assert "1 lecture not drafted yet, or changed since: L2" in text
    assert "Word count" in text and 'href="/c/demo/coverage/l/L1/p/' in text
    assert "Draft every lecture again" in text
    post(client, "/c/demo/coverage/draft")
    post(client, "/c/demo/coverage/draft", {"all": "1"})
    assert started[0]["commands"] == [coverage.draft_command()]
    assert started[1]["commands"] == [coverage.draft_command(every=True)]
    assert started[1]["commands"][0][-1] == "--all"


def test_a_hand_kept_file_is_shown_with_its_conversion_and_converted_once(root):
    _lecture(root, "L1.pdf", POINTS)
    _write(_course_root(root) / "coverage.md",
           "# Mine\n\n### L1 Course introduction\n| Slides | Point | Status |\n|---|---|---|\n"
           "| 1.2 | Word count | ex:word-count (p2) |\n")
    client = _browser(root)

    text = _page(client)
    assert "Convert coverage.md to coverage.json" in text
    assert "read 1 lecture and 1 point from it" in text
    assert "1 covered row(s) had no gap kind" in text
    assert "Use as coverage" not in text  # the file comes first

    response = post(client, "/c/demo/coverage/convert")
    assert "notice=coverage-converted" in response.location
    store = cs.load(_course_root(root))
    assert store.lectures["L1"].title == "L1 Course introduction"
    assert store.lectures["L1"].points[0].covered_by == [
        cs.Covered(mark="ex:word-count", batch="p2")]
    backup = (_course_root(root) / "coverage.md.bak").read_text(encoding="utf-8")
    assert backup.startswith("# Mine")
    assert post(client, "/c/demo/coverage/convert").status_code == 400  # once


def test_a_point_is_edited_on_its_page_and_becomes_the_owners(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)
    post(client, "/c/demo/coverage/use")
    point = cs.load(_course_root(root)).lectures["L1"].points[1]

    form = client.get(f"/c/demo/coverage/l/L1/p/{point.id}/", base_url=BASE).get_data(as_text=True)
    assert 'value="the counts with a combiner"' in form and "From a draft (pages 2-2)" in form

    response = post(client, f"/c/demo/coverage/l/L1/p/{point.id}/save", {
        "slides": "1.2", "point": "Word count", "status": "gap-code",
        "check": "the counts with and without a combiner", "note": "",
        "covered": "ex:word-count (p2-w1), ex:word-count-2 (p2-w1)"})
    assert "notice=coverage-saved" in response.location
    saved = cs.load(_course_root(root)).lectures["L1"].points[1]
    assert saved.owner_edited and saved.check == "the counts with and without a combiner"
    assert [c.mark for c in saved.covered_by] == ["ex:word-count", "ex:word-count-2"]
    assert saved.id == point.id  # the same slides and text: the same id
    assert "ex:word-count, ex:word-count-2 (p2-w1)" in (
        _course_root(root) / "coverage.md").read_text(encoding="utf-8")

    bad = post(client, f"/c/demo/coverage/l/L1/p/{point.id}/save",
               {"slides": "", "point": "x", "status": "odd", "covered": "nonsense"})
    assert bad.status_code == 400
    assert "Slides:" in bad.get_data(as_text=True) and "Status:" in bad.get_data(as_text=True)


def test_points_are_added_removed_kept_and_lecture_notes_saved(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)
    post(client, "/c/demo/coverage/use")

    response = post(client, "/c/demo/coverage/l/L1/add", {
        "slides": "1.2", "point": "Combiner vs reducer", "status": "gap-tracing"})
    assert "notice=coverage-saved" in response.location
    store = cs.load(_course_root(root))
    added = store.lectures["L1"].points[2]
    assert (added.point, added.status, added.owner_edited) == (
        "Combiner vs reducer", "gap-tracing", True)
    assert post(client, "/c/demo/coverage/l/L1/add", {
        "slides": "1.2", "point": "Combiner vs reducer", "status": "concept"}).status_code == 400

    # A draft no longer found the first point: the page offers Remove and Keep.
    store.lectures["L1"].points[0].removed = True
    cs.save(_course_root(root), store)
    text = _page(client)
    assert "1 to confirm" in text and "No longer found in the lecture" in text
    first, second = store.lectures["L1"].points[0], store.lectures["L1"].points[1]
    post(client, f"/c/demo/coverage/l/L1/p/{first.id}/keep")
    kept = cs.load(_course_root(root)).lectures["L1"].points[0]
    assert not kept.removed and kept.owner_edited
    post(client, f"/c/demo/coverage/l/L1/p/{second.id}/drop")
    assert [p.point for p in cs.load(_course_root(root)).lectures["L1"].points] == [
        "Admin", "Combiner vs reducer"]

    post(client, "/c/demo/coverage/l/L1/notes", {"title": "L1 Intro", "notes": "Both halves."})
    lecture = cs.load(_course_root(root)).lectures["L1"]
    assert (lecture.title, lecture.notes) == ("L1 Intro", "Both halves.")
    assert "**L1 Intro**" in (_course_root(root) / "coverage.md").read_text(encoding="utf-8")
    assert post(client, "/c/demo/coverage/l/L9/notes", {"title": "x"}).status_code == 400


def test_a_hand_edited_rendered_file_is_flagged_and_written_again(root):
    _lecture(root, "L1.pdf", POINTS)
    client = _browser(root)
    post(client, "/c/demo/coverage/use")
    assert "was edited after the last save" not in _page(client)

    path = _course_root(root) / "coverage.md"
    path.write_text(path.read_text(encoding="utf-8") + "| 9 | typed | gap-code |\n",
                    encoding="utf-8")
    assert "was edited after the last save" in _page(client)

    response = post(client, "/c/demo/coverage/render")
    assert "notice=coverage-rendered" in response.location
    assert "typed" not in path.read_text(encoding="utf-8")


def test_edit_opens_the_data_the_file_or_the_draft_in_vscode(root):
    _lecture(root, "L1.pdf", POINTS)
    launcher = FakeLauncher()
    client = _browser(root, launcher)
    assert post(client, "/c/demo/coverage/edit").status_code == 404  # nothing yet
    _write(_course_root(root) / "coverage.md", "# Mine\n")
    _write(_course_root(root) / cov.DRAFT_FILE, "# draft\n")
    post(client, "/c/demo/coverage/edit")
    post(client, "/c/demo/coverage/edit", {"file": "draft"})
    post(client, "/c/demo/coverage/convert")
    post(client, "/c/demo/coverage/edit")

    assert [p.name for p in launcher.opened] == ["coverage.md", cov.DRAFT_FILE, "coverage.json"]


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
