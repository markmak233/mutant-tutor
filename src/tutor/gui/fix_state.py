"""Read-only wizard state and bounded failure prefilling from known course records.

GET only reads diagnostics and existing task/result files. Workers alone prepare/send
previews; fixed file names and private-path checks never accept a request-supplied path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from tutor import execution
from tutor.doctor_advice import transaction
from tutor.gui import doctor_page, environment_diagnostics, llm_settings, tasks

FAILURE_LIMIT = 64 * 1024
TEXT_LIMIT = 512 * 1024
_IMPORT = re.compile(r"^(?:host )?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*) imports(?:\s|:)")


def row_id(row) -> str:
    value = json.dumps([row.status, row.text, row.next_step], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain(course, path: Path):
    """Check the whole relative chain, including a task directory and log file."""
    relative = path.relative_to(course.workspace.root)
    current = course.workspace.root
    for part in relative.parts:
        current /= part
        if execution._is_link(current):
            raise ValueError("setup fix records cannot follow links")


def seed(course, identifier):
    """Resolve a current uncovered Problem; append only the last failed install/build tail."""
    row = next((r for r in environment_diagnostics.rows(course)
                if r.status == "FAIL" and r.fix == "ask-model" and row_id(r) == identifier), None)
    if row is None:
        raise ValueError("This problem changed or has a rule-based fix. Check Environment again.")
    heading = (row.text + "\n" + row.next_step).encode("utf-8")[:FAILURE_LIMIT]
    separator = b"\n\nLast failed setup task (log tail):\n"
    remaining = FAILURE_LIMIT - len(heading) - len(separator)
    failed = next((t for t in tasks.list_tasks(course.workspace.root)
                   if t.status == tasks.FAILED
                   and t.meta.get("kind") in {"install", "build"}), None)
    if failed and remaining > 0:
        path = failed.folder / tasks.LOG_FILE
        _plain(course, path)
        try:
            with path.open("rb") as handle:
                size = handle.seek(0, 2)
                handle.seek(max(0, size - remaining))
                heading += separator + handle.read(remaining)
        except FileNotFoundError:
            pass
    # Dropping partial code points preserves the byte limit, even for non-ASCII tails.
    match = _IMPORT.match(row.text)
    return heading.decode("utf-8", errors="ignore"), match[1] if match else ""


def text(course, folder, name, *, limit=TEXT_LIMIT):
    """Display a complete bounded result or none; approval text is never silently cut."""
    path = folder / name
    _plain(course, path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def data(course, folder, name):
    try:
        result = json.loads(text(course, folder, name) or "null")
        return result if isinstance(result, dict) else {}
    except ValueError:
        return {}


@dataclass
class View:
    identifier: str
    form: dict
    preview: str | None
    advice: dict
    verification: dict
    independent: dict
    diff: str | None
    preview_ready: bool
    advice_ready: bool
    verification_ready: bool
    verified: bool
    diff_ready: bool
    applied: bool
    retained: bool
    history: list

    @property
    def highest(self):
        return 4 if self.diff_ready or self.applied else (
            3 if self.verification_ready else 2 if self.advice_ready else 1)


def view(course, identifier):
    folder, form = doctor_page.selected(course, identifier)
    failure = text(course, folder, "failure.log", limit=FAILURE_LIMIT)
    if failure is None:
        raise ValueError("The selected failure is unavailable; start from Environment again.")
    history = [t for t in tasks.list_tasks(course.workspace.root)
               if t.meta.get("doctor_input") == identifier]

    def complete(action):
        latest = next((t for t in history if t.meta.get("action") == action), None)
        return latest is None or latest.status == tasks.DONE

    def exists(name):
        path = folder / name
        _plain(course, path)
        return path.is_file()

    preview = text(course, folder, "preview.txt")
    advice = data(course, folder, "advice.json")
    verification = data(course, folder, "verification.json")
    diff = text(course, folder, "diff.txt", limit=256 * 1024)
    verification_ready = bool(verification) and (
        complete("verify") or verification.get("passed") is False)
    verified = (verification_ready and verification.get("passed") is True
                and exists("repair.json"))
    return View(identifier, {**form, "failure": failure}, preview, advice, verification,
                data(course, folder, "independent.json") if complete("independent") else {}, diff,
                bool(preview) and exists("preview.json") and complete("preview"),
                bool(advice) and complete("advice"), verification_ready, verified,
                verified and bool(diff) and exists("apply.json") and complete("repair-preview"),
                exists("applied.json"), retained(course), history)


def retained(course):
    path = course.workspace.root / transaction.RECEIPT
    _plain(course, path)
    return path.is_file()


def choose_second(course, identifier, second):
    """Changing a destination invalidates the send/apply previews before any new task."""
    folder, form = doctor_page.selected(course, identifier)
    proposed = {p.name for p in llm_settings.profiles(course)}
    if not second or second == form["profile"] or second not in proposed:
        raise ValueError("Choose a different configured profile for independent review.")
    path = folder / "input.json"
    path.write_text(json.dumps({**form, "second": second}), encoding="utf-8")
    for name in ("preview.json", "preview.txt", "apply.json", "diff.txt"):
        path = folder / name
        _plain(course, path)
        path.unlink(missing_ok=True)
