"""Doctor page data, checked private inputs and fixed task commands; no model calls here."""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

from tutor import doctor, execution
from tutor.doctor_advice import verification
from tutor.gui import llm_settings, tasks

_ID = re.compile(r"[0-9a-f]{32}")
ACTIONS = {"preview", "advice", "verify", "independent", "repair-preview", "apply"}


def report(course):
    """Default deterministic doctor only, with explicit row/output callbacks."""
    rows = []
    doctor.main([str(course.workspace.root)], output=lambda line: None, on_row=rows.append)
    return rows


def folder(course, identifier: str | None = None, *, create: bool = False) -> Path:
    """Never follow a symlink/junction through a private input storage directory."""
    root = course.workspace.root
    parts = [root / "logs", root / "logs" / "doctor"]
    if identifier is not None:
        if not _ID.fullmatch(identifier):
            raise ValueError("invalid doctor input identifier")
        parts.append(parts[-1] / identifier)
    for path in parts:
        if execution._is_link(path) or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("doctor inputs must stay inside their course")
        if create:
            path.mkdir(exist_ok=True, mode=0o700)
    return parts[-1]


def selected(course, identifier: str):
    """Read one bounded local input manifest, never a command/URL from task metadata."""
    path = folder(course, identifier)
    for file in (path / "input.json", path / "failure.log"):
        if execution._is_link(file):
            raise ValueError("doctor input files cannot be links")
    raw = (path / "input.json").read_bytes()
    if len(raw) > 4096:
        raise ValueError("doctor input metadata is too large")
    data = json.loads(raw.decode("utf-8"))
    if set(data) != {"profile", "second", "module"}:
        raise ValueError("invalid doctor input metadata")
    profiles = {p.name for p in llm_settings.profiles(course)}
    if data["profile"] not in profiles or data["second"] and data["second"] not in profiles:
        raise ValueError("choose existing model profiles on the Models page")
    if data["module"]:
        verification.probe_script(data["module"])
    return path, data


def create_input(course, failure: str, profile: str, second: str, module: str) -> str:
    """Explicitly selected text only, bounded before any write/request; no file discovery."""
    if not failure.strip() or len(failure.encode("utf-8")) > 64 * 1024:
        raise ValueError("select a nonempty failure record of at most 64 KiB")
    profiles = {p.name for p in llm_settings.profiles(course)}
    if profile not in profiles or second and second not in profiles:
        raise ValueError("select configured model profiles")
    if module:
        verification.probe_script(module)
    identifier = uuid.uuid4().hex
    path = folder(course, identifier, create=True)
    (path / "failure.log").write_text(failure, encoding="utf-8")
    (path / "input.json").write_text(json.dumps({"profile": profile, "second": second,
                                                "module": module}), encoding="utf-8")
    return identifier


def command(course, identifier: str, action: str, env_file: Path | None = None) -> list[str]:
    """Only fixed actions and checked storage IDs become argv; no shell is involved."""
    if action not in ACTIONS:
        raise ValueError("invalid doctor action")
    selected(course, identifier)
    args = [sys.executable, "-m", "tutor.gui.doctor_worker", str(course.workspace.root),
            identifier, action]
    if action == "apply":
        args.append("--confirm")
    return args + (["--env-file", str(env_file)] if env_file and env_file.is_file() else [])


def inputs(course):
    """Re-read manifests/task files on every page: no cached progress claim."""
    base = folder(course)
    if not base.is_dir():
        return []
    rows = []
    history = tasks.list_tasks(course.workspace.root)
    candidates = [p for p in base.iterdir() if _ID.fullmatch(p.name) and not execution._is_link(p)]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:20]:
        try:
            _, data = selected(course, path.name)
            record = path / "repair.json"
            preview = (path / "preview.json").is_file()
            rows.append({"id": path.name, **data, "preview": preview,
                         "repair": record.is_file(),
                         "apply_preview": (path / "apply.json").is_file(),
                         "tasks": [t for t in history
                                   if t.meta.get("doctor_input") == path.name]})
        except (ValueError, OSError, TypeError):
            continue
    return rows
