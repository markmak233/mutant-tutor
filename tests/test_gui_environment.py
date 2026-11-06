"""The Environment page (GUI step 7a): envcheck in the course's venv, [requires] and
[isolation] written with tomlkit, and the two installs as tasks. No venv, Docker or network
here: processes and Docker are stand-ins, and tasks.start is replaced where a route would
start one."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor import workspace  # noqa: E402
from tutor.gui import courses, environment, llm_settings, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402
from tutor.materials import cache  # noqa: E402
from tutor.materials import requires as requires_draft  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
TOML = """course = "demo"   # the course id

[requires]
python = "3.11"   # the venv's Python
java = "17"
pyspark = "3.5"

[llm.roles]
generate = "cheap"

[generation]
thinking = true
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    _write(tmp_path / "demo-course" / "workspace.toml", TOML)
    _write(tmp_path / "fresh" / "workspace.toml",
           workspace.WORKSPACE_TOML.format(course="fresh", version="3.11"))
    return tmp_path


@pytest.fixture
def course(root):
    return courses.get_course(root, "demo-course")


def _venv(course) -> Path:
    python = environment.venv_python(course)
    _write(python, "")
    return python


def _toml(course) -> str:
    return (course.workspace.root / "workspace.toml").read_text(encoding="utf-8")


def _browser(root):
    client = create_app(root, TOKEN, port=5000).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


@pytest.fixture
def started(monkeypatch):
    """Records the tasks a route would start, instead of starting them."""
    calls = []

    def fake_start(course_root, title, commands, env, meta=None):
        calls.append({"title": title, "commands": commands, "env": env, "meta": meta})
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    return calls


class FakeRun:
    """Stands in for subprocess.run: Docker by its verb, the venv's commands by module."""

    def __init__(self, docker_fail=(), size="1729000000", envcheck=None):
        self.docker_fail = set(docker_fail)
        self.size = size
        self.envcheck = envcheck
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "docker":
            verb = " ".join(command[1:3]) if command[1] == "image" else command[1]
            out = {"info": "24.0.6\n", "image inspect": self.size + "\n"}.get(verb, "")
            return subprocess.CompletedProcess(command, 1 if verb in self.docker_fail else 0,
                                               out, "")
        if command[1:3] == ["-m", "tutor.envcheck"]:
            return subprocess.CompletedProcess(command, 0, self.envcheck or "", "Traceback\n"
                                               "ModuleNotFoundError: No module named 'tutor'\n")
        return subprocess.CompletedProcess(command, 0, "# tutor-run-demo:abc\nFROM python\n",
                                           "")


# --- the check ---------------------------------------------------------------------------------


def test_the_check_runs_in_the_courses_venv_from_the_repository(course):
    python = _venv(course)
    answer = {"course": "demo", "python": str(python), "results": [
        {"name": "java", "required": "17", "found": None, "hint": "set JAVA_HOME", "ok": False}]}
    run = FakeRun(envcheck=json.dumps(answer))

    check = environment.check(course, run)

    command, kwargs = run.calls[0]
    assert command == [str(python), "-m", "tutor.envcheck", "--json"]
    assert kwargs["cwd"] == tasks.REPO  # where .env is, which envcheck reads itself
    assert kwargs["env"]["WORKSPACE_DIR"] == str(course.workspace.root)
    assert check.results[0]["name"] == "java" and not check.ok


def test_without_a_venv_the_check_says_install_creates_it(course):
    run = FakeRun()
    check = environment.check(course, run)
    assert "no .venv" in check.problem and run.calls == []


def test_a_venv_without_the_tool_gives_the_last_error_line(course):
    _venv(course)
    check = environment.check(course, FakeRun(envcheck="not json"))
    assert "No module named 'tutor'" in check.problem


# --- [requires] ----------------------------------------------------------------------------------


def test_blank_rows_are_skipped_and_special_names_take_lower_case():
    table, problems = environment.check_requires(["Java", "", "torch"], ["17", "", "2"], None)
    assert problems == [] and table == {"java": "17", "torch": "2"}


