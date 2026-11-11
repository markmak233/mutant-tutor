"""Per-user packaged application paths, computed without creating or reading anything."""

import os
import sys
from pathlib import Path

from tutor import project_source


def home() -> Path:
    """TUTOR_HOME permits a portable/scratch install without touching the real home."""
    if value := os.environ.get("TUTOR_HOME"):
        return Path(value).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Tutor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Tutor"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tutor"


def commands() -> Path:
    """Source commands read the checkout's config; packaged commands use private config."""
    return project_source.checkout() or home() / "config"


def prepare() -> Path:
    """Only explicit GUI launch/task actions create the packaged configuration folder."""
    path = commands()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
