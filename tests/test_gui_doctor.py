"""Doctor GUI routes on synthetic courses; task launch/probes are stand-ins."""

import pytest
import test_gui_environment as support

from tutor import pythons
from tutor.doctor_rows import Row
from tutor.gui import doctor_page, tasks
from tutor.gui.app import create_app

BASE = support.BASE
root = support.root
started = support.started


@pytest.fixture
def client(root):
    client = create_app(root, "test-token", port=5000, env_file=root / "selected.env").test_client()
    client.get("/?token=test-token", base_url=BASE)
    return client


def test_page_does_no_probe_or_paid_task_until_requested(client, monkeypatch):
    monkeypatch.setattr(doctor_page, "report", lambda c: pytest.fail("initial GET probed"))
    monkeypatch.setattr(tasks, "start", lambda *a, **kw: pytest.fail("initial GET started a task"))
    text = client.get("/c/demo-course/doctor/", base_url=BASE).get_data(as_text=True)
    assert "Check this course now" in text and "No machine check has run" in text
    assert "Preview selected data only" in text and "Package version bounds" in text


def test_checks_show_structured_results_and_existing_fix_paths(client, monkeypatch):
    monkeypatch.setattr(doctor_page, "report", lambda c: [Row("FAIL", "synthetic missing venv",
                                                             "install course venv")])
    text = client.get("/c/demo-course/doctor/?check=1", base_url=BASE).get_data(as_text=True)
    assert "synthetic missing venv" in text and "install course venv" in text
    assert "/c/demo-course/environment/#host-install" in text


def test_bounds_editor_saves_only_its_explicit_table(client, root):
    path = root / "demo-course" / "workspace.toml"
    before = path.read_text(encoding="utf-8")
    response = support.post(client, "/c/demo-course/doctor/bounds", {"name": "demo", "spec": "<2"})
    assert response.status_code == 303
    text = path.read_text(encoding="utf-8")
    assert "# the course id" in text and "package-bounds" in text
    assert before.split("[generation]")[1] in text
    response = support.post(client, "/c/demo-course/doctor/bounds",
                            {"name": "pyspark", "spec": "<2"})
    assert response.status_code == 400 and "pyspark" in response.get_data(as_text=True)


def test_selected_failure_is_saved_then_only_a_fixed_preview_command_starts(client, root, started):
    path = root / "demo-course" / "workspace.toml"
    path.write_text(path.read_text(encoding="utf-8") +
                    '\n[llm.profiles.cheap]\nprovider = "custom"\nmodel = "demo"\n',
                    encoding="utf-8")
    response = support.post(client, "/c/demo-course/doctor/input", {
        "failure": "synthetic failure", "profile": "cheap", "module": "demo"})
    assert response.status_code == 303 and len(started) == 1
    call = started[0]
    assert call["meta"]["action"] == "preview" and "doctor_worker" in call["commands"][0][2]
    assert call["commands"][0][-1] == "preview" and "--env-file" not in call["commands"][0]
    assert list((root / "demo-course" / "logs" / "doctor").glob("*/failure.log"))


def test_apply_and_discard_require_explicit_confirmation(client, started):
    response = support.post(client, "/c/demo-course/doctor/retained/finish")
    assert response.status_code == 400 and not started
    response = support.post(client, "/c/demo-course/doctor/retained/finish", {"confirmed": "on"})
    assert response.status_code == 303 and started[0]["commands"][0][-1] == "--confirm"


def test_unknown_profiles_and_cross_origin_actions_do_not_start_tasks(client, started):
    response = support.post(client, "/c/demo-course/doctor/input", {
        "failure": "synthetic", "profile": "unknown"})
    assert response.status_code == 400 and not started
    response = client.post("/c/demo-course/doctor/bounds", base_url=BASE,
                           data={"name": "demo", "spec": "<2"})
    assert response.status_code == 403


def test_broken_or_missing_venv_has_a_page_recreation_path(client, root, monkeypatch):
    # Reuse the existing guarded Python-switch action, never a path supplied by a model.
    offered = pythons.Found("3.12.3", root / "synthetic-python")
    monkeypatch.setattr(pythons, "find", lambda: [offered])
    text = client.get("/c/demo-course/environment/check", base_url=BASE).get_data(as_text=True)
    assert "Make .venv with Python 3.12" in text