@pytest.mark.parametrize("names, versions, says", [
    (["torch", "Torch"], ["2", "2"], "listed twice"),
    (["torch"], [""], "give a version prefix"),
    ([""], ["2"], "has no name"),
    (["pyspark"], ["3.5"], "needs a JVM"),
    (["torch"], [">=2"], "version prefix"),
    (["<b>x</b>"], ["1"], "not a package name"),
])
def test_a_table_that_would_not_install_is_refused(names, versions, says):
    table, problems = environment.check_requires(names, versions, None)
    assert table is None and says in problems[0]


def test_a_hadoop_the_image_cannot_build_is_refused_only_for_the_container():
    rows = (["java", "hadoop"], ["17", "2.7"])
    assert environment.check_requires(*rows, "host")[0] == {"java": "17", "hadoop": "2.7"}
    table, problems = environment.check_requires(*rows, "container")
    assert table is None and "no known release" in problems[0]


def test_saving_requires_keeps_comments_and_every_other_table(course):
    before = tomllib.loads(_toml(course))

    assert environment.save_requires(course, {"python": "3.12", "java": "17",
                                              "torch": "2"}) == []

    text = _toml(course)
    after = tomllib.loads(text)
    assert after["requires"] == {"python": "3.12", "java": "17", "torch": "2"}
    assert {k: v for k, v in after.items() if k != "requires"} == {
        k: v for k, v in before.items() if k != "requires"}
    assert 'course = "demo"   # the course id' in text
    assert 'java = "17"\ntorch = "2"\n\n[llm.roles]' in text  # after the last name
    assert (course.workspace.root / "workspace.toml.bak").read_text(encoding="utf-8") == TOML


# --- where tests run --------------------------------------------------------------------------


def test_the_host_needs_the_tick_and_nothing_is_written_without_it(course):
    problems = environment.save_runner(course, "host", understood=False)
    assert "your rights" in problems[0] and _toml(course) == TOML
    assert environment.save_runner(course, "anywhere", understood=True)[0].startswith("Choose")


def test_choosing_writes_isolation_after_inits_commented_lines(root):
    fresh = courses.get_course(root, "fresh")

    assert environment.save_runner(fresh, "container", understood=False) == []

    text = _toml(fresh)
    assert tomllib.loads(text)["isolation"] == {"runner": "container"}
    assert text.index('# runner = "container"') < text.index("[isolation] #")
    assert environment.runner(fresh) == "container"
    assert environment.save_runner(fresh, "host", understood=True) == []
    assert tomllib.loads(_toml(fresh))["isolation"] == {"runner": "host"}


# --- the test image ---------------------------------------------------------------------------


def test_the_image_view_says_where_it_stands(course, monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/usr/bin/docker")
    _venv(course)

    ready = environment.docker_view(course, FakeRun())
    assert ready.state == "ready" and ready.size == 1_729_000_000
    assert ready.recipe.startswith("# tutor-run-demo")
    assert environment.docker_view(course, FakeRun(docker_fail={"image inspect"})).state == (
        "image-missing")
    stopped = environment.docker_view(course, FakeRun(docker_fail={"info"}))
    assert stopped.state == "docker-stopped" and "not running" in stopped.detail
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    assert environment.docker_view(course, FakeRun()).state == "docker-missing"


def test_a_requires_table_with_no_recipe_says_so(root, course):
    _write(course.workspace.root / "workspace.toml",
           'course = "demo"\n[requires]\njava = "17"\nhadoop = "2.7"\n')
    view = environment.docker_view(courses.get_course(root, "demo-course"), FakeRun())
    assert view.state == "cannot-build" and "no known release" in view.detail


def test_the_downloads_name_every_source_the_recipe_uses():
    found = dict(environment.downloads({"python": "3.11", "java": "17", "hadoop": "3.3",
                                        "pyspark": "3.5"}))
    assert "Docker Hub" in found["python:3.11-slim-bookworm"]
    assert "apt" in found["openjdk-17-jre-headless and procps"]
    assert "SHA-512" in found["Hadoop 3.3.6, about 730 MB"]
    assert found["pytest, hypothesis, pyspark"] == "PyPI (pip)"


# --- the page and its actions ------------------------------------------------------------------


def test_the_page_shows_the_table_and_what_install_runs(root):
    page = _browser(root).get("/c/demo-course/environment/", base_url=BASE)
    text = page.get_data(as_text=True)

    assert page.status_code == 200
    assert 'value="pyspark"' in text and "https://pypi.org/project/pyspark/" in text
    assert "pypi.org/project/java/" not in text  # not a pip package
    assert "&#39;pyspark==3.5.*&#39;" in text  # the pip command, shown before the button
    assert 'hx-get="/c/demo-course/environment/check"' in text
    assert 'hx-get="/c/demo-course/environment/docker"' in text


def test_rows_are_added_and_removed_in_the_page_with_htmx(root):
    """+ appends a blank row fetched from the server; − drops its own row (htmx's "delete"
    swap); neither writes anything, so the file is changed only by Save."""
    client = _browser(root)
    text = client.get("/c/demo-course/environment/", base_url=BASE).get_data(as_text=True)

    assert text.count('name="name"') == 3  # one row per entry, no blank ones
    assert text.count('hx-swap="delete"') == 3 and 'hx-target="closest tr"' in text
    assert 'hx-target="#requires-rows" hx-swap="beforeend"' in text

    row = client.get("/c/demo-course/environment/requires-row", base_url=BASE)
    html = row.get_data(as_text=True)
    assert row.status_code == 200 and html.strip().startswith("<tr>")
    assert 'name="name" value=""' in html and 'hx-swap="delete"' in html
    assert _toml(courses.get_course(root, "demo-course")) == TOML


def test_an_empty_table_starts_with_one_blank_row(root):
    _write(root / "demo-course" / "workspace.toml", 'course = "demo"\n')
    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)
    assert text.count('name="name" value=""') == 1


