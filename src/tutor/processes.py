"""The processes of one test run: the child and everything it starts, ended together, and
the run's own temp dir removed afterwards. tutor.execution starts a run with one of these and
turns its exit into a RunResult; this module knows nothing of pytest or of results.

ProcessTree is a run on this machine; ContainerRun is a `docker run` client and the container
it started, which ending the client alone would leave running.
"""

from __future__ import annotations

import codecs
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
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


def stream_output(tree: ProcessTree, say: Callable[[str], None],
                  deadline: float) -> tuple[str, str | None]:
    """Deliver chunks while monitoring a silent child too; always end its whole tree.

    No text reads have occurred on Popen's stdout yet, so its binary buffer can be read
    directly. The decoder lives in one reader thread; callbacks run only on the main thread.
    This works with Windows pipes as well as POSIX and does not wait for newline characters.
    """
    chunks: queue.Queue[str | BaseException | None] = queue.Queue()
    output, reason = [], None

    def read() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while data := tree.proc.stdout.buffer.read1(4096):
                if text := decoder.decode(data):
                    chunks.put(text)
            if text := decoder.decode(b"", final=True):
                chunks.put(text)
        except BaseException as e:
            chunks.put(e)
        finally:
            chunks.put(None)

    reader = threading.Thread(target=read, name="tutor-output", daemon=True)
    reader.start()
    ended = False
    try:
        while not ended:
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            if tree.proc.poll() is not None:
                break  # kill leftover children before waiting for their inherited pipe
            try:
                chunk = chunks.get(timeout=min(0.1, max(0, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if chunk is None:
                ended = True
            elif isinstance(chunk, BaseException):
                raise chunk
            else:
                output.append(chunk)
                say(chunk)
        # EOF need not mean the process exited: it may close stdout then loop forever.
        if ended:
            try:
                tree.proc.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                reason = "timeout"
    except KeyboardInterrupt:
        reason = "keyboard_interrupt"
    finally:
        tree.kill()
        try:
            tree.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # the tree was killed; never let a slow exit replace the run's result
        # Once the tree is gone EOF should follow immediately. Bound the wait anyway;
        # a deliberately detached host descendant can outlive a POSIX process group.
        reader.join(timeout=2)
        if not reader.is_alive():
            tree.proc.stdout.close()
    # Drain the last output without printing it twice; preserve it after timeout/Ctrl+C.
    try:
        while not chunks.empty():
            chunk = chunks.get_nowait()
            if isinstance(chunk, str):
                output.append(chunk)
                say(chunk)
            elif isinstance(chunk, BaseException):
                raise chunk
    except KeyboardInterrupt:
        # Fast processes may finish before the first callback. Ctrl+C while draining
        # their final output still returns an interrupted result to the host recorder.
        reason = "keyboard_interrupt"
        while not chunks.empty():
            chunk = chunks.get_nowait()
            if isinstance(chunk, str):
                output.append(chunk)
    return "".join(output), reason
