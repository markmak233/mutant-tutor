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
    with pytest.raises(ValueError, match="alive"):
        state.existing(tmp_path)
    monkeypatch.setattr(state, "_alive", lambda pid: False)
    assert state.existing(tmp_path) is None and state.read(tmp_path) is None


def test_second_gui_launch_reuses_verified_server(tmp_path, monkeypatch, capsys):
    state.save(tmp_path, 8000, TOKEN)
    monkeypatch.setattr(state, "reachable", lambda *a: True)
    import waitress

    monkeypatch.setattr(waitress, "create_server", lambda *a, **k: pytest.fail("second server"))
    assert gui_main.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert "Reused the verified server" in capsys.readouterr().out


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
