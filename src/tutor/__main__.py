"""Small installed/module command dispatcher; command modules remain lazily imported."""

from __future__ import annotations

import argparse
import sys

# What `tutor --help` says of each command, in the order it lists them. Kept here, not read
# from the modules, so that help imports none of them (a GUI or Docker import is slow).
COMMANDS = {
    "gui": "start the pages in your browser (or open the running ones)",
    "shortcut": "put a launcher for the pages on your desktop",
    "doctor": "check what this computer and a course have and lack",
    "test": "run one exercise's tests in the course's container",
    "python": "list or install a Python for courses, through uv",
}


def main(argv: list[str] | None = None) -> int:
    """Each command owns its options and help; imports stay lazy for a small dispatcher."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "python":
        from tutor.python_manager import main as python_main

        return python_main(args[1:])
    if args and args[0] == "gui":
        from tutor.gui.__main__ import main as gui_main

        return gui_main(args[1:])
    if args and args[0] == "shortcut":
        from tutor.shortcut import main as shortcut_main

        return shortcut_main(args[1:])
    if args and args[0] == "doctor":
        from tutor.doctor import main as doctor_main

        return doctor_main(args[1:])
    if args and args[0] == "test":
        from tutor.test_command import main as test_main

        return test_main(args[1:])
    # No known command (or --help): argparse lists the commands with a line each, then
    # exits 0 for --help or 2 with its usage for anything else.
    parser = argparse.ArgumentParser(
        prog="tutor", description="Local coding practice",
        epilog="`tutor COMMAND --help` gives a command's own options.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for name, purpose in COMMANDS.items():
        commands.add_parser(name, help=purpose)
    parser.parse_args(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
