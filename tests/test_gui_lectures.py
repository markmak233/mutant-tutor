"""Lectures and new courses in the GUI (step 5): what is read, uploads saved all or nothing,
the commands they start, and a new course made by `tutor.workspace init` as a task. No
command runs here: tasks.start is replaced where a route would start one."""

import io
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import courses, lectures, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
PDF = b"%PDF-1.4\n% a made-up lecture\n"
TOML = """course = "demo"

[llm.roles]
vision = "seer"
generate = "seer"

[llm.profiles.seer]
provider = "glm"
model = "glm-5.3-flash"

[isolation]
runner = "host"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cache(course_root: Path, pdf: str, reads: list[str]) -> None:
    """A cache file as tutor.materials writes it, one page per entry of `reads`."""
    pages = [f"## p.{n} · slide 1.{n}\n<!-- read: {read} · hash: h{n} -->\npage {n}\n"
             for n, read in enumerate(reads, 1)]
    _write(course_root / "cache" / "lectures" / f"{pdf}.md", f"# {pdf}\n\n" + "\n".join(pages))


@pytest.fixture
def root(tmp_path):
    """A course with three lectures: read, read with two image pages waiting, not read."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", TOML)
    for name in ("L1.pdf", "L2.pdf", "L3.pdf"):
        (course / "lectures").mkdir(parents=True, exist_ok=True)
        (course / "lectures" / name).write_bytes(PDF)
    (course / "lectures" / "notes.txt").write_text("x", encoding="utf-8")
    (course / "lectures" / ".hidden.pdf").write_bytes(PDF)
    _cache(course, "L1.pdf", ["text", "text"])
    _cache(course, "L2.pdf", ["text", "pending", "partial"])
    return tmp_path / "courses"


@pytest.fixture
def course(root):
    return courses.get_course(root, "demo-course")


class File:
    """An upload as werkzeug hands it over."""

    def __init__(self, filename, data=PDF):
        self.filename = filename
        self.stream = io.BytesIO(data)


def _browser(root):
    client = create_app(root, TOKEN, port=5000).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE},
                       content_type="multipart/form-data")


@pytest.fixture
def started(monkeypatch):
    """Records the tasks a route would start, instead of starting them."""
    calls = []

    def fake_start(course_root, title, commands, env, meta=None):
        calls.append({"root": course_root, "title": title, "commands": commands, "env": env,
                      "meta": meta})
        course_root.joinpath(tasks.TASKS_DIR).mkdir(parents=True, exist_ok=True)
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    return calls


# --- what is read ------------------------------------------------------------------------------


def test_each_pdf_shows_what_the_cache_holds(course):
    rows = {r.name: r for r in lectures.lecture_rows(course)}

    assert list(rows) == ["L1.pdf", "L2.pdf", "L3.pdf"]  # no hidden file, no other kind
    assert (rows["L1.pdf"].pages, rows["L1.pdf"].waiting) == (2, 0)
    assert (rows["L2.pdf"].pages, rows["L2.pdf"].waiting) == (3, 2)  # pending and partial
    assert not rows["L3.pdf"].read


def test_the_vision_model_is_named_or_missing(course, tmp_path):
    assert lectures.vision_model(course) == "seer (glm: glm-5.3-flash)"
    _write(course.workspace.root / "workspace.toml", 'course = "demo"\n[llm.roles]\n')
    assert lectures.vision_model(course) is None
    _write(course.workspace.root / "workspace.toml", 'course = "demo"\n')
    assert "LLM_" in lectures.vision_model(course)  # no [llm]: the .env variables


def test_read_commands_name_each_pdf_by_its_full_path(course):
    folder = course.workspace.lecture_dir
    text = lectures.read_command([folder / "L1.pdf"])
    vision = lectures.read_command([folder / "L2.pdf"], vision=True)

    assert text[1:] == ["-m", "tutor.materials", str((folder / "L1.pdf").resolve())]
    assert vision[3] == "--vision" and Path(vision[4]).is_absolute()


# --- uploads -----------------------------------------------------------------------------------


def test_uploads_are_saved_with_their_names(tmp_path):
    decomposed = unicodedata.normalize("NFD", "Vorlesung Übung (2).PDF")
    saved, problems = lectures.save_pdfs(tmp_path, [File("L4 Streams.pdf"), File(decomposed),
                                                    File("C:\\Users\\x\\L5.pdf")])

    assert problems == []
    assert [p.name for p in saved] == ["L4 Streams.pdf", "Vorlesung Übung (2).pdf", "L5.pdf"]
    assert all(p.read_bytes() == PDF for p in saved)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in saved)


