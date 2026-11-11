"""Disposable environments for gated proposals; installation is explicitly requested.

No live venv/course is written. Host subprocesses have user rights (not a sandbox).
Container installs build an owned temporary image; probes retain execution's limits.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from tutor import container, execution, pythons, workspace
from tutor.doctor_advice import facts, verification
from tutor.doctor_advice.gate import numeric_specifier
from tutor.doctor_advice.models import Change, package_name

# Exact measured versions may have PEP 440 pre/post/local syntax. This is a safe spelling
# check, not a resolver: pip decides satisfiability. URLs/options/whitespace cannot enter.
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]*")
_INSTALL = ["-m", "pip", "--disable-pip-version-check", "--no-input", "install",
            "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple"]


def requirements(found: facts.Facts, changes: tuple[Change, ...]) -> list[str]:
    """Recreate measured distributions, replacing changed pins rather than intersecting.

    tutor is repository code mounted by execution, not a possibly unrelated public PyPI
    project of that name. Pip/venv bootstrap themselves; all other distributions keep pins.
    """
    specs = {}
    if found.direct - {"tutor", "pip"}:
        raise facts.FactsError("local/URL distributions cannot be safely recreated from PyPI; "
                               "verify them separately without substituting public names")
    for name, version in found.installed.items():
        name = package_name(name)
        if name in {"tutor", "pip"}:
            continue
        if not _VERSION.fullmatch(version):
            raise facts.FactsError("installed versions cannot be safely recreated from PyPI")
        specs[name] = f"{name}=={version}"
    for change in changes:
        if change.kind != "python":
            name = package_name(change.package)
            if name in {"tutor", "pip"}:
                raise facts.FactsError("tool/bootstrap packages are not disposable course repairs")
            specs[name] = name + (numeric_specifier(change.spec) if change.spec else "")
    return [specs[n] for n in sorted(specs)]


class Disposable:
    """One original identity, freshly created environment for each cumulative trial."""

    def __init__(self, found, course, module, *, run=None, probe=None, find=None):
        self.found, self.course = found, course
        self.script = verification.probe_script(module)
        self.run = subprocess.run if run is None else run
        self.probe = execution.run_environment_probe if probe is None else probe
        self.find = pythons.find if find is None else find
        self.sources = (course.root / "workspace.toml",) if course else ()
        self.image = container.image_for(self.sources)
        self.python = (workspace.venv_python(course.root / ".venv") if course
                       else Path(sys.executable))

    def _command(self, command, *, timeout=180, **kwargs):
        # PIP_CONFIG_FILE=os.devnull disables *all* pip configuration, including global
        # extra indexes. A minimal map strips PIP_* overrides and API credentials first.
        env = facts.runtime_environment(docker=command[0] == "docker")
        env.update(PIP_CONFIG_FILE=os.devnull, PYTHONIOENCODING="utf-8")
        options = {**kwargs, "env": env, "capture_output": True, "text": True,
                   "encoding": "utf-8", "errors": "replace", "timeout": timeout}
        return self.run(command, **options)

    def baseline(self) -> verification.Check:
        return self._probe(image=self.image, python=None if self.image else self.python)

    def _probe(self, *, image=None, python=None) -> verification.Check:
        result = self.probe(self.script, sources=self.sources, image=image, python=python,
                            env=facts.runtime_environment(docker=bool(image)))
        return verification.measured(result)

    def verify(self, changes: tuple[Change, ...]) -> verification.Check:
        """Clean temporary venv/image even after failed install, probe or Ctrl+C."""
        specs = requirements(self.found, changes)
        try:
            if self.image:
                return self._image(specs)
            python = self.python
            for change in changes:
                if change.kind == "python":
                    selected = next((p for p in self.find(run=self._command)
                                     if p.version == change.python), None)
                    if selected is None:
                        return verification.Check(False, "offered Python is no longer available")
                    python = selected.path
            with tempfile.TemporaryDirectory(prefix="tutor-doctor-venv-") as directory:
                target = Path(directory) / "venv"
                made = self._command([str(python), "-I", "-m", "venv", str(target)], timeout=60)
                if made.returncode:
                    return verification.Check(False, "temporary venv creation failed")
                executable = workspace.venv_python(target)
                if specs and self._command([str(executable), *_INSTALL, *specs]).returncode:
                    return verification.Check(False, "temporary wheel installation failed")
                return self._probe(python=executable)
        except (OSError, subprocess.TimeoutExpired):
            return verification.Check(False, "temporary environment operation failed or timed out")

    def _image(self, specs: list[str]) -> verification.Check:
        # Only an already diagnosed local image is the base. No context/course file is
        # sent to Docker. A unique tag avoids replacing any course's image or another run.
        name = "tutor-doctor-" + uuid.uuid4().hex
        recipe = f"FROM {self.image}\nUSER root\nRUN python -m venv /opt/tutor-doctor\n"
        if specs:
            argv = ["env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "PIP_CONFIG_FILE=/dev/null",
                    "/opt/tutor-doctor/bin/python", *_INSTALL, *specs]
            recipe += "RUN " + shlex.join(argv) + "\n"
        recipe += ('ENV PATH="/opt/tutor-doctor/bin:${PATH}"\n'
                   'ENV PYSPARK_PYTHON="/opt/tutor-doctor/bin/python"\nUSER runner\n')
        try:
            built = self._command(["docker", "build", "--pull=false", "--rm", "--tag", name, "-"],
                                  input=recipe, timeout=300)
            if built.returncode:
                return verification.Check(False, "temporary image wheel installation failed")
            return self._probe(image=name)
        finally:
            # No pruning/old-course-image removal. If the daemon is disconnected cleanup
            # cannot be guaranteed; report that explicitly rather than claiming success.
            try:
                removed = self._command(["docker", "image", "rm", name], timeout=15)
                if removed.returncode and 'No such image' not in removed.stderr:
                    raise facts.FactsError("temporary image cleanup failed; "
                                           "inspect tutor-doctor images")
            except (OSError, subprocess.TimeoutExpired):
                raise facts.FactsError("temporary image cleanup unavailable; "
                                       "inspect tutor-doctor images") from None
