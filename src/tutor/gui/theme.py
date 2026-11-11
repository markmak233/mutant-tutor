"""Light or dark pages by choice (owner, 2026-11-11: "override the night mode"). The
stylesheet follows the system's `prefers-color-scheme` by itself (CSS `color-scheme: light
dark` and `light-dark()` colours, one definition per colour); the footer's Theme switch
writes "light" or "dark" into `<html data-theme>`, which the stylesheet turns into
`color-scheme: light` or `dark` alone, and "" (System) leaves it to the system. The
choice is kept with the language in the GUI's settings file (gui/i18n.py `save_setting`),
for every course; no script runs for it."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from tutor.gui import i18n
from tutor.gui.i18n import N_

SYSTEM, LIGHT, DARK = "", "light", "dark"
# The footer's switch: value -> label (translated when shown); System first, the default.
THEMES = ((SYSTEM, N_("System")), (LIGHT, N_("Light")), (DARK, N_("Dark")))


def chosen(root: Path) -> str:
    """"light", "dark", or "" for the system's (also for a value that is neither)."""
    value = i18n.setting(root, "theme")
    return value if value in (LIGHT, DARK) else SYSTEM


def choose(root: Path, value: str) -> None:
    i18n.save_setting(root, "theme", value if value in (LIGHT, DARK) else SYSTEM)


def install(app: Flask) -> None:
    """`gui_theme` (the choice) and `gui_themes` (the switch's rows) in every template."""
    @app.context_processor
    def theme_context():
        return {"gui_theme": chosen(app.config["ROOT"]), "gui_themes": THEMES}
