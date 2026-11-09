"""Small immutable diagnostic rows, shared without importing the command or registry."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    """An actionable failure/warning, or a successful/skipped observation."""

    status: str
    text: str
    next_step: str = ""