@pytest.mark.parametrize(("name", "data", "problem"), [
    ("slides.pptx", PDF, "only PDF"),
    ("notes.pdf", b"PK\x03\x04 a zip file", "not a PDF"),
    (".hidden.pdf", PDF, "rename it"),
    ("-x.pdf", PDF, "rename it"),
    ("a<script>.pdf", PDF, "rename it"),
    ("a[1].pdf", PDF, "rename it"),
    ("L1.pdf", PDF, "exists"),  # never over an existing lecture
    ("l1.PDF", PDF, "exists"),  # nor one whose name differs only in case
])
def test_one_bad_file_saves_none(tmp_path, name, data, problem):
    (tmp_path / "L1.pdf").write_bytes(b"%PDF- the old one")

    saved, problems = lectures.save_pdfs(tmp_path, [File("good.pdf"), File(name, data)])

    assert saved == [] and problem in " ".join(problems)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["L1.pdf"]  # no part file left
    assert (tmp_path / "L1.pdf").read_bytes() == b"%PDF- the old one"


def test_a_file_over_the_limit_saves_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lectures, "MAX_PDF_BYTES", 100)
    monkeypatch.setattr(lectures, "_CHUNK", 16)

    saved, problems = lectures.save_pdfs(tmp_path, [File("big.pdf", PDF + b"x" * 200)])

    assert saved == [] and "larger than" in problems[0] and list(tmp_path.iterdir()) == []


def test_the_same_name_twice_in_one_upload_saves_none(tmp_path):
    saved, problems = lectures.save_pdfs(tmp_path, [File("a.pdf"), File("A.pdf")])

    assert saved == [] and "exists" in problems[0]


def test_an_empty_choice_is_a_problem(tmp_path):
    assert lectures.save_pdfs(tmp_path, [File("")])[1] == ["Choose at least one PDF."]


# --- the lecture page --------------------------------------------------------------------------


def test_the_page_lists_lectures_and_the_paid_button(root):
    page = _browser(root).get("/c/demo-course/lectures/", base_url=BASE).get_data(as_text=True)

    assert "L1.pdf" in page and "not read" in page and "2 image pages waiting" in page
    assert "Transcribe 2 image pages" in page and "seer (glm: glm-5.3-flash)" in page


def test_an_upload_reads_only_the_new_files(root, started):
    response = post(_browser(root), "/c/demo-course/lectures/upload",
                    {"pdf": [(io.BytesIO(PDF), "L4.pdf"), (io.BytesIO(PDF), "L5.pdf")]})

    folder = (root / "demo-course" / "lectures").resolve()
    assert (folder / "L4.pdf").read_bytes() == PDF
    assert started[0]["commands"] == [lectures.read_command([folder / "L4.pdf",
                                                             folder / "L5.pdf"])]
    assert "--vision" not in started[0]["commands"][0]  # the free read only
    assert started[0]["title"] == "Read 2 lectures" and started[0]["meta"] == {"kind": "read"}
    assert response.headers["Location"] == "/c/demo-course/tasks/20260102-100000-0abcde/"


def test_a_refused_upload_names_its_file_escaped_and_starts_nothing(root, started):
    response = post(_browser(root), "/c/demo-course/lectures/upload",
                    {"pdf": [(io.BytesIO(PDF), "ok.pdf"), (io.BytesIO(PDF), "<b>x.pdf")]})
    page = response.get_data(as_text=True)

    assert response.status_code == 400 and started == []
    assert "&lt;b&gt;x.pdf" in page and "<b>x" not in page
    assert not (root / "demo-course" / "lectures" / "ok.pdf").exists()


def test_transcribing_reads_only_the_lectures_with_image_pages(root, started):
    post(_browser(root), "/c/demo-course/lectures/transcribe")

    command = started[0]["commands"][0]
    assert command[3] == "--vision" and [Path(p).name for p in command[4:]] == ["L2.pdf"]
    assert started[0]["title"] == "Transcribe 2 image pages"