def test_the_course_page_links_to_it_and_the_gap_links_where_to_choose(root):
    text = _browser(root).get("/c/fresh/", base_url=BASE).get_data(as_text=True)
    assert 'class="button" href="/c/fresh/environment/"' in text
    assert 'warning. <a href="/c/fresh/environment/">Environment →</a>' in text


def test_the_check_part_shows_each_result(root, monkeypatch):
    monkeypatch.setattr(environment, "check", lambda course: environment.Check("/v/python", [
        {"name": "java", "required": "17", "found": "21.0.1", "hint": "set JAVA_HOME",
         "ok": False},
        {"name": "pyspark", "required": "3.5", "found": "3.5.9", "hint": "", "ok": True}]))

    text = _browser(root).get("/c/demo-course/environment/check",
                              base_url=BASE).get_data(as_text=True)

    assert "chip-wrong-version" in text and "set JAVA_HOME" in text and "chip-ok" in text
    assert 'hx-target="#env-check"' in text  # Check again


def test_the_image_part_offers_build_only_when_docker_answers(root, monkeypatch):
    view = environment.DockerView("container", "tutor-run-demo:abc", "image-missing",
                                  recipe="FROM python\n", downloads=[("x", "PyPI (pip)")])
    monkeypatch.setattr(environment, "docker_view", lambda course: view)
    client = _browser(root)

    text = client.get("/c/demo-course/environment/docker", base_url=BASE).get_data(as_text=True)
    assert "/c/demo-course/environment/build" in text and "FROM python" in text

    stopped = environment.DockerView("container", "tutor-run-demo:abc", "docker-stopped",
                                     "Docker is not running", recipe="FROM python\n")
    monkeypatch.setattr(environment, "docker_view", lambda course: stopped)
    text = client.get("/c/demo-course/environment/docker", base_url=BASE).get_data(as_text=True)
    assert "/environment/build" not in text and "Docker is not running" in text


def test_saving_the_table_from_the_form(root, course):
    response = post(_browser(root), "/c/demo-course/environment/requires",
                    {"name": ["python", "java", "pyspark", "", "mrjob"],
                     "version": ["3.11", "17", "3.5", "", "0.7"]})

    assert response.status_code == 303 and "notice=requires-saved" in response.location
    assert tomllib.loads(_toml(course))["requires"]["mrjob"] == "0.7"


def test_a_refused_table_comes_back_as_typed_and_escaped(root, course):
    response = post(_browser(root), "/c/demo-course/environment/requires",
                    {"name": ["python", "<b>x</b>"], "version": ["3.11", "1"]})
    text = response.get_data(as_text=True)

    assert response.status_code == 400 and "Nothing was saved" in text
    assert 'value="&lt;b&gt;x&lt;/b&gt;"' in text and "<b>x</b>" not in text
    assert _toml(course) == TOML


