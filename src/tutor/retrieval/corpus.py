"""What every retrieval route searches: the lecture cache, one page at a time.

The page is the unit of retrieval (docs/design.md, "RAG"). The cache already splits each
lecture into pages, a slide is short, and a page number can be checked against the slides,
so there is no separate chunker. Routes get these pages and nothing else: coverage.md holds
the answers they are measured against (see labels.py), so a route must never read it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tutor.config import WORKSPACE_FILE
from tutor.materials import cache, slides

# A rough estimate, not a tokenizer: it only has to be the same for every route, so that a
# token budget is equally strict on all of them.
CHARS_PER_TOKEN = 4
# Pages whose transcription is missing or cut off; generation leaves them out too.
_UNREAD = ("pending", "partial")

PageKey = tuple[str, int]  # (lecture number, PDF page), e.g. ("2.1", 12)


class RetrievalSettings(BaseModel):
    """The workspace.toml `[retrieval]` table. Course facts stay in the workspace."""

    # A typo such as `exclude_lecture` is an error, not a setting silently ignored.
    model_config = ConfigDict(extra="forbid")

    exclude_lectures: list[str] = Field(default_factory=list, description=(
        "Lecture numbers to leave out, e.g. a file that repeats another lecture's slides "
        "under other footers: a route finding the copy would be counted wrong."))


def load_settings(workspace: Path) -> RetrievalSettings:
    path = workspace / WORKSPACE_FILE
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return RetrievalSettings(**data.get("retrieval", {}))


@dataclass(frozen=True)
class Page:
    lecture: str  # the lecture's number: "2.1"
    number: int  # the PDF page
    slide: str | None  # the footer printed on the slide, when it has one: "2.15"
    text: str

    @property
    def key(self) -> PageKey:
        return (self.lecture, self.number)

    @property
    def tokens(self) -> int:
        return max(1, len(self.text) // CHARS_PER_TOKEN)


def load_pages(workspace: Path, exclude: list[str] | None = None) -> list[Page]:
    """Every readable page of the cached lectures, in course order."""
    lectures = slides.cached_lectures(workspace)
    excluded = set(exclude or ())
    # A misspelt number would otherwise leave the copy in and skew every result silently.
    if unknown := excluded - {number for number, _ in lectures}:
        raise ValueError(f"exclude_lectures names lectures not in the cache: {sorted(unknown)}")
    pages = []
    for number, path in lectures:
        if number in excluded:
            continue
        for entry in sorted(cache.load(path).values(), key=lambda e: e.number):
            if entry.read not in _UNREAD and entry.body.strip():
                pages.append(Page(number, entry.number, entry.label, entry.body))
    return pages
