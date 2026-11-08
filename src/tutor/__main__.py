"""Small installed/module command dispatcher; additional packaging commands come later."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """Keep test's own parser in charge of its options, including its help text."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "test":
        from tutor.test_command import main as test_main

        return test_main(args[1:])
    parser = argparse.ArgumentParser(prog="tutor", description="Local coding practice")
    parser.add_argument("command", choices=("test",))
    parser.parse_args(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
