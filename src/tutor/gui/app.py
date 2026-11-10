"""The Flask app: the checks every request passes first, the headers every answer gets,
and the error pages. Each page area's routes are a module of `gui/routes/` (pages,
tasks, plans, lectures, keys, models, environment, coverage), added by create_app;
the paragraphs below say what each area does and how it stays safe.

Who may use it. The server listens on 127.0.0.1 only, but any web page in the same browser can
still send requests to localhost. So each launch makes a random token, given once in the
URL that `python -m tutor.gui` opens; the first request swaps it for a cookie and redirects to
the same page without it, so the token does not stay in the address bar or the history. A
request without the token gets 403. The cookie is HttpOnly (no script reads it) and
SameSite=Strict (another site's page cannot make the browser send it).

DNS rebinding: a hostile site can make its own domain name point at 127.0.0.1, and the browser
would then treat this server as that site. The Host header still carries the hostile name,
so only 127.0.0.1 and localhost with this server's port are served.

What a page shows. Jinja2 escapes every value (Flask turns it on for .html templates), so
generated text cannot become markup or script; the one exception is a question's Markdown,
which rendering.py turns into HTML with raw HTML off. The Content-Security-Policy
allows scripts from this server only, with no inline script, as a second line. This step only
reads: every route is GET and changes nothing.

Actions. A route that does something on this machine (open a folder, open VS Code) answers
POST only, so a link or an image cannot trigger it; the SameSite cookie already keeps other
sites out, and the Origin header must also be this server's. The action then redirects to its
page (303) with a fixed notice code, so a reload does not repeat it and the page never echoes
text from the request.

Long tasks (step 3) run existing CLI commands in the background (tasks.py, runner.py); their
page polls the end of the log once a second with htmx, which runs with eval, script tags and
its inline styles switched off, so the CSP stays as strict.

Generation (step 4) is such a task: the new-batch form starts `python -m tutor.graph plan`,
and the plan page approves, sends back or continues it with `resume` (generation.py). The
form is the one page that shows what a request sent: when a value is refused, the form comes
back with the values typed, escaped like any other.

Lectures and new courses (step 5): uploads go into the course's lecture folder and are read
by `python -m tutor.materials` as a task; a new course is `python -m tutor.workspace init`
as a task, with no lectures: they are added on its Lectures page after (lectures.py). Uploads
and the other forms refuse with the problems listed, each naming the file it is about.

Settings (step 6): the Keys page writes `.env` (keys.py), and a course's Models page its
workspace.toml's [llm] (llm_settings.py). A key is never sent back to the browser: a page
shows its last 4 characters, and a refused key is not put back in the form.

Environment (step 7a): a course's Environment page checks this machine against [requires]
with the course's venv, edits [requires] and [isolation], and starts the two installs the
GUI may run, Python packages into the course's .venv and the test image (environment.py).
The checks that ask a process or Docker load into the page with htmx, so it opens at once.
"""

from __future__ import annotations

import hmac
import re
from pathlib import Path
from types import ModuleType

from flask import Flask, abort, redirect, render_template, request, url_for

from tutor.gui import i18n, launch, launcher_state, lectures, routes, tasks
from tutor.gui.i18n import N_
from tutor.gui.routes.common import NOTICES, UPLOAD_LIMIT_BYTES

COOKIE = "tutor_token"
_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": under it the browser sends `Origin: null` on every POST (the Fetch
    # spec; seen in Chrome, 2026-11-06), and the Origin check refused our own buttons. Under
    # "same-origin" this server's pages send their origin, and other sites get no referrer.
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",  # pages hold private course material
}


