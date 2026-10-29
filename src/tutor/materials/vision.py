"""Transcribe an image page with a vision model (docs/design.md, "Reading files").

The page goes in as a PNG together with its text layer (which has the slide's own words
but nothing drawn as an image), and comes back as Markdown. Python code blocks in the
answer are parsed with `ast`; one that does not parse gets a note, not a rejection, since
lecture code is sometimes illustrative only.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

from tutor.llm import LLMConfig, chat, image_message
from tutor.materials.cache import TRUNCATED_NOTE, Transcriber
from tutor.materials.pdf import Page

# Recorded with every transcription in the cache; raise it whenever PROMPT changes, so that
# the next --vision run transcribes the older pages again (tests guard against forgetting).
PROMPT_VERSION = 1
PROMPT = """Transcribe this lecture slide into Markdown, for a student who cannot see it.

Rules:
- Transcribe everything on the slide, including text, code and diagrams inside images.
- Code and terminal sessions: copy them exactly, character for character, in fenced code
  blocks with a language tag (python, java, sql, bash). Pseudocode goes in a block tagged
  text. Keep symbols such as ← ∈ ≤ and subscripts as they appear. Never fix, complete or
  reformat code, even if it looks wrong.
- Diagrams: describe structure as text, e.g. "mapper -> (word, 1) -> combiner -> reducer",
  with every label that appears in them.
- Tables: Markdown tables.
- Photos, logos, clip art and word clouds: one short line saying what they show.
- Anything you cannot read: write [?] in its place. Do not guess.
- Never repeat yourself, and never add links, URLs or image references that are not
  printed on the slide.
- Output only the transcription: no introduction, no comments about the slide.

The slide's text layer, which has its typed words but nothing drawn inside images:
---
{text}
---"""

MAX_TOKENS = 2048
REPETITION_NOTE = "<!-- note: a repetition loop was cut off here; check against the slide -->"
# A unit of 1-200 characters repeated back to back at least MIN_REPEATS times: "&nbsp;"
# filling a blank area, "data-data-...", a word-cloud list starting over and over.
LOOP = re.compile(r"(.{1,200}?)\1{7,}", re.S)
FENCE = re.compile(r"^```(\w*)\s*\n(.*?)^```", re.M | re.S)


def make_transcriber(config: LLMConfig, render: Callable[[int], bytes],
                     log_path: Path | None = None) -> Transcriber:
    """`render(page_number)` gives the page as PNG."""
    def transcribe(page: Page) -> str:
        # Copying needs no reasoning: with thinking on, Gemma 4 26B-A4B spent about 85% of
        # its output tokens on it and took 34 s instead of 5 s for the same transcription.
        # The provider decides how thinking is turned off (llm.PROVIDERS).
        # max_tokens: a model caught repeating itself once ran into the 300 s timeout; a
        # slide's transcription needs a few hundred tokens.
        reply = chat(config, [image_message(PROMPT.format(text=page.text), render(page.number))],
                     purpose="vision", log_path=log_path, thinking=False,
                     max_tokens=MAX_TOKENS)
        text, looped = trim_repetition(strip_outer_fence(reply.text))
        text = with_code_notes(text)
        if looped:
            # At temperature 0 a rerun loops the same way, so keep what came before.
            text += "\n" + REPETITION_NOTE
        elif reply.finish_reason == "length":
            # Cut off without a loop: really too long. Kept, but marked so that the page is
            # transcribed again (cache: read "partial").
            text += "\n" + TRUNCATED_NOTE
        return text
    return transcribe


def trim_repetition(text: str) -> tuple[str, bool]:
    """Cut the text at the first runaway repetition; True if one was found. Short legitimate
    repeats (a table's rows, "----") stay, since a loop must repeat a unit 8 times and run
    at least 400 characters."""
    for match in LOOP.finditer(text):
        if len(match.group(0)) >= 400:
            return text[:match.start()] + match.group(1), True
    return text, False


def strip_outer_fence(text: str) -> str:
    """Some models wrap the whole answer in ```markdown ... ```; unwrap it."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, re.S)
    return match.group(1) if match else text.strip()


def with_code_notes(markdown: str) -> str:
    notes = code_notes(markdown)
    return markdown + "".join(f"\n<!-- note: {n} -->" for n in notes)


def code_notes(markdown: str) -> list[str]:
    """One note per Python code block that does not parse (">>> " prompts removed first)."""
    notes = []
    for number, (language, code) in enumerate(FENCE.findall(markdown), 1):
        if language.lower() not in ("python", "py"):
            continue
        lines = code.splitlines()
        if any(line.startswith(">>>") for line in lines):
            # An interactive session mixes code and its output; keep only the code lines.
            lines = [line[4:] for line in lines if line.startswith((">>> ", "... "))]
        try:
            ast.parse("\n".join(lines))
        except SyntaxError as e:
            notes.append(f"code block {number} does not parse as Python "
                         f"(line {e.lineno}: {e.msg})")
    return notes
