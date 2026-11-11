"""Environment's doctor rows and guarded fix controls on a synthetic course.

Use the real doctor orchestrator with command-keyed process stand-ins. Task/model
callbacks are forbidden on GET; POSTs only record the existing fixed task commands.
"""

from html.parser import HTMLParser

import pytest
import test_doctor as doctor_support
import test_gui_environment as support

import tutor
from tutor import course_requirements, doctor, doctor_checks, envcheck, pythons
from tutor.doctor_rows import Row
from tutor.gui import courses, doctor_page, environment, tasks
from tutor.gui.app import create_app

rig = doctor_support.rig
started = support.started
BASE = support.BASE


class Controls(HTMLParser):
    """Read native disabled/href/action attributes rather than test-only HTTP headers."""

    def __init__(self, text):
        super().__init__()
        self.buttons, self.links, self.forms = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"button", "a", "form"}:
            getattr(self, {"button": "buttons", "a": "links", "form": "forms"}[tag]).append(
                dict(attrs))


@pytest.fixture
def page(tmp_path, rig, monkeypatch):
    # The CLI rig fakes optional-package availability; Flask needs the real root spec.
    monkeypatch.setattr(doctor_checks.importlib.util, "find_spec",
                        lambda name: tutor.__spec__ if name == "tutor" else object())
    root = doctor_support.course(tmp_path, declarations='[requires]\npython="3.11"\n')
    rig.requirements = [envcheck.CheckResult("python", "3.11", "3.11.9")]
    monkeypatch.setattr(pythons, "find", lambda: [])

    def report(course):
        rows = []
        doctor.main([str(course.workspace.root)], run=rig, output=lambda text: None,
                    on_row=rows.append)
        return rows

    def check(course):
        result = course_requirements.check(course.workspace, run=rig)
        return environment.Check(result.python, result.results, result.problem)

    monkeypatch.setattr(doctor_page, "report", report)
    monkeypatch.setattr(environment, "check", check)
    client = create_app(tmp_path, "synthetic", port=5000).test_client()
    client.get("/?token=synthetic", base_url=BASE)
    return client, root, rig


def get(page):
    return page[0].get("/c/course/environment/check", base_url=BASE).get_data(as_text=True)


def test_missing_venv_is_one_problem_with_existing_install_action(page, started):
    client, root, _ = page
    python = doctor_checks.workspace.venv_python(root / ".venv")
    python.unlink()
    text = get(page)
    assert text.count(">Problem</span>") == 1 and "see the .venv row above" in text
    assert 'action="/c/course/environment/install"' in text
    assert 'name="profile"' not in text and started == []
    response = support.post(client, "/c/course/environment/install")
    assert response.status_code == 303 and len(started) == 1
    assert started[0]["commands"] == [environment.install_command(courses.get_course(root.parent,
                                                                                    "course"))]


def test_container_missing_image_offers_build_without_a_second_host_check(page):
    _, root, rig = page
    path = root / "workspace.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('runner = "host"',
                                                            'runner = "container"'),
                    encoding="utf-8")
    rig.image = "missing"
    text = get(page)
    assert 'action="/c/course/environment/build"' in text and "Build the test image" in text
    assert "tools on this machine: not needed" in text
    assert not any(c[-1] == course_requirements.PROBE for c in rig.calls)


def test_python_conflict_offers_existing_guarded_switch(page, monkeypatch):
    _, root, rig = page
    rig.requirements = [envcheck.CheckResult("python", "3.12", "3.11.9", "recreate .venv")]
    monkeypatch.setattr(pythons, "find", lambda: [pythons.Found("3.12.9", root / "python312")])
    text = get(page)
    assert "Choose another Python" in text and 'id="python-switch"' in text
    assert 'action="/c/course/environment/python"' in text
    assert "Make .venv with Python 3.12" in text


def test_host_docker_down_is_attention_not_problem(page):
    page[2].docker = "down"
    text = get(page)
    assert "Needs attention" in text and ">Problem</span>" not in text
    assert 'href="/c/course/environment/host-setup#docker"' in text


def test_fix_none_has_only_next_step_and_duplicate_problem_appears_once(page, monkeypatch):
    row = Row("FAIL", "synthetic unsupported problem", "correct its declaration")
    monkeypatch.setattr(doctor_page, "report", lambda course: [row, row])
    text = get(page)
    controls = Controls(text)
    assert text.count(row.text) == 1 and row.next_step in text
    assert not controls.forms and not controls.links
    assert len(controls.buttons) == 1 and "hx-get" in controls.buttons[0]  # readonly refresh


def test_running_course_disables_all_fix_controls_and_switch(page, monkeypatch):
    _, root, _ = page
    rows = [Row("FAIL", "install", "", "install-venv"),
            Row("FAIL", "image", "", "build-image"),
            Row("FAIL", "host python: conflict", "", "switch-python"),
            Row("FAIL", "host java: missing", "", "host-guide"),
            Row("WARN", "runner", "", "choose-runner"),
            Row("FAIL", "unknown import", "", "ask-model")]
    monkeypatch.setattr(doctor_page, "report", lambda course: rows)
    monkeypatch.setattr(pythons, "find", lambda: [pythons.Found("3.12.9", root / "python312")])
    task = tasks.Task("20260102-100000-abcdef", root, "Existing install", tasks.RUNNING,
                      "", None, [], [], 1)
    monkeypatch.setattr(tasks, "running", lambda root: task)
    page[2].requirements = [envcheck.CheckResult("python", "3.12", "3.11.9", "recreate .venv")]
    text = get(page)
    controls = Controls(text)
    assert "Existing install" in text
    assert all("disabled" in b for b in controls.buttons if "hx-get" not in b)
    assert len(controls.links) == 1 and "/tasks/" in controls.links[0]["href"]
    # The original install button above the diagnostic table is disabled too.
    full = page[0].get("/c/course/environment/", base_url=BASE).get_data(as_text=True)
    assert 'class="primary" disabled>Install into .venv' in full


def test_unknown_import_links_to_the_fix_wizard(page, monkeypatch):
    row = {"name": "example imports", "required": "unknown_runtime"}
    assert doctor_checks.requirement_fix(row) == "ask-model"
    monkeypatch.setattr(doctor_page, "report", lambda course: [
        Row("FAIL", "unknown import", "review the failure", "ask-model")])
    text = get(page)
    assert "Ask a model for a fix" in text and "coming in the next step" not in text
    assert "/c/course/environment/fix/" in Controls(text).links[0]["href"]


def test_initial_page_and_diagnostic_get_never_start_a_task_or_model(page, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("GET started work")

    monkeypatch.setattr(tasks, "start", forbidden)
    # The rig already forbids config.load_env and llm.chat in the server.
    initial = page[0].get("/c/course/environment/", base_url=BASE)
    assert initial.status_code == 200 and page[2].calls == []
    assert 'hx-trigger="load"' in initial.get_data(as_text=True)
    assert "<th>Check</th><th>Result</th><th>What to do</th>" in get(page)


def test_guide_anchors_and_runner_destination_are_present(page):
    client, _, rig = page
    rig.requirements = [envcheck.CheckResult("java", "17", None, "set JAVA_HOME")]
    text = get(page)
    assert 'href="/c/course/environment/host-setup#java"' in text
    guide = client.get("/c/course/environment/host-setup", base_url=BASE).get_data(as_text=True)
    assert 'id="python"' in guide and 'id="docker"' in guide
    full = client.get("/c/course/environment/", base_url=BASE).get_data(as_text=True)
    assert 'id="runner-choice"' in full
