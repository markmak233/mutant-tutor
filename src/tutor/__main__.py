"""Small installed/module command dispatcher; additional packaging commands come later."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """Each command owns its options and help; imports stay lazy for a small dispatcher."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "doctor":
        from tutor.doctor import main as doctor_main

        return doctor_main(args[1:])
    if args and args[0] == "test":
        from tutor.test_command import main as test_main

        return test_main(args[1:])
    parser = argparse.ArgumentParser(prog="tutor", description="Local coding practice")
    parser.add_argument("command", choices=("test", "doctor"))
    parser.parse_args(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
