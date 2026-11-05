"""The question of an exercise as Markdown, for reading in a previewer.

The question lives in student.py's module docstring, the one source; question.md is made
from it by code (no model), so the two never disagree. Two layouts:

- Markdown (docs/design.md, "question.md format"; generated batches from 2026-10-31): the
  docstring is already Markdown, a raw string so LaTeX keeps its backslashes, starting with
  "# <title>". Code adds what a model should not have to keep right: the line under the
  title (lecture, slides, mode, type, difficulty, from metadata.json), the contents line,
  the rules (---) and "How to run" with the real folder; a "## Run" section's commands
  are folded into it.
- Plain (earlier batches): section labels on their own lines ("Task:"), indented data. A
  docstring is plain text read in an editor, which the owner found hard to read
  (2026-10-29); in Markdown its labels become headings and its indented data, tables and
  commands become code blocks.

    python -m tutor.question BATCH_DIR   writes question.md for every exercise of a batch
"""

from __future__ import annotations

import ast
import json
import re
import sys
import textwrap
from pathlib import Path

QUESTION_FILE = "question.md"
REPO = Path(__file__).resolve().parents[2]

# A section label at the start of a line: "Input:", "Example (min_readings = 2):",
# "Run the tests:". Only labels starting with these words, so a sentence with a colon
# ("Keep a combiner: the fix is ...") stays a sentence.
_LABEL = re.compile(
    r"^((?:Concept|Symptom|Task|Your|Input|Output|Example|Examples|Constraints|Order|"
    r"Parameter|Parameters|Slides|Source|Teaching|Notes|Run)\b[^:{}]{0,40}):(?:\s+(.*))?$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s")
COPYABLE = ("Example", "Run")  # sections whose indented lines become code blocks
# The Markdown layout's section heading, and an inline formula.
_HEADING = re.compile(r"^## (.+?)\s*$")
_MATH = re.compile(r"\$[^$\n]+\$")


def _docstring(student_source: str) -> str:
    """student.py's module docstring, cleaned of its indentation; "" if none or broken."""
    try:
        return ast.get_docstring(ast.parse(student_source), clean=True) or ""
    except SyntaxError:
        return ""


def is_markdown(doc: str) -> bool:
    """The Markdown layout starts with a "# " title; the plain one with bare title text."""
    return doc.startswith("# ")


def question_markdown(student_source: str, metadata: dict | None = None,
                      mode: str | None = None) -> str:
    """student.py's module docstring as Markdown, without "How to run"; "" when it has
    none. `metadata` and `mode` fill the Markdown layout's line under the title."""
    doc = _docstring(student_source)
    if not doc:
        return ""
    if is_markdown(doc):
        return markdown_question(doc, metadata, mode)
    title, *lines = doc.splitlines()
    out = [f"# {title.strip()}", ""]
    i, in_list, copyable = 0, False, False
    while i < len(lines):
        line = lines[i]
        label = _LABEL.match(line)
        if label:
            out += ["", f"## {label.group(1)}", ""]
            rest, in_list = label.group(2), False
            # Code blocks only where there is something to copy: the example (code, sample
            # data) and the commands. Elsewhere indented text is plain lines, since a page
            # of grey boxes is harder to read (owner, 2026-10-30).
            copyable = label.group(1).startswith(COPYABLE)
            i += 1
            if rest:
                # Lines aligned under the inline text ("Input:  spark -- ...\n        rows")
                # belong to it: one code block where copyable, else plain lines.
                start, block = line.index(rest), [rest]
                while i < len(lines) and lines[i].startswith(" ") and lines[i].strip():
                    block.append(lines[i][start:] if lines[i][:start].isspace()
                                 else lines[i].strip())
                    i += 1
                if len(block) > 1:
                    out += (_fence(block) if copyable else [b.strip() for b in block]) + [""]
                else:  # a sentence that may go on in the next lines
                    out.append(_command(rest))
            continue
        if in_list and line.startswith(" ") and line.strip():
            out.append(line.strip())  # a list item's wrapped line, not a code block
            i += 1
            continue
        if line.startswith("    "):
            block = []
            # One code block for the indented run, blank lines inside it included.
            while i < len(lines) and (lines[i].startswith("    ") or (
                    not lines[i].strip() and i + 1 < len(lines)
                    and lines[i + 1].startswith("    "))):
                block.append(lines[i])
                i += 1
            if copyable:
                out += _fence(textwrap.dedent("\n".join(block)).splitlines()) + [""]
            else:  # plain lines, left-aligned: Markdown reads 4 spaces as a code block
                out += [b.strip() for b in block] + [""]
            continue
        in_list = bool(_LIST_ITEM.match(line)) or (in_list and bool(line.strip()))
        out.append(line.strip())
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(_keep_breaks(out))).strip() + "\n"


