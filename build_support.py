"""Controlled public resources for wheels and source distributions (stdlib only).

Build isolation has not installed tutor's runtime dependencies. Never import the package
here, follow links, scan a workspace or include private notes/configuration/cache files.
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path

ROOT_FILES = ("pyproject.toml", "setup.py", "build_support.py", "README.md", "LICENSE")
SUFFIXES = {".py", ".pyi", ".c", ".h", ".md", ".json", ".txt", ".csv", ".html", ".css", ".js"}


def public_files(root: Path):
    """Only public package/example roots and explicit build metadata can enter bundles."""
    for name in ROOT_FILES:
        path = root / name
        if path.is_symlink():
            raise ValueError("public build metadata cannot include links")
        if path.is_file():
            yield path
    for base in (root / "src" / "tutor", root / "examples"):
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            tag = getattr(path.lstat(), "st_reparse_tag", 0)
            if path.is_symlink() or tag in {
                    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1),
                    getattr(stat, "IO_REPARSE_TAG_SYMLINK", -2)}:
                raise ValueError("public build resources cannot include links or junctions")
            if path.is_file() and path.suffix in SUFFIXES and path.name != "validation.json":
                yield path


def bundle(root: Path, package: Path) -> list[Path]:
    """No recursive bundle-in-bundle: generated archives live only in build output."""
    package.mkdir(parents=True, exist_ok=True)
    archive = package / "_source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in public_files(root):
            handle.write(path, path.relative_to(root).as_posix())
    examples = package / "_examples"
    shutil.rmtree(examples, ignore_errors=True)
    for path in public_files(root):
        if path.is_relative_to(root / "examples"):
            destination = examples / path.relative_to(root / "examples")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    return [archive, *[p for p in examples.rglob("*") if p.is_file()]]
