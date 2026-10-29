"""The question of an exercise as Markdown, for reading in a previewer.

The question lives in student.py's module docstring, the one source; question.md is made
from it by code (no model), so the two never disagree. A docstring is plain text read in an
editor, which the owner found hard to read (2026-10-29); in Markdown its section labels
become headings and its indented data, tables and commands become code blocks.

    python -m tutor.question BATCH_DIR   writes question.md for every exercise of a batch
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

QUESTION_FILE = "question.md"

# A section label at the start of a line: "Input:", "Example (min_readings = 2):",
# "Run the tests:". Only labels starting with these words, so a sentence with a colon
# ("Keep a combiner: the fix is ...") stays a sentence.
_LABEL = re.compile(
    r"^((?:Concept|Symptom|Task|Your|Input|Output|Example|Examples|Constraints|Order|"
    r"Parameter|Parameters|Slides|Source|Teaching|Notes|Run)\b[^:{}]{0,40}):(?:\s+(.*))?$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s")


def question_markdown(student_source: str) -> str:
    """student.py's module docstring as Markdown; "" when it has none."""
    try:
        doc = ast.get_docstring(ast.parse(student_source), clean=True)
    except SyntaxError:
        return ""
    if not doc:
        return ""
    title, *lines = doc.splitlines()
    out = [f"# {title.strip()}", ""]
    i, in_list = 0, False
    while i < len(lines):
        line = lines[i]
        label = _LABEL.match(line)
        if label:
            out += ["", f"## {label.group(1)}", ""]
            rest, in_list = label.group(2), False
            i += 1
            if rest:
                # Lines aligned under the inline text ("Input:  spark -- ...\n        rows")
                # keep that alignment, so they go in a code block together with it.
                start, block = line.index(rest), [rest]
                while i < len(lines) and lines[i].startswith(" ") and lines[i].strip():
                    block.append(lines[i][start:] if lines[i][:start].isspace()
                                 else lines[i].strip())
                    i += 1
                if len(block) > 1:
                    out += _fence(block) + [""]
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
            out += _fence(textwrap.dedent("\n".join(block)).splitlines()) + [""]
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
    return ["```text", *block, "```"]


def write_question(student_source: str, exercise_dir: Path) -> bool:
    """Write exercise_dir/question.md; False (nothing written) when there is no question."""
    text = question_markdown(student_source)
    if text:
        (exercise_dir / QUESTION_FILE).write_text(text)
    return bool(text)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.split("\n\n")[-1])
        return 2
    batch = Path(argv[0])
    for solution in sorted((batch / "solutions").glob("*/")):
        exercise = batch / "exercises" / solution.name
        if not exercise.is_dir():
            continue
        # student.py holds the question in every batch (week 1-3 skeletons are code only);
        # the skeleton is the fallback when the owner's half-edited student.py does not parse.
        sources = [exercise / "student.py", solution / "skeleton.py"]
        written = any(path.is_file() and write_question(path.read_text(), exercise)
                      for path in sources)
        print(f"{solution.name}: {'question.md' if written else 'no question'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
