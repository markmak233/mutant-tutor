"""Reading a lecture saved as an HTML page into sections, each cached like a PDF page.

Some courses give their notes as web pages, not slides (owner, 2026-11-07): a Typora export
of the lecture notes, or a Moodle page saved from the browser (a quiz review whose question
texts teach the topic). An HTML page has no pages, so it is cut into sections, and each
section is cached as one page, `## p.N · slide L.N`: p.N is the section's number, and the
label puts the lecture's number from the file name before it (lecture10.html -> 10.1, 10.2,
...), a dotted number as a slide footer is. Everything that reads the cache (plans, the
coverage and requires drafts, generation, the judge) then works as it does on a PDF.

Only text is taken, as Markdown: headings, paragraphs, lists, tables, code blocks exactly as
written (Typora's code blocks are read line by line from its editor's markup), and formulas
as their TeX source (Typora keeps it in a `math/tex` script; Moodle writes `\\( ... \\)` in the
text). Nothing in the page runs and nothing is fetched: the parser only reads the file.

What is left out, beyond scripts, styles and drawings:
- everything outside the page's content: Typora's `#write`, Moodle's `role="main"` region,
  else `<main>`, `<article>` or `<body>`; navigation, a browser extension's buttons and the
  signed-in user's name are outside it;
- form fields: a saved Moodle quiz holds the student's answers in them, and hidden fields
  hold the session's keys (sesskey). Attributes are never copied, so neither are links;
- in a Moodle quiz review, the attempt's summary (times, grade), each question's mark and
  state, and the feedback on the student's own input; the question text, the general
  feedback and the right answer stay.

Sections: a heading starts one; Moodle starts one per question; a section longer than
TARGET_CHARS is cut at a blank line, never inside a code block. Images are not read: an
image in the content leaves a line `[image: name]`, so a reader knows one was there.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from tutor.materials.pdf import Page

TARGET_CHARS = 3000  # about one slide's worth or a few; cut longer sections at a blank line
# Deeper markup is flattened, so rendering (one call per level) stays far inside Python's
# recursion limit of 1000; saved pages nest about 30 deep.
MAX_DEPTH = 200
SUFFIXES = (".html", ".htm")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
# Never content: what runs, draws, or takes input. Scripts holding TeX are read apart.
_DROP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
              "embed", "video", "audio", "button", "input", "select", "option", "textarea",
              "nav", "aside", "footer", "mjx-container", "math", "head", "title"}
# Screen-reader copies, editor internals (Typora's code editor, its rendered diagrams).
_DROP_CLASSES = {"accesshide", "sr-only", "visually-hidden", "CodeMirror-measure",
                 "CodeMirror-gutters", "CodeMirror-cursors", "md-diagram-panel",
                 "MathJax_Preview", "MathJax", "MathJax_SVG", "md-toc"}
# A Moodle quiz review: what is about this attempt, not the question (see the docstring).
_MOODLE_DROP = {"info", "quizreviewsummary", "gradingdetails", "stackinputfeedback",
                "stackprtfeedback", "questionflag", "submitbtns", "history", "im-controls",
                "im-feedback", "feedbackspan", "validationerror", "specificfeedback",
                "othernav"}
# Moodle's verdict on the student's answer ("Correct answer, well done."); a question's own
# box carries the same class, and stays.
_MOODLE_VERDICTS = {"correct", "incorrect", "partiallycorrect"}
# An implied end: these close an open one of their own kind (HTML lets the end tag go).
_SELF_CLOSING = {"p": {"p"}, "li": {"li"}, "tr": {"tr", "td", "th"}, "td": {"td", "th"},
                 "th": {"td", "th"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}}
_SECTION = "\x00section\x00"  # marks where a Moodle question starts; NUL is never in text


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class _Tree(HTMLParser):
    """The page as a tree of tags and text: HTMLParser only reports tags one by one."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: v or "" for k, v in attrs})
        closes = _SELF_CLOSING.get(tag, set())
        if self.stack[-1].tag in closes:
            self.stack.pop()
        self.stack[-1].children.append(node)
        if tag not in _VOID and len(self.stack) < MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        # Close up to the nearest open tag of this name; a stray end tag changes nothing.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _find(node: _Node, test) -> _Node | None:
    """The first node, in document order, that passes `test`."""
    todo: list[_Node] = [node]
    while todo:
        current = todo.pop(0)
        if test(current):
            return current
        todo[0:0] = [c for c in current.children if isinstance(c, _Node)]
    return None


