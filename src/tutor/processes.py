"""The processes of one test run: the child and everything it starts, ended together, and
the run's own temp dir removed afterwards. tutor.execution starts a run with one of these and
turns its exit into a RunResult; this module knows nothing of pytest or of results.

ProcessTree is a run on this machine; ContainerRun is a `docker run` client and the container
it started, which ending the client alone would leave running.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

if sys.platform == "win32":  # Job Objects stand in for process groups there
    from tutor import winjob


def remove_run_tmp(path: Path, wait_s: float = 5.0) -> None:
    """Delete a run's temp dir, retrying for a moment. On Windows the native library a JVM
    unpacks there (liblz4-java-*.so) could not be deleted for a while after every process
    of the run had exited (WinError 5; most likely a virus scan of the new file). What is
    still locked after `wait_s` is left behind, as it was in the shared %TEMP% before."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.monotonic() >= deadline:
                shutil.rmtree(path, ignore_errors=True)  # delete what it can
                return
            time.sleep(0.1)


class ProcessTree:
    """The child and every process it starts, killed as one. PySpark starts a JVM under
    pytest, and killing only pytest would orphan it, still running in the temp dir.

    POSIX: a new session is a new process group, which the child's descendants join, so
    one killpg reaches them all, even after their parent has exited.
    Windows has no process groups: a Job Object does the same (tutor/winjob.py). The child
    is assigned to the job right after it starts; a process it started in that moment
    would stay outside, but Python takes far longer to start up and import pytest than
    that, and only then do tests start anything."""

    def __init__(self, cmd: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self._job = winjob.Job() if sys.platform == "win32" else None
        self.proc = subprocess.Popen(
            # The output is decoded as UTF-8 (what PYTHONIOENCODING makes the child write);
            # bytes a program wrote some other way are replaced, not a crash.
            cmd, cwd=cwd, env=env, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=self._job is None,  # POSIX only; the job replaces it
        )
        if self._job is not None:
            try:
                self._job.add(self.proc.pid)
            except OSError:  # not contained, so do not let it run: end it and report
                self.proc.kill()
                self.proc.communicate()
                self._job.close()
                raise

    def kill(self) -> None:
        if self._job is not None:
            self._job.kill()  # returns once every process in the job is gone
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # the whole group has already exited

    def __enter__(self) -> ProcessTree:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._job is not None:
            self._job.close()  # KILL_ON_JOB_CLOSE: also ends what an exception left running


class ContainerRun(ProcessTree):
    """A `docker run` client and its container. Ending the client alone leaves the code
    running inside (measured with `docker exec`), so the container is killed by name; every
    process in it ends with it, and `--rm` removes it."""

    def __init__(self, name: str, cmd: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self._name = name
        super().__init__(cmd, cwd, env)

    def kill(self) -> None:
        if self.proc.poll() is None:  # a client that has exited means the container has too
            try:
                subprocess.run(["docker", "kill", self._name], capture_output=True, timeout=30)
                self.proc.wait(timeout=5)  # lets the client hand over the last output
            except (OSError, subprocess.TimeoutExpired):
                pass  # the client is ended below, and the container's `timeout` ends it
        super().kill()

    def __exit__(self, exc_type: object, *exc: object) -> None:
        if exc_type is not None:  # Ctrl+C or a crash here: do not leave the container running
            self.kill()
        super().__exit__(exc_type, *exc)
