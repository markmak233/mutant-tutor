"""The per-file markdown cache (docs/design.md, "Reading files").

`$WORKSPACE_DIR/cache/<source path>.md` holds one section per page:

    ## p.27 · slide 2.33
    <!-- read: vision · model: glm-5.3-flash · prompt: 1 · 2026-10-28 · hash: 3f2a9c... -->
    (the page's text)

`read` is `text` (the text layer), `vision` (transcribed from an image of the page),
`pending` (an image page whose transcription has not run yet; the text layer stands in) or
`partial` (a transcription cut off at the output limit). `prompt` is the version of the
transcription prompt (vision.PROMPT_VERSION). A transcription checked against the slide and
fixed by hand is marked `corrected`. A page is read again only if its hash changed, or if a
transcriber is given and the page is pending, partial, or transcribed with an older prompt
and not corrected (a newer prompt never silently replaces a hand-checked page), so every
page costs one read per prompt version.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from tutor.materials.pdf import Page

# Written by vision.py when a transcription hits the output limit.
TRUNCATED_NOTE = "<!-- note: cut off at the output limit; transcribe again -->"

HEADER = re.compile(r"^## p\.(\d+)(?: · slide (.+))?$")
META = re.compile(
    r"^<!-- read: (\w+)(?: · model: (.+?))?(?: · prompt: (\w+))?( · corrected)?"
    r"(?: · (\d{4}-\d{2}-\d{2}))? · hash: (\w+) -->$"
)
# page -> transcription of its image, or None if it could not be made (stays pending)
Transcriber = Callable[[Page], "str | None"]


@dataclass(frozen=True)
class Entry:
    number: int
    label: str | None
    read: str  # "text", "vision", "pending" or "partial"
    content_hash: str
    body: str
    model: str | None = None
    date: str | None = None
    prompt: str | None = None  # transcription prompt version; None before it was recorded
    corrected: bool = False  # fixed by hand against the slide


def outdated(entry: Entry, prompt: str | None) -> bool:
    """A transcription made with another prompt version than `prompt`, not hand-corrected."""
    return (prompt is not None and entry.read == "vision" and entry.prompt != prompt
            and not entry.corrected)


def cache_path(workspace: Path, source: Path, lecture_dir: Path | None = None) -> Path:
    """cache/<path>.md, keeping the source's extension so that `x.pdf` and a later `x.pptx`
    do not share a cache file. A source in the lecture folder (LECTURE_DIR, default
    <workspace>/lectures) maps to cache/lectures/<path inside it> wherever that folder is,
    so a lecture's cache is found by its name (lecture_cache); any other source must be
    inside the workspace (ValueError) and mirrors its path there."""
    resolved = source.resolve()
    lectures = (lecture_dir or workspace / "lectures").resolve()
    if resolved.is_relative_to(lectures):
        relative = Path("lectures") / resolved.relative_to(lectures)
    else:
        relative = resolved.relative_to(workspace.resolve())
    return workspace / "cache" / relative.with_name(relative.name + ".md")


def lecture_cache(workspace: Path, lecture: str) -> Path:
    """The cache of lecture `lecture` (e.g. "L4"), read from <lecture folder>/L4.pdf."""
    return workspace / "cache" / "lectures" / f"{lecture}.pdf.md"


def update(pages: list[Page], old: dict[int, Entry], transcribe: Transcriber | None = None,
           model: str | None = None, today: str | None = None,
           prompt: str | None = None) -> tuple[list[Entry], int]:
    """New entries for `pages`, reusing every old entry whose page is unchanged. `prompt`
    is the transcriber's prompt version. Returns the entries and how many pages were read
    now (text layer or transcription)."""
    entries, read_now = [], 0
    for page in pages:
        previous = old.get(page.number)
        unchanged = previous is not None and previous.content_hash == page.content_hash
        redo = unchanged and transcribe is not None and (
            previous.read in ("pending", "partial") or outdated(previous, prompt))
        if unchanged and not redo:
            entries.append(replace(previous, label=page.label))
            continue
        transcription = transcribe(page) if page.needs_vision and transcribe else None
        if transcription is not None:
            read_now += 1
            read = "partial" if TRUNCATED_NOTE in transcription else "vision"
            entries.append(Entry(page.number, page.label, read, page.content_hash,
                                 transcription.strip(), model, today, prompt))
        elif unchanged:
            entries.append(replace(previous, label=page.label))  # still pending or outdated
        else:
            read_now += 1
            read = "pending" if page.needs_vision else "text"
            entries.append(Entry(page.number, page.label, read, page.content_hash, page.text))
    return entries, read_now


def render(source_name: str, entries: list[Entry]) -> str:
    parts = [f"# {source_name}\n"]
    for e in entries:
        header = f"## p.{e.number}" + (f" · slide {e.label}" if e.label else "")
        meta = [f"read: {e.read}"]
        if e.model:
            meta.append(f"model: {e.model}")
        if e.prompt:
            meta.append(f"prompt: {e.prompt}")
        if e.corrected:
            meta.append("corrected")
        if e.date:
            meta.append(e.date)
        meta.append(f"hash: {e.content_hash}")
        parts.append(f"{header}\n<!-- {' · '.join(meta)} -->\n{e.body}\n")
    return "\n".join(parts)


def parse(text: str) -> dict[int, Entry]:
    entries: dict[int, Entry] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = HEADER.match(lines[i])
        meta = META.match(lines[i + 1]) if header and i + 1 < len(lines) else None
        if not meta:
            i += 1
            continue
        start = i = i + 2
        # A body line that merely looks like a header does not end the section.
        while i < len(lines) and not (HEADER.match(lines[i]) and i + 1 < len(lines)
                                      and META.match(lines[i + 1])):
            i += 1
        read, model, prompt, corrected, date, digest = meta.groups()
        number = int(header.group(1))
        entries[number] = Entry(number, header.group(2), read, digest,
                                "\n".join(lines[start:i]).strip(), model, date, prompt,
                                corrected is not None)
    return entries


def load(path: Path) -> dict[int, Entry]:
    return parse(path.read_text()) if path.is_file() else {}


def save(path: Path, text: str) -> None:
    """Write through a temp file in the same folder, so an interrupted run never leaves a
    half-written cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
