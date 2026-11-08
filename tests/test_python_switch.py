"""Another Python for a course's .venv: finding Pythons (tutor.pythons), making the .venv anew
with one (tutor.workspace init --replace-venv), which ones suit a course and the switch from
the Environment page (tutor.gui.python_switch). No real interpreter is run and no venv made:
processes are stand-ins."""

import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor import pythons, workspace
from tutor.config import WorkspaceError

# --- finding Pythons -------------------------------------------------------------------------


def _answers(table: dict[str, tuple[str, str]]):
    """A stand-in for subprocess.run asking an interpreter: path -> (version, base path)."""
    def run(cmd, **kwargs):
        if cmd[0] not in table:
            raise OSError("not there")
        version, base = table[cmd[0]]
        return SimpleNamespace(returncode=0, stdout=f"{version}\n{base}\n", stderr="")
    return run


def test_find_asks_each_python_keeps_3_11_on_and_names_each_version_once(tmp_path):
    names = {"python3.12": tmp_path / "a" / "python3.12", "python3.10": tmp_path / "python3.10",
             "python3.13": tmp_path / "python3.13", "python3": tmp_path / "python3"}
    run = _answers({str(names["python3.12"]): ("3.12.4", "/base/python3.12"),
                    str(names["python3.10"]): ("3.10.9", "/base/python3.10"),
                    str(names["python3.13"]): ("3.13.1", "/base/python3.13"),
                    str(names["python3"]): ("3.13.1", "/base/python3.13")})  # the same one
    found = pythons.find(which=lambda name: str(names[name]) if name in names else None, run=run)
    # Paths compared as Paths: Windows writes them with backslashes.
    assert [(f.version, f.path) for f in found] == [("3.13.1", Path("/base/python3.13")),
                                                    ("3.12.4", Path("/base/python3.12"))]
    assert found[0].minor == "3.13"


def test_an_interpreter_that_does_not_answer_is_left_out():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="broken")
    assert pythons.ask(Path("python3.12"), run) is None
    assert pythons.find(which=lambda name: "/x/" + name, run=run) == []


def test_windows_pythons_come_from_the_py_launchers_list(monkeypatch):
    monkeypatch.setattr("tutor.pythons.sys.platform", "win32")
    listed = (" -V:3.13 *        C:\\Program Files\\Python313\\python.exe\n"
              " -V:3.12          C:\\Python312\\python.exe\n")

    def run(cmd, **kwargs):
        if cmd[1:] == ["-0p"]:
            return SimpleNamespace(returncode=0, stdout=listed, stderr="")
        version = "3.13.0" if "313" in cmd[0] else "3.12.7"
        return SimpleNamespace(returncode=0, stdout=f"{version}\n{cmd[0]}\n", stderr="")

    found = pythons.find(which=lambda name: "C:\\py.exe" if name == "py" else None, run=run)
    assert [f.version for f in found] == ["3.13.0", "3.12.7"]
    assert str(found[0].path) == "C:\\Program Files\\Python313\\python.exe"


# --- making the .venv anew -------------------------------------------------------------------


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "c313"
    root.mkdir()
    (root / "workspace.toml").write_text('course = "c313"\n\n[requires]\npython = "3.14"\n'
                                         'java = "17"\npyspark = "3.5"\n', encoding="utf-8")
    old = workspace.venv_python(root / ".venv")
    old.parent.mkdir(parents=True)
    old.write_text("the old one", encoding="utf-8")
    return root


def _make_venv(made):
    def make(venv_dir, python=None):
        made.append(python)
        venv_python = workspace.venv_python(venv_dir)
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("the new one", encoding="utf-8")
    return make


def test_a_switch_makes_the_venv_anew_and_drops_the_one_before(course_dir):
    made = []
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731
    steps = workspace.init(course_dir, make_venv=_make_venv(made), run=ok,
                           python=Path("/base/python3.13"), python_version="3.13",
                           replace_venv=True)
    venv_python = workspace.venv_python(course_dir / ".venv")
    assert venv_python.read_text(encoding="utf-8") == "the new one"
    assert made == [Path("/base/python3.13")]
    assert not (course_dir / ".venv.old").exists()
    assert "created .venv (Python 3.13)" in steps and "deleted .venv.old" in steps


def test_a_failed_switch_puts_the_venv_before_back(course_dir):
    failed = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "no such version")  # noqa: E731
    with pytest.raises(WorkspaceError, match="pip install failed"):
        workspace.init(course_dir, make_venv=_make_venv([]), run=failed,
                       python=Path("/base/python3.13"), python_version="3.13",
                       replace_venv=True)
    venv_python = workspace.venv_python(course_dir / ".venv")
    assert venv_python.read_text(encoding="utf-8") == "the old one"
    assert not (course_dir / ".venv.old").exists()


