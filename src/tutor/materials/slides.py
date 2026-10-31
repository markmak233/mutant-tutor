"""An exercise's lecture pages, from the lecture cache, for a model that reads them.

Exercises name their lecture and pages in metadata `source`, in the forms the batches
happen to use ("L2.2", "Lecture 5: Topic C", "Lecture_4.2-Topic_B"; pages "12-15, 20",
"p.8-p.10", "L1.1 3-8; L1.2 5-9"). Only the lecture's number is reliable, so the cache
file is found by it (cache/lectures/Lecture_<number>-....pdf.md).

Used by the held-out writer (tutor/heldout_writer.py) and the teaching-quality judge
(tutor/judge/): both see the whole lecture with the exercise's own pages marked, as
generation sees it when it fits.
"""

from __future__ import annotations

import re
from pathlib import Path

from tutor.materials import cache

# Characters of lecture pages a prompt may hold, as generation's context_chars: lectures
# that fit go whole, else only the exercise's own pages (the largest lecture is ~53k).
CONTEXT_CHARS = 120_000
# A lecture number as names write it: "2.2", "5", "7.1".
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# "L1.1 3-8, 12": a group of pages that names its lecture first.
_LABELLED = re.compile(r"^\s*L(\d+(?:\.\d+)?)\s+(.*)$")
# A cache file's name: "Lecture_7.1-Some_Topic.pdf.md" -> "7.1".
_CACHE_NAME = re.compile(r"^Lecture_(\d+(?:\.\d+)?)[-_]")


def lecture_numbers(source) -> list[str]:
    """The lecture numbers metadata `source.lecture` names, in order: "L2.1, L2.2" ->
    ["2.1", "2.2"]. [] for a hand-made exercise's "synthetic" source."""
    if not isinstance(source, dict):
        return []
    return _NUMBER.findall(source.get("lecture") or "")


def lecture_context(workspace: Path, source, limit: int = CONTEXT_CHARS) -> str:
    """The lectures metadata `source` names, as "## <lecture> p.N · slide L" sections, the
    exercise's own pages (`source.pdf_pages`) marked. Whole lectures if they fit in
    `limit` characters, else only the exercise's own pages. "" when no lecture is found (a
    hand-made exercise's "synthetic" source, or a lecture not in the cache)."""
    if not isinstance(source, dict):
        return ""
    numbers = lecture_numbers(source)
    own: dict[str, set[int]] = {}
    for lecture, pages in page_groups(source.get("pdf_pages") or "", numbers):
        own.setdefault(lecture, set()).update(pages)
    lectures = []  # (name, its pages, the exercise's pages in it)
    for number in dict.fromkeys([*numbers, *own]):  # in order, each once
        path = lecture_file(workspace, number)
        if path is not None:
            lectures.append((path.name.removesuffix(".pdf.md"), cache.load(path),
                             own.get(number, set())))
    whole = sum(len(e.body) for _, entries, _ in lectures
                for e in entries.values()) <= limit
    sections = []
    for name, entries, mine in lectures:
        for number in sorted(entries):
            if whole or number in mine:
                e = entries[number]
                label = f" · slide {e.label}" if e.label else ""
                mark = " · this exercise's page" if number in mine else ""
                sections.append(f"## {name} p.{number}{label}{mark}\n{e.body}")
    return "\n\n".join(sections)


def page_groups(pdf_pages: str, lectures: list[str]) -> list[tuple[str, set[int]]]:
    """(lecture number, PDF pages) from the forms the batches use: "12-15, 20-22",
    "p.8-p.10, p.14", "16–21" (an en dash), or per lecture "L1.1 3-8; L1.2 5-9".
    A group without a lecture label belongs to the exercise's only lecture; with several
    lectures and no label, which pages are whose is unknown, so the group is left out."""
    groups = []
    for part in pdf_pages.split(";"):
        labelled = _LABELLED.match(part)
        if labelled:
            lecture, text = labelled.group(1), labelled.group(2)
        elif len(lectures) == 1:
            lecture, text = lectures[0], part
        else:
            continue
        pages: set[int] = set()
        for item in text.replace("–", "-").replace("p.", "").split(","):
            bounds = [b.strip() for b in item.split("-")]
            if 1 <= len(bounds) <= 2 and all(b.isdigit() for b in bounds):
                pages.update(range(int(bounds[0]), int(bounds[-1]) + 1))
        if pages:
            groups.append((lecture, pages))
    return groups


def lecture_file(workspace: Path, number: str) -> Path | None:
    """cache/lectures/Lecture_<number>-....pdf.md. The character after the number must be
    "-" or "_", so lecture 5 does not match Lecture_5.2-...."""
    pattern = re.compile(rf"^Lecture_{re.escape(number)}[-_]")
    found = sorted(p for p in (workspace / "cache" / "lectures").glob("*.pdf.md")
                   if pattern.match(p.name))
    return found[0] if found else None


def lectures_taught(workspace: Path, source) -> list[str]:
    """The cached lectures up to and including the exercise's last one, in course order,
    by name ("Lecture_5-Topic_C"): what a student has been taught when solving it.
    [] when the source names no lecture."""
    numbers = lecture_numbers(source)
    if not numbers:
        return []
    last = max(_order(n) for n in numbers)
    taught = []
    for path in (workspace / "cache" / "lectures").glob("*.pdf.md"):
        named = _CACHE_NAME.match(path.name)
        if named and _order(named.group(1)) <= last:
            taught.append((_order(named.group(1)), path.name.removesuffix(".pdf.md")))
    return [name for _, name in sorted(taught)]


def _order(number: str) -> tuple[int, ...]:
    """"5" -> (5,), "5.2" -> (5, 2): so 5 < 5.2 < 7.1, which plain strings get wrong
    for "10" after "9"."""
    return tuple(int(part) for part in number.split("."))