def create_app(root: Path, token: str, port: int | None = None,
               launcher: ModuleType = launch, env_file: Path | None = None) -> Flask:
    """`root`: the folder holding the course folders. `port`: the one the server listens on,
    for the Host check; set later through app.config["PORT"] when the server picks it.
    `launcher`: what opens folders and VS Code (tests pass a stand-in). `env_file`: where
    the Keys page keeps keys, by default the command config folder's .env (checkout or
    private packaged app config), which only the child commands read."""
    app = Flask(__name__)
    # MAX_CONTENT_LENGTH: a request larger than this gets 413 before it is read; each lecture
    # file has its own limit (lectures.MAX_LECTURE_BYTES).
    app.config.update(ROOT=root.resolve(), TOKEN=token, PORT=port,
                      MAX_CONTENT_LENGTH=UPLOAD_LIMIT_BYTES,
                      ENV_FILE=env_file or tasks.REPO / ".env", LAUNCHER=launcher)

    @app.before_request
    def check_request():
        port = app.config["PORT"]
        if request.host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            abort(403, N_("This server answers only at 127.0.0.1 or localhost."))
        # The local launcher alone can challenge this endpoint, without browser cookies.
        # No other request accepts header authentication or loses its Origin check.
        if request.path == "/_launcher" and request.method == "GET":
            if not hmac.compare_digest(request.headers.get("X-Tutor-Launch", "").encode(),
                                       token.encode()):
                abort(403)
            return None
        given = request.args.get("token")
        if given is not None and hmac.compare_digest(given, token):
            # Swap the token in the URL for the cookie, and show the page without it.
            response = redirect(request.path)
            response.set_cookie(COOKIE, token, httponly=True, samesite="Strict")
            return response
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            abort(403, N_("Open the link that `python -m tutor.gui` printed."))
        if request.method != "GET" and request.headers.get("Origin") != f"http://{request.host}":
            abort(403, N_("An action must come from this GUI's own pages."))
        return None

    # One form for every time a page shows (tasks.TIME_FORMAT, local): `| local_time`.
    app.add_template_filter(tasks.local_time, "local_time")
    # A coverage point's status column, as coverage.md writes it (the Coverage page).
    from tutor.materials.coverage_store import status_cell

    app.add_template_filter(status_cell, "status_cell")
    # The pages in the owner's language (gui/i18n.py): `_()` and {% trans %} in templates.
    i18n.install(app)

    @app.context_processor
    def notice():
        found = NOTICES.get(request.args.get("notice", ""))
        return {"notice": {"kind": found[0], "text": found[1]} if found else None}

    @app.get("/_launcher")
    def launcher_identity():
        challenge = request.args.get("challenge", "")
        if not re.fullmatch(r"[0-9a-f]{32}", challenge):
            abort(403)
        return {"proof": launcher_state.proof(token, root, challenge)}

    @app.after_request
    def add_headers(response):
        response.headers.update(_HEADERS)
        return response

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    def error(e):
        # The message, translated when it is one of the pages' own (marked N_() at the
        # abort); gettext alone, no formatting, as a command's text may carry a `%`.
        return render_template("error.html", code=e.code,
                               message=i18n.gettext_(e.description)), e.code

    @app.errorhandler(413)
    def too_large(e):
        """An upload over UPLOAD_LIMIT_BYTES, or a folder of more than MAX_FOLDER_FILES
        files: said in words, with the way back to the form."""
        limit = f"{UPLOAD_LIMIT_BYTES // (1024 * 1024)} MB"
        name = (request.view_args or {}).get("name")
        _ = i18n.gettext_
        if request.endpoint == "stage_lecture_folder":
            message = _("The folder was not taken: the browser sends all of it, videos and "
                        "images too, and it is over %(limit)s or %(files)d files. Choose one "
                        "of its subfolders, or pick the lecture files themselves under Add "
                        "lectures.") % {"limit": limit, "files": lectures.MAX_FOLDER_FILES}
        else:
            message = (_("The upload is over %(limit)s in all: upload fewer files at a time.")
                       % {"limit": limit})
        back = url_for("lecture_list", name=name) if name else None
        return render_template("error.html", code=413, message=message, back=back), 413

    # Every page area's routes (gui/routes/), after the checks above.
    routes.register_all(app)
    return app
