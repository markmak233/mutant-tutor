"""Live isolated runs deliver partial UTF-8 without sacrificing deadlines or cleanup."""

import io
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tutor import execution
from tutor.attempt_transport import EVENT_FILE, EXPORT_ENV, read_events
from tutor.execution import LearnerOptions, Outcome, RunResult, run_pytest
from tutor.processes import stream_output


def test_output_arrives_before_exit_and_a_split_utf8_character_survives(tmp_path):
    """The child waits for a host acknowledgement: end-only output would deadlock it."""
    ack = tmp_path / "ack"
    source = tmp_path / "test_live.py"
    source.write_text(
        "import os, pathlib, time\n"
        "def test_live():\n"
        "    word = '\u732b'.encode('utf-8')\n"
        "    os.write(1, b'READY:' + word[:1])\n"
        f"    ack = pathlib.Path({str(ack)!r})\n"
        "    while not ack.exists():\n        time.sleep(0.01)\n"
        "    os.write(1, word[1:] + b'\\n')\n", encoding="utf-8")
    chunks = []

    def output(chunk):
        chunks.append(chunk)
        if "READY:" in chunk:
            ack.write_text("continue", encoding="utf-8")

    result = run_pytest({source.name: source}, 10, {"PYTEST_ADDOPTS": "-s"},
                        on_output=output)
    assert result.outcome is Outcome.PASSED
    assert "READY:\u732b" in result.output == "".join(chunks)


def test_silent_run_still_times_out_and_leaves_no_reader(tmp_path, monkeypatch):
    source = tmp_path / "test_silent.py"
    source.write_text("import time\ndef test_silent():\n    time.sleep(60)\n", encoding="utf-8")
    monkeypatch.setenv("TUTOR_TIMEOUT_FACTOR", "1")
    result = run_pytest({source.name: source}, 1, on_output=lambda chunk: None)
    assert result.outcome is Outcome.TIMEOUT and result.stop_reason == "timeout"
    assert not any(t.name == "tutor-output" for t in threading.enumerate())


@pytest.mark.parametrize("interruption", [False, True])
def test_callback_failure_and_ctrl_c_end_the_run(tmp_path, monkeypatch, interruption):
    """An exception in the consumer must not leave pytest alive in its temp directory."""
    source = tmp_path / "test_wait.py"
    source.write_text("import time\ndef test_wait():\n    print('READY', flush=True)\n"
                      "    time.sleep(60)\n", encoding="utf-8")
    trees = []
    tree_class = execution.ProcessTree

    def tree(*args):
        instance = tree_class(*args)
        trees.append(instance)
        return instance

    def output(chunk):
        if interruption:
            raise KeyboardInterrupt
        raise RuntimeError("terminal failed")

    monkeypatch.setattr(execution, "ProcessTree", tree)
    if interruption:
        result = run_pytest({source.name: source}, env={"PYTEST_ADDOPTS": "-s"}, on_output=output)
        assert result.stop_reason == "keyboard_interrupt"
    else:
        with pytest.raises(RuntimeError, match="terminal failed"):
            run_pytest({source.name: source}, env={"PYTEST_ADDOPTS": "-s"}, on_output=output)
    assert trees and trees[0].proc.poll() is not None
    assert not any(t.name == "tutor-output" for t in threading.enumerate())


def test_export_uses_the_hooks_without_loading_credentials(tmp_path, monkeypatch):
    """Capture the run-local artifact before cleanup; no exercise code runs in the host test."""
    source = tmp_path / "test_export.py"
    source.write_text(
        "import tutor.attempts\n"
        "def forbidden(*args):\n    raise AssertionError('loaded credentials')\n"
        "tutor.attempts.load_env = forbidden\n"
        "def test_wrong():\n    assert False, 'wrong on purpose'\n", encoding="utf-8")
    observed = []
    original = execution._run_with_env

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(read_events(args[1] / EVENT_FILE))
        return result

    monkeypatch.setattr(execution, "_run_with_env", capture)
    result = run_pytest({source.name: source}, env={EXPORT_ENV: "1"})
    assert result.outcome is Outcome.FAILED
    assert observed[0].tally.failed == 1 and observed[0].exitstatus == 1
    assert "test_wrong: AssertionError: wrong on purpose" in observed[0].tally.first_error
    assert "loaded credentials" not in result.output


def test_learner_result_keeps_staged_bytes_before_the_run_can_change_them(tmp_path, monkeypatch):
    """The fake backend mutates its copy, while the runner must keep the earlier input."""
    source = tmp_path / "student.c"
    source.write_bytes(b"original input")
    monkeypatch.setattr(execution, "image_for", lambda sources: "demo-image")

    def run(image, workdir, *args, **kwargs):
        (workdir / "student.c").write_bytes(b"changed by tests")
        rows = [{"event": "start"},
                {"event": "report", "kind": "failed", "error": "test_it: wrong"},
                {"event": "finish", "exitstatus": 1}]
        (workdir / EVENT_FILE).write_text(
            "".join(json.dumps({"version": 1, **row}) + "\n" for row in rows), encoding="utf-8")
        return RunResult(Outcome.FAILED, 1, 0.1, "failed")

    monkeypatch.setattr(execution, "_run_in_container", run)
    result = run_pytest({source.name: source}, learner=LearnerOptions(source.name))
    assert result.learner.source == b"original input" and result.learner.suffix == ".c"
    assert result.learner.events.tally.failed == 1


def test_ctrl_c_during_final_output_drain_still_returns_an_interruption():
    """An already-exited process deterministically exercises the final drain branch."""
    proc = SimpleNamespace(stdout=io.TextIOWrapper(io.BytesIO(b"last output")),
                           poll=lambda: 0, wait=lambda **kwargs: 0)
    tree = SimpleNamespace(proc=proc, kill=lambda: None)

    def interrupted(chunk):
        raise KeyboardInterrupt

    output, reason = stream_output(tree, interrupted, time.monotonic() + 5)
    assert output == "last output" and reason == "keyboard_interrupt"
