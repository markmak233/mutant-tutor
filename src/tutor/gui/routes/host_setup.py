"""Read-only host instructions: course metadata in, packaged guidance out.

No environment probes, lecture scans, model calls or installers run on this page.
Platform selection changes the instructions only, not the course's configuration.
"""

import sys

from flask import Flask, abort, render_template, request

from tutor.gui import environment
from tutor.gui.i18n import N_
from tutor.gui.routes.common import course_or_404
from tutor.languages import course_languages

# Explicit choices avoid treating a query value as a template name or detecting WSL.
PLATFORMS = {"macos": "macOS", "linux": "Linux", "windows": "Windows", "wsl": "WSL"}


def register(app: Flask) -> None:
    """Keep the guide separate from the Environment page's process-backed checks."""

    @app.get("/c/<name>/environment/host-setup")
    def host_setup(name: str):
        course = course_or_404(name)
        actual = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
        platform = request.args.get("platform", actual)
        if platform not in PLATFORMS:
            abort(400, N_("Choose macOS, Linux, Windows or WSL."))
        # Reuse the language registry and parsed requirements; do not infer undeclared JVMs.
        ws = course.workspace
        languages = course_languages({"languages": ws.languages})
        return render_template(
            "host_setup.html", course=course, platform=platform, platforms=PLATFORMS,
            requires=ws.effective_requires, languages=languages, runner=environment.runner(course),
        )
