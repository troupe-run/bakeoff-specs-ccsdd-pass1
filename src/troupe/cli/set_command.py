"""CLI ``troupe config set PATH VALUE [--reason TEXT] [--in-include]``: registration and handler.

Requirements: config-set-writeback 1.1, 1.5, 1.6, 16.1, 16.2, 16.4; config-lineage 6.1-6.5;
config-include-overrides 10.1-10.7.

Imports only :mod:`troupe.cli.shell` from the CLI package (never ``troupe.cli.main``, which imports
this module), so the import chain is ``troupe.cli.shell <- set_command <- troupe.cli.main``.

VALUEs starting with ``-``: plain argparse takes ``-1e3``, ``-.inf`` or ``-x`` for (unknown)
options and exits 2. The ``set`` parser therefore treats an argument as an option only when it
starts with ``--`` (``--theatre DIR``, ``--theatre=DIR``, ``--help``, or an unknown ``--bogus``,
which still exits 2) or is exactly ``-h``; every other argument starting with a single ``-`` is a
positional PATH or VALUE. A VALUE spelled like a long option (e.g. ``--theatre``) or ``-h`` is
still read as the option (conservative: ``set a --theatre`` exits 2 rather than silently writing
the text ``--theatre``); such a VALUE is given after ``--``, e.g. ``set a -- --theatre``.

``--reason TEXT`` (config-lineage 6.1-6.5) is a plain argparse option under the same rule: it may
stand before, between or after PATH and VALUE, also as ``--reason=TEXT``; the last one given counts
(argparse ``store``); TEXT is kept exactly, including an empty text. ``--reason`` with no TEXT
after it (at the end, or followed by another ``--`` option or ``-h``) is argparse's usage error,
exit 2, before any state is touched. A TEXT starting with a single ``-`` other than ``-h`` is
taken as TEXT (``--reason -x``), but one spelled like an option (``-h``, ``--theatre``) is not; the
documented, always-safe form for any TEXT starting with ``-`` is ``--reason=TEXT``, e.g.
``--reason=-h``. The reason goes into the cli context of the set's own batch only, so the
reconcile events recorded while the theatre is opened keep a ``null`` reason (6.3).

``--in-include`` (config-include-overrides Requirement 10, CR-B) is an argparse ``store_true`` flag
under the same rule: it takes no value and may stand before, between or after PATH and VALUE,
together with ``--reason`` (given more than once it is still just on). With it, a new key is
written by the step 1 placement (into the included side of the deepest existing ancestor, no
conversion) instead of as an override; existing values, appends and removals do not depend on it.
``--in-include=TEXT`` is argparse's usage error ("ignored explicit argument"), exit 2, raised while
parsing, so no file is read or changed and no history state is created or changed (10.6).
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
from troupe.config.lineage import cli_context
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

    The set's own batch is recorded with ``cli_context(args.actor, args.reason)``: the command's
    actor and its ``--reason`` TEXT (``None`` without the option, config-lineage 6.1, 6.2).
    ``args.in_include`` (``--in-include``) is passed to :func:`set_value` as the placement choice
    for new keys (config-include-overrides 10.1-10.5).

    PATH and VALUE are parsed before the theatre is opened, so their usage errors (exit 2) create
    no history state; the theatre is opened (and imported, 1.6) in a ``with`` block so the history
    store is closed when the command ends. Every error propagates to the foundation's ``run``.
    """
    path = ConfigPath.parse(args.path)
    value = parse_value(args.value, path)
    with open_theatre(args.theatre) as theatre:
        set_value(
            theatre,
            path,
            value,
            cli_context(args.actor, args.reason),
            in_include=args.in_include,
        )


def add_set_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``set PATH VALUE`` with ``--theatre``, ``--reason TEXT`` and ``--in-include``."""
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
    parser.add_argument(
        "--reason",
        metavar="TEXT",
        default=None,
        help=(
            "why the change is made, recorded with its history events (the last one counts; "
            "give a TEXT starting with '-' as --reason=TEXT)"
        ),
    )
    parser.add_argument(
        "--in-include",
        action="store_true",
        help=(
            "write a new key into the included file holding its deepest existing parent "
            "mapping instead of as an override in the including file"
        ),
    )
    parser.set_defaults(handler=handle_set)


# Static conformance to the foundation's protocols (checked by ``mypy --strict``).
_REGISTRAR: SubcommandRegistrar = add_set_subparser
_HANDLER: Handler = handle_set