def _keep_breaks(out: list[str]) -> list[str]:
    """End a text line followed by another with a Markdown line break, so the question
    keeps its lines ("input: ..." then "output: ...") instead of running them together."""
    fenced = False
    result = []
    for line, after in zip(out, [*out[1:], ""], strict=True):
        if line.startswith("```"):
            fenced = not fenced
        plain = (line.strip() and not fenced and not line.startswith(("#", "```"))
                 and after.strip() and not after.startswith(("#", "```"))
                 and not _LIST_ITEM.match(after))
        result.append(line + "  " if plain else line)
    return result


def _command(text: str) -> str:
    """"python student.py   (prints ...)" with the command as code; other text as it is."""
    match = re.match(r"((?:python|pytest) .*?)(\s{2,}.*|\s+\(.*)?$", text)
    return f"`{match.group(1)}`{match.group(2) or ''}" if match else text


def _fence(block: list[str]) -> list[str]:
    """Lines as a plain-text code block."""
    return ["```text", *block, "```"]


def _shown(location: Path) -> str:
    """The folder as "How to run" shows it. A workspace exercise shows its absolute path,
    which pastes anywhere (the workspace is private); one inside the repository shows a
    path from the repository's folder, since public files never hold a real absolute path."""
    location = location.resolve()
    try:
        return location.relative_to(REPO).as_posix()
    except ValueError:
        return str(location)


def run_block(location: Path) -> str:
    """How to run the exercise from a terminal (owner, 2026-10-30): its folder, the tests,
    the program, a hint. The plain layout's version: its Run section stays above it."""
    shown = _shown(location)
    return (
        "\n## How to run\n\n"
        "With the repository's virtual environment active:\n\n"
        "```bash\n"
        f'cd "{shown}"\n'
        "pytest test_student.py      # the tests\n"
        "python student.py           # the program, with the arguments Run shows, if any\n"
        "```\n\n"
        "Stuck? `hints.md` in this folder has three levels of hints, each more specific; "
        f'or, from the repository\'s folder: `python -m tutor.hint "{shown}"`\n'
    )


# --- the Markdown layout ----------------------------------------------------------------


