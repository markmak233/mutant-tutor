"""Small installed/module command dispatcher; command modules remain lazily imported."""

from __future__ import annotations

import argparse
import sys


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
    parser = argparse.ArgumentParser(prog="tutor", description="Local coding practice")
    parser.add_argument("command", choices=("test", "doctor", "gui", "shortcut", "python"))
    parser.parse_args(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
