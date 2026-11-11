"""Actual image metadata probe has no course mount, model call or image creation path."""

import subprocess

import pytest
import test_container_real as real

from tutor import doctor_checks, execution
from tutor.doctor_advice import facts, verification

image_course = real.course


@pytest.mark.docker
def test_metadata_is_read_from_existing_image_without_mount_or_install(image_course):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "build" not in command and "pull" not in command
        return subprocess.run(command, **kwargs)

    result = facts.collect(doctor_checks.read_course(image_course), run=run)
    assert result.source == "course image" and result.context.python_locked
    assert "pytest" in result.installed and result.context.current_python.startswith("3.11")
    command = next(c for c in calls if facts.PROBE in c)
    assert "-v" not in command and "--mount" not in command


@pytest.mark.docker
def test_fixed_import_dependency_probe_uses_container_execution(image_course):
    result = execution.run_environment_probe(verification.probe_script("json"),
                                             sources=[image_course / "workspace.toml"],
                                             env=facts.runtime_environment(docker=True))
    assert verification.measured(result).passed, result.output
