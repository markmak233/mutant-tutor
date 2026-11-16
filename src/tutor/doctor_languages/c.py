"""C-specific ASan machine warning, host practice limits and container-only validation."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tutor.config import WORKSPACE_FILE
from tutor.doctor_languages import LanguageDoctor
from tutor.doctor_rows import Row

MMAP_BITS = "/proc/sys/vm/mmap_rnd_bits"
# gcc 12's AddressSanitizer hangs now and then above this (67 of 300 runs at 32 on Docker
# Desktop's WSL 2 VM, 0 of 300 at 28; gcc 14 none at 32: docs/design.md, "C", 2026-11-16).
SAFE_BITS = 28
HANGING_GCC = "12"
DOCKER_TIMEOUT_S = 60  # one `cat` in an existing image; a sleeping Docker VM wakes first


def machine_checks(run: Callable, course=None) -> list[Row]:
    """Whether gcc 12's AddressSanitizer can hang here: only a course whose [requires] gcc
    is 12 can be affected, and the kernel that counts is the one its tests run on. With the
    container that is Docker's (a VM on macOS and Windows, which the host cannot read), so
    the value is read through Docker in the course's own image; on the host, from /proc."""
    if course is None or course.ws is None:
        return []  # no course: nothing says which gcc its C would use
    gcc = course.ws.effective_requires.get("gcc")
    if gcc is None or gcc.split(".")[0] != HANGING_GCC:
        return []  # gcc 14 (the default since 2026-11-16) does not hang at any value
    if course.runner == "container":
        bits, unread = _bits_in_image(course, run)
    elif sys.platform.startswith("linux"):
        bits, unread = _bits_on_host()
    else:
        return []  # host C elsewhere: Windows runs none (platform_problem), macOS has clang
    if bits is None:
        return [Row("SKIP", f"C AddressSanitizer: mmap_rnd_bits is unavailable ({unread})")]
    if bits > SAFE_BITS:
        fix = 'set gcc = "14" in workspace.toml [requires]'
        fix += (", then build the test image again" if course.runner == "container" else
                f" and install it; or sudo sysctl -w vm.mmap_rnd_bits={SAFE_BITS} (reduces "
                "address randomisation until the next restart)")
        return [Row("WARN", f"C AddressSanitizer may hang: gcc 12 with mmap_rnd_bits = {bits}",
                    fix + ' (docs/design.md, "C")')]
    return [Row("OK", f"C AddressSanitizer: mmap_rnd_bits = {bits}")]


def _bits_on_host() -> tuple[int | None, str]:
    """The host kernel's value, or None and why not. The file is root-only on some
    distributions (the owner's Ubuntu VM, 2026-11-16), so a normal user may not read it."""
    try:
        return int(Path(MMAP_BITS).read_text(encoding="utf-8").strip()), ""
    except PermissionError:
        return None, f"only root can read it here: sudo cat {MMAP_BITS}"
    except (OSError, ValueError):
        return None, f"{MMAP_BITS} cannot be read"


def _bits_in_image(course, run: Callable) -> tuple[int | None, str]:
    """The value inside the course's built image, as root (the file is root-only there too).
    The command is fixed and reads one file: no exercise code runs, there is no network, and
    nothing is pulled or built; a missing image or a stopped Docker only skips the row."""
    from tutor import container  # doctor's import of the language registry stays light

    image = container.image_for([course.root / WORKSPACE_FILE])
    try:
        done = run(["docker", "run", "--rm", "--pull", "never", "--network", "none",
                    "--user", "0", "--entrypoint", "cat", image, MMAP_BITS],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=DOCKER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None, "Docker did not answer"
    if done.returncode != 0:
        return None, "build the test image first, with Docker running"
    try:
        return int(done.stdout.strip()), ""
    except ValueError:
        return None, "the image printed no number"


def platform_problem(platform: str, runner: str | None) -> str | None:
    """Only host student tests are restricted here; validation has its own policy."""
    if platform == "win32" and runner != "container":
        return 'C runs in WSL or in the container (docs/design.md, "C")'
    return None


DOCTOR = LanguageDoctor("c", "station-above-average-c", machine_checks, platform_problem,
                        validation_needs_container=True)
