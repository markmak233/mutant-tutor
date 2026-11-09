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


@pytest.mark.parametrize("suffix", ["", "/", "/?check=1"])
def test_old_doctor_address_redirects_without_probes_or_paid_tasks(client, monkeypatch, suffix):
    monkeypatch.setattr(doctor_page, "report", lambda c: pytest.fail("initial GET probed"))
    monkeypatch.setattr(tasks, "start", lambda *a, **kw: pytest.fail("initial GET started a task"))
    response = client.get("/c/demo-course/doctor" + suffix, base_url=BASE)
    assert response.status_code == 302
    assert response.location == "/c/demo-course/environment/#host-install"


def test_environment_has_one_version_editor_without_a_separate_doctor_link(client):
    text = client.get("/c/demo-course/environment/", base_url=BASE).get_data(as_text=True)
    assert "Package version bounds" not in text and 'href="/c/demo-course/doctor/' not in text
    assert 'action="/c/demo-course/environment/requires"' in text


def test_removed_bounds_route_cannot_write_a_second_version_table(client, root):
    path = root / "demo-course" / "workspace.toml"
    before = path.read_text(encoding="utf-8")
    response = support.post(client, "/c/demo-course/doctor/bounds", {"name": "demo", "spec": "<2"})
    assert response.status_code == 404 and path.read_text(encoding="utf-8") == before
    assert not path.with_name("workspace.toml.bak").exists()


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
    response = client.post("/c/demo-course/environment/requires", base_url=BASE,
                           data={"name": "demo", "version": "<2"})
    assert response.status_code == 403


def test_broken_or_missing_venv_has_a_page_recreation_path(client, root, monkeypatch):
    # Reuse the existing guarded Python-switch action, never a path supplied by a model.
    offered = pythons.Found("3.12.3", root / "synthetic-python")
    monkeypatch.setattr(pythons, "find", lambda: [offered])
    monkeypatch.setattr(doctor_page, "report", lambda course: [
        Row("FAIL", "course .venv is missing", "install it", "install-venv")])
    text = client.get("/c/demo-course/environment/check", base_url=BASE).get_data(as_text=True)
    assert "Make .venv with Python 3.12" in text
