"""question.md as HTML, safely: the one place where the GUI marks text as markup.

The question is written by a model (or by hand from the slides), so it is untrusted text.
markdown-it-py turns its Markdown into HTML with raw HTML switched off: a `<script>` in the
file comes out as the visible text "<script>", and links with `javascript:`, `vbscript:`,
`file:` or `data:` targets are dropped by its link check. The Content-Security-Policy would
still block a script that got through. Headings get the ids GitHub gives them, so the
question's own contents links ("[Task](#task)") work here as they do on GitHub.

Two things questions use that plain Markdown without HTML would show as source (counted
2026-11-06 over 60 questions):
- `<details>` blocks (17 questions) for worked steps that stay folded. Only that exact
  shape is recognised, a `<details>` line, an optional `<summary>...</summary>` and a
  `</details>` line, and only these fixed tags are written; the summary and the content are
  rendered like the rest, so no HTML from the file gets through.
- LaTeX between dollars (31 questions). The server turns it into MathML, which browsers draw
  without any script, so the CSP stays as strict. latex2mathml copies text it does not
  understand into its output as it is (`\\text{<script>}` came out as a script tag, and
  `\\href` as a link), so its output is parsed and kept only if every element and attribute
  is plain MathML; otherwise the formula is shown as escaped source.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

from latex2mathml.converter import convert
from markdown_it import MarkdownIt
from markupsafe import Markup, escape
from mdit_py_plugins.dollarmath import dollarmath_plugin

_NOT_IN_SLUG = re.compile(r"[^\w\- ]")
_DETAILS = re.compile(r"^\s*<details>\s*(?:<summary>(.*)</summary>)?\s*$")
_SUMMARY = re.compile(r"^\s*<summary>(.*)</summary>\s*$")
_END_DETAILS = re.compile(r"^\s*</details>\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

# Presentation MathML only: no links, no `annotation-xml` that could carry HTML, no events.
_MATHML_TAGS = frozenset(
    "math mrow mi mo mn ms mtext mspace msub msup msubsup mfrac msqrt mroot mover munder "
    "munderover mtable mtr mtd mstyle mpadded mphantom menclose merror mfenced".split())
_MATHML_ATTRS = frozenset(
    "display mathvariant stretchy fence separator lspace rspace accent accentunder "
    "movablelimits columnalign columnlines columnspacing rowalign rowlines rowspacing frame "
    "framespacing displaystyle scriptlevel width height depth linethickness notation minsize "
    "maxsize symmetric largeop form open close separators align voffset linebreak".split())


def _mathml(latex: str, options: dict) -> str:
    """One formula as MathML, or as escaped source if it cannot be made safely."""
    source = f'<code class="math-source">{escape(latex)}</code>'
    try:
        tree = ET.fromstring(convert(latex, display="block" if options["display_mode"]
                                     else "inline"))
    except Exception:  # noqa: BLE001 - any failure of the converter or the parse: show source
        return source
    for element in tree.iter():
        element.tag = element.tag.rpartition("}")[2]  # drop the namespace; HTML sets it
        if element.tag not in _MATHML_TAGS or not _MATHML_ATTRS.issuperset(element.attrib):
            return source
    return ET.tostring(tree, encoding="unicode")  # escapes all text again


# CommonMark, plus the tables and strikethrough GitHub shows; "html": False is the guard.
# allow_digits=False: "$5 and $10" is money, not a formula.
_MD = (MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
       .use(dollarmath_plugin, allow_digits=False, double_inline=True, allow_labels=False,
            renderer=_mathml))


@dataclass
class _Details:
    summary: str | None
    parts: list = field(default_factory=list)  # str (Markdown) or _Details


def _split(text: str) -> list:
    """The text as Markdown chunks and `<details>` blocks, nested. Lines in a fenced code
    block are never read as tags, and a block left open ends with the text."""
    root: list = []
    stack = [root]
    chunk: list[str] = []
    waiting: _Details | None = None  # a block whose <summary> may be on the next line
    in_fence = False

    def flush() -> None:
        if chunk:
            stack[-1].append("\n".join(chunk))
            chunk.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence:
            if waiting is not None:
                if not line.strip():
                    continue
                found = _SUMMARY.match(line)
                waiting.summary, waiting = (found.group(1) if found else None), None
                if found:
                    continue
            if opened := _DETAILS.match(line):
                flush()
                block = _Details(opened.group(1))
                stack[-1].append(block)
                stack.append(block.parts)
                waiting = None if opened.group(1) is not None else block
                continue
            if _END_DETAILS.match(line) and len(stack) > 1:
                flush()
                stack.pop()
                continue
        chunk.append(line)
    flush()
    return root


def slug(heading: str) -> str:
    """GitHub's heading id: lower case, punctuation dropped, spaces to hyphens."""
    return _NOT_IN_SLUG.sub("", heading.strip().lower()).replace(" ", "-")


def _render(parts: list, seen: Counter) -> str:
    html = []
    for part in parts:
        if isinstance(part, _Details):
            summary = _MD.renderInline(part.summary) if part.summary else "Details"
            html.append(f"<details><summary>{summary}</summary>\n{_render(part.parts, seen)}"
                        "</details>\n")
            continue
        tokens = _MD.parse(part)
        for opening, inline in zip(tokens, tokens[1:], strict=False):
            if opening.type == "heading_open":
                base = slug(inline.content)
                # A repeated heading gets -1, -2, ... as on GitHub.
                opening.attrSet("id", f"{base}-{seen[base]}" if seen[base] else base)
                seen[base] += 1
        html.append(_MD.renderer.render(tokens, _MD.options, {}))
    return "".join(html)


def question_html(text: str) -> Markup:
    return Markup(_render(_split(text), Counter()))
