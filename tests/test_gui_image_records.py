"""Inline/history image views use synthetic data, fake probes and fake task launches."""

import pytest
import test_container as docker_support
import test_gui_environment as support

from tutor import config, image_records, llm
from tutor.gui import environment, tasks

root = support.root
course = support.course
started = support.started


def test_environment_loads_record_only_inside_step_four(root, monkeypatch):
    # The initial page stays quick; only the independently requested fragment probes.
    monkeypatch.setattr(image_records, "view", lambda ws: pytest.fail("initial page probed"))
    client = support._browser(root)
    text = client.get("/c/demo-course/environment/", base_url=support.BASE).get_data(as_text=True)
    top = text.split("<h1>Environment</h1>", 1)[1].split('<section class="step"', 1)[0]
    assert "Four steps:" in top and "<a " not in top
    assert "/python-downloads" not in text and 'href="/c/demo-course/doctor/' not in text
    step_four = text.split('id="test-image"', 1)[1]
    assert 'hx-get="/c/demo-course/environment/image-record/summary"' in step_four
    step_two = text.split('id="host-install"', 1)[1].split('id="runner-choice"', 1)[0]
    assert 'href="/c/demo-course/environment/host-setup"' in step_two
    guide = client.get("/c/demo-course/environment/host-setup", base_url=support.BASE)
    assert guide.status_code == 200


def test_a_course_tested_on_this_machine_asks_for_no_image_record(root, course):
    """It has no image, so step 4 says only that none is needed (owner, 2026-11-09)."""
    assert environment.save_runner(course, "host", understood=True) == []
    text = support._browser(root).get("/c/demo-course/environment/",
                                      base_url=support.BASE).get_data(as_text=True)
    assert "image-record/summary" not in text.split('id="test-image"', 1)[1]


def test_missing_record_has_refresh_and_history_reachable_from_summary(root, monkeypatch):
    monkeypatch.setattr(image_records, "view", lambda ws: image_records.View("missing"))
    client = support._browser(root)
    response = client.get("/c/demo-course/environment/image-record/summary", base_url=support.BASE)
    text = response.get_data(as_text=True)
    assert "No image record yet" in text and "Refresh current image record" in text
    assert 'href="/c/demo-course/environment/image-record"' in text
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


@pytest.fixture
def inventory(root, course, monkeypatch):
    """Build provenance and inventory with the command-keyed Docker stand-in only."""
    docker = docker_support.FakeDocker()
    docker_support.build(course.workspace, docker, say=lambda line: None)
    data = image_records.view(course.workspace, docker).data
    data["inventory"]["tools"]["java"] = "17.0.1"
    data["inventory"]["packages"]["pyspark"] = "3.5.1"
    monkeypatch.setattr(image_records, "view", lambda ws: image_records.View("current", data))
    monkeypatch.setattr(environment, "check", lambda c: environment.Check("synthetic", [
        {"name": "python", "found": "3.11.17"}, {"name": "java", "found": "17.0.1"},
        {"name": "pyspark", "found": "3.5.2"}]))
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: pytest.fail("GET loaded .env"))
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("GET called a model"))
    monkeypatch.setattr(tasks, "start", lambda *a, **kw: pytest.fail("GET started a task"))
    return support._browser(root), data


def summary(inventory):
    return inventory[0].get("/c/demo-course/environment/image-record/summary",
                            base_url=support.BASE).get_data(as_text=True)


def test_current_summary_shows_build_time_and_only_differences_as_information(inventory, course):
    before = {p.relative_to(course.workspace.root): p.read_bytes()
              for p in course.workspace.root.rglob("*") if p.is_file()}
    text = summary(inventory)
    # Shown as every other page shows a time: local, to the second, not the stored UTC text.
    built = inventory[1]["provenance"]["built_at"]
    assert tasks.local_time(built) in text and built not in text and "Built at" in text
    assert "<td>pyspark</td>" in text and "3.5.1" in text and "3.5.2" in text
    assert "<td>python</td>" not in text and "<td>java</td>" not in text
    assert "information, not a setup failure" in text and "notice-bad" not in text
    assert before == {p.relative_to(course.workspace.root): p.read_bytes()
                      for p in course.workspace.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("state", ["stale", "unavailable", "invalid"])
def test_uncurrent_records_never_compare_host_versions(inventory, monkeypatch, state):
    monkeypatch.setattr(image_records, "view", lambda ws:
                        image_records.View(state, inventory[1] if state == "stale" else None))
    monkeypatch.setattr(environment, "check", lambda c: pytest.fail("uncurrent comparison"))
    text = summary(inventory)
    assert "Refresh current image record" in text and "<td>pyspark</td>" not in text
    if state == "stale":
        assert "Old image record" in text and "Stored build time" in text


def test_unknown_host_versions_are_not_reported_as_matching(inventory, monkeypatch):
    monkeypatch.setattr(environment, "check", lambda c:
                        environment.Check(None, problem="The course venv is missing."))
    text = summary(inventory)
    assert "Versions unavailable for comparison" in text and 'href="#host-install"' in text
    assert "No version differences found" not in text


def test_legacy_build_time_stays_unknown_and_matching_versions_need_no_table(inventory):
    inventory[1]["provenance"] = None
    inventory[1]["inventory"]["packages"]["pyspark"] = "3.5.2"
    text = summary(inventory)
    assert "Unknown for this older image" in text and "No version differences found" in text
    assert "<table>" not in text


def test_busy_course_disables_refresh_in_summary_and_history(inventory, course, monkeypatch):
    task = tasks.Task("20260102-100000-abcdef", course.workspace.root, "Build", tasks.RUNNING,
                      "", None, [], [], None)
    monkeypatch.setattr(tasks, "running", lambda root: task)
    for suffix in ("/summary", ""):
        text = inventory[0].get("/c/demo-course/environment/image-record" + suffix,
                                base_url=support.BASE).get_data(as_text=True)
        assert 'disabled>Refresh current image record' in text


def test_unbuildable_recipe_still_has_a_gui_recovery_path(root):
    # Reading history must not undo the summary's graceful unavailable-state handling.
    path = root / "demo-course" / "workspace.toml"
    path.write_text('course="demo"\n[requires]\njava="17"\nhadoop="2.7"\n',
                    encoding="utf-8")
    client = support._browser(root)
    for suffix in ("/summary", ""):
        response = client.get("/c/demo-course/environment/image-record" + suffix,
                               base_url=support.BASE)
        assert response.status_code == 200
        assert "Refresh current image record" in response.get_data(as_text=True)
        if not suffix:
            assert '#course-needs"' in response.get_data(as_text=True)