def test_nothing_to_transcribe_or_no_model_starts_nothing(root, course, started):
    _cache(course.workspace.root, "L2.pdf", ["text"])
    client = _browser(root)
    response = post(client, "/c/demo-course/lectures/transcribe")
    assert response.headers["Location"].endswith("?notice=nothing-to-transcribe")

    _cache(course.workspace.root, "L2.pdf", ["pending"])
    _write(course.workspace.root / "workspace.toml", 'course = "demo"\n[llm.roles]\n')
    response = post(client, "/c/demo-course/lectures/transcribe")
    assert response.headers["Location"].endswith("?notice=no-vision-model") and started == []


def test_too_large_a_request_is_refused_before_it_is_read(root, started):
    app = create_app(root, TOKEN, port=5000)
    app.config["MAX_CONTENT_LENGTH"] = 1000
    client = app.test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)

    response = post(client, "/c/demo-course/lectures/upload",
                    {"pdf": [(io.BytesIO(PDF + b"x" * 5000), "big.pdf")]})

    assert response.status_code == 413 and started == []


# --- new courses -------------------------------------------------------------------------------


def test_a_new_course_saves_its_pdfs_then_runs_init_and_reads_them(root, started):
    response = post(_browser(root), "/new-course",
                    {"folder": "comp1234", "course": "", "pdf": [(io.BytesIO(PDF), "W1.pdf")]})

    folder = (root / "comp1234").resolve()
    call = started[0]
    assert (folder / "lectures" / "W1.pdf").read_bytes() == PDF
    assert call["root"] == folder and call["meta"] == {"kind": "create"}
    assert call["commands"][0][1:] == ["-m", "tutor.workspace", "init", str(folder),
                                       "--course=comp1234"]  # the id defaults to the folder
    assert call["commands"][1] == lectures.read_command([folder / "lectures" / "W1.pdf"])
    assert call["env"] == {"WORKSPACE_DIR": str(folder), "LECTURE_DIR": str(folder / "lectures")}
    assert response.headers["Location"] == "/c/comp1234/tasks/20260102-100000-0abcde/"


def test_a_new_course_without_pdfs_only_runs_init(root, started):
    post(_browser(root), "/new-course", {"folder": "empty-course", "course": "ec"})

    assert len(started[0]["commands"]) == 1 and started[0]["commands"][0][-1] == "--course=ec"
    assert (root / "empty-course" / "lectures").is_dir()


@pytest.mark.parametrize(("folder", "course_id", "problem"), [
    ("demo-course", "", "exists already"),
    ("Comp1234", "", "lowercase"),
    ("../out", "", "lowercase"),
    ("ok", "bad id!", "course id"),
])
def test_a_refused_new_course_creates_nothing(root, started, folder, course_id, problem):
    before = sorted(p.name for p in root.iterdir())

    response = post(_browser(root), "/new-course", {"folder": folder, "course": course_id})

    assert response.status_code == 400 and problem in response.get_data(as_text=True)
    assert sorted(p.name for p in root.iterdir()) == before and started == []


def test_a_bad_pdf_removes_the_new_folder_again(root, started):
    response = post(_browser(root), "/new-course",
                    {"folder": "comp1234", "pdf": [(io.BytesIO(b"not a pdf"), "W1.pdf")]})

    assert response.status_code == 400 and not (root / "comp1234").exists() and started == []


def test_no_new_course_inside_a_git_repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    folder, _, problems = lectures.check_new_course(tmp_path, "comp1234", "")

    assert folder is None and "git repository" in problems[0]


def test_a_course_being_created_shows_only_its_task(root, started):
    client = _browser(root)
    post(client, "/new-course", {"folder": "comp1234"})
    folder = root / "comp1234" / tasks.TASKS_DIR / "20260102-100000-0abcde"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "Create comp1234", "status": "running", "started": "2026-01-02 10:00:00",
        "commands": [], "exit_codes": [], "pid": None, "meta": {"kind": "create"}}))
    _write(folder / tasks.LOG_FILE, "created workspace.toml\n")

    page = client.get("/c/comp1234/tasks/20260102-100000-0abcde/", base_url=BASE)

    assert page.status_code == 200 and "Create comp1234" in page.get_data(as_text=True)
    assert client.get("/c/comp1234/", base_url=BASE).status_code == 404  # not a course yet
    assert lectures.being_created(root, "demo-course") is None  # it has a workspace.toml


def test_a_new_course_page_lists_what_is_still_to_set_up(root):
    _write(root / "fresh" / "workspace.toml", 'course = "fresh"\n')

    page = _browser(root).get("/c/fresh/", base_url=BASE).get_data(as_text=True)

    assert "No lectures yet" in page and "No model writes exercises" in page
    assert "not chosen" in page and "No coverage.md" in page


