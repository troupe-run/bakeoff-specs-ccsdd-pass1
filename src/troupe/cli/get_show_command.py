"""CLI read commands: ``troupe config get PATH`` and ``troupe config show [PATH] [--sources]``.

config-get-show 1.1-1.5, 2.1-2.7, 6.1-6.3, 6.5, 6.7, 6.8.

``get`` prints the scalar at PATH exactly as step 1's ``show PATH`` did and refuses a mapping or a
list with exit 5 and a hint to use ``config show`` (raised by the core ``get_value``). Its parser
is the standard :class:`~troupe.cli.shell.TroupeArgumentParser`, so a missing or surplus PATH and
any unknown option (including ``--sources``) are usage errors (exit 2).

``show`` prints whatever PATH names: a scalar without ``--sources`` exactly as ``get`` prints it
(2.1), and otherwise the node as block YAML (:func:`~troupe.cli.yaml_output.render_yaml`): a
mapping or list (2.2), the whole configuration when PATH is omitted (2.3), or a scalar alone as a
one-document YAML scalar (2.6). With ``--sources`` (before or after PATH, 2.7) every line holding
a scalar ends with `` # from <file>``, the file labelled relative to the theatre directory (5.1,
5.4). A missing PATH exits 5 (2.4); a surplus PATH, an unknown option or a malformed PATH exits 2
(2.5).

Both handlers are buffered: on any error stdout stays empty (6.7). The theatre is opened the default
way, so a never-imported theatre is imported and an imported one is reconciled before PATH is
resolved (6.2, 6.3); the ``with`` block closes the history store (6.8). Nothing here writes a file
(6.5): this module imports nothing from ``troupe.config.writeback``.

Imports only :mod:`troupe.cli.shell` and :mod:`troupe.cli.render` from the CLI package (never
``troupe.cli.main``, which imports this module).
"""

import argparse
import io

from troupe.cli.render import format_show_output, source_label
from troupe.cli.shell import (
    Handler,
    SubcommandRegistrar,
    TroupeArgumentParser,
    theatre_option_parent,
)
from troupe.cli.yaml_output import SourceLabeler, render_yaml
from troupe.config.model import ConfigScalar, SourceFile
from troupe.config.paths import ConfigPath
from troupe.config.theatre import get_value, node_at, open_theatre


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


def handle_show(args: argparse.Namespace, buffer: io.BytesIO) -> None:
    """Handler of ``troupe config show [PATH] [--sources]``: print the value or section at PATH.

    PATH (when given) is parsed before the theatre is opened, so a malformed PATH (exit 2) creates
    no history state (2.5). A scalar without ``--sources`` is printed exactly as ``get`` prints it
    (2.1); everything else is the YAML output, labelled with source files under ``--sources``
    (2.2, 2.3, 2.6). Every error propagates to the foundation's ``run``.
    """
    path = None if args.path is None else ConfigPath.parse(args.path)
    with open_theatre(args.theatre) as theatre:
        node = node_at(theatre, path)
        if isinstance(node, ConfigScalar) and not args.sources:
            buffer.write(format_show_output(node.value))
            return
        label: SourceLabeler | None = None
        if args.sources:
            directory = theatre.directory

            def label(file: SourceFile) -> str:
                return source_label(file, directory)

        buffer.write(render_yaml(node, label=label))


def add_show_subparser(subparsers: "argparse._SubParsersAction[TroupeArgumentParser]") -> None:
    """Register ``show [PATH] [--sources]`` with the shared ``--theatre`` option."""
    parser = subparsers.add_parser(
        "show",
        parents=[theatre_option_parent()],
        help="print the value or section at PATH as YAML (default: the whole configuration)",
        description=(
            "Print the value, mapping or list at PATH (the whole configuration when PATH is "
            "omitted) as YAML with every include resolved. A scalar is printed as 'get' prints it "
            "unless --sources is given."
        ),
    )
    parser.add_argument(
        "path",
        metavar="PATH",
        nargs="?",
        default=None,
        help="dotted path, e.g. agents or items.0 (default: the whole configuration)",
    )
    parser.add_argument(
        "--sources",
        action="store_true",
        help="annotate each value with the file it comes from",
    )
    parser.set_defaults(handler=handle_show)


# Static conformance to the foundation's protocols (checked by ``mypy --strict``).
_REGISTRAR: SubcommandRegistrar = add_get_subparser
_HANDLER: Handler = handle_get
_SHOW_REGISTRAR: SubcommandRegistrar = add_show_subparser
_SHOW_HANDLER: Handler = handle_show
