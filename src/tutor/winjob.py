"""Windows Job Objects: kill a child process and every process it started, as one.

The Windows counterpart of a POSIX process group (docs/design.md, "Code execution safety").
Windows records only each process's parent pid, so walking the tree (`taskkill /T`) misses
a grandchild whose parent has already exited, such as the JVM PySpark starts under pytest.
A Job Object is a kernel container instead: a process assigned to it stays in it, every
process it starts later joins it automatically, and one call ends them all.

Only execution.py imports this, and only on Windows: ctypes.WinDLL does not exist elsewhere.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# Constants from the Windows SDK (winnt.h).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000  # closing the last handle ends every process
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1  # information classes for Set/QueryInformation
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001  # the rights AssignProcessToJobObject needs on the process
_PROCESS_SET_QUOTA = 0x0100


# The structures the two information classes above read and write, field for field as in
# winnt.h; only LimitFlags and ActiveProcesses are used, the rest only gives them their
# offsets.
class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


# use_last_error: ctypes saves GetLastError() right after each call, so _check can report
# the real Windows error instead of one a later call overwrote.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Argument and return types, so 64-bit handles are not truncated to a C int.
for _name, _args, _result in [
    ("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
    ("SetInformationJobObject",
     [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
    ("QueryInformationJobObject",
     [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD],
     wintypes.BOOL),
    ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
    ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
    ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
    ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
]:
    _function = getattr(_kernel32, _name)
    _function.argtypes, _function.restype = _args, _result


def _check(ok: object, what: str) -> None:
    """Raise OSError with Windows' own message if a call returned 0 / NULL."""
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


class Job:
    """One Job Object. Assign the child right after it starts, kill() after the run,
    close() at the end; close() also ends anything still running (KILL_ON_JOB_CLOSE),
    which covers this Python process crashing before kill()."""

    def __init__(self) -> None:
        self._handle = _kernel32.CreateJobObjectW(None, None)  # unnamed, default security
        _check(self._handle, "CreateJobObjectW")
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            _check(_kernel32.SetInformationJobObject(
                self._handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits)), "SetInformationJobObject")
        except OSError:
            self.close()
            raise

    def add(self, pid: int) -> None:
        """Put a running process into the job; the processes it starts from then on join
        too. A process it started before this call stays outside: the caller assigns the
        child at once, while it is still starting Python, long before it can start one."""
        process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        _check(process, "OpenProcess")
        try:
            _check(_kernel32.AssignProcessToJobObject(self._handle, process),
                   "AssignProcessToJobObject")
        finally:
            _kernel32.CloseHandle(process)

    def active(self) -> int:
        """How many processes in the job are still running."""
        info = _Accounting()
        _check(_kernel32.QueryInformationJobObject(
            self._handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info), None), "QueryInformationJobObject")
        return info.ActiveProcesses

    def kill(self, wait_s: float = 5.0) -> None:
        """End every process in the job and wait until they are gone. Termination is
        asynchronous, and a process that has not exited yet still holds its working
        directory open, so deleting the run's temp dir right away would fail."""
        _check(_kernel32.TerminateJobObject(self._handle, 1), "TerminateJobObject")
        deadline = time.monotonic() + wait_s
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.02)

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
