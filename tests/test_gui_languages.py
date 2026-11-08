"""A course's language on the GUI (owner, 2026-11-07): ticking C on the Environment page, the
host refused for a C course, a C compiler that only differs here, asking every lecture again,
and New batch held back for C until C generation is built. No venv, Docker or model: tasks
are recorded instead of started. Lecture lines are made up."""

import json
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import courses, environment, languages_page, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402
from tutor.gui.routes.common import C_NEEDS_CONTAINER  # noqa: E402
from tutor.materials import cache  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
TOML = """course = "cdemo"   # the course id

[requires]
python = "3.11"   # the venv's Python

[llm.profiles.cheap]
provider = "glm"
model = "glm-5.3-flash"

[llm.roles]
plan = "cheap"
generate = "cheap"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    _write(tmp_path / "cdemo" / "workspace.toml", TOML)
    entries = [cache.Entry(1, None, "text", "h1", "Compile it:\ngcc -Wall -std=c99 celsius.c"),
               cache.Entry(2, None, "text", "h2", "gcc -std=c99 -Wall -c utility.c")]
    _write(tmp_path / "cdemo" / "cache" / "lectures" / "lecture02.html.md",
           cache.render("lecture02.html", entries))
    return tmp_path


@pytest.fixture
def course(root):
    return courses.get_course(root, "cdemo")


@pytest.fixture
def started(monkeypatch):
    calls = []

    def fake_start(course_root, title, commands, env, meta=None):
        calls.append({"title": title, "commands": commands, "meta": meta})
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    return calls


def _browser(root):
    client = create_app(root, TOKEN, port=5000).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


def _data(course) -> dict:
    return tomllib.loads((course.workspace.root / "workspace.toml").read_text(encoding="utf-8"))


def _page(client, path="/c/cdemo/environment/") -> str:
    return client.get(path, base_url=BASE).get_data(as_text=True)


def test_a_python_course_lists_python_alone_and_offers_c_with_why(root):
    """No C settings until C is in the list; "Add a language" offers it, saying the lectures
    compile C, and names the languages not supported yet."""
    text = _page(_browser(root))

    assert "<strong>Python</strong>" in text and "<strong>C</strong>" not in text
    assert "C: compiler settings" not in text and 'name="flags"' not in text
    assert '<option value="c">C</option>' in text
    assert "<option disabled>Java (not supported yet)</option>" in text
    assert ("The lectures compile C: 2 of 2 compile lines, e.g. lecture02.html p.1: "
            "<code>gcc -Wall -std=c99 celsius.c") in text


def test_adding_c_from_the_list_uses_the_lectures_flags_and_shows_its_settings(root, course):
    client = _browser(root)
    response = post(client, "/c/cdemo/environment/languages/add", {"language": "c"})

    assert response.status_code == 303 and "notice=language-added" in response.location
    data = _data(course)
    assert data["languages"] == {"c": {"flags": ["-Wall", "-std=c99"]}}
    assert data["requires"]["gcc"] == languages_page.IMAGE_GCC
    text = _page(client)
    assert "<strong>C</strong> <span class=\"muted\">gcc</span>" in text
    assert 'name="version:gcc" value="12"' in text and ">Remove</button>" in text
    assert "C: compiler settings" in text and 'value="-Wall -std=c99"' in text
    assert '<option value="c">' not in text  # added already: no form, the others named
    assert 'name="language"' not in text.split("C: compiler settings")[0].split(
        "Save the versions")[1] and "Not supported yet: assembly, C#, C++" in text


def test_a_language_that_cannot_be_added_is_refused(root, course):
    before = (course.workspace.root / "workspace.toml").read_text(encoding="utf-8")
    response = post(_browser(root), "/c/cdemo/environment/languages/add",
                    {"language": "java"})
    assert response.status_code == 400 and "not a language that can be added" in (
        response.get_data(as_text=True))
    assert (course.workspace.root / "workspace.toml").read_text(encoding="utf-8") == before


def test_ticking_c_writes_its_table_its_headers_and_gcc_keeping_the_rest(root, course):
    response = post(_browser(root), "/c/cdemo/environment/language",
                    {"c": "on", "flags": " -Wall  -std=c99 ", "headers": "unistd.h, sys/stat.h"})

    assert response.status_code == 303 and "notice=language-saved" in response.location
    data = _data(course)
    assert data["languages"] == {"c": {"flags": ["-Wall", "-std=c99"]}}
    assert data["safety"] == {"allow_headers": ["unistd.h", "sys/stat.h"]}
    assert data["requires"] == {"python": "3.11", "gcc": languages_page.IMAGE_GCC}
    text = (course.workspace.root / "workspace.toml").read_text(encoding="utf-8")
    assert "# the course id" in text and "# the venv's Python" in text
    assert (course.workspace.root / "workspace.toml.bak").is_file()
    # VS Code's C settings follow the flags (step 4b).
    editor = json.loads((course.workspace.root / ".vscode" / "c_cpp_properties.json")
                        .read_text(encoding="utf-8"))
    assert editor["configurations"][0]["cStandard"] == "c99"


def test_removing_c_takes_its_table_headers_and_gcc_and_keeps_the_rest(root, course):
    """gcc is C's row in the language list, so it goes with C (owner, 2026-11-07)."""
    client = _browser(root)
    post(client, "/c/cdemo/environment/language", {"c": "on", "flags": "-Wall", "headers": "x.h"})

    response = post(client, "/c/cdemo/environment/languages/remove", {"language": "c"})

    assert "notice=language-removed" in response.location
    data = _data(course)
    assert "languages" not in data and "safety" not in data
    assert data["requires"] == {"python": "3.11"}
    assert not (course.workspace.root / ".vscode" / "c_cpp_properties.json").exists()


