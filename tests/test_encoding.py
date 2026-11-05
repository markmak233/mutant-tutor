"""Every text file the code reads or writes names its encoding.

Without encoding=, Python uses the platform's default: UTF-8 on macOS and Linux, the ANSI
code page (cp1252) on Windows, where lecture text, questions and hints with any
non-Latin character then fail or turn into mojibake. ruff's unspecified-encoding rule
finds only calls on objects it can type (10 of 295 on 2026-10-31), so this test reads the
source with ast instead, and fails on Linux CI too, not only on Windows.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED = [ROOT / "src", ROOT / "tests"]
TEXT_CALLS = {"read_text", "write_text", "open", "fdopen"}
# `<module>.open(...)` that is not a text file: os.open takes flags, the others are
# archives or streams with their own rules.
OTHER_OPENS = {"os", "io", "tarfile", "zipfile", "webbrowser"}


def _missing_encoding(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        name = func.attr
        if name == "open" and isinstance(func.value, ast.Name) and func.value.id in OTHER_OPENS:
            return False
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return False
    if name not in TEXT_CALLS or any(kw.arg == "encoding" for kw in call.keywords):
        return False
    # Binary mode needs no encoding: open(path, "rb"), path.open("wb"), mode="ab".
    mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if mode is None:
        # open(path, mode) and os.fdopen(fd, mode); path.open(mode)
        position = 1 if isinstance(func, ast.Name) or name == "fdopen" else 0
        mode = call.args[position] if len(call.args) > position else None
    return not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def test_every_text_file_call_names_its_encoding():
    found = []
    for folder in CHECKED:
        for path in sorted(folder.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found += [f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                      for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and _missing_encoding(node)]
    assert found == [], "add encoding=\"utf-8\" to: " + ", ".join(found)
