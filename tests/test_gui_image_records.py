"""Image-record page/actions use synthetic data and fake task launches."""

import test_container as docker_support
import test_gui_environment as support

from tutor import image_records
from tutor.gui import environment

root = support.root
course = support.course
started = support.started


def test_environment_links_to_record_and_missing_state_has_refresh_fix(root, monkeypatch):
    monkeypatch.setattr(image_records, "view", lambda ws:
                        image_records.View("missing", detail="none"))
    client = support._browser(root)
    text = client.get("/c/demo-course/environment/", base_url=support.BASE).get_data(as_text=True)
    assert "/c/demo-course/environment/image-record" in text
    response = client.get("/c/demo-course/environment/image-record", base_url=support.BASE)
    text = response.get_data(as_text=True)
    assert "Refresh current image record" in text and "Build or rebuild" in text


def test_legacy_provenance_and_course_venv_comparison_are_explicit(root, course, monkeypatch):
    data = image_records.record(course.workspace, docker_support.FakeDocker())
    monkeypatch.setattr(image_records, "view", lambda ws: image_records.View("current", data))
    monkeypatch.setattr(environment, "check", lambda c: environment.Check(
        "synthetic-course-python", [{"name": "python", "found": "3.13.2"}]))
    text = support._browser(root).get("/c/demo-course/environment/image-record",
                                     base_url=support.BASE).get_data(as_text=True)
    assert "provenance is unknown" in text and "3.13.2" in text and "different" in text
    assert "not an automatic failure or pin" in text


def test_refresh_is_metadata_only_and_uses_current_tool_command(root, started):
    response = support.post(support._browser(root),
                            "/c/demo-course/environment/image-record/refresh")
    assert response.status_code == 303
    command = started[0]["commands"][0]
    assert command[2] == "tutor.image_records"
    assert not {"build", "pull", "install"}.intersection(command)