def _text(node: _Node | str) -> str:
    """All the text below a node, as it stands."""
    if isinstance(node, str):
        return node
    return "".join(_text(c) for c in node.children)


@dataclass
class _Shape:
    content: _Node
    moodle: bool


def _shape(document: _Node) -> _Shape:
    if write := _find(document, lambda n: n.attrs.get("id") == "write"):
        return _Shape(write, False)  # Typora
    if main := _find(document, lambda n: n.attrs.get("role") == "main"
                     or n.attrs.get("id") == "region-main"):
        moodle = _find(main, lambda n: "que" in n.classes) is not None
        return _Shape(main, moodle)
    for tag in ("main", "article", "body"):
        if found := _find(document, lambda n, t=tag: n.tag == t):
            return _Shape(found, False)
    return _Shape(document, False)


class _Markdown:
    """Renders the content node as Markdown text, with _SECTION before each Moodle question."""

    def __init__(self, moodle: bool):
        self.moodle = moodle

    def dropped(self, node: _Node) -> bool:
        return node.tag in _DROP_TAGS or self.hidden(node)

    def hidden(self, node: _Node) -> bool:
        """Not shown to a reader, or not part of the content (see the module docstring)."""
        if "hidden" in node.attrs or node.attrs.get("type") == "hidden":
            return True
        if node.attrs.get("aria-hidden") == "true":
            return True
        if re.search(r"display\s*:\s*none", node.attrs.get("style", "")):
            return True
        classes = node.classes
        if classes & _DROP_CLASSES:
            return True
        return self.moodle and bool(classes & _MOODLE_DROP or (
            classes & _MOODLE_VERDICTS and "que" not in classes))

    def render(self, node: _Node | str) -> str:
        if isinstance(node, str):
            return re.sub(r"\s+", " ", node)
        tag = node.tag
        if tag == "script" and node.attrs.get("type", "").startswith("math/tex"):
            tex = _text(node).strip()
            return f"\n\n$$\n{tex}\n$$\n\n" if "display" in node.attrs["type"] else f"${tex}$"
        if tag == "select" and not self.hidden(node):
            # A drop-down's choices are part of the question; which one was selected is the
            # student's answer, and is not kept. An empty choice is "(no answer given)".
            choices = [" ".join(_text(o).split()) for o in node.children
                       if isinstance(o, _Node) and o.tag == "option" and o.attrs.get("value")]
            return f" [{' / '.join(choices)}] " if choices else ""
        if self.dropped(node):
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            if "icon" in node.classes:
                return ""
            name = node.attrs.get("alt") or Path(node.attrs.get("src", "")).name
            return f"[image: {name}]" if name else "[image]"
        if tag == "pre":
            return self.code_block(node)
        parts = []
        for child in node.children:  # a loop, not a generator: one stack frame per level
            parts.append(self.render(child))
        inner = "".join(parts)
        if self.moodle and "que" in node.classes:
            number = _find(node, lambda n: "qno" in n.classes)
            title = f"### Question {_text(number).strip()}" if number else "### Question"
            return f"\n\n{_SECTION}\n\n{title}\n\n{inner.strip()}\n\n"
        if re.fullmatch(r"h[1-6]", tag):
            text = " ".join(inner.split())
            return f"\n\n{'#' * int(tag[1])} {text}\n\n" if text else ""
        if tag == "li":
            # A nested list sits in the item's text, so indenting the item's lines nests it;
            # it follows its item's text without a blank line, as a written list does.
            body = re.sub(r"\n\s*\n(?=- )", "\n", inner.strip())
            return "\n- " + body.replace("\n", "\n  ")
        if tag in ("ul", "ol"):
            return f"\n\n{inner.strip()}\n\n"
        if tag == "tr":
            cells = [" ".join(self.render(c).split()) for c in node.children
                     if isinstance(c, _Node) and c.tag in ("td", "th") and not self.dropped(c)]
            return "\n| " + " | ".join(cells) + " |" if any(cells) else ""
        if tag == "table":
            return f"\n\n{inner.strip()}\n\n"
        if tag == "code":
            return f"`{inner.strip()}`" if inner.strip() else ""
        if tag in ("strong", "b"):
            return f"**{inner.strip()}**" if inner.strip() else inner
        if tag in ("p", "div", "section", "article", "header", "figure", "figcaption",
                   "blockquote", "dl", "dt", "dd", "form", "fieldset", "main", "body", "hr"):
            return f"\n\n{inner.strip()}\n\n"
        return inner

    def code_block(self, node: _Node) -> str:
        """A fenced block with the code exactly as written. Typora's editor puts each line
        in its own `pre.CodeMirror-line`, next to measuring copies that are left out."""
        language = node.attrs.get("lang", "")
        lines = []
        todo: list[_Node | str] = [node]
        while todo:
            current = todo.pop(0)
            if isinstance(current, str) or self.dropped(current):
                continue
            if current is not node and "CodeMirror-line" in current.classes:
                lines.append(_text(current))
                continue
            todo[0:0] = current.children
        text = "\n".join(lines) if lines else _text(node)
        text = text.replace("​", "").strip("\n")
        return f"\n\n```{language}\n{text}\n```\n\n" if text.strip() else ""


