"""The synthetic lectures in examples/lectures/: each reads into the sections a plan cites,
and every piece of code in it does what the lecture says it does.

These lectures were written for the repository (no course's material) to give public demo
courses. An exercise is generated from what a lecture says, so a wrong output in a lecture
would become a wrong exercise: the tests below run each code block and compare what it
prints, or the error it raises, with the output block that follows it.

How a lecture marks its blocks (every one is a <pre>):
- <code class="language-python">: Python, run in one session per lecture;
  with "raises", its last statement must raise the error the output block names.
- <code class="language-c">: a whole C program, compiled and run on its own.
- either with "fragment": a few lines that show one point, not run.
- <pre class="input">: what the program before it reads from standard input.
- <pre class="output">: what the code before it prints; with "stderr", what a C program
  writes to standard error when it exits with status 1.
- any other <code> (a shell command): shown, not run.

The code run here is the repository's own source, written and reviewed like any module, not
a model's draft, which is why it runs in the test's process (Python) or straight through this
machine's compiler (C), not through the isolated runner.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import types
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tutor.materials.html import TARGET_CHARS, read_html
from tutor.testkit_c import ALWAYS_FLAGS, DEFAULT_FLAGS, LINK_FLAGS, host_problem

ROOT = Path(__file__).resolve().parents[1] / "examples" / "lectures"
LECTURES = sorted(ROOT.glob("*/*.html"))
C_LECTURES = [p for p in LECTURES if p.parent.name.startswith("c-")]
PYTHON_LECTURES = [p for p in LECTURES if p not in C_LECTURES]
# How many sections each lecture is written in: one per <h2>, the page title joining the first.
SECTIONS = 8
needs_cc = pytest.mark.skipif(host_problem() is not None, reason=str(host_problem()))


def _id(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


class _Blocks(HTMLParser):
    """The lecture's <pre> blocks in order, as (kind, text). Kinds: "python", "raises", "c",
    "input", "output", "stderr", and "shown" for what is not run (a fragment, a command)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._kind: str | None = None  # set while inside a <pre>
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "pre":
            # The <pre>'s own class says input or output; code is told apart by its <code>.
            if "output" in classes:
                self._kind = "stderr" if "stderr" in classes else "output"
            else:
                self._kind = "input" if "input" in classes else "shown"
            self._text = []
        elif tag == "code" and self._kind == "shown":
            if "fragment" in classes:
                pass  # a few lines that show one point: shown, in either language
            elif "language-python" in classes:
                self._kind = "raises" if "raises" in classes else "python"
            elif "language-c" in classes:
                self._kind = "c"

    def handle_endtag(self, tag):
        if tag == "pre" and self._kind:
            self.blocks.append((self._kind, "".join(self._text)))
            self._kind = None

    def handle_data(self, data):
        if self._kind:
            self._text.append(data)


def _blocks(path: Path) -> list[tuple[str, str]]:
    parser = _Blocks()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.blocks


def _lines(text: str) -> list[str]:
    """Text as lines without trailing spaces or a trailing blank line, for comparing."""
    return [line.rstrip() for line in text.rstrip().splitlines()]


def test_there_are_lectures_to_check():
    # A moved folder would otherwise turn every test below into zero cases, silently.
    assert len(PYTHON_LECTURES) >= 3 and len(C_LECTURES) >= 3


@pytest.mark.parametrize("path", LECTURES, ids=_id)
def test_a_lecture_reads_into_its_sections(path):
    pages = read_html(path)
    number = "".join(c for c in path.stem if c.isdigit())
    # week2.html -> 2.1 ... 2.8: the labels a plan item's "slides" field cites.
    assert [p.label for p in pages] == [f"{number}.{n}" for n in range(1, SECTIONS + 1)]
    # No section was long enough to be cut in two, and nothing waits for a vision model.
    assert all(len(p.text) <= TARGET_CHARS for p in pages)
    assert not any(p.needs_vision for p in pages)
    # Each section opens with its heading (the first one with the page title above it).
    assert all(p.text.startswith("#") for p in pages)


