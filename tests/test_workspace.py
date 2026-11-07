import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from tutor.config import WorkspaceError, load_workspace
from tutor.validation.safety import workspace_allow_imports
from tutor.workspace import REPO, SETTINGS, init, main, venv_python


class FakeInstall:
    """Stands in for venv creation and pip: no network, and records the commands."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.venvs = 0

    def make_venv(self, venv_dir: Path) -> None:
        self.venvs += 1
        venv_python(venv_dir).parent.mkdir(parents=True)
        venv_python(venv_dir).write_text("", encoding="utf-8")

    def run(self, command, **kwargs):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, self.returncode, "", "no network")


def make(root: Path, fake: FakeInstall | None = None, **kwargs) -> list[str]:
    fake = fake or FakeInstall()
    return init(root, make_venv=fake.make_venv, run=fake.run, **kwargs)


def test_creates_a_workspace_the_rest_of_the_code_accepts(tmp_path):
    root = tmp_path / "ws"

    make(root, course="demo")

    ws = load_workspace({"WORKSPACE_DIR": str(root)})
    assert ws.course == "demo"
    assert "python" in ws.requires
    assert ws.lecture_dir.is_dir()
    assert workspace_allow_imports(root) == frozenset()


def test_course_defaults_to_the_folder_name(tmp_path):
    make(tmp_path / "neural-nets")

    text = (tmp_path / "neural-nets" / "workspace.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text)["course"] == "neural-nets"


def test_the_container_is_offered_but_left_off(tmp_path):
    """Switching it on needs Docker and a built image, which init does not install; the
    commented lines must still be a working switch once uncommented."""
    make(tmp_path / "ws")

    text = (tmp_path / "ws" / "workspace.toml").read_text(encoding="utf-8")
    assert "isolation" not in tomllib.loads(text)
    switched_on = text.replace("# [isolation]", "[isolation]").replace(
        '# runner = "container"', 'runner = "container"')
    assert tomllib.loads(switched_on)["isolation"] == {"runner": "container"}


def test_terminals_get_the_venv_and_this_folders_paths(tmp_path):
    """Without WORKSPACE_DIR here, the attempts plugin skips the folder's tests silently."""
    make(tmp_path / "ws")

    vscode = tmp_path / "ws" / ".vscode"
    env = json.loads((vscode / "settings.json").read_text(encoding="utf-8"))[
        "terminal.integrated.env.osx"]
    assert env["ZDOTDIR"] == "${workspaceFolder}/.vscode/zsh"
    assert env["WORKSPACE_DIR"] == "${workspaceFolder}"
    assert env["LECTURE_DIR"] == "${workspaceFolder}/lectures"
    zshrc = (vscode / "zsh" / ".zshrc").read_text(encoding="utf-8")
    assert zshrc.index('source "$HOME/.zshrc"') < zshrc.index(".venv/bin/activate")


def test_installs_this_repository_into_the_new_venv_with_its_extras(tmp_path):
    fake = FakeInstall()

    make(tmp_path / "ws", fake, extras="gen,dev")

    assert fake.venvs == 1
    command = fake.commands[0]
    assert command[0] == str(venv_python(tmp_path / "ws" / ".venv"))
    assert command[1:6] == ["-m", "pip", "install", "-q", "-e"]
    assert command[6] == f"{REPO}[gen,dev]"


