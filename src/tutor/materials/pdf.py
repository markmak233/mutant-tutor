"""Read a PDF page by page with pypdfium2 (docs/design.md, "Reading files").

For each page: its text layer, its label (the slide or page number printed in the footer),
whether it needs the vision model, and a hash of its content.

A page needs the vision model when images that carry content cover at least VISION_SHARE
of it. Two kinds of image carry none: decoration repeated at the same place on many pages
(logo, template), and images with text-layer characters on top (a title's shadow effect, a
text box's background), whose words the text layer already has. Measured on the first
course's lectures: a 25% threshold missed a slide of three small code screenshots; 5%
without the text rule also flagged every section-divider slide; 5% with it caught every
code and pseudocode image checked by eye and flagged 162 of 462 pages.
"""

from __future__ import annotations

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
FOOTER_BAND = 0.12  # bottom share of the page searched for the label
LABEL = re.compile(r"^\d+(\.\d+)*$")  # "2.31", "12"


@dataclass(frozen=True)
class Page:
    number: int  # 1-based PDF page
    label: str | None  # e.g. "2.31", None if the footer has no number
    text: str
    needs_vision: bool
    content_hash: str  # of the text and the images' raw data, not of a rendering


@dataclass(frozen=True)
class _Raw:
    text: str
    label: str | None
    area: float
    boxes: list[tuple[tuple[int, int, int, int], float]]  # rounded box, its area
    image_data: bytes


def read_pdf(path: Path) -> list[Page]:
    pdf = pdfium.PdfDocument(path)
    try:
        raws = [_read_page(page) for page in pdf]
    finally:
        pdf.close()
    counts = Counter(box for raw in raws for box in {b for b, _ in raw.boxes})
    repeated = {b for b, n in counts.items() if n >= max(3, REPEATED_SHARE * len(raws))}
    pages = []
    for number, raw in enumerate(raws, 1):
        image_area = sum(area for box, area in raw.boxes if box not in repeated)
        digest = hashlib.sha256(raw.text.encode() + raw.image_data).hexdigest()[:16]
        pages.append(Page(number, raw.label, raw.text,
                          image_area / raw.area >= VISION_SHARE, digest))
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


def _read_page(page) -> _Raw:
    width, height = page.get_size()
    textpage = page.get_textpage()
    text = "\n".join(line.rstrip() for line in textpage.get_text_range().splitlines()).strip()
    chars = [textpage.get_charbox(i) for i in range(textpage.count_chars())]
    centres = [((left + right) / 2, (bottom + top) / 2) for left, bottom, right, top in chars]
    footer = textpage.get_text_bounded(left=0, bottom=0, right=width, top=FOOTER_BAND * height)
    labels = [token for token in footer.split() if LABEL.match(token)]
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
        boxes.append(((round(left), round(bottom), round(right), round(top)),
                      abs(right - left) * abs(top - bottom)))
        data += b"".join(bytes(image.get_data(decode_simple=False)) for image in images)
    return _Raw(text, labels[-1] if labels else None, width * height, boxes, data)
