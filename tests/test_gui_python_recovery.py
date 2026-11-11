"""Download navigation follows actual Python recovery/discovery, with all work faked."""

import pytest
import test_gui_environment as support

from tutor import config, llm, python_manager, pythons
from tutor.doctor_rows import Row
from tutor.gui import doctor_page, environment, tasks

root = support.root
BASE = support.BASE
DOWNLOAD = "/c/demo-course/environment/python-downloads"


@pytest.fixture
def recovery(root, monkeypatch):
    """A missing venv justifies discovery; later cases replace the measured evidence."""
    monkeypatch.setattr(doctor_page, "report", lambda c: [
        Row("FAIL", "course .venv is missing", "install it", "install-venv")])
    monkeypatch.setattr(environment, "check", lambda c: environment.Check(None, problem="missing"))
    monkeypatch.setattr(pythons, "find", lambda: [])
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: pytest.fail("server loaded .env"))
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("model called"))
    monkeypatch.setattr(tasks, "start", lambda *a, **kw: pytest.fail("GET started work"))
    return support._browser(root)


def check(client):
    return client.get("/c/demo-course/environment/check", base_url=BASE).get_data(as_text=True)


def test_no_discovered_python_links_to_the_existing_consent_page(recovery, monkeypatch):
    text = check(recovery)
    assert 'id="python-switch"' in text and f'href="{DOWNLOAD}"' in text
    assert "No suitable Python here." in text and "Download one with uv" in text
    entry = python_manager.Entry("synthetic-python", "3.12.10", None)
    monkeypatch.setattr(python_manager, "catalog", lambda: [entry])
    monkeypatch.setattr(python_manager, "course_results", lambda ws: [])
    response = recovery.get(DOWNLOAD, base_url=BASE)
    assert response.status_code == 200
    assert 'name="confirm" value="yes" required' in response.get_data(as_text=True)
    assert support.post(recovery, DOWNLOAD, {"identifier": entry.identifier}).status_code == 400


@pytest.mark.parametrize("version, download", [("3.14.1", True), ("3.12.10", False)])
def test_only_incompatible_installed_pythons_offer_download(recovery, root, monkeypatch,
                                                          version, download):
    # The synthetic course declares PySpark 3.5, whose existing rule rejects Python 3.14.
    monkeypatch.setattr(pythons, "find", lambda: [pythons.Found(version, root / "python")])
    text = check(recovery)
    assert (f'href="{DOWNLOAD}"' in text) is download
    if not download:
        assert "Make .venv with Python 3.12" in text


def test_compatible_current_python_counts_even_without_an_alternative(recovery, root, monkeypatch):
    monkeypatch.setattr(doctor_page, "report", lambda c: [
        Row("FAIL", "host python: version declaration differs", "check version", "switch-python")])
    monkeypatch.setattr(environment, "check", lambda c: environment.Check("synthetic", [
        {"name": "python", "required": "3.12", "found": "3.11.17", "ok": False}]))
    monkeypatch.setattr(pythons, "find", lambda: [pythons.Found("3.11.17", root / "python")])
    text = check(recovery)
    assert "No suitable alternative" in text and f'href="{DOWNLOAD}"' not in text


@pytest.mark.parametrize("status, text", [
    ("WARN", ".venv made before the tutor command existed"),
    ("FAIL", "host example: required 1, found not found"),
])
def test_install_only_repairs_do_not_claim_python_discovery_found_nothing(
        recovery, monkeypatch, status, text):
    monkeypatch.setattr(doctor_page, "report", lambda c: [Row(status, text, "", "install-venv")])
    monkeypatch.setattr(environment, "check", lambda c: environment.Check("synthetic", [
        {"name": "python", "found": "3.11.17", "ok": True},
        {"name": "example", "found": None, "ok": False}]))
    monkeypatch.setattr(pythons, "find", lambda: pytest.fail("unneeded Python discovery"))
    text = check(recovery)
    assert 'id="python-switch"' not in text and DOWNLOAD not in text
    assert 'action="/c/demo-course/environment/install"' in text


def test_healthy_course_has_no_download_entry_or_discovery(recovery, monkeypatch):
    monkeypatch.setattr(doctor_page, "report", lambda c: [Row("OK", "course is ready")])
    monkeypatch.setattr(pythons, "find", lambda: pytest.fail("unneeded Python discovery"))
    assert DOWNLOAD not in check(recovery)


def test_busy_course_disables_download_navigation(recovery, root, monkeypatch):
    task = tasks.Task("20260102-100000-abcdef", root, "Install", tasks.RUNNING,
                      "", None, [], [], None)
    monkeypatch.setattr(tasks, "running", lambda root: task)
    text = check(recovery)
    assert "disabled>Download one with uv" in text and f'href="{DOWNLOAD}"' not in text
