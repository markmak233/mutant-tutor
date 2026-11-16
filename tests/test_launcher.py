"""Local-launch identity, entry points and platform file formats on synthetic roots."""

import base64
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tutor import __main__ as dispatcher
from tutor import locations, shortcut
from tutor.gui import __main__ as gui_main
from tutor.gui import launcher_state as state
from tutor.gui.app import create_app

TOKEN = "synthetic-launch-token-for-tests-only"


def test_challenge_is_authenticated_and_bound_to_root(tmp_path):
    app = create_app(tmp_path, TOKEN, port=8000)
    client = app.test_client()
    challenge = "0" * 32
    url = "/_launcher?challenge=" + challenge
    assert client.get(url, base_url="http://127.0.0.1:8000").status_code == 403
    headers = {"X-Tutor-Launch": TOKEN}
    answer = client.get(url, base_url="http://127.0.0.1:8000", headers=headers)
    assert answer.json == {"proof": state.proof(TOKEN, tmp_path, challenge)}
    assert answer.json["proof"] != state.proof(TOKEN, tmp_path / "other", challenge)
    assert client.get(url, base_url="http://attacker:8000", headers=headers).status_code == 403
    assert client.get("/", base_url="http://127.0.0.1:8000", headers=headers).status_code == 403


def test_state_rejects_corruption_and_preserves_newer_launch(tmp_path):
    with state.locked(tmp_path):
        state.save(tmp_path, 8000, TOKEN)
        assert state.read(tmp_path)["port"] == 8000
        state.clear(tmp_path, "another-token")
        assert state.read(tmp_path)
        state.clear(tmp_path, TOKEN)
    (tmp_path / state.STATE).write_text('{"url":"https://attacker"}', encoding="utf-8")
    with pytest.raises(ValueError, match="reset-state"):
        state.read(tmp_path)
    with state.locked(tmp_path, reset=True):
        assert state.read(tmp_path) is None


def test_launch_lock_refuses_second_start(tmp_path):
    with state.locked(tmp_path), pytest.raises(ValueError, match="locked"), state.locked(tmp_path):
        pytest.fail("two launches acquired one root")


def test_existing_unverified_live_process_is_never_replaced(tmp_path, monkeypatch):
    state.save(tmp_path, 8000, TOKEN)
    monkeypatch.setattr(state, "reachable", lambda *a: False)
    monkeypatch.setattr(state, "_alive", lambda pid: True)
    monkeypatch.setattr(state, "listening", lambda port: True)
    with pytest.raises(ValueError, match="alive"):
        state.existing(tmp_path)
    monkeypatch.setattr(state, "_alive", lambda pid: False)
    assert state.existing(tmp_path) is None and state.read(tmp_path) is None


def test_a_reused_pid_with_a_closed_port_is_stale_state(tmp_path, monkeypatch):
    """The window was closed (no clear() ran) and Windows gave the pid to another program:
    nothing answers on the old port, so the old server is gone and the launch goes on."""
    state.save(tmp_path, 8000, TOKEN)
    monkeypatch.setattr(state, "reachable", lambda *a: False)
    monkeypatch.setattr(state, "_alive", lambda pid: True)
    monkeypatch.setattr(state, "listening", lambda port: False)
    assert state.existing(tmp_path) is None and state.read(tmp_path) is None


def test_a_stale_state_of_a_closed_window_lets_the_next_launch_start(tmp_path, monkeypatch,
                                                                     capsys):
    """The whole path a second click takes: a dead server's state is removed and a new
    server starts, with state of its own that its stop removes."""
    import socket

    import waitress

    with socket.socket() as probe:  # a port nothing listens on any more
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    (tmp_path / state.STATE).write_text(json.dumps({
        "root": state.identity(tmp_path), "port": dead_port, "token": "a" * 43,
        "pid": 2 ** 22 + 7}), encoding="utf-8")  # above every default pid limit: no process
    saved = []

    class Server:
        effective_port = "8123"

        def run(self):
            saved.append(state.read(tmp_path))

        def close(self):
            pass

    monkeypatch.setattr(waitress, "create_server", lambda *a, **k: Server())
    monkeypatch.setattr(locations, "prepare", lambda: tmp_path)
    assert gui_main.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert saved[0]["port"] == 8123 and saved[0]["token"] != "a" * 43
    assert state.read(tmp_path) is None


