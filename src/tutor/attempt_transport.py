"""Bounded, versioned pytest events exported from a throwaway run to the host.

Events carry observations, never workspace paths or instructions to write host files.
Flushing each line preserves completed observations when the runner kills a timed-out test.
"""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path

from tutor.attempt_records import Tally

EXPORT_ENV = "TUTOR_ATTEMPT_REPORT"
EVENT_FILE = "tutor-attempt-events.jsonl"
MAX_BYTES = 50_000_000  # same ceiling as the existing JUnit reader
MAX_LINE = 4096  # events hold only a kind and the 300-character first-error excerpt
KINDS = frozenset({"passed", "failed", "errors", "skipped"})


@dataclass(frozen=True)
class AttemptEvents:
    """A checked prefix of hook observations; exitstatus is absent after a forced kill."""

    tally: Tally
    exitstatus: int | None = None


@dataclass(frozen=True)
class LearnerData:
    """Host-frozen input plus exported observations; no temporary paths escape the runner."""

    source: bytes
    suffix: str
    events: AttemptEvents | None = None
    problem: str | None = None


def append_event(event: str, **fields: object) -> None:
    """Write only the fixed run-local file; a caller cannot choose a host destination."""
    with Path(EVENT_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"version": 1, "event": event, **fields}) + "\n")


def read_events(path: Path, *, partial: bool = False) -> AttemptEvents:
    """Read a regular bounded file after execution; reject malformed complete events."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError("attempt report is not a bounded regular file")
    tally, started, finished, exitstatus = Tally(), False, False, None
    # Bound readline too, so a hostile single line cannot bypass the per-event limit.
    with path.open("r", encoding="utf-8") as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise ValueError("attempt event is too long")
            if not line.endswith("\n"):
                if partial:
                    break  # a kill may interrupt the last write, but not earlier lines
                raise ValueError("attempt report has an unfinished event")
            row = json.loads(line)
            if (not isinstance(row, dict) or type(row.get("version")) is not int
                    or row["version"] != 1 or finished):
                raise ValueError("invalid attempt event version or order")
            event = row.get("event")
            if event == "start" and not started and set(row) == {"version", "event"}:
                started = True
            elif event == "report" and started:
                if set(row) != {"version", "event", "kind", "error"}:
                    raise ValueError("invalid attempt observation fields")
                kind, error = row["kind"], row["error"]
                if (not isinstance(kind, str) or kind not in KINDS
                        or (error is not None and (not isinstance(error, str) or len(error) > 300))
                        or (kind in ("failed", "errors") and not error)
                        or (kind in ("passed", "skipped") and error is not None)):
                    raise ValueError("invalid attempt observation")
                setattr(tally, kind, getattr(tally, kind) + 1)
                tally.first_error = tally.first_error or error
            elif event == "finish" and started:
                exitstatus = row.get("exitstatus")
                if (set(row) != {"version", "event", "exitstatus"}
                        or type(exitstatus) is not int or exitstatus not in range(6)):
                    raise ValueError("invalid attempt exit status")
                finished = True
            else:
                raise ValueError("invalid attempt event order")
    if not started or (not partial and not finished):
        raise ValueError("attempt report did not finish")
    return AttemptEvents(tally, exitstatus)
