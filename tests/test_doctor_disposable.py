"""Disposable adapter processes/probes are replaced; no downloaded code runs in tests."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import test_doctor_advice_facts as support
import test_doctor_advice_service as service_support

from tutor import execution, workspace
from tutor.doctor_advice import sandbox, verification
from tutor.doctor_advice.models import Change


class Commands:
    """Command-keyed process failures, including interruption during a Docker build."""

    def __init__(self, failure=None):
        self.calls, self.failure = [], failure

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        assert kwargs["capture_output"] and kwargs["timeout"] > 0
        assert "DOCTOR_TOKEN" not in kwargs["env"]
        if self.failure == "interrupt" and "build" in command:
            raise KeyboardInterrupt
        code = int(self.failure == "install" and "install" in command)
        return subprocess.CompletedProcess(command, code, "", "")


def probe(script, **kwargs):
    data = {"passed": True, "detail": "passed", "missing": None}
    return execution.RunResult(execution.Outcome.PASSED, 0, 0.0,
                               verification.MARKER + json.dumps(data))


def test_host_trial_uses_temporary_venv_and_removes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCTOR_TOKEN", "sk-test-not-a-real-key")
    run = Commands()
    selected = support.course(tmp_path)
    trial = sandbox.Disposable(service_support.snapshot(), selected, "demo", run=run, probe=probe)
    result = trial.verify((Change("version", package="demo", spec="<2"),))
    assert result.passed
    make, install = [c for c, kw in run.calls]
    assert make[0] == str(workspace.venv_python(selected.root / ".venv"))
    assert not Path(make[-1]).exists() and "demo<2" in install
    assert "--only-binary=:all:" in install and "https://pypi.org/simple" in install
    assert all(kw["env"]["PIP_CONFIG_FILE"] for c, kw in run.calls)


def test_failed_install_is_not_verified_and_never_uses_original_venv(tmp_path):
    run = Commands("install")
    result = sandbox.Disposable(service_support.snapshot(), support.course(tmp_path), "demo",
                                run=run, probe=lambda *a, **kw: pytest.fail("install failed"))
    assert not result.verify(()).passed
    assert not Path(run.calls[0][0][-1]).exists()


@pytest.mark.parametrize("failure", [None, "interrupt"])
def test_container_trial_is_owned_image_and_cleans_on_interrupt(tmp_path, failure):
    run = Commands(failure)
    trial = sandbox.Disposable(service_support.snapshot(), support.course(tmp_path, "container"),
                               "demo", run=run, probe=probe)
    if failure:
        with pytest.raises(KeyboardInterrupt):
            trial.verify(())
    else:
        assert trial.verify(()).passed
    built, removed = run.calls
    assert built[0][:2] == ["docker", "build"] and built[0][-1] == "-"
    assert "FROM tutor-run-demo:" in built[1]["input"]
    assert "USER runner" in built[1]["input"] and "PIP_CONFIG_FILE=/dev/null" in built[1]["input"]
    assert removed[0][:3] == ["docker", "image", "rm"]
    assert removed[0][-1] == built[0][built[0].index("--tag") + 1]


def test_disposable_interpreter_disappearance_does_not_fall_back(tmp_path):
    trial = sandbox.Disposable(service_support.snapshot(), support.course(tmp_path), "demo",
                               run=Commands(), probe=probe, find=lambda **kw: [])
    assert not trial.verify((Change("python", python="3.12.3"),)).passed
    assert not trial.run.calls


def test_execution_probe_refuses_a_host_override_for_container_policy(monkeypatch):
    monkeypatch.setattr(execution, "image_for", lambda sources: "synthetic-image")
    with pytest.raises(execution.ContainerError, match="host"):
        execution.run_environment_probe("pass", python=Path("python"))


def test_execution_probe_routes_to_selected_interpreter_and_strips_parent_map(monkeypatch):
    monkeypatch.setattr(execution, "image_for", lambda sources: None)
    seen = []

    def host(workdir, timeout, env, command, **kwargs):
        seen.append((command, kwargs, workdir))
        assert (workdir / "probe.py").read_text(encoding="utf-8") == "pass"
        return "sentinel"

    monkeypatch.setattr(execution, "_run_in", host)
    assert execution.run_environment_probe("pass", python=Path("chosen-python")) == "sentinel"
    command, kwargs, workdir = seen[0]
    assert command[0] == "chosen-python" and kwargs["base_env"] == {} and not workdir.exists()


def test_local_distributions_and_tool_packages_are_not_replaced_by_public_names():
    found = replace(service_support.snapshot(), direct=frozenset({"demo"}))
    with pytest.raises(sandbox.facts.FactsError, match="local/URL"):
        sandbox.requirements(found, ())
    with pytest.raises(sandbox.facts.FactsError, match="tool/bootstrap"):
        sandbox.requirements(service_support.snapshot(),
                             (Change("version", package="tutor", spec="<2"),))
