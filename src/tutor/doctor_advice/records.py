"""Private repair artifacts contain hashes/structured changes, never paths or failure text.

A saved verification label is not trusted for application: current facts are compared,
permissions gated again, and the same baseline/candidate checks must be repeated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from tutor.config import _enclosing_git_repo
from tutor.doctor_advice.models import Change
from tutor.doctor_advice.service import AdviceInputError
from tutor.doctor_advice.verification import probe_script


@dataclass(frozen=True)
class Repair:
    """A portable preview artifact; original course identity is bound by digests."""

    module: str
    changes: tuple[Change, ...]
    workspace: str
    environment: str


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def environment_digest(found) -> str:
    data = {"python": found.context.current_python, "installed": dict(found.installed),
            "source": found.source, "direct": sorted(found.direct)}
    return digest(json.dumps(data, sort_keys=True).encode("utf-8"))


def capture(found, course, module: str, changes: tuple[Change, ...]) -> Repair:
    if course is None:
        raise AdviceInputError("saving an applicable repair requires a course")
    probe_script(module)
    return Repair(module, tuple(changes), digest((course.root / "workspace.toml").read_bytes()),
                  environment_digest(found))


def check_target(path: Path) -> None:
    """Reject an unusable output before a paid verification request, without reserving it."""
    if (_enclosing_git_repo(path.parent.resolve()) or path.is_symlink()
            or path.name.lower() in {"student.py", "student.c", "reference.py", "workspace.toml"}
            or path.name.lower().startswith(".env") or path.exists() or not path.parent.is_dir()):
        raise AdviceInputError("save a new private JSON repair file outside git and answer files")


def save(path: Path, record: Repair) -> None:
    """Exclusive 0600 output, outside repositories, and never an answer/credential file."""
    check_target(path)
    data = {"schema": 1, **asdict(record)}
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
    except OSError:
        raise AdviceInputError("repair output must be a new writable private file") from None


def read(path: Path) -> Repair:
    """Bounded typed artifact parsing; do not echo untrusted fields in error messages."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        with path.open("rb") as handle:
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise ValueError
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if (set(data) != {"schema", "module", "changes", "workspace", "environment"}
                or type(data["schema"]) is not int or data["schema"] != 1):
            raise ValueError
        probe_script(data["module"])
        if not all(isinstance(data[n], str) and re.fullmatch(r"[0-9a-f]{64}", data[n])
                   for n in ("workspace", "environment")):
            raise ValueError
        if not isinstance(data["changes"], list) or not 1 <= len(data["changes"]) <= 9:
            raise ValueError
        fields = {"kind", "python", "package", "spec"}
        if any(not isinstance(c, dict) or set(c) != fields for c in data["changes"]):
            raise ValueError
        return Repair(data["module"], tuple(Change(**c) for c in data["changes"]),
                      data["workspace"], data["environment"])
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise AdviceInputError("select a valid bounded doctor repair JSON file") from None
