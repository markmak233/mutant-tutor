"""Own project source for course installs, independent of a checkout's filesystem layout.

Wheels carry a controlled public source archive. Extract it temporarily and install a
regular wheel into the course, never an editable temporary path or a PyPI name fallback.
"""

from __future__ import annotations

import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Source:
    path: Path
    editable: bool


def checkout() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    valid = (root / "pyproject.toml").is_file() and (root / "src" / "tutor").is_dir()
    return root if valid else None


def examples() -> Path:
    root = checkout()
    return root / "examples" if root else Path(__file__).resolve().parent / "_examples"


@contextmanager
def source():
    """Validate every archive member/total size before extracting any source file."""
    if root := checkout():
        yield Source(root, True)
        return
    archive = Path(__file__).resolve().parent / "_source.zip"
    if not archive.is_file():
        raise ValueError("this installation has no bundled source; "
                         "reinstall a complete tutor wheel")
    with tempfile.TemporaryDirectory(prefix="tutor-source-") as raw:
        root = Path(raw)
        try:
            with zipfile.ZipFile(archive) as handle:
                members = handle.infolist()
                if sum(m.file_size for m in members) > 50 * 1024 * 1024 or len(members) > 10_000:
                    raise ValueError("bundled source exceeds its limits")
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                            or ":" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                        raise ValueError("bundled source contains an unsafe path/link")
                handle.extractall(root)
        except (OSError, zipfile.BadZipFile):
            raise ValueError("bundled source is unreadable; reinstall a complete wheel") from None
        if not (root / "pyproject.toml").is_file() or not (root / "src" / "tutor").is_dir():
            raise ValueError("bundled source is incomplete")
        yield Source(root, False)
