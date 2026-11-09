"""Real Docker diagnostic: a missing image is reported without creating or pulling it."""

import subprocess

import pytest

from tutor import container, doctor


@pytest.mark.docker
def test_missing_course_image_reports_build_command_without_installing(tmp_path, capsys):
    if problem := container.docker_problem():
        pytest.skip(problem)
    # Unique, plainly synthetic course name; no image creation or deletion needed.
    name = "doctor-missing-" + tmp_path.name.lower().replace("_", "-")
    (tmp_path / "workspace.toml").write_text(
        f'course = "{name}"\n[isolation]\nrunner = "container"\n', encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "build" not in command and "pull" not in command
        return subprocess.run(command, **kwargs)

    assert doctor.main([str(tmp_path)], run=run) == 1
    text = capsys.readouterr().out
    assert "OK    Docker" in text and "FAIL  test image" in text
    assert container.BUILD_COMMAND in text
    assert any(cmd[:3] == ["docker", "image", "inspect"] for cmd in calls)
