"""Entry point of the ``troupe`` console script: the ``config`` command group.

Scaffold only: subcommands, error mapping and output buffering are added by later tasks.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn


def build_parser() -> argparse.ArgumentParser:
    """Build the ``troupe`` parser with its ``config`` command group."""
    parser = argparse.ArgumentParser(prog="troupe")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config", help="inspect a theatre's configuration")
    return parser


def run(argv: Sequence[str]) -> int:
    """Parse ``argv`` and return the process exit code."""
    build_parser().parse_args(argv)
    return 0


def main() -> NoReturn:
    """Console-script entry point."""
    sys.exit(run(sys.argv[1:]))