@pytest.mark.parametrize("path", LECTURES, ids=_id)
def test_every_input_and_output_block_follows_code(path):
    kinds = [kind for kind, _ in _blocks(path)]
    assert any(kind in {"python", "raises", "c"} for kind in kinds), "a lecture with no code"
    for index, kind in enumerate(kinds):
        before = kinds[index - 1] if index else ""
        if kind in {"output", "stderr"}:
            # After the code itself, or after the input that code reads.
            assert before in {"python", "raises", "c", "input"}, (index, before)
        if kind == "input":
            # After a C program, or after the output of that program's earlier run.
            assert before in {"c", "output", "stderr"}, (index, before)


@pytest.mark.parametrize("path", PYTHON_LECTURES, ids=_id)
def test_a_lectures_python_prints_what_the_lecture_shows(path, tmp_path, monkeypatch):
    # Week 3 writes and reads files by relative name: they go to a temporary folder.
    monkeypatch.chdir(tmp_path)
    blocks = _blocks(path)
    # One module per lecture: later blocks use names that earlier ones define, as a reader
    # following the lecture in one session would. It is a real module in sys.modules
    # because @dataclass looks its class's module up there.
    module = types.ModuleType("lecture")
    monkeypatch.setitem(sys.modules, "lecture", module)
    namespace = module.__dict__
    for index, (kind, text) in enumerate(blocks):
        if kind not in {"python", "raises"}:
            continue
        following = blocks[index + 1] if index + 1 < len(blocks) else ("", "")
        expected = _lines(following[1]) if following[0] == "output" else []
        printed = io.StringIO()
        if kind == "raises":
            with pytest.raises(Exception) as raised, contextlib.redirect_stdout(printed):
                exec(compile(text, f"{path.name} block {index}", "exec"), namespace)
            # The lecture shows the traceback's last line: "Type: message", or the type
            # alone when there is no message (a bare assert).
            message = str(raised.value)
            name = type(raised.value).__name__
            assert [f"{name}: {message}" if message else name] == expected, text
        else:
            with contextlib.redirect_stdout(printed):
                exec(compile(text, f"{path.name} block {index}", "exec"), namespace)
            # A block with no output block after it must print nothing.
            assert _lines(printed.getvalue()) == expected, text


def _runs(blocks: list[tuple[str, str]], start: int) -> list[tuple[str, str, str]]:
    """The runs shown after the C program at `start`, as (input, stream, expected): each
    output block is one run, with the input block before it when there is one."""
    runs, given = [], ""
    for kind, text in blocks[start + 1:]:
        if kind == "input":
            given = text
        elif kind in {"output", "stderr"}:
            runs.append((given, kind, text))
            given = ""
        else:
            break  # the next piece of code: this program's runs are over
    return runs


@needs_cc
@pytest.mark.parametrize("path", C_LECTURES, ids=_id)
def test_a_lectures_c_programs_print_what_the_lecture_shows(path, tmp_path):
    blocks = _blocks(path)
    programs = [i for i, (kind, _) in enumerate(blocks) if kind == "c"]
    assert programs, "a C lecture with no whole program"
    for index in programs:
        source = tmp_path / f"block{index}.c"
        binary = tmp_path / f"block{index}"
        source.write_text(blocks[index][1] + "\n", encoding="utf-8")
        # The flags the lectures compile with, warnings as errors (a lecture's program must
        # be clean), and the sanitizers every exercise build gets: a program that reads past
        # an array would stop here instead of teaching it.
        built = subprocess.run(
            ["cc", *DEFAULT_FLAGS, "-Werror", *ALWAYS_FLAGS, "-o", str(binary), str(source),
             *LINK_FLAGS],
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
        assert built.returncode == 0, f"{path.name} block {index}:\n{built.stderr}"
        runs = _runs(blocks, index)
        assert runs, f"{path.name} block {index}: a program with no output shown"
        for given, stream, expected in runs:
            ran = subprocess.run([str(binary)], input=given + "\n" if given else "",
                                 capture_output=True, text=True, encoding="utf-8",
                                 timeout=20, check=False)
            where = f"{path.name} block {index}, input {given!r}:\n{ran.stderr}"
            if stream == "stderr":
                # A failing run: the message on standard error, nothing printed, status 1.
                assert ran.returncode == 1, where
                assert _lines(ran.stderr) == _lines(expected) and ran.stdout == "", where
            else:
                assert ran.returncode == 0, where
                assert _lines(ran.stdout) == _lines(expected), where
