"""An exercise's lecture pages, from the lecture cache, for a model that reads them.

Exercises name their lecture and pages in metadata `source`. The pipeline writes the exact
name of the cache file (cache/lectures/<name>.pdf.md, or .html.md for notes saved as a web page),
so a course may name its files anything.
Earlier batches hold what a model wrote ("L2.2", "Lecture 5: Topic C"; pages "12-15, 20",
"p.8-p.10", "L1.1 3-8; L1.2 5-9"): there only the number after the word for lecture is
reliable, and the cache file is found by it (Lecture_<number>-....pdf.md).

Used by the held-out writer (tutor/heldout_writer.py) and the teaching-quality judge
(tutor/judge/): both see the whole lecture with the exercise's own pages marked, as
generation sees it when it fits. A named lecture that yields no pages is a warning: both
still run without slides, and their results then mean less.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

from tutor.materials import cache

# Characters of lecture pages a prompt may hold, as generation's context_chars: lectures
# that fit go whole, else only the exercise's own pages. Courses differ: one's largest lecture
# is ~53k characters, another's ~200k.
CONTEXT_CHARS = 120_000
# A lecture's number where a name writes it: "L2.2", "Lecture 5:", "Lecture_7.1-", "Lectures 3".
# Only right after that word, or "Lecture 7 (CS1234, Week 7, 2026)" would name four lectures.
_NUMBER = re.compile(r"\b(?:Lectures?|L)[\s_]*(\d+(?:\.\d+)?)", re.IGNORECASE)
# "L1.1 3-8, 12": a group of pages that names its lecture first.
_LABELLED = re.compile(r"^\s*L(\d+(?:\.\d+)?)\s+(.*)$")
# A cache file's name: "Lecture_7.1-Some_Topic.pdf.md" -> "7.1".
_CACHE_NAME = re.compile(r"^Lecture_(\d+(?:\.\d+)?)[-_]")


def lecture_number(text: str) -> str | None:
    """The first lecture number a name carries ("Lecture_9.1-MapReduce_V" -> "9.1"), or
    None for a name written without one."""
    found = _NUMBER.search(text)
    return found.group(1) if found else None


def lecture_numbers(source) -> list[str]:
    """The lecture numbers metadata `source.lecture` names, in order and each once:
    "L2.1, L2.2" -> ["2.1", "2.2"]. [] for a hand-made exercise's "synthetic" source."""
    if not isinstance(source, dict):
        return []
    # Each once: pages without a label belong to "the only lecture", which a repeat would hide.
    return list(dict.fromkeys(_NUMBER.findall(source.get("lecture") or "")))


def lecture_context(workspace: Path, source, limit: int = CONTEXT_CHARS,
                    mark: bool = True) -> str:
    """The lectures metadata `source` names, as "## <lecture> p.N · slide L" sections, the
    exercise's own pages (`source.pdf_pages`) marked unless `mark` is False (the judge
    names them apart instead, so the pages read the same for every exercise of the lecture
    and a provider's prompt cache serves them). Whole lectures if they fit in `limit`
    characters, else only the exercise's own pages. "" for a hand-made exercise's
    "synthetic" source; also "", with a warning, when `source` names a lecture but no page of
    it is found (not in the cache, or too long with its own pages unreadable)."""
    lectures = _lectures(workspace, source)
    whole = sum(len(e.body) for _, entries, _ in lectures
                for e in entries.values()) <= limit
    sections = []
    for name, entries, mine in lectures:
        for number in sorted(entries):
            if whole or number in mine:
                e = entries[number]
                label = f" · slide {e.label}" if e.label else ""
                own = " · this exercise's page" if mark and number in mine else ""
                sections.append(f"## {name} p.{number}{label}{own}\n{e.body}")
    if not sections and isinstance(source, dict) and source.get("lecture"):
        warnings.warn(f"no pages found for lecture {source['lecture']!r} (pdf_pages "
                      f"{source.get('pdf_pages')!r}): this runs without slides", stacklevel=2)
    return "\n\n".join(sections)


def own_pages(workspace: Path, source) -> list[str]:
    """The exercise's own pages as "<lecture> p.N" lines, in lecture and page order: what
    lecture_context(mark=False) leaves out of the sections, for a line of its own."""
    return [f"{name} p.{number}" for name, entries, mine in _lectures(workspace, source)
            for number in sorted(entries) if number in mine]


