"""The Flask app: routes, and the checks every request passes first.

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
"""

from __future__ import annotations

import hmac
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request

from tutor.eval import report
from tutor.gui import courses, rendering

COOKIE = "tutor_token"
_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",  # pages hold private course material
}
# How each state reads on a page, in the report's order.
STATUS_LABELS = {report.PASSED: "passed", report.STARTED: "started",
                 report.UNLOGGED: "changed, no run logged", report.NOT_STARTED: "not started"}


def create_app(root: Path, token: str, port: int | None = None) -> Flask:
    """`root`: the folder holding the course folders. `port`: the one the server listens on,
    for the Host check; set later through app.config["PORT"] when the server picks it."""
    app = Flask(__name__)
    app.config.update(ROOT=root.resolve(), TOKEN=token, PORT=port)

    @app.before_request
    def check_request():
        port = app.config["PORT"]
        if request.host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            abort(403, "This server answers only at 127.0.0.1 or localhost.")
        given = request.args.get("token")
        if given is not None and hmac.compare_digest(given, token):
            # Swap the token in the URL for the cookie, and show the page without it.
            response = redirect(request.path)
            response.set_cookie(COOKIE, token, httponly=True, samesite="Strict")
            return response
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            abort(403, "Open the link that `python -m tutor.gui` printed.")
        return None

    @app.after_request
    def add_headers(response):
        response.headers.update(_HEADERS)
        return response

    @app.errorhandler(403)
    @app.errorhandler(404)
    def error(e):
        return render_template("error.html", code=e.code, message=e.description), e.code

    def course_or_404(name: str) -> courses.Course:
        course = courses.get_course(app.config["ROOT"], name)
        if course is None:
            abort(404, "No such course.")
        return course

    @app.get("/")
    def home():
        found, problems = courses.find_courses(app.config["ROOT"])
        return render_template("courses.html", root=app.config["ROOT"],
                               views=[courses.course_view(c) for c in found], problems=problems,
                               labels=STATUS_LABELS)

    @app.get("/c/<name>/")
    def course(name: str):
        view = courses.course_view(course_or_404(name))
        return render_template("course.html", view=view, labels=STATUS_LABELS)

    @app.get("/c/<name>/b/<batch>/")
    def batch(name: str, batch: str):
        course = course_or_404(name)
        view = courses.batch_view(course, batch)
        if view is None:
            abort(404, "No such batch.")
        return render_template("batch.html", course=course, batch=view, labels=STATUS_LABELS)

    @app.get("/c/<name>/b/<batch>/e/<exercise>/")
    def exercise(name: str, batch: str, exercise: str):
        course = course_or_404(name)
        view = courses.batch_view(course, batch)
        row = next((r for r in view.exercises if r.id == exercise), None) if view else None
        if row is None:
            abort(404, "No such exercise.")
        text = courses.question(course, batch, exercise)
        return render_template("exercise.html", course=course, batch=view, row=row,
                               question=rendering.question_html(text) if text else None,
                               labels=STATUS_LABELS)

    return app
