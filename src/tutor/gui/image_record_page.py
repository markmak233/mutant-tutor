"""Read-only image/venv facts shared by step 4's summary and the full history page.

Use the existing immutable-image/recipe currency check. An old record never becomes a
current comparison, and host versions come only from this course's venv probe.
"""

from dataclasses import dataclass

from tutor import image_records
from tutor.gui import environment


@dataclass(frozen=True)
class Snapshot:
    view: image_records.View
    host: environment.Check | None
    rows: list[dict]

    @property
    def differences(self):
        """Differences are plain information, kept separate from missing measurements."""
        return [r for r in self.rows if r["state"] == "different"]

    @property
    def unknown(self):
        """Missing measurements cannot support a claim that versions match."""
        return [r for r in self.rows if r["state"] == "unknown"]


def snapshot(course) -> Snapshot:
    """No history scan or venv probe until a current image inventory is established."""
    view = image_records.view(course.workspace)
    host = environment.check(course) if view.status == "current" else None
    rows = (image_records.comparison(course.workspace, view.data, host.results)
            if host is not None else [])
    return Snapshot(view, host, rows)
