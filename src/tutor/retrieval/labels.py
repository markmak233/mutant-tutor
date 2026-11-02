"""The labelled queries retrieval is measured on, read from the workspace's coverage.md.

coverage.md was written by hand for another reason (choosing the next batch), so it makes
labels no retrieval route has influenced. Each row names a knowledge point and its slides,
which is a query and its relevant pages, in the same words a batch plan uses at generation:

    ### L3 Some lecture
    | Slides | Point | Status |
    |---|---|---|
    | 3.15-3.20, 3.26 | Joins on a shared key | gap-code |

The labels are not complete: a page nobody listed may still be relevant. So recall, which
only asks whether the listed pages were found, can be trusted further than precision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from tutor.retrieval.corpus import Page, PageKey

COVERAGE_FILE = "coverage.md"
CROSS_LECTURE_FILE = "retrieval/cross-lecture.jsonl"
# Statuses with no knowledge point to find: course admin, and points taken off the list.
_NOT_A_QUERY = ("n/a", "dropped")
# About one query in this many goes to the dev split, the rest to the test split.
_DEV_EVERY = 3

_LECTURE = re.compile(r"^### L(\d+(?:\.\d+)?)\b")
# A slides cell: "3.15", "3.15-3.20", or several of those separated by commas.
_RANGE = r"\d+(?:\.\d+)+(?:-\d+(?:\.\d+)+)?"
_SLIDES = re.compile(rf"^{_RANGE}(?:,\s*{_RANGE})*$")


@dataclass(frozen=True)
class Row:
    lecture: str
    slides: str
    point: str
    status: str


@dataclass(frozen=True)
class Query:
    id: str
    text: str
    relevant: frozenset[PageKey]
    split: str  # "dev": for tuning; "test": for the final numbers only; "cross": spans lectures

    @property
    def lectures(self) -> set[str]:
        return {lecture for lecture, _ in self.relevant}


def parse_coverage(text: str) -> list[Row]:
    """The table rows under each `### L<number>` heading. Any other `##` heading ends a
    lecture, so tables in other sections (labs, for one) are not read as labels."""
    rows = []
    lecture = None
    for line in text.splitlines():
        if heading := _LECTURE.match(line):
            lecture = heading.group(1)
        elif line.startswith("#"):
            lecture = None
        elif lecture and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            # The header and separator rows fail the slides pattern, so they drop out here.
            if len(cells) == 3 and _SLIDES.match(cells[0]):
                rows.append(Row(lecture, cells[0], cells[1], cells[2]))
    return rows


def _position(slide: str) -> tuple[int, ...]:
    """"4.15" -> (4, 15): compared as numbers, so 4.9 comes before 4.15."""
    return tuple(int(part) for part in slide.split("."))


def _page_numbers(pages: list[Page], slides: str) -> set[int] | None:
    """The PDF pages a slides cell means, or None if a range matches no page. A range runs
    from its first slide's page to its last, so pages without a footer between them count,
    as they do for the slices generation reads."""
    numbers: set[int] = set()
    for part in slides.split(","):
        low, _, high = part.strip().partition("-")
        first, last = _position(low), _position(high or low)
        matching = [p.number for p in pages
                    if p.slide and re.fullmatch(r"\d+(?:\.\d+)+", p.slide)
                    and first <= _position(p.slide) <= last]
        if not matching:
            return None
        numbers |= {p.number for p in pages if min(matching) <= p.number <= max(matching)}
    return numbers


def _digest(row: Row) -> str:
    return hashlib.sha256(f"{row.lecture}\n{row.point}".encode()).hexdigest()


def queries_from(rows: list[Row], pages: list[Page]) -> tuple[list[Query], list[str]]:
    """The queries, and a line per row that could not be labelled, to show instead of
    silently measuring on fewer queries.

    A row's split comes from the hash of that row alone, so it needs no stored file, and
    adding rows or lectures later never moves an existing query between the splits: a query
    seen while tuning could otherwise slip into the test split and flatter its numbers."""
    by_lecture: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        by_lecture[page.lecture].append(page)
    queries, problems = [], []
    for row in rows:
        if row.status.startswith(_NOT_A_QUERY):
            continue
        numbers = _page_numbers(by_lecture.get(row.lecture, []), row.slides)
        if not numbers:
            problems.append(f"L{row.lecture} {row.slides}: no page has these slides")
            continue
        digest = _digest(row)
        relevant = frozenset((row.lecture, number) for number in numbers)
        split = "dev" if int(digest, 16) % _DEV_EVERY == 0 else "test"
        queries.append(Query(digest[:10], row.point, relevant, split))
    return queries, problems


def load_queries(workspace: Path, pages: list[Page]) -> tuple[list[Query], list[str]]:
    path = workspace / COVERAGE_FILE
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: the labels come from it")
    return queries_from(parse_coverage(path.read_text(encoding="utf-8")), pages)


def load_cross_lecture(workspace: Path, pages: list[Page]) -> tuple[list[Query], list[str]]:
    """The hand-written queries whose answer spans lectures, one JSON object per line:

        {"query": "...", "slides": {"2": "2.23-2.25", "4": "4.8-4.14"}}

    They live apart from coverage.md, whose rows each belong to one lecture, and are frozen
    before any route runs on them. The slides still come from coverage.md's rows wherever one
    fits, so these labels do not depend on what any route happens to return."""
    path = workspace / CROSS_LECTURE_FILE
    if not path.is_file():
        return [], []
    by_lecture: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        by_lecture[page.lecture].append(page)
    queries, problems = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        relevant: set[PageKey] = set()
        missing = []
        for lecture, slides in row["slides"].items():
            numbers = _page_numbers(by_lecture.get(lecture, []), slides)
            if numbers:
                relevant |= {(lecture, number) for number in numbers}
            else:
                missing.append(f"L{lecture} {slides}")
        if missing:
            problems.append(f"{row['query']}: no page has {', '.join(missing)}")
            continue
        query_id = hashlib.sha256(row["query"].encode()).hexdigest()[:10]
        queries.append(Query(query_id, row["query"], frozenset(relevant), "cross"))
    return queries, problems