def test_never_overwrites_an_existing_file(tmp_path):
    root = tmp_path / "ws"
    (root / ".vscode").mkdir(parents=True)
    (root / "workspace.toml").write_text('course = "mine"\n', encoding="utf-8")
    (root / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")

    steps = make(root, course="other")

    assert (root / "workspace.toml").read_text(encoding="utf-8") == 'course = "mine"\n'
    assert (root / ".vscode" / "settings.json").read_text(encoding="utf-8") == "{}\n"
    assert "kept    workspace.toml" in steps
    assert "kept    .vscode/settings.json" in steps
    assert "created .vscode/zsh/.zshrc" in steps


def test_running_again_keeps_the_venv_but_installs_again(tmp_path):
    """Installing every time finishes one that failed and adds extras named later."""
    root, fake = tmp_path / "ws", FakeInstall()

    make(root, fake)
    steps = make(root, fake, extras="gen,dev,bigdata")

    assert fake.venvs == 1
    assert "kept    .venv" in steps
    assert fake.commands[-1][-1] == f"{REPO}[gen,dev,bigdata]"


def test_the_requires_packages_go_into_the_venv_in_the_same_pip_call(tmp_path):
    """Running init again after editing [requires] is how the venv catches up."""
    root, fake = tmp_path / "ws", FakeInstall()
    root.mkdir()
    (root / "workspace.toml").write_text(
        'course = "demo"\n[requires]\npython = "3.11"\njava = "17"\npyspark = "3.5"\n'
        "torch = 2\n", encoding="utf-8")

    steps = make(root, fake)

    assert fake.commands[0][6:] == [f"{REPO}[gen,dev]", "pyspark==3.5.*", "torch==2.*"]
    assert steps[-1] == "installed tutor[gen,dev], pyspark==3.5.*, torch==2.* into .venv"


def test_a_new_course_installs_only_this_package(tmp_path):
    fake = FakeInstall()
    make(tmp_path / "ws", fake)
    assert fake.commands[0][6:] == [f"{REPO}[gen,dev]"]


@pytest.mark.parametrize("table", ['pyspark = "3.5"', 'pyspark = ">=3.5"', "[[broken"])
def test_a_requires_table_that_cannot_be_installed_stops_before_anything_changes(tmp_path,
                                                                                 table):
    root, fake = tmp_path / "ws", FakeInstall()
    root.mkdir()
    (root / "workspace.toml").write_text(f'course = "demo"\n[requires]\n{table}\n',
                                         encoding="utf-8")

    with pytest.raises(WorkspaceError, match="workspace.toml"):
        make(root, fake)

    assert sorted(p.name for p in root.iterdir()) == ["workspace.toml"]
    assert fake.commands == [] and fake.venvs == 0


def test_a_failed_install_is_reported(tmp_path):
    with pytest.raises(WorkspaceError, match="no network"):
        make(tmp_path / "ws", FakeInstall(returncode=1))


def test_refuses_a_folder_inside_a_git_repository_and_changes_nothing(tmp_path):
    (tmp_path / ".git").mkdir()
    root = tmp_path / "sub" / "ws"

    with pytest.raises(WorkspaceError, match="git repository"):
        make(root)

    assert not (tmp_path / "sub").exists()


@pytest.mark.parametrize("kwargs", [{"course": 'a"b'}, {"extras": "gen;rm"}, {"extras": "gen dev"}])
def test_refuses_names_that_could_break_the_toml_or_the_pip_command(tmp_path, kwargs):
    with pytest.raises(WorkspaceError):
        make(tmp_path / "ws", **kwargs)

    assert not (tmp_path / "ws").exists()


def test_settings_are_valid_json():
    assert json.loads(SETTINGS)["python.terminal.activateEnvironment"] is True


def test_command_line_reports_a_refusal(tmp_path, capsys):
    (tmp_path / ".git").mkdir()

    assert main(["init", str(tmp_path / "ws")]) == 1

    assert "git repository" in capsys.readouterr().err


def test_each_step_is_said_as_it_happens_and_the_install_before_it_starts(tmp_path):
    """The GUI shows the log while init runs: a step said only at the end looked stuck."""
    said: list[str] = []
    fake = FakeInstall()
    at_install: list[str] = []

    def run(command, **kwargs):
        at_install.extend(said)  # what was said by the time the install started
        return fake.run(command, **kwargs)

    steps = init(tmp_path / "ws", make_venv=fake.make_venv, run=run, say=said.append)

    assert "created workspace.toml" in at_install and "created .venv (Python" in at_install[-2]
    assert at_install[-1].startswith("installing tutor[gen,dev]")
    assert [line for line in said if line in steps] == steps  # every step, in order


@pytest.mark.parametrize(("flags", "standard"), [
    (["-Wall", "-std=c99"], "c99"),
    (["-std=gnu11"], "gnu11"),
    (["-std=c18"], "c17"),
    (["-ansi"], "c89"),
    (["-std=c99", "-std=c11"], "c11"),  # the last one wins, as in gcc
    (["-Wall"], "${default}"),
    (["-std=c2y"], "${default}"),  # a name VS Code does not have
])
def test_vs_code_is_told_the_courses_c_standard(flags, standard):
    from tutor.workspace import c_standard

    assert c_standard(flags) == standard


def test_vs_codes_c_settings_are_written_and_removed_only_when_ours(tmp_path):
    from tutor.workspace import C_EDITOR_FILE, c_editor_config_kept, write_c_editor_config

    assert write_c_editor_config(tmp_path, ["-Wall", "-std=c99"])
    data = json.loads((tmp_path / C_EDITOR_FILE).read_text(encoding="utf-8"))
    [config] = data["configurations"]
    assert (config["cStandard"], config["compilerArgs"]) == ("c99", ["-Wall", "-std=c99"])
    assert write_c_editor_config(tmp_path, None)
    assert not (tmp_path / C_EDITOR_FILE).exists()

    own = '{"configurations": [{"name": "Mac", "cStandard": "c17"}], "version": 4}\n'
    (tmp_path / C_EDITOR_FILE).write_text(own, encoding="utf-8")
    assert c_editor_config_kept(tmp_path)
    assert not write_c_editor_config(tmp_path, ["-std=c99"])
    assert not write_c_editor_config(tmp_path, None)
    assert (tmp_path / C_EDITOR_FILE).read_text(encoding="utf-8") == own