def test_the_requirements_are_checked_against_the_python_switched_to(course_dir):
    """PySpark 3.5 conflicts with the course's 3.14 (why it switches), not with 3.13."""
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731
    with pytest.raises(WorkspaceError, match="PySpark 3.5"):
        workspace.init(course_dir, make_venv=_make_venv([]), run=ok)
    workspace.init(course_dir, make_venv=_make_venv([]), run=ok, python=Path("/p"),
                   python_version="3.13", replace_venv=True)


# --- which Pythons suit a course, and the page --------------------------------------------------

pytest.importorskip("flask")  # the rest is the GUI's (the `gui` extra)
from tutor.gui import python_switch  # noqa: E402

FOUND = [pythons.Found(v, Path(f"/b/python{v[:4]}"))
         for v in ("3.14.1", "3.13.3", "3.12.10", "3.11.9")]
AUDIOOP = {"name": "pydub imports", "required": "audioop", "found": None, "ok": False,
           "hint": "pydub imports `audioop`"}


def test_a_removed_import_rules_out_every_python_from_its_removal_on():
    found = python_switch.options({"python": "3.13", "pydub": "0.25"}, [AUDIOOP], "3.13.3",
                                  find=lambda: FOUND)
    by = {o.found.minor: o for o in found}
    assert by["3.14"].problems == ["pydub imports `audioop`, which Python 3.13 removed"]
    assert by["3.13"].current and not by["3.13"].offered
    assert by["3.12"].offered and by["3.11"].offered


def test_a_known_conflict_rules_out_its_pythons():
    conflict = {"name": "python", "required": "not 3.14.1", "found": None, "ok": False, "hint": ""}
    found = python_switch.options({"python": "3.14", "java": "17", "pyspark": "3.5"}, [conflict],
                                  "3.14.1", find=lambda: FOUND)
    assert [o.found.minor for o in found if o.offered] == ["3.13", "3.12", "3.11"]
    assert python_switch.related([conflict]) and not python_switch.related(
        [{"name": "pyspark", "ok": False}])


def test_record_sets_requires_python_and_keeps_the_rest(course_dir):
    assert python_switch.record(course_dir, "3.13") == []
    data = tomllib.loads((course_dir / "workspace.toml").read_text(encoding="utf-8"))
    assert data["requires"] == {"python": "3.13", "java": "17", "pyspark": "3.5"}
    assert (course_dir / "workspace.toml.bak").is_file()


@pytest.fixture
def page(tmp_path, monkeypatch):
    """A course whose check reports pydub's removed import, the Pythons stand-ins, and tasks
    recorded instead of started."""
    from tutor.gui import environment, tasks
    from tutor.gui.app import create_app

    root = tmp_path / "root"
    (root / "c313").mkdir(parents=True)
    (root / "c313" / "workspace.toml").write_text(
        'course = "c313"\n\n[requires]\npython = "3.13"\npydub = "0.25"\n', encoding="utf-8")
    monkeypatch.setattr(environment, "check", lambda course: environment.Check("/v", [AUDIOOP]))
    monkeypatch.setattr(pythons, "find", lambda: FOUND)
    monkeypatch.setattr(pythons, "ask", lambda path, run=None: FOUND[1])
    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append({"title": title, "commands": commands})
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "", None,
                          commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    client = create_app(root, "t", port=5000).test_client()
    client.get("/?token=t", base_url="http://127.0.0.1:5000")
    return client, started


def _post(client, data):
    return client.post("/c/c313/environment/python", base_url="http://127.0.0.1:5000",
                       data=data, headers={"Origin": "http://127.0.0.1:5000"})


def test_the_check_offers_the_pythons_that_suit_the_course(page):
    client, _ = page
    text = client.get("/c/c313/environment/check",
                      base_url="http://127.0.0.1:5000").get_data(as_text=True)
    assert "Another Python for this course." in text
    assert "Make .venv with Python 3.12" in text and "Make .venv with Python 3.11" in text
    assert "Make .venv with Python 3.14" not in text and "which Python 3.13 removed" in text
    assert "this .venv&#39;s" in text or "this .venv's" in text


def test_the_switch_starts_only_for_a_python_offered(page):
    client, started = page
    refused = _post(client, {"python": "/usr/bin/evil"})
    assert refused.status_code == 303 and "notice=python-not-offered" in refused.location
    # The page posts each path as it renders it: str(Path), backslashes on Windows.
    assert _post(client, {"python": str(Path("/b/python3.14"))}).status_code == 303  # not suited
    assert started == []
    _post(client, {"python": str(Path("/b/python3.12"))})
    [task] = started
    assert task["title"] == "Make .venv with Python 3.12"
    [command] = task["commands"]
    assert command[1:3] == ["-m", "tutor.gui.python_switch"]
    assert command[-1] == str(Path("/b/python3.12"))