def _lectures(workspace: Path, source) -> list[tuple[str, dict, set[int]]]:
    """(lecture name, its cached pages, the exercise's page numbers in it) for each lecture
    `source` names; [] for a source that is not a dict (a hand-made exercise's)."""
    if not isinstance(source, dict):
        return []
    pdf_pages = source.get("pdf_pages") or ""
    lectures = []  # (name, its pages, the exercise's pages in it)
    if (named := _named_file(workspace, source)) is not None:
        # One lecture, so every group of pages is its own, labelled or not.
        mine = {page for _, pages in page_groups(pdf_pages, [_lecture_name(named)])
                for page in pages}
        lectures.append((_lecture_name(named), cache.load(named), mine))
    else:
        numbers = lecture_numbers(source)
        own: dict[str, set[int]] = {}
        for lecture, pages in page_groups(pdf_pages, numbers):
            own.setdefault(lecture, set()).update(pages)
        for number in dict.fromkeys([*numbers, *own]):  # in order, each once
            path = lecture_file(workspace, number)
            if path is not None:
                lectures.append((_lecture_name(path), cache.load(path),
                                 own.get(number, set())))
    return lectures


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
    """cache/lectures/Lecture_<number>-....pdf.md (or .html.md). The character after the
    number must be "-" or "_", so lecture 5 does not match Lecture_5.2-...."""
    pattern = re.compile(rf"^Lecture_{re.escape(number)}[-_]")
    found = sorted(p for p in cache.lecture_caches(workspace) if pattern.match(p.name))
    return found[0] if found else None


def _lecture_name(path: Path) -> str:
    """A cache file's lecture: Lecture_5-Topic_C.pdf.md -> "Lecture_5-Topic_C"."""
    return cache.lecture_name(path) or path.name


def _named_file(workspace: Path, source: dict) -> Path | None:
    """The cache file `source.lecture` names exactly, or None. A plain name only: a model may
    have written the text, and "../" must not read outside the cache."""
    lecture = source.get("lecture") or ""
    if not lecture or Path(lecture).name != lecture:
        return None
    path = cache.lecture_cache(workspace, lecture)
    return path if path.is_file() else None


def _file_order(name: str) -> tuple:
    """Sort key for the course order, from the numbers in a file's name wherever they stand:
    "L2" before "L10", "Week_5" before "Week_5.2", "CS1234_L3" before "CS1234_L10"."""
    return tuple(int(number) for number in re.findall(r"\d+", name)), name.lower()


def lectures_taught(workspace: Path, source) -> list[str]:
    """The cached lectures up to and including the exercise's last one, in course order,
    by name ("Lecture_5-Topic_C"): what a student has been taught when solving it.
    [] when the source names no lecture."""
    if isinstance(source, dict) and (named := _named_file(workspace, source)) is not None:
        names = sorted((_lecture_name(path) for path in cache.lecture_caches(workspace)),
                       key=_file_order)
        if unordered := [name for name in names if not re.search(r"\d", name)]:
            # No guessing: alphabetical order is not the course's.
            warnings.warn(f"no number in the name of {unordered}, so the course order is unknown: "
                          f"only {_lecture_name(named)!r} counts as taught", stacklevel=2)
            return [_lecture_name(named)]
        return names[:names.index(_lecture_name(named)) + 1]
    numbers = lecture_numbers(source)
    if not numbers:
        return []
    last = max(_order(n) for n in numbers)
    return [_lecture_name(path) for number, path in cached_lectures(workspace)
            if _order(number) <= last]


def cached_lectures(workspace: Path) -> list[tuple[str, Path]]:
    """(lecture number, cache file) of every cached lecture, in course order."""
    found = []
    for path in cache.lecture_caches(workspace):
        if named := _CACHE_NAME.match(path.name):
            found.append((named.group(1), path))
    return sorted(found, key=lambda item: _order(item[0]))


def _order(number: str) -> tuple[int, ...]:
    """"5" -> (5,), "5.2" -> (5, 2): so 5 < 5.2 < 7.1, which plain strings get wrong
    for "10" after "9"."""
    return tuple(int(part) for part in number.split("."))