def test_a_course_with_everything_set_lists_only_what_is_missing(course):
    _write(course.workspace.root / "coverage.md", "| lecture |\n")
    rows = lectures.lecture_rows(course)

    assert lectures.setup_gaps(course, rows) == ["1 lecture not read yet."]


def test_the_home_page_offers_a_new_course(root):
    page = _browser(root).get("/", base_url=BASE).get_data(as_text=True)

    assert "/new-course" in page


def test_upload_reads_with_the_same_python(course):
    assert lectures.read_command([course.workspace.lecture_dir / "L1.pdf"])[0] == sys.executable


# --- a new course's progress -------------------------------------------------------------------


def _create_task(root, status="running", codes=(), commands=2):
    folder = root / "comp1234" / tasks.TASKS_DIR / "20260102-100000-0abcde"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "Create comp1234", "status": status, "started": "2026-01-02 10:00:00",
        "commands": [["python"]] * commands, "exit_codes": list(codes),
        "pid": os.getpid() if status == "running" else None, "meta": {"kind": "create"}}))
    _write(folder / tasks.LOG_FILE, "")
    return tasks.get(root / "comp1234", folder.name)


def _states(root, task):
    course = lectures.being_created(root, "comp1234") or courses.get_course(root, "comp1234")
    return [s.state for s in lectures.create_progress(course, task)]


def test_the_stages_follow_the_files_init_makes(root):
    task = _create_task(root)
    assert _states(root, task) == ["running", "waiting", "waiting", "waiting"]

    course_root = root / "comp1234"
    _write(course_root / ".vscode" / "settings.json", "{}")
    _write(course_root / "workspace.toml", 'course = "x"\n')
    assert _states(root, task) == ["done", "running", "waiting", "waiting"]

    for bin_dir, suffix in (("bin", ""), ("Scripts", ".exe")):  # either platform
        _write(course_root / ".venv" / bin_dir / f"python{suffix}", "")
    assert _states(root, task) == ["done", "running", "waiting", "waiting"]  # pip not yet
    for bin_dir, suffix in (("bin", ""), ("Scripts", ".exe")):
        _write(course_root / ".venv" / bin_dir / f"pip{suffix}", "")
    assert _states(root, task) == ["done", "done", "running", "waiting"]

    task = _create_task(root, codes=(0,))
    assert _states(root, task) == ["done", "done", "done", "running"]


def test_a_failed_install_shows_where_it_stopped(root):
    _write(root / "comp1234" / "workspace.toml", 'course = "x"\n')
    _write(root / "comp1234" / ".vscode" / "settings.json", "{}")
    task = _create_task(root, status="failed", codes=(1, 1), commands=1)

    assert _states(root, task) == ["done", "failed", "waiting"]


def test_a_ready_course_sends_the_watching_page_on_to_it(root):
    _write(root / "comp1234" / "workspace.toml", 'course = "x"\n')
    task = _create_task(root, status="done", codes=(0, 0))
    client = _browser(root)
    path = f"/c/comp1234/tasks/{task.id}/log"

    polled = client.get(path, base_url=BASE, headers={"HX-Request": "true"})
    assert polled.headers["HX-Redirect"] == "/c/comp1234/"
    # Opened later, the task page stays: no redirect outside htmx's poll.
    assert "HX-Redirect" not in client.get(path, base_url=BASE).headers


def test_a_failed_course_stays_on_its_log(root):
    _write(root / "comp1234" / "workspace.toml", 'course = "x"\n')
    task = _create_task(root, status="failed", codes=(1,))

    polled = _browser(root).get(f"/c/comp1234/tasks/{task.id}/log", base_url=BASE,
                                headers={"HX-Request": "true"})

    assert "HX-Redirect" not in polled.headers and "did not finish" in polled.get_data(as_text=True)


@pytest.mark.parametrize(("seconds", "text"), [(5, "5 s"), (65, "1 min 05 s"), (-3, "")])
def test_elapsed_reads_as_minutes_and_seconds(monkeypatch, seconds, text):
    monkeypatch.setattr(tasks, "_age_s", lambda started: seconds)
    task = tasks.Task("x", Path("."), "t", tasks.RUNNING, "2026-01-02 10:00:00", None, [], [], 1)

    assert lectures.elapsed(task) == text