def test_choosing_the_host_without_the_tick_is_refused(root, course):
    response = post(_browser(root), "/c/demo-course/environment/runner", {"runner": "host"})
    assert response.status_code == 400 and _toml(course) == TOML
    response = post(_browser(root), "/c/demo-course/environment/runner",
                    {"runner": "host", "understood": "on"})
    assert response.status_code == 303 and environment.runner(course) == "host"


def test_install_runs_init_again_as_a_task(root, course, started):
    response = post(_browser(root), "/c/demo-course/environment/install")

    assert response.status_code == 303
    [task] = started
    assert task["commands"] == [environment.install_command(course)]
    assert task["commands"][0][2:5] == ["tutor.workspace", "init", str(course.workspace.root)]
    assert task["env"]["WORKSPACE_DIR"] == str(course.workspace.root)


def test_build_needs_the_venv_and_docker_then_runs_in_the_venv(root, course, started,
                                                               monkeypatch):
    client = _browser(root)
    response = post(client, "/c/demo-course/environment/build")
    assert "notice=no-venv" in response.location and started == []

    python = _venv(course)
    stopped = environment.DockerView("container", "img", "docker-stopped", "not running")
    monkeypatch.setattr(environment, "docker_view", lambda course: stopped)
    response = post(client, "/c/demo-course/environment/build")
    assert "notice=docker-stopped" in response.location and started == []

    ready = environment.DockerView("container", "img", "image-missing")
    monkeypatch.setattr(environment, "docker_view", lambda course: ready)
    response = post(client, "/c/demo-course/environment/build")
    assert response.status_code == 303
    assert started[0]["commands"] == [[str(python), "-m", "tutor.container", "build"]]


# --- step 7b: the model's draft --------------------------------------------------------------


def _lecture(course, name="L1.pdf"):
    """A cached lecture (made-up text) and the stored answer of a model for it."""
    entries = [cache.Entry(1, None, "text", "h1", "import cv2\npip install pyspark==3.5.1"),
               cache.Entry(2, None, "text", "h2", "Install MySQL 8 for the lab.")]
    path = course.workspace.root / "cache" / "lectures" / f"{name}.md"
    _write(path, cache.render(name, entries))
    return path, {e.number: e for e in entries}


def _answer(course, path, entries, found):
    out = requires_draft.result_path(course.workspace.root, path)
    _write(out, json.dumps({"hash": requires_draft.lecture_hash(entries),
                            "model": "local: m", "found": found}))


FOUND = [
    {"kind": "pip", "name": "pyspark", "version": "3.5", "page": 1,
     "quote": "pip install pyspark==3.5.1"},
    {"kind": "pip", "name": "opencv-python", "import_name": "cv2", "page": 1,
     "quote": "import cv2"},
    {"kind": "other", "name": "MySQL", "version": "8", "page": 2,
     "quote": "Install MySQL 8 for the lab."},
    {"kind": "pip", "name": "numpy", "page": 2, "quote": "import numpy"},  # made up
]


def test_without_read_lectures_the_draft_points_to_the_lectures_page(root):
    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)
    assert "No lecture is read yet" in text and "/c/demo-course/lectures/" in text


def test_a_lecture_not_asked_yet_offers_the_run_naming_model_and_size(root, course,
                                                                      started):
    _write(course.workspace.root / "workspace.toml",
           'course = "demo"\n[requires]\npython = "3.11"\n'
           '[llm.profiles.cheap]\nprovider = "glm"\nmodel = "glm-5.3-flash"\n'
           '[llm.roles]\nplan = "cheap"\n')
    _lecture(course)
    client = _browser(root)

    text = client.get("/c/demo-course/environment/", base_url=BASE).get_data(as_text=True)
    assert "1 lecture not looked at yet" in text
    assert "cheap (glm: glm-5.3-flash), the plan model" in text
    assert "/c/demo-course/environment/draft" in text

    response = post(client, "/c/demo-course/environment/draft")
    assert response.status_code == 303
    assert started[0]["commands"] == [environment.draft_command()]


def test_the_draft_shows_checked_candidates_with_their_sources(root, course):
    path, entries = _lecture(course)
    _answer(course, path, entries, FOUND)

    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)

    assert 'name="accept" value="pyspark"' in text and 'value="3.5"' in text
    assert 'name="accept" value="opencv-python"' in text and "chip-name-from-the-model" in text
    assert "chip-listed" in text  # pyspark is in [requires] already, at 3.5
    assert "Also named, not checked here" in text and "MySQL" in text
    assert "1 entry dropped by the checks" in text and "not on page 2" in text
    assert "pip install pyspark==3.5.1" in text  # the quote, as a source


