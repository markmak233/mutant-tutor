"""API keys in the repository's `.env` (GUI step 6): list them, add, replace and remove them.

Where (owner, 2026-11-06): the `.env` the commands already read (config.load_env, in the
repository, where every GUI task runs), so the command line and the GUI share one place.
The user adds and removes keys by name, not only the ones a profile names.

What the GUI does with a key: writes it, then never shows it again in full; a page shows the
last 4 characters of a long one, to tell two apart. The file is parsed as text here and never
loaded into the GUI's own environment, which VS Code and its terminals inherit
(docs/design.md, "Local GUI"). Only key-like names (…_KEY, …_TOKEN, …_SECRET) and the names
a course's profile gives in `api_key_env` are listed or removed; WORKSPACE_DIR and the other
settings in the file are left as they are. Every other line, comments included, is kept as
written. The file is replaced in one step and readable by its owner only (0600).

A variable already set in the environment the GUI was started from wins over `.env` (as
load_env decides), so a page says when a key there shadows the one in the file.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_KEY_LIKE = re.compile(r"_(KEY|TOKEN|SECRET)$")
MAX_VALUE = 500
SHOWN_TAIL = 4  # characters shown of a key, and only of one at least MIN_TAIL_LENGTH long
MIN_TAIL_LENGTH = 16


@dataclass(frozen=True)
class KeyRow:
    name: str
    tail: str | None  # "…abcd"; "" when set but too short to show any; None when not set
    used_by: list[str] = field(default_factory=list)  # "course: profile", from api_key_env
    shadowed: bool = False  # also set in the GUI's environment, which commands then use

    @property
    def is_set(self) -> bool:
        return self.tail is not None

    @property
    def by_course(self) -> list[tuple[str, list[str]]]:
        """`used_by` as the page shows it: one (course, its profiles) per course, so a key
        that five models of a course name reads as one line, not the course five times
        (owner, 2026-11-10)."""
        grouped: dict[str, list[str]] = {}
        for entry in self.used_by:
            course, _, profile = entry.partition(": ")
            grouped.setdefault(course, []).append(profile)
        return list(grouped.items())


def read(env_file: Path) -> dict[str, str]:
    """NAME -> value for each `NAME=value` line, as config.load_env reads them (the first
    one of a name wins there too, through setdefault)."""
    found: dict[str, str] = {}
    for line in _lines(env_file):
        parsed = _parse(line)
        if parsed and parsed[0] not in found:
            found[parsed[0]] = parsed[1]
    return found


def rows(env_file: Path, used_by: dict[str, list[str]]) -> list[KeyRow]:
    """The keys to show: key-like names in the file and every name a profile uses, set or
    not, by name. `used_by`: name -> the "course: profile" entries that name it."""
    values = read(env_file)
    names = sorted({n for n in values if _KEY_LIKE.search(n)} | set(used_by))
    return [KeyRow(n, _tail(values[n]) if n in values else None, sorted(used_by.get(n, [])),
                   n in os.environ) for n in names]


def set_key(env_file: Path, name: str, value: str) -> str | None:
    """Add or replace one key; a problem, or None when saved."""
    value = value.strip()
    if not NAME.match(name):
        return ("A key's name takes capital letters, digits and '_', starting with a letter, "
                "e.g. DEEPSEEK_API_KEY.")
    if not value:
        return "Paste the key first."
    if len(value) > MAX_VALUE or any(c.isspace() or c in "'\"" or ord(c) < 32 for c in value):
        return "That does not look like a key: no spaces, quotes or line breaks."
    lines = _lines(env_file)
    line = f"{name}={value}"
    at = next((i for i, text in enumerate(lines) if (_parse(text) or ("",))[0] == name), None)
    if at is None:
        lines.append(line)
    else:
        lines[at] = line
        # Later lines of the same name were ignored before; leave none that could mislead.
        lines = [t for i, t in enumerate(lines) if i <= at or (_parse(t) or ("",))[0] != name]
    _write(env_file, lines)
    return None


def delete_key(env_file: Path, name: str, used_by: dict[str, list[str]]) -> str | None:
    """Remove every line setting `name`; only a key-like name or one a profile uses."""
    if not NAME.match(name) or not (_KEY_LIKE.search(name) or name in used_by):
        return "Only keys can be removed here."
    lines = _lines(env_file)
    kept = [t for t in lines if (_parse(t) or ("",))[0] != name]
    if len(kept) == len(lines):
        return "This key is not in .env."
    _write(env_file, kept)
    return None


def _parse(line: str) -> tuple[str, str] | None:
    """(name, value) of a `NAME=value` line, as config.load_env reads it; None otherwise."""
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    name, value = text.split("=", 1)
    return name.strip(), value.strip().strip("'\"")


def _tail(value: str) -> str:
    return f"…{value[-SHOWN_TAIL:]}" if len(value) >= MIN_TAIL_LENGTH else ""


def _lines(env_file: Path) -> list[str]:
    try:
        return env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _write(env_file: Path, lines: list[str]) -> None:
    """Replace the file in one step, through a new file readable by its owner only."""
    tmp = env_file.with_name(f".env.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, env_file)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(env_file, 0o600)
