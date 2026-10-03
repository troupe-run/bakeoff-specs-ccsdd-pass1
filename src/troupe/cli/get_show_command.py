"""CLI read commands: ``troupe config get PATH`` (config-get-show 1.1-1.5, 6.1-6.3, 6.5, 6.7, 6.8).

``get`` prints the scalar at PATH exactly as step 1's ``show PATH`` did and refuses a mapping or a
list with exit 5 and a hint to use ``config show`` (raised by the core ``get_value``). Its parser
is the standard :class:`~troupe.cli.shell.TroupeArgumentParser`, so a missing or surplus PATH and
any unknown option (including ``--sources``) are usage errors (exit 2).

The handler is buffered: on any error stdout stays empty (6.7). The theatre is opened the default
way, so a never-imported theatre is imported and an imported one is reconciled before PATH is
resolved (6.2, 6.3); the ``with`` block closes the history store (6.8). Nothing here writes a file
(6.5): this module imports nothing from ``troupe.config.writeback``.

Imports only :mod:`troupe.cli.shell` and :mod:`troupe.cli.render` from the CLI package (never
``troupe.cli.main``, which imports this module).
"""

import argparse
import io

from troupe.cli.render import format_show_output
from troupe.cli.shell import (
    Handler,
    SubcommandRegistrar,
    TroupeArgumentParser,
    theatre_option_parent,
)
from troupe.config.paths import ConfigPath
from troupe.config.theatre import get_value, open_theatre


def handle_get(args: argparse.Namespace, buffer: io.BytesIO) -> None:
    """Handler of ``troupe config get PATH``: print the scalar at PATH.

    PATH is parsed before the theatre is opened, so a malformed PATH (exit 2) creates no history
    state (1.5). Every error propagates to the foundation's ``run``.
    """
    path = ConfigPath.parse(args.path)
    with open_theatre(args.theatre) as theatre:
        buffer.write(format_show_output(get_value(theatre, path)))


def add_get_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``get PATH`` with the shared ``--theatre`` option."""
    parser = subparsers.add_parser(
        "get",
        parents=[theatre_option_parent()],
        help="print the value at PATH",
        description=(
            "Print the scalar value at PATH as written in the configuration files. "
            "Mappings and lists are printed by 'show'."
        ),
    )
    parser.add_argument("path", metavar="PATH", help="dotted path, e.g. agents.lead or items.0")
    parser.set_defaults(handler=handle_get)


# Static conformance to the foundation's protocols (checked by ``mypy --strict``).
_REGISTRAR: SubcommandRegistrar = add_get_subparser
_HANDLER: Handler = handle_get
