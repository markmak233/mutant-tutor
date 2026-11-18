"""Environments without credentials, for the processes this tool starts.

Two uses:
- `environment()`: an allowlist for diagnostic Python/Docker subprocesses. Docker alone
  retains CLI context/transport locations. Never forward model credentials, registry auth
  blobs or inherited pip configuration into diagnostic children.
- `without_credentials()`: a denylist for runs of exercise code on this machine (the host
  runner, `execution._run_in`). Those runs need whatever the course's tools read (JAVA_HOME,
  HADOOP_HOME, SPARK_*, PATH, a compiler's variables), which no fixed allowlist covers, so
  only what is a credential is taken out: generated code may read `os.environ` (the static
  check allows it), and a model's key has no business in a test run.

Neither is a sandbox: code running on the host can still read a file such as `.env` by its
path. The container is the boundary (docs/design/execution.md, "Code execution safety").
"""

import os
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

_RUNTIME = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "LC_CTYPE"}
_DOCKER = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
           "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY",
           "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"}

# A variable named like a credential: one of these words as a whole part of the name
# (DEEPSEEK_API_KEY, GITHUB_TOKEN, KEY_FILE), or one of two names that hold one. Whole
# parts only, so MONKEY or KEYBOARD_LAYOUT stay. Names are inspected, never printed.
SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|APIKEY)(?:_|$)"
    r"|^(?:AUTHORIZATION|DOCKER_AUTH_CONFIG)$", re.IGNORECASE)


def secret_name(name: str) -> bool:
    """Whether a variable is named like a credential (also the redactor's rule)."""
    return bool(SECRET_NAME.search(name))


def environment(*, docker: bool = False) -> dict[str, str]:
    """Names are compared case-insensitively for native Windows environments."""
    allowed = _RUNTIME | (_DOCKER if docker else set())
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


def key_names(sources: Iterable[Path]) -> frozenset[str]:
    """The variables that the courses above `sources` name as a model's key: every
    `api_key_env` in a course's workspace.toml and in the shared llm.toml of the folder
    above it. For a key under a name SECRET_NAME does not recognise (`MY_PROVIDER=...`).

    Read as plain TOML, wherever the value sits, and never refused: a file that does not
    parse names nothing here, and the command that needs it reports it.
    """
    from tutor.config import WORKSPACE_FILE  # here: config imports this module
    from tutor.llm import SHARED_FILE

    names: set[str] = set()
    checked: set[Path] = set()
    for source in sources:
        for folder in Path(source).resolve().parents:
            if folder in checked:
                break  # and so was everything above it
            checked.add(folder)
            if (folder / WORKSPACE_FILE).is_file():
                # The course's own profiles, then the cards and profiles its folder shares.
                for path in (folder / WORKSPACE_FILE, folder.parent / SHARED_FILE):
                    names |= _named_keys(path)
    return frozenset(names)


def _named_keys(path: Path) -> set[str]:
    """Every string under an `api_key_env` key in one TOML file, at any depth."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    found: set[str] = set()
    todo = [data]
    while todo:  # tables inside tables: [llm.profiles.<name>], [providers.<name>]
        table = todo.pop()
        for key, value in table.items():
            if isinstance(value, dict):
                todo.append(value)
            elif key == "api_key_env" and isinstance(value, str) and value:
                found.add(value)
    return found


def without_credentials(env: Mapping[str, str], also: Iterable[str] = ()) -> dict[str, str]:
    """`env` without the variables named like a credential and without those in `also`
    (key_names). Names are compared without case, as Windows does."""
    named = {name.upper() for name in also}
    return {k: v for k, v in env.items() if not secret_name(k) and k.upper() not in named}