def test_listening_tells_a_closed_port_from_an_open_one():
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert state.listening(port)
    assert not state.listening(port)


def test_second_gui_launch_reuses_verified_server(tmp_path, monkeypatch, capsys):
    state.save(tmp_path, 8000, TOKEN)
    monkeypatch.setattr(state, "reachable", lambda *a: True)
    import waitress

    monkeypatch.setattr(waitress, "create_server", lambda *a, **k: pytest.fail("second server"))
    assert gui_main.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert "Reused the verified server" in capsys.readouterr().out



def _course(root: Path, name: str, runner: str | None) -> None:
    """A course folder under `root`; `runner` is its [isolation] runner, None for none."""
    (root / name).mkdir()
    text = f'course = "{name}"\n' + (f'\n[isolation]\nrunner = "{runner}"\n' if runner else "")
    (root / name / "workspace.toml").write_text(text, encoding="utf-8")


class DockerStandIn:
    """Docker as `_wake_docker` sees it (container.docker_state's kind), and the starts asked."""

    def __init__(self, monkeypatch, kind="stopped", desktop=True):
        from tutor import container

        self.started = 0
        state = container.DockerState(kind, f"the {kind} problem", f"the {kind} fix") \
            if kind else container.DockerState(None)
        monkeypatch.setattr(container, "docker_state", lambda *a, **k: state)
        monkeypatch.setattr(container, "start_docker_desktop", self._start)
        self.desktop = desktop

    def _start(self, *args):
        self.started += 1
        return "open -a Docker" if self.desktop else None


def test_launch_starts_a_stopped_docker_desktop_for_a_container_course(tmp_path, monkeypatch,
                                                                       capsys):
    _course(tmp_path, "boxed", "container")
    docker = DockerStandIn(monkeypatch)
    # A reused server's launch too: it waits for the start before its process ends.
    state.save(tmp_path, 8000, TOKEN)
    monkeypatch.setattr(state, "reachable", lambda *a: True)
    assert gui_main.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert docker.started == 1
    assert "starting it" in capsys.readouterr().out


@pytest.mark.parametrize("runner", [None, "host"])
def test_launch_leaves_docker_alone_when_no_course_needs_it(tmp_path, monkeypatch, runner):
    _course(tmp_path, "local", runner)
    docker = DockerStandIn(monkeypatch)
    gui_main._wake_docker(tmp_path)
    assert docker.started == 0


def test_launch_says_what_to_do_when_docker_cannot_be_started(tmp_path, monkeypatch, capsys):
    _course(tmp_path, "local", "host")
    _course(tmp_path, "boxed", "container")
    running = DockerStandIn(monkeypatch, kind=None)
    gui_main._wake_docker(tmp_path)
    assert running.started == 0 and capsys.readouterr().out == ""

    missing = DockerStandIn(monkeypatch, kind="missing")
    gui_main._wake_docker(tmp_path)
    assert missing.started == 0  # never installs: only says to
    assert "install Docker Desktop" in capsys.readouterr().out

    DockerStandIn(monkeypatch, desktop=False)
    gui_main._wake_docker(tmp_path)
    assert "start Docker yourself" in capsys.readouterr().out

    # Installed but not on this terminal's PATH, or not this user's: starting would not
    # help, so the launcher only passes on what to do.
    for kind in ("not-found", "denied"):
        other = DockerStandIn(monkeypatch, kind=kind)
        gui_main._wake_docker(tmp_path)
        assert other.started == 0
        assert f"the {kind} problem: the {kind} fix." in capsys.readouterr().out


def test_help_shows_the_whole_description(capsys):
    with pytest.raises(SystemExit) as exit_:
        gui_main.main(["--help"])
    assert exit_.value.code == 0
    # argparse wraps to the terminal's width, so compare the words, not the lines.
    assert "Ctrl+C stops the server." in " ".join(capsys.readouterr().out.split())

def test_waitress_text_port_is_saved_as_integer(tmp_path, monkeypatch):
    import waitress

    class Server:
        effective_port = "8000"

        def run(self):
            assert state.read(tmp_path)["port"] == 8000

        def close(self):
            pass

    monkeypatch.setattr(waitress, "create_server", lambda *a, **k: Server())
    monkeypatch.setattr(locations, "prepare", lambda: tmp_path)
    assert gui_main.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert state.read(tmp_path) is None


