"""Full CLI acceptance uses synthetic courses, metadata probes and fake chat only."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import test_doctor as support
import test_doctor_advice_service as service_support

from tutor import config, doctor, envcheck, llm, pythons
from tutor.doctor_advice import facts

rig = support.rig
_LOAD_ENV = config.load_env


class Tools(support.Tools):
    """Metadata probe is identified by its fixed script, not by invocation order."""

    def __call__(self, command, **kwargs):
        if facts.PROBE in command:
            self.calls.append(command)
            assert "OPENAI_API_KEY" not in kwargs["env"]
            assert "DOCTOR_TOKEN" not in kwargs["env"]
            payload = {"python": "3.13.2", "installed": [["demo", "1.0"]]}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return super().__call__(command, **kwargs)


@pytest.fixture
def ready(tmp_path, rig, monkeypatch):
    for name, value in service_support.environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(pythons, "find", lambda **kw: [pythons.Found("3.12.3", Path("py312"))])
    failure = tmp_path / "failure.log"
    failure.write_text("synthetic failure: Authorization: Bearer sk-test-not-a-real-key",
                       encoding="utf-8")
    root = support.course(tmp_path, declarations='[requires]\ndemo = "1"\n')
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: llm.Reply(service_support.answer(), 1, 1, 0))
    return root, failure, Tools()


def invoke(ready, *args):
    root, failure, run = ready
    return doctor.main([str(root), "--failure", str(failure), *args], run=run)


def test_preview_is_send_material_but_never_calls_chat(ready, monkeypatch, capsys):
    def forbidden(*a, **kw):
        pytest.fail("preview called a model")
    monkeypatch.setattr(llm, "chat", forbidden)
    assert invoke(ready, "--preview-advice") == 0
    text = capsys.readouterr().out
    assert "Advice destination:" in text and "course venv" in text
    assert "sk-test-not-a-real-key" not in text
    assert "preview only" in text and "All checks passed" in text


def test_advice_is_unverified_and_does_not_clear_existing_failures(ready, monkeypatch, capsys):
    ready[2].requirements = [
        envcheck.CheckResult("demo", "1", None, "synthetic missing dependency")]
    assert invoke(ready, "--advise") == 1
    text = capsys.readouterr().out
    assert "model-proposed, unverified: version demo<2" in text
    assert "1 failed, 1 warnings" in text


@pytest.mark.parametrize("args", [[], ["--advise"], ["--preview-advice"],
                                 ["--smoke", "--advise"], ["--profile", "demo"],
                                 ["--advise", "--preview-advice"]])
def test_invalid_flag_combinations_are_usage_errors(rig, args):
    if not args:
        args = ["--failure", "missing.log"]
    with pytest.raises(SystemExit) as exc:
        doctor.main(args, run=rig)
    assert exc.value.code == 2 and not rig.calls


def test_bad_failure_input_and_explicit_env_file_are_usage_errors(ready, monkeypatch, capsys):
    root, failure, run = ready
    failure.write_bytes(b"\xff")
    assert invoke(ready, "--advise") == 2 and not run.calls
    assert "UTF-8" in capsys.readouterr().err
    failure.write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(config, "load_env", _LOAD_ENV)
    assert invoke(ready, "--advise", "--env-file", str(root / "missing.env")) == 2


def test_course_model_role_is_required_and_assistant_profiles_write_nothing(ready, capsys):
    root, failure, run = ready
    marker = root / "workspace.toml"
    base = marker.read_text(encoding="utf-8")
    profile = ('\n[llm.profiles.demo]\nprovider = "assistant"\nmodel = "demo"\n'
               '[llm.roles]\ngenerate = "demo"\n')
    marker.write_text(base + profile, encoding="utf-8")
    assert invoke(ready, "--advise") == 1
    assert "configure a doctor role" in capsys.readouterr().out
    assert invoke(ready, "--advise", "--profile", "demo") == 1
    assert "assistant request files" in capsys.readouterr().out
    assert not (root / ".exchange").exists()


def test_explicit_credentials_do_not_enter_environment_or_probe_and_files_stay_unchanged(
        ready, tmp_path, monkeypatch, capsys):
    root, failure, run = ready
    marker = root / "workspace.toml"
    marker.write_text(marker.read_text(encoding="utf-8") +
                      '\n[llm.profiles.demo]\nprovider = "custom"\nmodel = "demo-model"\n'
                      'base_url = "https://example.invalid/v1"\napi_key_env = "DOCTOR_TOKEN"\n'
                      '[llm.roles]\ndoctor = "demo"\n', encoding="utf-8")
    env_file = tmp_path / "selected.env"
    env_file.write_text("DOCTOR_TOKEN=sk-test-not-a-real-key\n", encoding="utf-8")
    before = dict(os.environ)
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(config, "load_env", _LOAD_ENV)
    assert invoke(ready, "--advise", "--env-file", str(env_file)) == 0
    assert dict(os.environ) == before
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
    assert "sk-test-not-a-real-key" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["error", "interrupt", "unexpected", "bad-reply"])
def test_model_failure_or_interrupt_has_no_raw_output_or_traceback(ready, monkeypatch,
                                                                capsys, mode):
    def chat(*args, **kwargs):
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "error":
            raise llm.LLMError("HTTP 401 sk-test-not-a-real-key")
        if mode == "unexpected":
            raise RuntimeError("sk-test-not-a-real-key")
        return llm.Reply("not JSON sk-test-not-a-real-key", 1, 1, 0)
    monkeypatch.setattr(llm, "chat", chat)
    assert invoke(ready, "--advise") == (130 if mode == "interrupt" else 1)
    captured = capsys.readouterr()
    assert "sk-test-not-a-real-key" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_no_course_targets_tool_environment(ready, tmp_path, capsys):
    root, failure, run = ready
    assert doctor.main([str(tmp_path), "--advise", "--failure", str(failure)], run=run) == 0
    assert "tool interpreter" in capsys.readouterr().out


def test_container_failure_never_calls_model_or_probes_host(ready, monkeypatch, capsys):
    root, failure, run = ready
    marker = root / "workspace.toml"
    marker.write_text(marker.read_text(encoding="utf-8").replace('runner = "host"',
                                                               'runner = "container"'),
                      encoding="utf-8")
    run.docker = "down"
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("Docker unavailable"))
    assert invoke(ready, "--advise") == 1
    assert not any(facts.PROBE in c for c in run.calls)
    assert "start it before requesting image advice" in capsys.readouterr().out


def test_advice_imports_do_not_need_gui_or_generation_extra():
    # Trusted static import check; no course, config file, model or package index involved.
    code = '''import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert name.split('.')[0] not in {'flask', 'langgraph', 'tomlkit'}
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import tutor.doctor_advice.service
print('imports ok')
'''
    done = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True,
                          text=True, timeout=15)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "imports ok"