def to_markdown(html: str) -> tuple[str, bool]:
    """The page's content as Markdown, and whether it is a Moodle quiz (one section per
    question)."""
    tree = _Tree()
    # A NUL is never text in HTML (a broken copy can be nothing else), and marks sections here.
    tree.feed(html.replace("\x00", ""))
    tree.close()
    shape = _shape(tree.root)
    text = _Markdown(shape.moodle).render(shape.content)
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), shape.moodle


def sections(markdown: str, moodle: bool = False) -> list[str]:
    """The text cut where a heading (or, in a Moodle quiz, a question) starts, and where a
    section grows past TARGET_CHARS at a blank line outside a code block. A heading with
    nothing under it joins the next section."""
    out: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush():
        body = "\n".join(current).strip()
        current.clear()
        if not body:
            return
        if out and all(line.startswith("#") or not line.strip() for line in out[-1].splitlines()):
            out[-1] = out[-1] + "\n\n" + body  # a lone heading leads the next section
        else:
            out.append(body)

    for line in markdown.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line == _SECTION:
            flush()
            continue
        if not in_fence and not moodle and re.match(r"#{1,6} ", line):
            flush()
        current.append(line)
        if not in_fence and not line.strip() and sum(len(x) + 1 for x in current) > TARGET_CHARS:
            flush()
    flush()
    return out


def lecture_number(path: Path) -> str | None:
    """The number in a lecture's file name, put before its section numbers: "lecture10" ->
    "10", "Lecture_5.2-Topic" -> "5.2". A browser's "(4)" for a second download is not one."""
    stem = re.sub(r"\s*\(\d+\)$", "", path.stem)
    found = re.search(r"\d+(?:\.\d+)*", stem)
    # "lecture01" -> "1": labels are compared as numbers, and read better without the zero.
    return ".".join(str(int(part)) for part in found.group(0).split(".")) if found else None


def decode(data: bytes) -> str:
    """UTF-8 as saved by browsers and Typora; else Windows' Western encoding, which can
    decode any byte."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_html(path: Path) -> list[Page]:
    """The lecture's sections as pages: text only, so none needs the vision model."""
    markdown, moodle = to_markdown(decode(path.read_bytes()))
    number = lecture_number(path)
    pages = []
    for n, text in enumerate(sections(markdown, moodle), 1):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        pages.append(Page(n, f"{number}.{n}" if number else str(n), text, False, digest))
    return pages


def looks_like_html(head: bytes) -> bool:
    """Whether a file's first bytes are an HTML page's (an upload is checked by its content,
    not only its name): a doctype or an <html> tag near the start."""
    start = head[:4096].lower()
    return b"<!doctype html" in start or b"<html" in start
