"""Fixtures shared by the repo's own tests."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def symlink() -> Callable[..., None]:
    """symlink(link, target, target_is_directory=False): make `link` point at `target`.
    Windows lets only Developer Mode or an administrator create symbolic links
    (WinError 1314); there a directory link becomes a junction instead, which any account
    may create and which the link checks must refuse just the same. A file link there
    skips the test."""
    def make(link: Path, target: Path, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as e:
            if getattr(e, "winerror", None) != 1314:  # ERROR_PRIVILEGE_NOT_HELD
                raise
            if not target_is_directory:
                pytest.skip("creating symbolic links needs Windows Developer Mode")
            make_junction(link, target)
    return make


def make_junction(link: Path, target: Path) -> None:
    """A Windows directory junction at `link` pointing at the folder `target`."""
    assert sys.platform == "win32"
    import _winapi  # CPython's own Windows calls; CreateJunction needs no privilege

    _winapi.CreateJunction(str(target.resolve()), str(link))
