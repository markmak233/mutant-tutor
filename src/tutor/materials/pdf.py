"""Read a PDF page by page with pypdfium2 (docs/design/layout.md, "Reading files").

For each page: its text layer, its label (the slide or page number printed in the footer),
whether it needs the vision model, and a hash of its content.

A page needs the vision model when images that carry content cover at least VISION_SHARE
of it, or when it has images and no text layer at all (a scan). Two kinds of image carry
none: the same image repeated at the same place on many pages (logo, template), and images
with text-layer characters on top (a title's shadow effect, a text box's background), whose
words the text layer already has. Measured on the first
course's lectures: a 25% threshold missed a slide of three small code screenshots; 5%
without the text rule also flagged every section-divider slide; 5% with it caught every
code and pseudocode image checked by eye and flagged 162 of 462 pages.

A page also needs the vision model when its words are drawn, not written: letters stored
as filled outlines (formulas and pseudocode from some slide tools) and handwriting from a
tablet are path objects, so they are neither in the text layer nor images, and such a page
used to be read as its few typed words with nothing saying so. What tells them from a
diagram is variety: outlined letters and pen strokes come in many shapes, while a grid's
cells, a plot's dots or a list's bullets repeat one or two. So the letter-sized filled
shapes that hold a curve are counted by kind (DRAWN_SIZE, DRAWN_SHAPES). Measured on five
courses' lectures, 4,669 pages: at 16 kinds the rule newly marked 302 of 335 unmarked
handwritten pages (the rest mostly near-empty title pages) and 91 pages of a course whose
every formula is an outline, none in two courses whose curves are diagrams, none in typed
notes, and 5 textbook pages whose figures have outlined labels. Counting the shapes
instead of their kinds marked 77 diagram pages whose text was all readable. Not caught: a
page with only a few outlined symbols, and ink stored as open pen strokes (none was at
hand to measure).
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

# An image box repeated on at least this share of the pages (and on 3 or more) is decoration.
REPEATED_SHARE = 0.30
# A page whose content images cover at least this share of it goes to the vision model.
VISION_SHARE = 0.05
TEXT_ON_IMAGE = 5  # characters on top of an image that make it a text effect
# A filled path with a curve whose box is at most this long (points) is letter-sized.
DRAWN_SIZE = 24.0
# A page with at least this many kinds of letter-sized shapes holds drawn words.
DRAWN_SHAPES = 16
# Bottom shares of the page searched for the label, lowest first. A number in the slide's
# body can reach a wide band ("Step size: 2" above the footer 6.10), so the lowest band
# holding a number decides.
FOOTER_BANDS = (0.03, 0.05, 0.07, 0.09, 0.12)
LABEL = re.compile(r"^\d+(\.\d+)*$")  # "2.31", "12"


@dataclass(frozen=True)
class Page:
    number: int  # 1-based PDF page
    label: str | None  # e.g. "2.31", None if the footer has no number
    text: str
    needs_vision: bool
    # Of the text, the images' raw data and, on a page of drawn words, its shapes' kinds;
    # not of a rendering.
    content_hash: str


@dataclass(frozen=True)
class _Raw:
    text: str
    label: str | None
    area: float
    boxes: list[tuple[tuple[int, int, int, int], str, float]]  # rounded box, image hash, area
    image_data: bytes
    # The kinds of letter-sized shapes, sorted, when there are DRAWN_SHAPES or more; else ().
    drawn: tuple[tuple[int, int, int, int], ...]


def read_pdf(path: Path) -> list[Page]:
    pdf = pdfium.PdfDocument(path)
    try:
        raws = [_read_page(page, number) for number, page in enumerate(pdf, 1)]
    finally:
        pdf.close()
    # The same image at the same place: three scanned pages share a box, not an image.
    counts = Counter(key for raw in raws for key in {(b, h) for b, h, _ in raw.boxes})
    repeated = {k for k, n in counts.items() if n >= max(3, REPEATED_SHARE * len(raws))}
    pages = []
    for number, raw in enumerate(raws, 1):
        image_area = sum(area for box, h, area in raw.boxes if (box, h) not in repeated)
        # The shapes are part of the hash only on a page of drawn words: every other page
        # keeps the hash it had, so its cache entry stands, while a page this rule newly
        # marks gets a new hash and is read again (as pending, until a vision run).
        shapes = repr(raw.drawn).encode() if raw.drawn else b""
        digest = hashlib.sha256(raw.text.encode() + raw.image_data + shapes).hexdigest()[:16]
        # A page with images and no text layer at all (a scan) has nothing else to read.
        scanned = bool(raw.boxes) and not raw.text
        pages.append(Page(number, raw.label, raw.text,
                          scanned or bool(raw.drawn)
                          or image_area / raw.area >= VISION_SHARE, digest))
    return pages


def render_png(path: Path, number: int, dpi: int = 150) -> bytes:
    """One page as PNG, for the vision model."""
    pdf = pdfium.PdfDocument(path)
    try:
        image = pdf[number - 1].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _find_label(textpage, width: float, height: float, number: int) -> str | None:
    """The slide number printed at the foot of PDF page `number`, None if there is none."""
    def numbers(band: float) -> list[str]:
        footer = textpage.get_text_bounded(left=0, bottom=0, right=width, top=band * height)
        return [token for token in footer.split() if LABEL.match(token)]

    for band in FOOTER_BANDS:
        labels = numbers(band)
        if labels:
            # A dotted number is a slide number: "6.52" beats the page counter "52" beside it.
            if dotted := [label for label in labels if "." in label]:
                return dotted[-1]
            # Plain footers that count PDF pages: the page's own number beats a stray "5" lower.
            if str(number) in numbers(FOOTER_BANDS[-1]):
                return str(number)
            return labels[-1]
    return None


def _drawn_shapes(page) -> tuple[tuple[int, int, int, int], ...]:
    """The kinds of letter-sized filled shapes on the page that hold a curve, sorted, when
    there are at least DRAWN_SHAPES of them (outlined letters, handwriting); else ().

    A kind is a shape's segment count, its curve count, and its box's width and height to
    half a point: the same letter, cell or dot drawn again is the same kind. Sizes are
    taken in the coordinates the shape is written in; inside a form (a slide placed on a
    handout page) they are the slide's own, which is where a letter has a letter's size."""
    fill, stroke = ctypes.c_int(), ctypes.c_int()
    kinds: set[tuple[int, int, int, int]] = set()
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH], max_depth=3):
        pdfium_c.FPDFPath_GetDrawMode(obj.raw, fill, stroke)
        if fill.value == 0:
            continue  # not filled: a line, a frame, an open pen stroke
        left, bottom, right, top = obj.get_bounds()
        width, height = right - left, top - bottom
        if max(width, height) > DRAWN_SIZE:
            continue  # larger than a letter: a diagram's shape
        segments = pdfium_c.FPDFPath_CountSegments(obj.raw)
        curves = sum(
            pdfium_c.FPDFPathSegment_GetType(pdfium_c.FPDFPath_GetPathSegment(obj.raw, i))
            == pdfium_c.FPDF_SEGMENT_BEZIERTO for i in range(max(segments, 0)))
        if curves:  # straight-sided small shapes are arrow heads, ticks and table cells
            kinds.add((segments, curves, round(width * 2), round(height * 2)))
    return tuple(sorted(kinds)) if len(kinds) >= DRAWN_SHAPES else ()


def _read_page(page, number: int) -> _Raw:
    width, height = page.get_size()
    textpage = page.get_textpage()
    text = "\n".join(line.rstrip() for line in textpage.get_text_range().splitlines()).strip()
    chars = [textpage.get_charbox(i) for i in range(textpage.count_chars())]
    centres = [((left + right) / 2, (bottom + top) / 2) for left, bottom, right, top in chars]
    label = _find_label(textpage, width, height, number)
    boxes, data = [], b""
    for obj in page.get_objects(max_depth=1):
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            images = [obj]
        elif obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            # Images inside a form XObject report bounds in the form's own coordinates, so
            # the form's bounds on the page stand for them.
            images = list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], form=obj))
        else:
            continue
        if not images:
            continue
        left, bottom, right, top = obj.get_bounds()
        covered = sum(left <= x <= right and bottom <= y <= top for x, y in centres)
        if covered >= TEXT_ON_IMAGE:
            continue
        image_data = b"".join(bytes(image.get_data(decode_simple=False)) for image in images)
        boxes.append(((round(left), round(bottom), round(right), round(top)),
                      hashlib.sha256(image_data).hexdigest()[:16],
                      abs(right - left) * abs(top - bottom)))
        data += image_data
    return _Raw(text, label, width * height, boxes, data, _drawn_shapes(page))
