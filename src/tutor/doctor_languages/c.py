"""C-specific ASan machine warning, host practice limits and container-only validation."""

import sys
from collections.abc import Callable
from pathlib import Path

from tutor.doctor_languages import LanguageDoctor
from tutor.doctor_rows import Row


def machine_checks(run: Callable) -> list[Row]:
    """A container shares Linux's kernel; too much address randomisation can hang ASan."""
    if not sys.platform.startswith("linux"):
        return []
    try:
        bits = int(Path("/proc/sys/vm/mmap_rnd_bits").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return [Row("SKIP", "C AddressSanitizer: mmap_rnd_bits is unavailable")]
    if bits > 28:
        return [Row("WARN", f"C AddressSanitizer may hang with mmap_rnd_bits = {bits}",
                    "sudo sysctl -w vm.mmap_rnd_bits=28 (reduces address randomisation; "
                    "gcc 12 AddressSanitizer may hang above 28; see docs/design.md)")]
    return [Row("OK", f"C AddressSanitizer: mmap_rnd_bits = {bits}")]


def platform_problem(platform: str, runner: str | None) -> str | None:
    """Only host student tests are restricted here; validation has its own policy."""
    if platform == "win32" and runner != "container":
        return 'C runs in WSL or in the container (docs/design.md, "C")'
    return None


DOCTOR = LanguageDoctor("c", "station-above-average-c", machine_checks, platform_problem,
                        validation_needs_container=True)