def test_a_courses_own_vs_code_c_settings_are_left_alone(root, course):
    own = course.workspace.root / ".vscode" / "c_cpp_properties.json"
    own.parent.mkdir(exist_ok=True)
    mine = '{"configurations": [{"name": "Mine"}], "version": 4}\n'
    own.write_text(mine, encoding="utf-8")

    response = post(_browser(root), "/c/cdemo/environment/language",
                    {"c": "on", "flags": "-Wall -std=c99", "headers": ""})

    assert "notice=language-saved-own-editor" in response.location
    assert own.read_text(encoding="utf-8") == mine


@pytest.mark.parametrize(("flags", "headers", "says"), [
    ("-Wall extra.c", "", "The compiler flags must be options"),
    ("-Wall", "../secret.h", "../secret.h is not a header name"),
    ("-Wall", "<unistd.h>", "&lt;unistd.h&gt; is not a header name"),  # escaped
])
def test_a_refused_language_form_writes_nothing_and_comes_back_as_typed(root, course, flags,
                                                                         headers, says):
    client = _browser(root)
    post(client, "/c/cdemo/environment/languages/add", {"language": "c"})
    before = (course.workspace.root / "workspace.toml").read_text(encoding="utf-8")

    response = post(client, "/c/cdemo/environment/language",
                    {"c": "on", "flags": flags, "headers": headers})

    text = response.get_data(as_text=True)
    assert response.status_code == 400 and says in text
    assert f'value="{flags}"' in text
    assert (course.workspace.root / "workspace.toml").read_text(encoding="utf-8") == before


def test_a_c_course_cannot_choose_the_host(root, course):
    client = _browser(root)
    post(client, "/c/cdemo/environment/language", {"c": "on", "flags": "-Wall"})

    text = _page(client)
    assert 'value="host" disabled' in text and "not for a C course" in text
    response = post(client, "/c/cdemo/environment/runner", {"runner": "host", "understood": "on"})
    assert response.status_code == 400
    assert "A C course&#39;s tests run only in a container" in response.get_data(as_text=True)
    assert "isolation" not in _data(course)


def test_a_language_not_supported_is_said_on_the_page_and_the_course_page(root, course):
    path = course.workspace.root / "workspace.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[languages.rust]\n", encoding="utf-8")
    client = _browser(root)

    assert "rust is not supported yet" in _page(client)
    assert "rust is not supported yet" in _page(client, "/c/cdemo/")


def test_a_c_compiler_that_differs_here_is_not_counted_missing(root, monkeypatch):
    results = [{"name": "python", "required": "3.11", "found": "3.11.9", "hint": "", "ok": True},
               {"name": "gcc", "required": "12", "found": "clang-21.0.0", "hint": "", "ok": False}]
    monkeypatch.setattr(environment, "check",
                        lambda course: environment.Check("/v/python", results))

    text = _page(_browser(root), "/c/cdemo/environment/check")

    assert "chip-differs" in text and "validation uses gcc 12 in the container" in text
    assert "chip-done" in text and "not met yet" not in text
    missing = {**results[1], "found": None}
    assert environment.Check("/v/python", [missing]).missing == [missing]


def test_every_lecture_can_be_asked_again(root, course, started):
    """Once all are looked at, the page still offers a new look (owner, 2026-11-07)."""
    client = _browser(root)
    response = post(client, "/c/cdemo/environment/draft", {"again": "1"})

    assert response.status_code == 303
    assert started[0]["commands"] == [environment.draft_command(again=True)]
    assert started[0]["commands"][0][-1] == "--again"
    assert started[0]["title"] == "Find requirements in every lecture again"


def test_new_batch_waits_for_a_c_course_to_run_its_tests_in_a_container(root, course,
                                                                        started):
    """Every draft of a C course on this machine would fail rule `safe`: nothing starts."""
    client = _browser(root)
    post(client, "/c/cdemo/environment/language", {"c": "on", "flags": "-Wall"})

    text = _page(client, "/c/cdemo/new-batch")
    assert "validated only in a container, so a batch starts once" in text
    assert 'action="/c/cdemo/new-batch"' not in text  # no form to start one
    form = {"label": "w1", "lecture": "lecture02.html", "mode": "learn", "how": "auto"}
    response = post(client, "/c/cdemo/new-batch", form)
    assert response.status_code == 409
    assert C_NEEDS_CONTAINER.startswith("A C course's")
    assert started == []
    assert "A C course&#39;s tests run only in a container" in _page(client, "/c/cdemo/")

    post(client, "/c/cdemo/environment/runner", {"runner": "container"})
    assert 'action="/c/cdemo/new-batch"' in _page(client, "/c/cdemo/new-batch")
    assert "A C course&#39;s tests run only in a container" not in _page(client, "/c/cdemo/")
