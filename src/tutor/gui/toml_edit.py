"""Writing tables of a course's workspace.toml, safely: [llm] from the Models page (step 6),
[requires], [safety] allow_imports and [isolation] from the Environment page (step 7); and,
the same way, the shared llm.toml beside the courses (edit_path; gui/providers.py).

tomlkit keeps the rest of the file as the owner wrote it, comments included (owner,
2026-11-06); a value changed loses only its own line's comment. Every save is checked
before the file is replaced: the result read back with tomllib must hold exactly the table
the caller intended, worked out from the old data and not from tomlkit, and every other
table and key unchanged. The file before is kept as workspace.toml.bak.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from collections.abc import Callable
from pathlib import Path

import tomlkit

from tutor.config import WORKSPACE_FILE

REFUSED = ("%s could not be changed safely, so it was left as it was. Edit it by "
           "hand, or report this.")



def read(root: Path) -> dict:
    """The course's workspace.toml as plain data; {} when it is missing or not TOML (a page
    then shows its defaults, and a save is refused by edit's own check)."""
    try:
        with (root / WORKSPACE_FILE).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

def edit(root: Path, table: str, change: Callable, expected: dict) -> list[str]:
    """Apply `change` (it gets the parsed tomlkit document) to the course's workspace.toml,
    check that `table` now reads as `expected` and nothing else moved, keep a backup, then
    replace the file in one step. [] when written, else the problem (nothing written)."""
    return edit_tables(root, change, {table: expected})


def edit_tables(root: Path, change: Callable, expected: dict[str, dict]) -> list[str]:
    """edit() for one save that changes several tables, such as [requires] with
    [safety] allow_imports: each must read as expected, and every other one unchanged."""
    return edit_path(root / WORKSPACE_FILE, change, expected)


def edit_path(path: Path, change: Callable, expected: dict[str, dict]) -> list[str]:
    """The checked write itself, for any TOML file: `change` gets the parsed tomlkit
    document (an empty one when the file does not exist yet), each table in `expected`
    must then read as given and every other table be unchanged, the file before is kept
    as `<name>.bak`, and the new text replaces it in one step. [] when written."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    doc = tomlkit.parse(text)
    change(doc)
    new = tomlkit.dumps(doc)
    try:
        before, after = tomllib.loads(text), tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return [REFUSED % path.name]
    if any(_bare(after.get(t, {})) != _bare(e) for t, e in expected.items()) or _rest(
            after, expected) != _rest(before, expected):
        return [REFUSED % path.name]
    if text:
        path.with_name(path.name + ".bak").write_text(text, encoding="utf-8")
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(new, encoding="utf-8", newline="")
    os.replace(tmp, path)
    return []


def put(table, key: str, value) -> None:
    """table[key] = value; a new key goes right after the table's last key, not after the
    comments and blank lines that end the table, which belong to what follows it. That
    uses tomlkit's Container._insert_after (pinned below 1.0, and a test shows it holds);
    without it, plain assignment, which is only less tidy."""
    if key in table:
        table[key] = value
        return
    last = [k for k, _ in table.value.body if k is not None]
    try:
        table.value._insert_after(last[-1], key, value)
    except (AttributeError, IndexError, KeyError):
        table[key] = value


def spaced(table):
    """A new table ending in a blank line, so the header after it is not run into it."""
    table.add(tomlkit.nl())
    return table


def _bare(section: dict) -> dict:
    """A table without its empty sub-tables: an empty [llm.roles] may be written out or not."""
    return {k: v for k, v in section.items() if v != {}}


def _rest(data: dict, tables) -> dict:
    return {k: v for k, v in data.items() if k not in tables}
