"""Root-specific local server reuse; persisted ports are verified by a fresh challenge.

Only loopback URLs assembled here are opened. State holds the launch secret privately;
neither a PID alone nor an unauthenticated HTTP response proves server identity. What does
prove a server gone is its port refusing connections: a GUI server binds its port before
its state is saved and keeps it until it stops (existing).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from tutor.gui.tasks import _alive

STATE = ".tutor-gui.json"
LOCK = ".tutor-gui.lock"


def identity(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def proof(token: str, root: Path, challenge: str) -> str:
    return hmac.new(token.encode("utf-8"),
                    f"{identity(root)}:{challenge}".encode(), hashlib.sha256).hexdigest()


def url(state: dict) -> str:
    return f"http://127.0.0.1:{state['port']}/?token={state['token']}"


def read(root: Path) -> dict | None:
    """Malformed or linked state requires an explicit reset, never an arbitrary URL."""
    path = root / STATE
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("root") != identity(root)
                or type(data.get("port")) is not int or not 1 <= data["port"] <= 65535
                or type(data.get("pid")) is not int or data["pid"] <= 0
                or not isinstance(data.get("token"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", data["token"])):
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        raise ValueError("invalid GUI state; use --reset-state after checking "
                         "the old server") from None


def reachable(root: Path, state: dict) -> bool:
    """Bypass proxies and redirects; accept only a bounded response proving this root/token."""
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    challenge = secrets.token_hex(16)
    request = Request(f"http://127.0.0.1:{state['port']}/_launcher?challenge={challenge}",
                      headers={"X-Tutor-Launch": state["token"]})
    fetch = build_opener(ProxyHandler({}), NoRedirect()).open  # HTTP binary stream, not text IO
    try:
        with fetch(request, timeout=2) as response:
            data = json.loads(response.read(1025))
        return isinstance(data, dict) and hmac.compare_digest(
            str(data.get("proof", "")), proof(state["token"], root, challenge))
    except (OSError, URLError, ValueError):
        return False


# Windows answers a connection to a closed loopback port only after retrying for about two
# seconds; a shorter wait would read "nothing there" as "something there, silent".
LISTEN_TIMEOUT_S = 5


def listening(port: int) -> bool:
    """Whether anything accepts connections on 127.0.0.1:`port`. Only a refusal says no:
    a timeout or another error might be the old server, busy, so it counts as yes."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=LISTEN_TIMEOUT_S):
            return True
    except ConnectionRefusedError:
        return False
    except OSError:
        return True


def save(root: Path, port: int, token: str) -> dict:
    data = {"root": identity(root), "port": port, "token": token, "pid": os.getpid()}
    # Called under the launch lock, after stale state has been removed.
    with open(root / STATE, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        json.dump(data, f)
    return data


def clear(root: Path, token: str) -> None:
    """Never remove a newer launch's state when an old server finally stops."""
    try:
        if (state := read(root)) and hmac.compare_digest(state["token"], token):
            (root / STATE).unlink()
    except (OSError, ValueError):
        pass


@contextmanager
def locked(root: Path, *, reset: bool = False):
    """An exclusive short startup lock prevents two simultaneous clicks starting servers."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / LOCK
    if reset:
        # Reset changes local metadata only; it never signals a process.
        for item in (path, root / STATE):
            item.unlink(missing_ok=True)
    try:
        with open(path, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        raise ValueError("GUI startup is locked; retry, or check the old launch and use "
                         "--reset-state for a stale lock") from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def existing(root: Path) -> dict | None:
    """The running server this root's state names, verified; else None, the stale state
    removed. A server stopped by closing its window (Windows' way to stop it), by SIGTERM
    or SIGHUP never runs its own clear(), so stale state is the usual case, not a rare one.
    It is refused only when the old process may still be the server: its pid is alive and
    something answers on its port. A pid alone is not enough: Windows reuses pids soon, and
    a launcher click whose old pid now belongs to another program would stop at "use
    --reset-state" in a window that closes at once (reasoned out from the code, 2026-11-16;
    SIGTERM leaving the state behind was measured)."""
    state = read(root)
    if state and reachable(root, state):
        return state
    if state:
        if _alive(state["pid"]) and listening(state["port"]):
            raise ValueError("previous GUI process is alive but did not verify; check its terminal "
                             "before using --reset-state")
        clear(root, state["token"])
    return None
