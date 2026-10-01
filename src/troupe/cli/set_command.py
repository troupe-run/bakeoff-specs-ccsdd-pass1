"""CLI ``troupe config set PATH VALUE``: registration and handler (1.1, 1.5, 1.6, 16.1, 16.2, 16.4).

Imports only :mod:`troupe.cli.shell` from the CLI package (never ``troupe.cli.main``, which imports
this module), so the import chain is ``troupe.cli.shell <- set_command <- troupe.cli.main``.

VALUEs starting with ``-``: plain argparse takes ``-1e3``, ``-.inf`` or ``-x`` for (unknown)
options and exits 2. The ``set`` parser therefore treats an argument as an option only when it
starts with ``--`` (``--theatre DIR``, ``--theatre=DIR``, ``--help``, or an unknown ``--bogus``,
which still exits 2) or is exactly ``-h``; every other argument starting with a single ``-`` is a
positional PATH or VALUE. A VALUE spelled like a long option (e.g. ``--theatre``) or ``-h`` is
still read as the option (conservative: ``set a --theatre`` exits 2 rather than silently writing
the text ``--theatre``); such a VALUE is given after ``--``, e.g. ``set a -- --theatre``.
"""

import argparse
import io
from typing import Any

from troupe.cli.shell import (
    Handler,
    SubcommandRegistrar,
    TroupeArgumentParser,
    theatre_option_parent,
)
from troupe.config.paths import ConfigPath
from troupe.config.theatre import open_theatre
from troupe.config.writeback.setter import set_value
from troupe.config.writeback.value import parse_value

_HELP_OPTION = "-h"


class _SetArgumentParser(TroupeArgumentParser):
    """The ``set`` parser: a single-dash argument other than ``-h`` is a positional."""

    # The return type of argparse's private ``_parse_optional`` differs between patch releases
    # (a tuple or a list of tuples); this override only adds ``None`` (= positional) and otherwise
    # returns the base result unchanged, so it is typed ``Any``.
    def _parse_optional(self, arg_string: str) -> Any:
        single_dash = arg_string.startswith("-") and not arg_string.startswith("--")
        if single_dash and arg_string != _HELP_OPTION:
            return None
        return super()._parse_optional(arg_string)


def handle_set(args: argparse.Namespace, out: io.BytesIO) -> None:
    """Handler of ``troupe config set PATH VALUE``: change the value at PATH; print nothing.

    PATH and VALUE are parsed before the theatre is opened, so their usage errors (exit 2) create
    no history state; the theatre is opened (and imported, 1.6) in a ``with`` block so the history
    store is closed when the command ends. Every error propagates to the foundation's ``run``.
    """
    path = ConfigPath.parse(args.path)
    value = parse_value(args.value, path)
    with open_theatre(args.theatre) as theatre:
        set_value(theatre, path, value)


def add_set_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``set PATH VALUE`` with the shared ``--theatre`` option."""
    parser = subparsers.add_parser(
        "set",
        parents=[theatre_option_parent()],
        help="set the value at PATH to VALUE",
        description=(
            "Set the value at PATH to VALUE (one YAML flow node) by editing the configuration "
            "files in place. A VALUE spelled like an option (e.g. --theatre) goes after '--'."
        ),
    )
    # Same parser, with the leading-dash rule above (no new state: only a method is overridden).
    parser.__class__ = _SetArgumentParser
    parser.add_argument("path", metavar="PATH", help="dotted path, e.g. agents.lead or items.0")
    parser.add_argument("value", metavar="VALUE", help="YAML flow value, e.g. 0.2, abc, [a, b]")
    parser.set_defaults(handler=handle_set)


# Static conformance to the foundation's protocols (checked by ``mypy --strict``).
_REGISTRAR: SubcommandRegistrar = add_set_subparser
_HANDLER: Handler = handle_set