def test_ticked_candidates_are_added_and_a_missing_version_is_asked_for(root, course):
    path, entries = _lecture(course)
    _answer(course, path, entries, FOUND)
    client = _browser(root)

    refused = post(client, "/c/demo-course/environment/accept",
                   {"accept": ["opencv-python"], "version:opencv-python": ""})
    text = refused.get_data(as_text=True)
    assert refused.status_code == 400 and "give a version prefix" in text
    assert 'value="opencv-python" aria-label="Add opencv-python" checked' in text
    assert _toml(course) == TOML

    response = post(client, "/c/demo-course/environment/accept",
                    {"accept": ["opencv-python", "not-in-the-draft"],
                     "version:opencv-python": "4", "version:not-in-the-draft": "1"})
    assert response.status_code == 303
    assert tomllib.loads(_toml(course))["requires"] == {
        "python": "3.11", "java": "17", "pyspark": "3.5", "opencv-python": "4"}


def test_a_course_without_models_gets_no_run_and_says_where_to_choose(root, course, started):
    """The GUI does not read .env, so it cannot tell whether LLM_* would answer."""
    _write(course.workspace.root / "workspace.toml", 'course = "demo"\n')
    _lecture(course)
    client = _browser(root)

    text = client.get("/c/demo-course/environment/", base_url=BASE).get_data(as_text=True)
    assert "No model is set for it" in text and "/environment/draft" not in text
    response = post(client, "/c/demo-course/environment/draft")
    assert "notice=no-setup-model" in response.location and started == []


def test_a_stated_patch_release_starts_the_box_at_major_minor(root, course):
    path, entries = _lecture(course)
    _answer(course, path, entries, [{"kind": "pip", "name": "pyspark", "version": "3.5.1",
                                     "page": 1, "quote": "pip install pyspark==3.5.1"}])
    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)
    assert 'name="version:pyspark" value="3.5"' in text and "the slides say 3.5.1" in text
    assert "chip-listed" in text and "chip-listed-other-version" not in text  # 3.5 fits
    assert environment.prefix("java", "17.0.2") == "17"


def test_nothing_ticked_writes_nothing(root, course):
    path, entries = _lecture(course)
    _answer(course, path, entries, FOUND)
    response = post(_browser(root), "/c/demo-course/environment/accept", {})
    assert response.status_code == 400 and _toml(course) == TOML


def test_the_models_page_offers_the_setup_role(root, course):
    _write(course.workspace.root / "workspace.toml",
           'course = "demo"\n[llm.profiles.cheap]\nprovider = "glm"\nmodel = "m"\n')
    text = _browser(root).get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    assert "<strong>setup</strong>" in text and "setup" in llm_settings.ROLES


# --- "exercises may import": [safety] allow_imports from the list (owner, 2026-11-06) -------------


def _save_list(root, rows, ticked=(), modules=None):
    """Post the list as the page does: one key per row, ticks and module boxes by key."""
    keys = [f"r{i}" for i in range(len(rows))]
    data = {"name": [n for n, _ in rows], "version": [v for _, v in rows], "key": keys,
            "import": [keys[i] for i in ticked]}
    for i, module in (modules or {}).items():
        data[f"module:{keys[i]}"] = module
    return post(_browser(root), "/c/demo-course/environment/requires", data)


def test_a_ticked_package_is_allowed_and_an_unticked_one_no_longer(root, course):
    rows = [("python", "3.11"), ("java", "17"), ("pyspark", "3.5"), ("scikit-learn", "1.4")]
    response = _save_list(root, rows, ticked=(2, 3), modules={2: "pyspark", 3: "sklearn"})
    assert response.status_code == 303
    data = tomllib.loads(_toml(course))
    assert data["safety"]["allow_imports"] == ["pyspark", "sklearn"]
    assert data["requires"]["scikit-learn"] == "1.4"
    assert "[safety] # modules exercises may import" in _toml(course)

    _save_list(root, rows, ticked=(3,), modules={2: "pyspark", 3: "sklearn"})
    assert tomllib.loads(_toml(course))["safety"]["allow_imports"] == ["sklearn"]


