"""An append-only file of JSON lines, for what a retrieval route has already paid a model for.

Rows are written as soon as they are paid for, so an interrupted run keeps them. A run cut off
mid-write can leave a broken last line: reading skips it, and the next write starts on a new
line so that it does not join the broken one and break as well.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


class JsonLines:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._starts_clean = True

    def read(self) -> list[dict]:
        if not self._path.is_file():
            return []
        text = self._path.read_text(encoding="utf-8")
        self._starts_clean = not text or text.endswith("\n")
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def append(self, rows: Iterable[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            if not self._starts_clean:
                f.write("\n")
                self._starts_clean = True
            for row in rows:
                f.write(json.dumps(row) + "\n")
