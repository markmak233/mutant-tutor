"""Bounded, read-only metadata probes in the environment being diagnosed.

These scripts are fixed tool code, not model/user code. No exercise/framework is explicitly
imported and no package is built, pulled or installed. Images never fall back to host metadata.
Credentials are
not inherited, and container probes have their own deadline and cleanup name.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from tutor import compat, container, pythons, workspace
from tutor.doctor_advice.models import AdviceContext, package_name, release
from tutor.doctor_checks import Course

# ASCII JSON avoids the child's native Windows code page; -I/-B exclude user site and
# current-directory imports, ignore PYTHONPATH and suppress bytecode writes.
PROBE = """# tutor-doctor-metadata-v1
import sys, json
from importlib import metadata
distributions = list(metadata.distributions())
installed = [[d.metadata["Name"], d.version] for d in distributions]
direct = [d.metadata["Name"] for d in distributions
          if any(str(f).replace(chr(92), "/").endswith(".dist-info/direct_url.json")
                 for f in (d.files or ()))]
print(json.dumps({"python": ".".join(map(str, sys.version_info[:3])),
                  "installed": installed, "direct": direct}))
"""
_RUNTIME = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "LC_CTYPE"}
# Docker Desktop selects its daemon through the user's CLI context. Keep its config
# locations/transport settings for Docker only; do not forward them to Python probes.
# Registry auth blobs and model/API credential variables are intentionally absent.
_DOCKER_RUNTIME = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                   "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY",
                   "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"}


class FactsError(ValueError):
    """No trusted facts are available; raw probe output must not reach the user/model."""


def runtime_environment(*, docker: bool = False) -> dict[str, str]:
    """Explicit child allowlist; Docker alone needs CLI context/transport locations."""
    allowed = _RUNTIME | (_DOCKER_RUNTIME if docker else set())
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


@dataclass(frozen=True)
class Facts:
    """Installed versions stay strings (possibly pre/local releases), not numeric prefixes."""

    context: AdviceContext
    installed: Mapping[str, str]
    source: str
    direct: frozenset[str] = frozenset()  # source/URL installs cannot be recreated by name


def collect(course: Course | None, *, run: Callable | None = None, find: Callable | None = None,
            docker_ready: bool | None = None, image_ready: bool | None = None) -> Facts:
    """Inspect one interpreter/image, then offer actual host Python choices when relevant."""
    run = subprocess.run if run is None else run
    runtime = runtime_environment()
    docker_runtime = runtime_environment(docker=True)

    def probe(command, **kwargs):
        # Credentials remain stripped, but Docker must retain its selected daemon context.
        env = docker_runtime if command[0] == "docker" else runtime
        return run(command, **kwargs, env=env, encoding="utf-8", errors="replace")

    if course and (course.error or course.ws is None):
        raise FactsError("workspace.toml did not load; correct it before requesting advice")
    runner = course.runner if course else "host"
    if runner not in (None, "host", "container"):
        raise FactsError("choose a valid course runner before requesting advice")
    is_image = runner == "container"
    name = None
    if is_image:
        if docker_ready is False or (docker_ready is None and container.docker_problem(probe)):
            raise FactsError("Docker is unavailable; start it before requesting image advice")
        image = container.image_for([course.root / "workspace.toml"])
        if image_ready is False or (image_ready is None and container.image_problem(image, probe)):
            raise FactsError("course image is not ready; build it on the Environment page")
        name = "tutor-doctor-" + secrets.token_hex(6)
        command = ["docker", "run", "--rm", "--pull", "never", "--name", name,
                   *container.limits(), image, "timeout", "-s", "KILL", "30",
                   "python", "-I", "-B", "-c", PROBE]
        source = "course image"
    else:
        python = workspace.venv_python(course.root / ".venv") if course else Path(sys.executable)
        if not python.is_file():
            raise FactsError("course Python is missing; install its venv on the Environment page")
        command = [str(python), "-I", "-B", "-c", PROBE]
        source = "course venv" if course else "tool interpreter"
    def cleanup() -> None:
        # A failed Docker client does not prove its container ended. Kill our name only;
        # the in-container timeout is a second bound if Docker cannot answer cleanup.
        if name:
            try:
                probe(["docker", "kill", name], capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    try:
        done = probe(command, capture_output=True, text=True, timeout=35)
    except BaseException as exc:
        cleanup()
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, (OSError, subprocess.TimeoutExpired)):
            raise FactsError("metadata probe did not complete; restore the selected environment"
                             ) from None
        raise
    if done.returncode:
        cleanup()
        raise FactsError("metadata probe failed; restore the selected environment before advice")
    try:
        data = json.loads(done.stdout)
        if not isinstance(data, dict) or set(data) not in (
                {"python", "installed"}, {"python", "installed", "direct"}):
            raise ValueError
        python = release(data["python"], interpreter=True)
        if not isinstance(data["installed"], list):
            raise ValueError
        installed = {}
        for entry in data["installed"]:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError
            package, version = package_name(entry[0]), entry[1]
            if not isinstance(version, str) or not version.strip():
                raise ValueError
            if package in installed and installed[package] != version:
                raise ValueError
            installed[package] = version
        if not isinstance(data.get("direct", []), list):
            raise ValueError
        direct = frozenset(package_name(n) for n in data.get("direct", []))
        if not direct.issubset(installed):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise FactsError("metadata probe returned invalid/ambiguous facts; repair the environment"
                         ) from None
    requires = course.ws.effective_requires if course else {}
    offered = frozenset() if is_image else frozenset(
        f.version for f in (find or pythons.find)(run=probe)
        if f.version != python and not compat.conflicts(requires, f.minor))
    try:
        context = AdviceContext(requires, frozenset(installed), python, offered,
                                python_locked=is_image)
    except ValueError:
        raise FactsError("correct the declared requirements before requesting advice") from None
    return Facts(context, MappingProxyType(dict(sorted(installed.items()))), source, direct)