def markdown_question(doc: str, metadata: dict | None = None, mode: str | None = None,
                      location: Path | None = None) -> str:
    """A Markdown-layout docstring as question.md: the title, the line under it, the
    contents, a rule; Concept, a rule; the other sections; a rule and "How to run" when
    `location` is known (a draft has none). The model writes only the title and the
    sections."""
    title, *lines = doc.splitlines()
    # (heading, its lines); text before the first "## " heading is kept unheaded.
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        heading = None if in_code else _HEADING.match(line)
        if heading:
            sections.append((heading.group(1), []))
        else:
            sections[-1][1].append(line)
    # "## Run" (a file-level program's command lines) moves into "How to run".
    commands, notes = _run_section(next((body for h, body in sections if h == "Run"), []))
    sections = [(h, body) for h, body in sections if h != "Run" and (h or _text(body))]
    headings = [h for h, _ in sections if h] + (["How to run"] if location else [])

    out = [title, ""]
    if under := _under_title(metadata, mode):
        out += [under, ""]
    out += ["**Contents:** " + " · ".join(f"[{h}](#{_anchor(h)})" for h in headings), "",
            "---", ""]
    for i, (heading, body) in enumerate(sections):
        # A rule after Concept: the lecture part ends and the task begins.
        if i > 0 and sections[i - 1][0] == "Concept":
            out += ["---", ""]
        out += ([f"## {heading}", ""] if heading else []) + [_text(body), ""]
    if location is not None:
        out += ["---", "", markdown_run_block(location, commands, notes)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def _text(lines: list[str]) -> str:
    """A section's lines as one block, a formula's | made safe in table rows."""
    out, in_code = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and line.lstrip().startswith("|"):
            # In a table row every | ends a cell, also inside $...$ ($m = |S|$ split a
            # generated table, 2026-10-31): there it becomes \vert, which KaTeX draws the same.
            line = _MATH.sub(lambda m: m.group(0).replace("|", "\\vert "), line)
        out.append(line)
    return "\n".join(out).strip()


def _run_section(lines: list[str]) -> tuple[list[str], list[str]]:
    """A "## Run" section's commands (the lines of its code blocks, without the pytest
    line "How to run" has already) and its notes (the text around them)."""
    commands, notes, in_code = [], [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif in_code and line.strip() and not line.strip().startswith("pytest"):
            commands.append(line.strip())
        elif not in_code and line.strip():
            notes.append(line.strip())
    return commands, notes


def _under_title(metadata: dict | None, mode: str | None) -> str:
    """"> **Lecture 3** · slides 3.10-3.14 · learn mode · implementation · medium";
    whatever is known of it. "" when nothing is."""
    metadata = metadata or {}
    source = metadata.get("source")
    parts = []
    if isinstance(source, dict):  # "synthetic" for the repository's examples
        if source.get("lecture"):
            parts.append(f"**{source['lecture']}**")
        if source.get("slides"):
            parts.append(f"slides {source['slides']}")
    if mode:
        parts.append(f"{mode} mode")
    parts += [str(metadata[key]) for key in ("type", "difficulty") if metadata.get(key)]
    return "> " + " · ".join(parts) if parts else ""


def _anchor(heading: str) -> str:
    """The link target a previewer gives a heading: "How to run" -> "how-to-run"."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).strip().replace(" ", "-")


def markdown_run_block(location: Path, commands: list[str] | None = None,
                       notes: list[str] | None = None) -> str:
    """The Markdown layout's "How to run": the folder, the tests, the program (the "## Run"
    section's commands as written, else plain `python student.py`), the Run section's
    notes, and the hint command."""
    shown = _shown(location)
    # Aligned with "# the tests", and at least one space before the comment.
    program = commands or [f"{'python student.py':<27} # the program"]
    return "\n".join([
        "## How to run", "",
        "With the repository's virtual environment active:", "",
        "```bash", f'cd "{shown}"', "pytest test_student.py      # the tests", *program,
        "```", "", *([" ".join(notes), ""] if notes else []),
        "> **Stuck?** `hints.md` in this folder has three levels of hints, each more "
        "specific; or ask",
        "> for a hint from the repository's folder:", ">",
        "> ```bash", f'> python -m tutor.hint "{shown}"', "> ```", ""])


def write_question(student_source: str, exercise_dir: Path,
                   location: Path | None = None, metadata: dict | None = None,
                   mode: str | None = None) -> bool:
    """Write exercise_dir/question.md; False (nothing written) when there is no question.
    `location`: where the exercise lives for good, for the "How to run" block (none
    while it is still a draft, whose folder is temporary). `metadata` (metadata.json) and
    `mode` (the batch's) fill the Markdown layout's line under the title."""
    doc = _docstring(student_source)
    if not doc:
        return False
    if is_markdown(doc):
        text = markdown_question(doc, metadata, mode, location)
    else:
        text = question_markdown(student_source)
        if location is not None:
            text += run_block(location)
    (exercise_dir / QUESTION_FILE).write_text(text, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    """python -m tutor.question BATCH_DIR: question.md for every exercise of the batch."""
    if len(argv) != 1:
        print(__doc__.split("\n\n")[-1])
        return 2
    batch = Path(argv[0])
    manifest = batch / "manifest.json"
    mode = (json.loads(manifest.read_text(encoding="utf-8")).get("mode")
            if manifest.is_file() else None)
    for solution in sorted((batch / "solutions").glob("*/")):
        exercise = batch / "exercises" / solution.name
        if not exercise.is_dir():
            continue
        meta = solution / "metadata.json"
        metadata = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else None
        # student.py holds the question in every batch (week 1-3 skeletons are code only);
        # the skeleton is the fallback when the owner's half-edited student.py does not parse.
        sources = [exercise / "student.py", solution / "skeleton.py"]
        written = any(path.is_file() and write_question(path.read_text(encoding="utf-8"),
                                                         exercise, exercise, metadata, mode)
                      for path in sources)
        print(f"{solution.name}: {'question.md' if written else 'no question'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
