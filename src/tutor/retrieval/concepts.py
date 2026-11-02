"""The concept index route, a small form of the "LLM wiki" pattern (docs/design.md, "RAG").

Instead of searching the pages at query time, a model reads each lecture once and compiles
its index: the knowledge points it teaches and the pages of each. To answer a query, a model
sees only that index, a small fraction of the lecture, and picks the points that fit. The
result is the pages of those points. So a page is found by what it teaches, even when the
query shares no word with it, and every page returned comes with the reason it was chosen.

Both steps are kept on disk, so a lecture is indexed once and a query asked once. A changed
lecture or a new prompt builds again, for that lecture only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from tutor.materials import cache
from tutor.retrieval.asker import Ask, digest, usage_of
from tutor.retrieval.corpus import Page, PageKey
from tutor.retrieval.jsonl import JsonLines

# Part of what a stored index or pick depends on: a new version builds them again.
PROMPT_VERSION = 1
# A lecture's index is far shorter than this; a reply cut off here is an error, not an index.
BUILD_MAX_TOKENS = 12_000
PICK_MAX_TOKENS = 1_000

BUILD_PROMPT = """\
You are writing the index of a lecture's slides, so that a reader can find every page that
teaches a topic without reading the slides. Each page starts with a "## p.N" line.

List the knowledge points the lecture teaches. For each one give:
- name: the point in a few words, in the lecture's own terms.
- aliases: other words a reader might search for: API, function and class names, synonyms.
- summary: one sentence on what the lecture teaches about it.
- pages: every page number N that teaches it, with its examples, its code and the pages that
  continue it, not only the first.

A page may belong to several points. Cover the whole lecture: anything a reader may look for
must be findable here. Leave out only pages with nothing to teach, such as a title page. Keep
points specific ("joining two inputs on a key"), not as broad as the lecture's subject."""

PICK_PROMPT = """\
Below is an index of lecture topics, one per line: an id, then the topic, other words for it,
and what the lecture teaches about it. A reader needs to learn the request that follows.

Choose the topics that teach it, the most useful first, and only those really needed. Reply
with their ids. If none fits, reply with an empty list."""


