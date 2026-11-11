"""Explicit clean-install acceptance, outside pytest: downloads only into a new scratch root.

Use an already available uv and Python; never download Python implicitly, read owner keys,
call a model or open a real course. Logs retain launch tokens privately, not in CI stdout.
Native desktop trust/click checks remain manual and are not implied by this HTTP smoke.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


def python_in(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(args, env, work, name, *, timeout=600):
    """Measure the program's own exit; keep its potentially private output out of stdout."""
    started = time.monotonic()
    with (work / (name + ".log")).open("wb") as log:
        done = subprocess.run(list(map(str, args)), env=env, cwd=work, stdout=log,
                              stderr=subprocess.STDOUT, timeout=timeout)
    elapsed = round(time.monotonic() - started, 2)
    print(f"{name}: exit {done.returncode}, {elapsed}s", flush=True)
    if done.returncode:
        raise RuntimeError(f"{name} failed; inspect its private scratch log")
    return elapsed


def accept(uv: Path, python: Path, wheel: Path, work: Path) -> dict:
    """A new directory is mandatory, so reruns cannot accidentally mix prior results."""
    work.mkdir(parents=True, exist_ok=False, mode=0o700)
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "LC_CTYPE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    # Isolate all package/config/bin/cache writes; no real .env or uv/pip user config.
    env.update(HOME=str(work / "home"), USERPROFILE=str(work / "home"),
               APPDATA=str(work / "appdata"), LOCALAPPDATA=str(work / "localappdata"),
               TUTOR_HOME=str(work / "app"), UV_TOOL_DIR=str(work / "tool"),
               UV_TOOL_BIN_DIR=str(work / "bin"), UV_CACHE_DIR=str(work / "cache"),
               UV_PYTHON_INSTALL_DIR=str(work / "pythons"), UV_NO_CONFIG="1",
               UV_PYTHON_DOWNLOADS="never", PIP_CONFIG_FILE=os.devnull,
               PIP_INDEX_URL="https://pypi.org/simple", PYTHONIOENCODING="utf-8")
    times = {}
    times["install"] = run([uv, "tool", "install", "--no-python-downloads", "--python", python,
                            "--default-index", "https://pypi.org/simple",
                            f"{wheel}[gen,gui,doctor]"], env, work, "install")
    own = python_in(work / "tool" / "tutor")
    # Installed code must not accidentally import the checkout or a developer PYTHONPATH.
    times["bundle"] = run([own, "-c", "from tutor import project_source as p; "
                           "assert p.checkout() is None; "
                           "assert (p.examples()/'station-longest-rise'/'exercise').is_dir()"],
                          env, work, "bundle")
    courses = work / "courses"
    courses.mkdir()
    course = courses / "synthetic"
    times["course"] = run([own, "-m", "tutor.workspace", "init", course, "--python", python],
                          env, work, "course")
    course_python = python_in(course / ".venv")
    times["smoke"] = run([course_python, "-m", "tutor", "doctor", course, "--smoke"],
                         env, work, "smoke")
    assert "smoke station-longest-rise: VALID" in (work / "smoke.log").read_text(encoding="utf-8")
    desktop = work / "desktop"
    desktop.mkdir()
    times["shortcut"] = run([own, "-m", "tutor", "shortcut", "--root", courses,
                              "--directory", desktop], env, work, "shortcut")
    # Real loopback server and second-process reuse; no automated browser is substituted.
    with (work / "gui.log").open("wb") as log:
        server = subprocess.Popen([str(own), "-m", "tutor", "gui", "--root", str(courses),
                                   "--no-browser"], env=env, cwd=work, stdout=log,
                                  stderr=subprocess.STDOUT)
        try:
            state_file = courses / ".tutor-gui.json"
            deadline = time.monotonic() + 30
            while (not state_file.is_file() and server.poll() is None
                   and time.monotonic() < deadline):
                time.sleep(0.1)
            data = json.loads(state_file.read_text(encoding="utf-8"))
            # The public page refuses no token, then the real launch URL swaps for a cookie.
            import http.cookiejar
            from urllib.error import HTTPError
            from urllib.request import HTTPCookieProcessor

            fetch = build_opener(ProxyHandler({}), HTTPCookieProcessor(
                http.cookiejar.CookieJar())).open
            base = f"http://127.0.0.1:{data['port']}"
            try:
                fetch(base + "/", timeout=5)
                raise RuntimeError("GUI unexpectedly served an unauthenticated page")
            except HTTPError as e:
                assert e.code == 403
            with fetch(base + "/?token=" + data["token"], timeout=5) as response:
                assert response.status == 200
            times["reuse"] = run([own, "-m", "tutor", "gui", "--root", courses, "--no-browser"],
                                 env, work, "reuse", timeout=15)
            assert "Reused the verified server" in (work / "reuse.log").read_text(encoding="utf-8")
        finally:
            server.terminate()  # SIGTERM, including a process started in the background
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    result = {"platform": sys.platform, "timings_seconds": times,
              "browser_clicks": "pending", "native_desktop_click": "pending"}
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("uv", "python", "wheel", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        uv = Path(shutil.which(str(args.uv)) or args.uv).resolve()
        python = Path(shutil.which(str(args.python)) or args.python).resolve()
        wheel = args.wheel.resolve()
        if wheel.is_dir():
            wheels = list(wheel.glob("*.whl"))
            if len(wheels) != 1:
                raise ValueError("wheel directory must contain exactly one wheel")
            wheel = wheels[0]
        accept(uv, python, wheel, args.work.resolve())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"acceptance: {type(e).__name__}; inspect the private logs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
