"""Fixtures shared by the repo's own tests."""

from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def symlink() -> Callable[..., None]:
    """symlink(link, target, target_is_directory=False): make `link` point at `target`, or
    skip the test where this account may not create links. Windows allows it only in
    Developer Mode or as administrator (WinError 1314); the code these tests cover, which
    finds and refuses links, runs anywhere."""
    def make(link: Path, target: Path, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314:  # ERROR_PRIVILEGE_NOT_HELD
                pytest.skip("creating symbolic links needs Windows Developer Mode")
            raise
    return make
