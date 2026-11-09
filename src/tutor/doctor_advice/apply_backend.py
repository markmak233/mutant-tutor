"""Actual install/build operations behind the explicit doctor application transaction."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tutor import config, container, pythons, workspace
from tutor.doctor_advice.sandbox import Disposable, requirements
from tutor.doctor_advice.service import AdviceSetupError


class Backend(Disposable):
    """Reuse verification's credential stripping, bounds and subprocess deadlines."""

    def prepare_python(self, changes):
        """Resolve the base before moving the original venv; never execute a stale path."""
        python = pythons.ask(self.python, run=self._command)
        if any(c.kind == "python" for c in changes):
            version = next(c.python for c in changes if c.kind == "python")
            python = next((p for p in self.find(run=self._command) if p.version == version), None)
        if python is None:
            raise AdviceSetupError("selected base Python is no longer available")
        return python.path

    def install(self, live: Path, python: Path, changes) -> None:
        """Build at the final path: venv console scripts cannot safely be relocated."""
        if self._command([str(python), "-I", "-m", "venv", str(live)], timeout=60).returncode:
            raise AdviceSetupError("replacement venv creation failed")
        command = workspace.install_command(live, workspace.DEFAULT_EXTRAS,
                                             requirements(self.found, changes))
        command += ["--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                    "--no-cache-dir"]
        if self._command(command, timeout=300).returncode:
            raise AdviceSetupError("replacement venv installation failed")

    def build(self, proposed: bytes) -> str:
        """New recipe against a temporary workspace; retain previous images for undo."""
        with tempfile.TemporaryDirectory(prefix="tutor-doctor-apply-") as directory:
            root = Path(directory)
            (root / "workspace.toml").write_bytes(proposed)
            selected = config.load_workspace({"WORKSPACE_DIR": str(root)})
            return container.build(selected, run=self._command, keep_older=True,
                                   installed=self.found.installed.get)