def test_an_allowed_module_no_row_names_stays(root, course):
    _write(course.workspace.root / "workspace.toml",
           TOML + '\n[safety]\nallow_imports = ["subprocess", "torch"]\n')
    _save_list(root, [("python", "3.11"), ("torch", "2")], ticked=())
    assert tomllib.loads(_toml(course))["safety"]["allow_imports"] == ["subprocess"]


def test_a_module_name_that_is_not_one_is_refused_and_nothing_is_written(root, course):
    response = _save_list(root, [("python", "3.11"), ("opencv-python", "4")], ticked=(1,),
                          modules={1: "cv2; rm"})
    text = response.get_data(as_text=True)
    assert response.status_code == 400 and "is not a module name" in text
    assert 'name="import" value="r1" checked' in text  # the tick comes back as typed
    assert _toml(course) == TOML


def test_the_page_ticks_what_is_allowed_and_names_modules_the_draft_saw(root, course):
    _write(course.workspace.root / "workspace.toml",
           'course = "demo"\n[requires]\npython = "3.11"\nopencv-python = "4"\n'
           'CamelPkg = "3"\n[safety]\nallow_imports = ["CamelPkg"]\n')
    path, entries = _lecture(course)
    _answer(course, path, entries, FOUND)  # the lecture imports cv2 for opencv-python

    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)

    assert 'name="module:r1" value="cv2"' in text  # from the draft, not a guess
    assert 'name="import" value="r1" aria' in text  # not allowed yet: unticked
    assert 'name="import" value="r2" checked' in text  # CamelPkg, as written (case kept)
    assert 'name="module:r0"' not in text  # python is not a module


def test_adding_draft_entries_leaves_allow_imports_alone(root, course):
    _write(course.workspace.root / "workspace.toml",
           TOML + '\n[safety]\nallow_imports = ["torch"]\n')
    path, entries = _lecture(course)
    _answer(course, path, entries, FOUND)
    post(_browser(root), "/c/demo-course/environment/accept",
         {"accept": ["opencv-python"], "version:opencv-python": "4"})
    assert tomllib.loads(_toml(course))["safety"] == {"allow_imports": ["torch"]}


def test_a_blank_row_comes_with_its_own_key_and_import_box(root):
    html = _browser(root).get("/c/demo-course/environment/requires-row",
                              base_url=BASE).get_data(as_text=True)
    key = html.split('name="key" value="')[1].split('"')[0]
    assert key.startswith("n") and f'name="import" value="{key}"' in html


# --- tasks that run exercise code use the course's own .venv on this machine --------------------


def test_on_this_machine_tasks_run_with_the_courses_venv(root, course):
    """Where the learner's tests run and [requires] is installed (owner, 2026-11-06)."""
    import sys

    from tutor.gui import generation

    assert courses.task_python(course) == sys.executable  # no .venv yet
    python = str(_venv(course))
    assert courses.task_python(course) == python  # host (not chosen) with a .venv
    batch = generation.NewBatch("w9", ["L1"], "learn", None, 20, "", False)
    assert generation.plan_command(batch, python)[0] == python
    assert generation.resume_command("w9", approve=True, python=python)[:3] == [
        python, "-m", "tutor.graph"]

    environment.save_runner(course, "container", understood=False)
    assert courses.task_python(courses.get_course(root, "demo-course")) == sys.executable


# --- the page as four steps, each saying whether it is done (owner, 2026-11-06) -----------------


def test_the_steps_come_in_the_order_they_are_done(root):
    text = _browser(root).get("/c/demo-course/environment/", base_url=BASE).get_data(
        as_text=True)
    order = [text.index(f'<span class="step-no">{n}</span>{title}') for n, title in (
        (1, "What the course needs"), (2, "Install on this machine"), (3, "Where tests run"),
        (4, "Test image"))]
    assert order == sorted(order)
    assert text.index("Find it in the lectures") < text.index("The list")
    assert 'Where tests run <span class="chip chip-to-do">' in text  # not chosen yet