class Concept(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str
    pages: list[int]


class LectureIndex(BaseModel):
    concepts: list[Concept]


class Picked(BaseModel):
    ids: list[str]


def _lecture_hash(pages: list[Page]) -> str:
    """Changes when anything the index was built from changes."""
    return digest(PROMPT_VERSION, [(page.number, page.text) for page in pages])


def build_lecture(pages: list[Page], ask: Ask) -> tuple[list[Concept], list[str]]:
    """One lecture's index, and a line for each thing dropped from what the model wrote. A
    page number outside the lecture is a mistake, and must not reach the results."""
    text = "\n\n".join(f"## p.{page.number}\n{page.text}" for page in pages)
    reply = ask("concept-index", BUILD_PROMPT, text, LectureIndex.model_json_schema(),
                BUILD_MAX_TOKENS)
    known = {page.number for page in pages}
    concepts, dropped = [], []
    for concept in LectureIndex.model_validate_json(reply).concepts:
        valid = sorted(set(concept.pages) & known)
        if invalid := sorted(set(concept.pages) - known):
            dropped.append(f"{concept.name}: no page {invalid}")
        if valid:
            concepts.append(concept.model_copy(update={"pages": valid}))
        else:
            dropped.append(f"{concept.name}: left with no pages")
    return concepts, dropped


class ConceptStore:
    """One file per lecture, for one model: a different model's index is a different index."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, lecture: str) -> Path:
        return self.directory / f"L{lecture}.json"

    def load(self, pages: list[Page]) -> list[Concept] | None:
        """The stored index of these pages' lecture; None if missing or out of date."""
        path = self._path(pages[0].lecture)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["hash"] != _lecture_hash(pages):
            return None
        return LectureIndex.model_validate(data).concepts

    def save(self, pages: list[Page], concepts: list[Concept]) -> None:
        data = {"hash": _lecture_hash(pages), "concepts": [c.model_dump() for c in concepts]}
        cache.save(self._path(pages[0].lecture), json.dumps(data, indent=1) + "\n")


def by_lecture(pages: Iterable[Page]) -> dict[str, list[Page]]:
    grouped: dict[str, list[Page]] = {}
    for page in pages:
        grouped.setdefault(page.lecture, []).append(page)
    return grouped


@dataclass(frozen=True)
class _Entry:
    lecture: str
    concept: Concept

    def line(self, entry_id: str) -> str:
        other = f" ({', '.join(self.concept.aliases)})" if self.concept.aliases else ""
        return f"{entry_id} | {self.concept.name}{other} | {self.concept.summary}"


class ConceptRetriever:
    """The concept route: the pages of the topics a model picks from the lectures' indexes.

    `run` separates repeated measurements of the same query: a model's answer may differ
    between runs, which is worth knowing, and a stored pick would hide it."""

    name = "concept"

    def __init__(self, pages: list[Page], store: ConceptStore, ask: Ask, run: int = 0) -> None:
        self._pages: dict[PageKey, Page] = {page.key: page for page in pages}
        self._ask = ask
        self._run = run
        self._entries: dict[str, list[_Entry]] = {}
        for lecture, lecture_pages in by_lecture(pages).items():
            concepts = store.load(lecture_pages)
            if concepts is not None:
                self._entries[lecture] = [_Entry(lecture, c) for c in concepts]
        self._all_lectures = list(by_lecture(pages))
        self._file = JsonLines(store.directory / "picks.jsonl")
        self._picks = {row["key"]: row for row in self._file.read()}
        self.used: list[dict] = []  # this process's picks, stored or new, for the summary

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        """Pages of the picked topics in the order picked, each page once."""
        scope = [lecture for lecture in self._all_lectures
                 if lectures is None or lecture in lectures]
        # Searching without an index would score as a miss, not show as a missing index.
        if missing := [lecture for lecture in scope if lecture not in self._entries]:
            raise ValueError(f"no concept index for lectures {missing}: "
                             f"run python -m tutor.retrieval --build-concepts")
        # The id holds the lecture, so ids stay stable whichever lectures are in scope.
        listing = {f"{lecture}/{position}": entry for lecture in scope
                   for position, entry in enumerate(self._entries[lecture])}
        found: dict[PageKey, Page] = {}
        for entry_id in self._pick(query, listing):
            if entry := listing.get(entry_id):  # an id the model made up finds nothing
                for number in entry.concept.pages:
                    key = (entry.lecture, number)
                    found.setdefault(key, self._pages[key])
        return list(found.values())

    def _pick(self, query: str, listing: dict[str, _Entry]) -> list[str]:
        text = "\n".join(entry.line(entry_id) for entry_id, entry in listing.items())
        key = digest(PROMPT_VERSION, self._run, query, text)
        if key not in self._picks:
            in_before, out_before = usage_of(self._ask)
            start = time.monotonic()
            # A reply cut off or not in the schema finds nothing, and is stored like any other:
            # asking again until one works would score the route better than it is. The model
            # has done this (2026-11-02). A request that fails is another error and is not caught:
            # it says nothing about the route, so it is not kept as an answer.
            try:
                reply = self._ask("concept-pick", PICK_PROMPT, f"{text}\n\nRequest: {query}",
                                  Picked.model_json_schema(), PICK_MAX_TOKENS)
                ids, failed = Picked.model_validate_json(reply).ids, False
            except ValueError:
                ids, failed = [], True
            in_after, out_after = usage_of(self._ask)
            row = {"key": key, "query": query, "run": self._run, "ids": ids, "failed": failed,
                   "tokens_in": in_after - in_before, "tokens_out": out_after - out_before,
                   "seconds": round(time.monotonic() - start, 2)}
            self._file.append([row])
            self._picks[key] = row
        self.used.append(self._picks[key])
        return self._picks[key]["ids"]
