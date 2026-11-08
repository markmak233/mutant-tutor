"""Untrusted event files preserve partial results without accepting host destinations."""

import json

import pytest

from tutor.attempt_transport import read_events


def report(tmp_path, *events, tail=""):
    """Small complete events make each malformed case independent of call order."""
    path = tmp_path / "report.jsonl"
    path.write_text("".join(json.dumps({"version": 1, **event}) + "\n"
                            for event in events) + tail, encoding="utf-8")
    return path


def test_partial_report_keeps_observations_but_discards_the_unfinished_last_line(tmp_path):
    path = report(tmp_path, {"event": "start"},
                  {"event": "report", "kind": "failed", "error": "test_it: wrong"},
                  tail='{"version":')
    parsed = read_events(path, partial=True)
    assert parsed.tally.failed == 1 and parsed.tally.first_error == "test_it: wrong"
    assert parsed.exitstatus is None
    with pytest.raises(ValueError, match="unfinished"):
        read_events(path)


@pytest.mark.parametrize("event", [
    {"event": "start", "workspace": "elsewhere"},
    {"event": "report", "kind": "passed", "error": None},
    {"event": "finish", "exitstatus": True},
    {"event": "finish", "exitstatus": 9},
])
def test_invalid_fields_and_order_are_rejected(tmp_path, event):
    with pytest.raises(ValueError):
        read_events(report(tmp_path, event), partial=True)


def test_call_pass_and_teardown_error_are_both_counted(tmp_path):
    path = report(tmp_path, {"event": "start"},
                  {"event": "report", "kind": "passed", "error": None},
                  {"event": "report", "kind": "errors", "error": "test_it: teardown"},
                  {"event": "finish", "exitstatus": 1})
    result = read_events(path)
    assert result.tally.passed == result.tally.errors == 1
    assert result.exitstatus == 1


def test_links_and_oversized_files_do_not_get_read(tmp_path, symlink, monkeypatch):
    path = report(tmp_path, {"event": "start"}, {"event": "finish", "exitstatus": 0})
    link = tmp_path / "link"
    symlink(link, path)
    with pytest.raises(ValueError, match="regular"):
        read_events(link)
    monkeypatch.setattr("tutor.attempt_transport.MAX_BYTES", 1)
    with pytest.raises(ValueError, match="bounded"):
        read_events(path)