def test_step_two_says_done_only_when_every_requirement_is_met(root, monkeypatch):
    def check(results):
        monkeypatch.setattr(environment, "check",
                            lambda course: environment.Check("/v/python", results))
        return _browser(root).get("/c/demo-course/environment/check",
                                  base_url=BASE).get_data(as_text=True)

    ok = {"name": "python", "required": "3.11", "found": "3.11.9", "hint": "", "ok": True}
    bad = {"name": "java", "required": "17", "found": None, "hint": "set JAVA_HOME", "ok": False}
    assert "chip-done" in check([ok]) and "chip-to-do" not in check([ok])
    assert "chip-to-do" in check([ok, bad]) and "1 of 2 not met yet" in check([ok, bad])


@pytest.mark.parametrize("runner, state, says", [
    ("host", "image-missing", "chip-not-needed"),
    ("container", "ready", "chip-done"),
    ("container", "image-missing", "chip-to-do"),
    (None, "image-missing", "Only if tests are to run in a container"),
])
def test_step_four_says_done_to_do_or_not_needed(root, monkeypatch, runner, state, says):
    view = environment.DockerView(runner, "tutor-run-demo:abc", state, size=10**9)
    monkeypatch.setattr(environment, "docker_view", lambda course: view)
    text = _browser(root).get("/c/demo-course/environment/docker",
                              base_url=BASE).get_data(as_text=True)
    assert says in text


# --- every task goes back to its page when it succeeds (owner, 2026-11-06) ----------------------


def _task(course, meta, status="done", codes=(0,)):
    folder = course.workspace.root / tasks.TASKS_DIR / "20260102-100000-0abcde"
    _write(folder / tasks.TASK_FILE, json.dumps({
        "title": "T", "status": status, "started": "2026-01-02 10:00:00",
        "commands": [["python"]] * len(codes), "exit_codes": list(codes), "pid": None,
        "meta": meta}))
    _write(folder / tasks.LOG_FILE, "Traceback: the error\n")
    return f"/c/{course.folder}/tasks/{folder.name}/"


@pytest.mark.parametrize("meta, target", [
    ({"kind": "read"}, "/c/demo-course/lectures/?notice=lectures-read"),
    ({"kind": "vision"}, "/c/demo-course/lectures/?notice=lectures-read"),
    ({"kind": "validate", "batch": "w9"}, "/c/demo-course/b/w9/?notice=validated"),
    ({"kind": "check"}, "/c/demo-course/models/?notice=models-checked"),
    ({"kind": "install"}, "/c/demo-course/environment/?notice=installed"),
    ({"kind": "build"}, "/c/demo-course/environment/?notice=image-built"),
    ({"kind": "requires-draft"}, "/c/demo-course/environment/?notice=draft-ready"),
    ({"kind": "approve", "label": "w9"}, "/c/demo-course/plans/w9/"),
])
def test_a_task_that_succeeded_sends_the_watching_page_back(root, course, meta, target):
    path = _task(course, meta)
    client = _browser(root)

    polled = client.get(path + "log", base_url=BASE, headers={"HX-Request": "true"})

    assert polled.headers["HX-Redirect"] == target
    assert "HX-Redirect" not in client.get(path + "log", base_url=BASE).headers  # not htmx


@pytest.mark.parametrize("status, codes", [("failed", (0, 1)), ("stopped", (0,))])
def test_a_task_that_failed_or_was_stopped_stays_on_its_log_with_a_link_back(
        root, course, status, codes):
    path = _task(course, {"kind": "install"}, status, codes)

    polled = _browser(root).get(path + "log", base_url=BASE, headers={"HX-Request": "true"})
    text = polled.get_data(as_text=True)

    assert "HX-Redirect" not in polled.headers
    assert "the error" in text and 'href="/c/demo-course/environment/">Back to Environment' in text


def test_a_task_of_no_known_kind_stays_and_an_edited_target_stays_on_this_server(root, course):
    path = _task(course, {"kind": "validate", "batch": "https://elsewhere.example/x"})
    polled = _browser(root).get(path + "log", base_url=BASE, headers={"HX-Request": "true"})
    assert polled.headers["HX-Redirect"].startswith("/c/demo-course/b/")

    path = _task(course, {})
    polled = _browser(root).get(path + "log", base_url=BASE, headers={"HX-Request": "true"})
    assert "HX-Redirect" not in polled.headers


def test_actions_need_this_servers_origin(root, course):
    client = _browser(root)
    response = client.post("/c/demo-course/environment/runner", base_url=BASE,
                           data={"runner": "container"})
    assert response.status_code == 403 and _toml(course) == TOML