def test_packaged_paths_use_private_override_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.setattr(locations.project_source, "checkout", lambda: None)
    monkeypatch.setenv("TUTOR_HOME", str(tmp_path / "portable"))
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert gui_main._root(None) == tmp_path / "portable" / "courses"
    assert locations.commands() == tmp_path / "portable" / "config"
    assert not (tmp_path / "portable").exists()


@pytest.mark.parametrize("platform,suffix", [("darwin", ".command"), ("linux", ".desktop")])
def test_shortcut_quotes_paths_and_never_overwrites(tmp_path, monkeypatch, platform, suffix):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "executable", "/synthetic path/python")
    root = tmp_path / 'courses $dollar `tick` %quote"'
    path = shortcut.create(tmp_path, root)
    content = path.read_text(encoding="utf-8")
    assert path.suffix == suffix
    if platform == "darwin":
        assert shlex.split(content.split("exec ", 1)[1]) == [
            sys.executable, "-m", "tutor", "gui", "--root", str(root)]
    else:
        assert "%%quote" in content and "\\\\$dollar" in content and "Terminal=true" in content
    with pytest.raises(FileExistsError):
        shortcut.create(tmp_path, root)
    assert path.read_text(encoding="utf-8") == content


def test_windows_link_uses_encoded_com_script_and_argument_quoting(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", "C:\\Synthetic Path\\python.exe")
    root = tmp_path / "course's folder"

    def run(command, **kwargs):
        # stdin closed, else powershell.exe waits on a redirected stdin that never ends.
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["timeout"] >= 60
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        assert "course''s folder" in script and "WScript.Shell" in script
        args = subprocess.list2cmdline(["-m", "tutor", "gui", "--root", str(root)])
        assert args.replace("'", "''") in script
        # The server's console opens minimised, its tooltip saying how to stop it
        # (owner, 2026-11-12: a normal window "stays there the whole time").
        assert "$s.WindowStyle=7;" in script and "close it to stop" in script
        raw = re.search(r"CreateShortcut\('((?:[^']|'')*)'\)", script)[1]
        target = Path(raw.replace("''", "'"))
        assert not target.exists() and target != tmp_path / "Tutor.lnk"
        target.write_bytes(b"synthetic link")
        return subprocess.CompletedProcess(command, 0)

    assert shortcut.create(tmp_path, root, run=run).suffix == ".lnk"


def _xdg(answer: str, code: int = 0):
    """A stand-in for `xdg-user-dir DESKTOP` printing `answer`."""
    def run(command, **kwargs):
        assert command == ["xdg-user-dir", "DESKTOP"]
        return subprocess.CompletedProcess(command, code, stdout=answer + "\n", stderr="")
    return run


def test_linux_desktop_is_the_one_of_the_users_language(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop = tmp_path / "Schreibtisch"
    desktop.mkdir()
    assert shortcut.default_directory(run=_xdg(str(desktop))) == desktop


def test_linux_without_xdg_user_dir_uses_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    assert shortcut.default_directory(run=missing) == tmp_path / "Desktop"


@pytest.mark.parametrize("answer", ["home", "missing"])
def test_linux_with_no_desktop_folder_asks_for_one(tmp_path, monkeypatch, answer):
    """No desktop set (xdg-user-dir answers the home folder) or none on disk: an error
    naming --directory, not a launcher put somewhere the user would not look (owner,
    2026-11-16)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path if answer == "home" else tmp_path / "Desktop"
    with pytest.raises(ValueError, match="--directory"):
        shortcut.default_directory(run=_xdg(str(folder)))


def test_shortcut_without_a_desktop_exits_2_and_says_why(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shortcut.subprocess, "run", _xdg(str(tmp_path)))
    assert shortcut.main([]) == 2
    assert "give --directory" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_a_given_directory_never_asks_for_the_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def asked(**kwargs):
        raise AssertionError("looked up the desktop")

    monkeypatch.setattr(shortcut, "default_directory", asked)
    assert shortcut.main(["--directory", str(tmp_path)]) == 0
    assert (tmp_path / "Tutor.command").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows itself for its desktop")
def test_windows_desktop_is_the_known_folder():
    """The value PowerShell's [Environment]::GetFolderPath('Desktop') gives, the INSTALL
    text's workaround until 2026-11-16."""
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                           "[Environment]::GetFolderPath('Desktop')"],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    assert shortcut.default_directory() == Path(done.stdout.decode("utf-8").strip())


def test_help_lists_every_command_with_what_it_does(capsys):
    """`tutor --help` used to list bare names ({test,doctor,gui,shortcut,python})."""
    with pytest.raises(SystemExit) as done:
        dispatcher.main(["--help"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    for name, purpose in dispatcher.COMMANDS.items():
        assert re.search(rf"^\s+{name}\s+{re.escape(purpose)}$", out, re.MULTILINE)


def test_every_command_the_dispatcher_runs_has_a_help_line(monkeypatch):
    """A command added to main() without a COMMANDS line would be missing from --help."""
    import inspect

    runs = set(re.findall(r'args\[0\] == "(\w+)"', inspect.getsource(dispatcher.main)))
    assert runs == set(dispatcher.COMMANDS)


@pytest.mark.parametrize("argv", [[], ["bogus"]])
def test_no_or_an_unknown_command_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as done:
        dispatcher.main(argv)
    assert done.value.code == 2 and "COMMAND" in capsys.readouterr().err


def test_gui_help_names_the_command_users_type(capsys):
    with pytest.raises(SystemExit):
        gui_main.main(["--help"])
    assert capsys.readouterr().out.startswith("usage: tutor gui ")


def test_dispatcher_imports_gui_and_shortcut_lazily(monkeypatch):
    monkeypatch.setattr(gui_main, "main", lambda args: 17)
    monkeypatch.setattr(shortcut, "main", lambda args: 19)
    assert dispatcher.main(["gui"]) == 17
    assert dispatcher.main(["shortcut"]) == 19


def test_reachable_refuses_wrong_challenge_response(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps({"proof": "wrong"}).encode("utf-8")

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith("http://127.0.0.1:8000/_launcher?")
            return Response()

    monkeypatch.setattr(state, "build_opener", lambda *a: Opener())
    assert not state.reachable(tmp_path, {"port": 8000, "token": TOKEN})


# --- opening the browser (WSL: through Windows' http handler, 2026-11-16) ------------------


class Opened:
    """Stand-ins for rundll32.exe's start and webbrowser.open, recording what each got."""

    def __init__(self, monkeypatch, *, rundll32="/mnt/c/Windows/System32/rundll32.exe",
                 found=True):
        self.started, self.browsed = [], []
        monkeypatch.setattr(gui_main.shutil, "which",
                            lambda name: rundll32 if name == "rundll32.exe" else None)
        monkeypatch.setattr(gui_main.subprocess, "Popen", self._popen)
        monkeypatch.setattr(gui_main.webbrowser, "open", self._browse)
        self.found = found

    def _popen(self, command, **kwargs):
        self.started.append(command)

    def _browse(self, url):
        self.browsed.append(url)
        return self.found


URL = "http://127.0.0.1:8123/?token=synthetic"


def test_under_wsl_the_link_opens_in_windows_browser(monkeypatch, capsys):
    """WSL's own webbrowser fell through to gio ("Operation not supported"); Windows'
    browser reaches the server there, so Windows' http handler gets the link. Not
    explorer.exe: given this link it opened the Documents folder (WSL, 2026-11-16)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    opened = Opened(monkeypatch)
    gui_main._open_browser(URL)
    assert opened.started == [["/mnt/c/Windows/System32/rundll32.exe",
                               "url.dll,FileProtocolHandler", URL]]
    assert opened.browsed == []
    assert capsys.readouterr().out == ""


def test_wsl_without_windows_interop_tries_the_usual_way(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    opened = Opened(monkeypatch, rundll32=None)
    gui_main._open_browser(URL)
    assert opened.started == [] and opened.browsed == [URL]


def test_plain_linux_never_runs_rundll32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    # raising=False: Windows' os has no uname at all (Windows CI, 2026-11-16), and the code
    # asks for it only on Linux, which this test pretends to be.
    monkeypatch.setattr(gui_main.os, "uname",
                        lambda: type("U", (), {"release": "7.0.0-34-generic"})(), raising=False)
    opened = Opened(monkeypatch)
    gui_main._open_browser(URL)
    assert opened.started == [] and opened.browsed == [URL]


def test_no_browser_found_says_to_open_the_link(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    Opened(monkeypatch, found=False)
    gui_main._open_browser(URL)
    assert gui_main.NOT_OPENED in capsys.readouterr().out
