"""Setuptools hooks add only controlled public source/example resources to distributions."""

import runpy
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

# PEP 517 executes setup.py without necessarily adding its directory to sys.path.
# Load this controlled adjacent build helper directly, without importing tutor.
_helpers = runpy.run_path(str(Path(__file__).with_name("build_support.py")))
bundle, public_files = _helpers["bundle"], _helpers["public_files"]


class PublicBuild(build_py):
    def run(self):
        super().run()
        self._public_outputs = bundle(Path(__file__).resolve().parent,
                                      Path(self.build_lib) / "tutor")

    def get_outputs(self, include_bytecode=1):
        return [*super().get_outputs(include_bytecode),
                *map(str, getattr(self, "_public_outputs", []))]


class PublicSource(sdist):
    def make_release_tree(self, base_dir, files):
        root = Path(__file__).resolve().parent
        allowed = {p.relative_to(root).as_posix() for p in public_files(root)}
        # Generated egg metadata is public package metadata needed by standard builders.
        chosen = [p for p in files if p.replace("\\", "/") in allowed or ".egg-info/" in p]
        chosen += sorted(allowed - {p.replace("\\", "/") for p in chosen})
        super().make_release_tree(base_dir, chosen)


setup(cmdclass={"build_py": PublicBuild, "sdist": PublicSource})
