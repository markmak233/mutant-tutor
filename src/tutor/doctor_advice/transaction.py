"""Apply/undo one revalidated repair, keeping explicit backups and no student-code writes.

Only this layer promotes live configuration/environments. Every original is kept until
success and available for explicit undo; failed installs/interrupts restore the old venv.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager

from tutor import execution, workspace
from tutor.doctor_advice import records
from tutor.doctor_advice.apply_backend import Backend
from tutor.doctor_advice.service import AdviceSetupError

BEFORE = "workspace.toml.doctor-before"
OLD_VENV = ".venv.doctor-before"
RECEIPT = ".doctor-repair.json"
LOCK = ".doctor-repair.lock"


def _plain(path) -> None:
    if execution._is_link(path):
        raise AdviceSetupError("repair paths cannot be symbolic links or junctions")


@contextmanager
def locked(root):
    """An exclusive per-course lock prevents overlapping doctor transactions."""
    path = root / LOCK
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise AdviceSetupError("another repair holds this course's lock; "
                               "finish that operation first") from None
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink()


def _atomic(path, raw: bytes) -> None:
    """Replace a file atomically after writing only a fresh owned sibling."""
    target = path.with_name(".doctor-" + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        os.replace(target, path)
    finally:
        target.unlink(missing_ok=True)


def apply(plan, course, *, backend=None) -> None:
    """Reproduce and verify again, then transact; a saved artifact cannot skip verification."""
    root = course.root
    path, backup, live, old = (root / "workspace.toml", root / BEFORE,
                               root / ".venv", root / OLD_VENV)
    backend = Backend(plan.found, course, plan.record.module) if backend is None else backend
    with locked(root):
        for item in (path, backup, live, old, root / RECEIPT):
            _plain(item)
        if any(p.exists() for p in (backup, old, root / RECEIPT)):
            raise AdviceSetupError("a retained repair exists; "
                                   "undo or finish it before another apply")
        if path.read_bytes() != plan.before:
            raise AdviceSetupError("workspace changed; preview and verify again")
        if backend.baseline().passed:
            raise AdviceSetupError("baseline now passes; no repair needs applying")
        if not backend.verify(plan.record.changes).passed:
            raise AdviceSetupError("saved repair did not pass fresh verification; nothing applied")
        is_host = course.runner != "container"
        selected = backend.prepare_python(plan.record.changes) if is_host else None
        if path.read_bytes() != plan.before:
            raise AdviceSetupError("workspace changed during verification; nothing applied")
        moved, promoted, created_backup, created_receipt = False, False, False, False
        try:
            descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created_backup = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(plan.before)
            if is_host:
                if not live.is_dir():
                    raise AdviceSetupError("original course venv disappeared")
                live.rename(old)
                moved = True
                backend.install(live, selected, plan.record.changes)
                check = backend._probe(python=workspace.venv_python(live))
            else:
                candidate = backend.build(plan.after)
                check = backend._probe(image=candidate)
            if not check.passed:
                raise AdviceSetupError("replacement failed the same check; restoring originals")
            if path.read_bytes() != plan.before:
                raise AdviceSetupError("workspace edited during application; "
                                       "preserve the owner's edit")
            _atomic(path, plan.after)
            promoted = True
            receipt = {"schema": 1, "before": records.digest(plan.before),
                       "after": records.digest(plan.after), "host": is_host}
            descriptor = os.open(root / RECEIPT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created_receipt = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(json.dumps(receipt).encode("utf-8"))
        except BaseException:
            # Never overwrite concurrent owner edits. The original venv can still be
            # restored independently; its scripts again point at their original path.
            if promoted and path.read_bytes() == plan.after:
                _atomic(path, plan.before)
            if moved:
                if live.exists():
                    shutil.rmtree(live)
                old.rename(live)
            if created_backup:
                backup.unlink(missing_ok=True)
            if created_receipt:
                (root / RECEIPT).unlink(missing_ok=True)
            raise


def finish(root, *, undo: bool) -> None:
    """Restore an unedited repair, or explicitly discard only original backup artifacts."""
    with locked(root):
        path, backup, live, old = (root / "workspace.toml", root / BEFORE,
                                   root / ".venv", root / OLD_VENV)
        receipt_path = root / RECEIPT
        for item in (path, backup, live, old, receipt_path):
            _plain(item)
        try:
            raw = receipt_path.read_bytes()
            if len(raw) > 4096:
                raise ValueError
            data = json.loads(raw.decode("utf-8"))
            if (set(data) != {"schema", "before", "after", "host"}
                    or type(data["schema"]) is not int or data["schema"] != 1
                    or type(data["host"]) is not bool
                    or undo and records.digest(path.read_bytes()) != data["after"]
                    or records.digest(backup.read_bytes()) != data["before"]
                    or data["host"] and not old.is_dir()):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise AdviceSetupError("no matching retained repair, or workspace/backup was edited; "
                                   "preserve the files and review before undo/finish") from None
        if undo:
            if data["host"]:
                discarded = root / (".doctor-discard-" + uuid.uuid4().hex)
                live.rename(discarded)
                try:
                    old.rename(live)
                    try:
                        _atomic(path, backup.read_bytes())
                    except BaseException:
                        live.rename(old)
                        raise
                except BaseException:
                    discarded.rename(live)
                    raise
                shutil.rmtree(discarded)
            else:
                _atomic(path, backup.read_bytes())
        elif data["host"]:
            shutil.rmtree(old)
        backup.unlink()
        receipt_path.unlink()
