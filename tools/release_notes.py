"""Print one version's section of CHANGELOG.md, for a GitHub Release's notes.

    python tools/release_notes.py VERSION [CHANGELOG]

Used by the Release workflow's publish job (.github/workflows/release.yml), so the page's
text is the changelog's and is written once. A section starts at the heading "## VERSION",
which may go on with a date ("## 0.1.0 (2026-11-17)"), and ends before the next "## "
heading. Exit code 1, with a message on standard error, when the changelog has no such
section or the section is empty: a release with no notes is refused, not published blank.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str | None:
    """The text under the "## <version>" heading, without the heading itself and without
    blank lines at its ends; None when there is no such heading."""
    # The version is followed by a space or the line's end, so 0.1.0 is not found in a
    # heading for 0.1.01 or 0.1.0rc1.
    heading = re.compile(rf"^## {re.escape(version)}(?:[ \t].*)?$")
    lines = changelog.splitlines()
    for index, line in enumerate(lines):
        if heading.match(line):
            body = []
            for following in lines[index + 1:]:
                if following.startswith("## "):
                    break  # the next version's section
                body.append(following)
            return "\n".join(body).strip("\n")
    return None


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    version = argv[0]
    path = Path(argv[1]) if len(argv) == 2 else Path("CHANGELOG.md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"release_notes: cannot read {path}: {error}", file=sys.stderr)
        return 1
    found = section(text, version)
    if found is None:
        print(f"release_notes: {path} has no section \"## {version}\"", file=sys.stderr)
        return 1
    if not found.strip():
        print(f"release_notes: the section \"## {version}\" of {path} is empty", file=sys.stderr)
        return 1
    # The notes hold characters outside ASCII now and then; a Windows console's code page
    # would garble or refuse them.
    sys.stdout.reconfigure(encoding="utf-8")
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
