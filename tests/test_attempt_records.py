"""The shared writer records tested bytes and keeps the host plugin's result precedence."""

import json

from tutor.attempt_records import Tally, write_attempt
from tutor.config import Workspace


def test_frozen_code_is_kept_once_even_when_the_editor_has_changed(tmp_path):
    """A failed run refers to its input, never the newer answer present at record time."""
    exercise = tmp_path / "batches" / "b" / "exercises" / "e"
    exercise.mkdir(parents=True)
    (exercise / "student.c").write_text("/* edited meanwhile */", encoding="utf-8")
    ws = Workspace(tmp_path, "demo", {}, tmp_path / "lectures")
    tested = b"int f(void) { return 0; }\n"
    lines = [write_attempt(ws, "b", "e", Tally(failed=1, first_error="wrong"), 1.2,
                           source=tested, suffix=".c") for _ in range(2)]
    assert lines[0]["code"] == lines[1]["code"]
    snapshot = ws.attempts_dir / lines[0]["code"]
    assert snapshot.suffix == ".c" and snapshot.read_bytes() == tested
    assert len(list(snapshot.parent.iterdir())) == 1
    assert (exercise / "student.c").read_text(encoding="utf-8") == "/* edited meanwhile */"


def test_timeout_retains_observed_failure_and_records_why_it_stopped(tmp_path):
    """Interruption cannot erase a failure already observed, and never becomes passed."""
    ws = Workspace(tmp_path, "demo", {}, tmp_path / "lectures")
    line = write_attempt(ws, "b", "e", Tally(passed=1), 2, interrupted=True,
                         stop_reason="timeout", selection="example")
    assert line["result"] == "interrupted" and "code" not in line
    assert line["stop_reason"] == "timeout" and line["selection"] == "example"
    stored = json.loads((ws.attempts_dir / "attempts.jsonl").read_text(encoding="utf-8"))
    assert stored == line
