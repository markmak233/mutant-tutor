"""The GUI's routes, one module per page area; `register_all` adds them all to the app.

Each module's `register(app)` defines its routes on the app itself, not on a Flask blueprint,
so an endpoint's name stays its function's name (`plan`, `environment_view`) and the templates'
`url_for` calls need no prefix. What the areas share is in `common`.
"""

from __future__ import annotations

from flask import Flask

from tutor.gui.routes import (
    coverage,
    doctor,
    environment,
    host_setup,
    image_records,
    keys,
    lectures,
    models,
    new_course,
    pages,
    plans,
    task_pages,
)


def register_all(app: Flask) -> None:
    """Register each page area once; all templates use these unprefixed endpoints."""
    for module in (pages, task_pages, plans, lectures, new_course, keys, models, environment,
                   doctor, coverage, host_setup, image_records):
        module.register(app)
