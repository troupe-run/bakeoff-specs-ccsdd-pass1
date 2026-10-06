"""Unit tests of the alias guard: refuse, expand or proceed for a set (config-aliases task 3.3).

The guard (``troupe.config.writeback.aliases``) walks PATH from the root to its node (or to the
deepest existing ancestor of a PATH that does not exist yet) and, for a collection replacement, the
changed parts of the subtree at PATH. The first alias place met gives an expansion site with
``--expand-alias`` and the alias refusal without it; the first merged entry met gives the
merge-key refusal either way; nothing met means proceed. ``anchor_uses`` indexes every anchored
node's written location with the aliases that use it.

Each case loads a real theatre configuration from disk and resolves PATH with the real
TargetResolver; every refusal's message is asserted exactly.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

from pathlib import Path

import pytest

from support import MakeTheatre
from troupe.config.errors import ExitCode, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, ConfigMapping, ConfigNode, ConfigSequence
from troupe.config.paths import ConfigPath
from troupe.config.writeback.aliases import AliasSite, alias_sites, anchor_uses, anchored_location
from troupe.config.writeback.target import resolve_target
from troupe.config.writeback.value import parse_value

SHARED = (
    "defaults: &d\n"
    "  model: opus\n"
    "  t: 1\n"
    "coder:\n"
    "  settings: *d\n"
    "  name: c\n"
    "reviewer:\n"
    "  <<: *d\n"
    "  t: 2\n"
)
"""`defaults` is anchored as `&d`, used by a plain alias (`coder.settings`) and by a merge key with
a shadowing own key `t` (`reviewer`)."""

NESTED = "base: &e\n  x: 1\ndefaults: &d\n  inner: *e\n  y: 2\ncoder:\n  settings: *d\n"
"""An alias `*e` inside the anchored node `&d`, which is itself aliased at `coder.settings`."""


def _alias_problem(name: str) -> str:
    return (
        f"value is shared through alias '*{name}' (anchor '&{name}'); setting it here would change "
        "every place that uses the anchor; use --expand-alias to give this place its own copy"
    )


def _merge_problem(key_path: str) -> str:
    return (
        f"value comes from merge key '<<' at {key_path}; merge keys are never written, "
        "and --expand-alias cannot expand them"
    )


def _document(
    make_theatre: MakeTheatre, text: str, files: dict[str, str] | None = None
) -> ConfigDocument:
    return load_config(make_theatre(text, files=files) / ".troupe" / "troupe.yml")


def _sites(
    document: ConfigDocument, path: str, value: str, *, expand: bool
) -> tuple[AliasSite, ...]:
    config_path = ConfigPath.parse(path)
    request = resolve_target(document, config_path, parse_value(value, config_path))
    return alias_sites(document, request, expand=expand)


def _refusal(document: ConfigDocument, path: str, value: str, *, expand: bool) -> WriteRefusedError:
    with pytest.raises(WriteRefusedError) as raised:
        _sites(document, path, value, expand=expand)
    assert raised.value.exit_code is ExitCode.WRITE_REFUSED
    return raised.value


def _node_at(document: ConfigDocument, path: str) -> ConfigNode:
    node: ConfigNode = document.root
    for segment in path.split("."):
        if isinstance(node, ConfigSequence):
            node = node.items[int(segment)]
            continue
        assert isinstance(node, ConfigMapping)
        entry = node.get(segment)
        assert entry is not None
        node = entry.value
    return node


def _root_file(document: ConfigDocument) -> Path:
    return document.root_file.path


def _token_text(site: AliasSite) -> str:
    alias = site.place.alias
    assert alias is not None
    return alias.token.file.text[alias.token.span.start : alias.token.span.end]


def test_path_through_an_alias_is_refused_naming_the_alias(make_theatre: MakeTheatre) -> None:
    """Req 5.1: `coder.settings.model` is reached through `*d`; without `--expand-alias` the set
    is refused (exit 3) naming the alias's file, PATH and `*d`, saying the value is shared and
    mentioning `--expand-alias`."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "coder.settings.model", "sonnet", expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "coder.settings.model",
        _alias_problem("d"),
    )
    assert str(error) == f"{_root_file(document)}: coder.settings.model: {_alias_problem('d')}"


def test_no_op_through_an_alias_is_still_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.1 (choice 17): a VALUE equal to the current value at a PATH reached through an alias
    is refused all the same."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "coder.settings.model", "opus", expand=False)
    assert error.problem == _alias_problem("d")


def test_path_through_an_alias_with_the_flag_gives_the_alias_site(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1, 8.1: with `--expand-alias` the alias on PATH is returned as the expansion site: the
    alias place, its parent, its key and its path."""
    document = _document(make_theatre, SHARED)
    sites = _sites(document, "coder.settings.model", "sonnet", expand=True)
    coder = _node_at(document, "coder")
    assert len(sites) == 1
    (site,) = sites
    assert site.place is _node_at(document, "coder.settings")
    assert site.parent is coder
    assert (site.key, str(site.path), _token_text(site)) == ("settings", "coder.settings", "*d")


def test_the_alias_own_key_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.1 (choice 2): a PATH naming the alias itself (`coder.settings`) is reached through
    `*d` and refused; with the flag it is the site."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "coder.settings", "{model: x}", expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "coder.settings",
        _alias_problem("d"),
    )
    (site,) = _sites(document, "coder.settings", "{model: x}", expand=True)
    assert str(site.path) == "coder.settings"


def test_deepest_existing_ancestor_through_an_alias_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1: a new key `coder.settings.extra.deep`, whose deepest existing ancestor
    `coder.settings` is the alias place, is refused; with the flag the alias is the site."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "coder.settings.extra.deep", "1", expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "coder.settings.extra.deep",
        _alias_problem("d"),
    )
    (site,) = _sites(document, "coder.settings.extra.deep", "1", expand=True)
    assert str(site.path) == "coder.settings"


def test_an_alias_place_holding_an_include_gives_the_alias_refusal_first(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1: `x` is an alias of the anchored include string `&i file:inc.yml`; replacing the
    include root (which the planner would also refuse) or setting inside it gives the alias
    refusal, which comes before any planner refusal."""
    document = _document(
        make_theatre,
        "i: &i file:inc.yml\nx: *i\n",
        files={".troupe/inc.yml": "k: v\n"},
    )
    for path, value in (("x", "plain"), ("x.k", "w")):
        error = _refusal(document, path, value, expand=False)
        assert (error.file, error.path, error.problem) == (
            _root_file(document),
            path,
            _alias_problem("i"),
        )
    (site,) = _sites(document, "x.k", "w", expand=True)
    assert site.place is _node_at(document, "x")
    assert _token_text(site) == "*i"


def test_an_alias_as_a_ref_value_is_seen_inside_the_include_form(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1: `r` is a mapping-form include whose `$ref` value is the alias `*i`; the alias is
    recorded on the `$ref` scalar of the form, and a PATH through `r` is refused naming `*i`. With
    the flag the site is that `$ref` scalar in the form."""
    document = _document(
        make_theatre,
        "i: &i file:inc.yml\nr:\n  $ref: *i\n  j: 2\n",
        files={".troupe/inc.yml": "k: v\n"},
    )
    error = _refusal(document, "r.k", "w", expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "r.k",
        _alias_problem("i"),
    )
    (site,) = _sites(document, "r.k", "w", expand=True)
    r = _node_at(document, "r")
    assert r.include is not None and r.include.form is not None
    assert site.parent is r.include.form
    assert (site.key, str(site.path), _token_text(site)) == ("$ref", "r.$ref", "*i")


def test_an_alias_in_the_overrides_of_a_merged_mapping_is_seen(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.1: `r.x` merges the included `x` with the override `x: *d`; the merged mapping keeps
    the included side's provenance, so the guard looks at the override side too and refuses
    `r.x.q` naming `*d`."""
    document = _document(
        make_theatre,
        "d: &d\n  q: 2\nr:\n  $ref: file:inc.yml\n  x: *d\n",
        files={".troupe/inc.yml": "x:\n  p: 1\n"},
    )
    error = _refusal(document, "r.x.q", "3", expand=False)
    assert (error.path, error.problem) == ("r.x.q", _alias_problem("d"))
    (site,) = _sites(document, "r.x.q", "3", expand=True)
    assert _token_text(site) == "*d"
    assert str(site.path) == "r.x"


def test_an_override_key_behind_an_aliased_ref_proceeds(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 6.3: `r.j` is written in `r`'s own overrides, not in the file `*i` includes, so it
    is not reached through `*i`: the guard finds nothing, with or without the flag. A new key of
    `r`, whose deepest existing ancestor is the include root, still counts `*i`."""
    document = _document(
        make_theatre,
        "i: &i file:inc.yml\nr:\n  $ref: *i\n  j: 2\n",
        files={".troupe/inc.yml": "k: v\n"},
    )
    for expand in (False, True):
        assert _sites(document, "r.j", "3", expand=expand) == ()
    error = _refusal(document, "r.new", "1", expand=False)
    assert (error.path, error.problem) == ("r.new", _alias_problem("i"))


def test_an_included_key_beside_an_aliased_override_proceeds(make_theatre: MakeTheatre) -> None:
    """Req 5.1, 6.3: in `r.x`, merged from the included `x: {p: 1}` and the override `x: *d`,
    `r.x.p` comes from the included file, not through `*d`: the guard finds nothing, with or
    without the flag."""
    document = _document(
        make_theatre,
        "d: &d\n  q: 2\nr:\n  $ref: file:inc.yml\n  x: *d\n",
        files={".troupe/inc.yml": "x:\n  p: 1\n"},
    )
    for expand in (False, True):
        assert _sites(document, "r.x.p", "5", expand=expand) == ()
    error = _refusal(document, "r.x", "{p: 1, q: 3}", expand=False)
    assert (error.path, error.problem) == ("r.x", _alias_problem("d"))


def test_merge_key_on_the_included_side_of_a_merged_mapping(make_theatre: MakeTheatre) -> None:
    """Req 9.2, 9.3: the included `m` has `<<: *d` and an own key `c`, and the override adds
    `m: {b: 2}`. `r.m.a` comes from the merge key (refused, naming the included file and
    `r.m.<<`), while `r.m.b` (the override) and `r.m.c` (an own key) proceed."""
    theatre = make_theatre(
        "r:\n  $ref: file:inc.yml\n  m:\n    b: 2\n",
        files={".troupe/inc.yml": "d: &d\n  a: 1\nm:\n  <<: *d\n  c: 3\n"},
    )
    document = load_config(theatre / ".troupe" / "troupe.yml")
    for expand in (False, True):
        error = _refusal(document, "r.m.a", "5", expand=expand)
        assert (error.file, error.path, error.problem) == (
            theatre / ".troupe" / "inc.yml",
            "r.m.a",
            _merge_problem("r.m.<<"),
        )
        assert _sites(document, "r.m.b", "5", expand=expand) == ()
        assert _sites(document, "r.m.c", "5", expand=expand) == ()
    error = _refusal(document, "r.m", "{b: 2, c: 3}", expand=False)
    assert (error.path, error.problem) == ("r.m", _merge_problem("r.m.<<"))
    assert _sites(document, "r", "{d: {a: 1}, m: {a: 1, b: 7, c: 3}}", expand=False) == ()


def test_merged_entry_is_refused_without_the_flag(make_theatre: MakeTheatre) -> None:
    """Req 9.2: `reviewer.model` comes from the merge key `<<: *d`; the set is refused (exit 3)
    naming the file, PATH and `<<`, saying merge keys are never written."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "reviewer.model", "sonnet", expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "reviewer.model",
        _merge_problem("reviewer.<<"),
    )


def test_merged_entry_is_refused_with_the_flag(make_theatre: MakeTheatre) -> None:
    """Req 9.2: `--expand-alias` cannot expand a merge key, so the same refusal is given with the
    flag, also for a new key below a merged entry's mapping."""
    document = _document(
        make_theatre, "defaults: &d\n  m:\n    a: 1\nreviewer:\n  <<: *d\n  t: 2\n"
    )
    for path in ("reviewer.m", "reviewer.m.a", "reviewer.m.b"):
        error = _refusal(document, path, "5", expand=True)
        assert (error.file, error.path, error.problem) == (
            _root_file(document),
            path,
            _merge_problem("reviewer.<<"),
        )


def test_own_keys_of_a_mapping_with_a_merge_key_proceed(make_theatre: MakeTheatre) -> None:
    """Req 6.3, 9.2: a key written in the mapping itself (`reviewer.t`) and a new key of that
    mapping are not reached through the merge key, so the guard finds nothing."""
    document = _document(make_theatre, SHARED)
    for path in ("reviewer.t", "reviewer.extra"):
        for expand in (False, True):
            assert _sites(document, path, "7", expand=expand) == ()


def test_replacement_changing_an_alias_below_path_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.2, 8.2: replacing `coder` with a mapping that changes `settings.model` would change a
    value reached through `*d` below PATH: refused without the flag, the alias site with it."""
    document = _document(make_theatre, SHARED)
    value = "{settings: {model: x, t: 1}, name: c}"
    error = _refusal(document, "coder", value, expand=False)
    assert (error.file, error.path, error.problem) == (
        _root_file(document),
        "coder",
        _alias_problem("d"),
    )
    (site,) = _sites(document, "coder", value, expand=True)
    assert site.place is _node_at(document, "coder.settings")
    assert (site.key, str(site.path)) == ("settings", "coder.settings")


def test_replacement_changing_a_scalar_alias_or_adding_to_an_alias_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.2: a replacement below PATH that changes a scalar alias place (`c.v: *n`), or adds a
    key to a mapping alias place (`coder.settings: *d`), changes a value reached through the
    alias, so it is refused naming that alias."""
    document = _document(make_theatre, SHARED + "n: &n 1\nc:\n  v: *n\n  w: 2\n")
    error = _refusal(document, "c", "{v: 5, w: 2}", expand=False)
    assert (error.path, error.problem) == ("c", _alias_problem("n"))
    assert _sites(document, "c", "{v: 1, w: 3}", expand=False) == ()
    value = "{settings: {model: opus, t: 1, new: 1}, name: c}"
    error = _refusal(document, "coder", value, expand=False)
    assert (error.path, error.problem) == ("coder", _alias_problem("d"))


def test_replacement_removing_an_alias_below_path_is_refused(make_theatre: MakeTheatre) -> None:
    """Req 5.2: a replacement of `coder` that drops `settings` would remove a value reached
    through `*d`, so it is refused too."""
    document = _document(make_theatre, SHARED)
    error = _refusal(document, "coder", "{name: c}", expand=False)
    assert (error.path, error.problem) == ("coder", _alias_problem("d"))
    error = _refusal(document, "coder", "scalar", expand=False)
    assert (error.path, error.problem) == ("coder", _alias_problem("d"))


def test_replacement_leaving_an_alias_unchanged_proceeds(make_theatre: MakeTheatre) -> None:
    """Req 5.2, 8.2: a replacement of `coder` that changes `name` and adds a key but leaves every
    value of `settings` unchanged ignores the alias: the guard finds nothing."""
    document = _document(make_theatre, SHARED)
    value = "{settings: {model: opus, t: 1}, name: other, extra: [1]}"
    for expand in (False, True):
        assert _sites(document, "coder", value, expand=expand) == ()


def test_replacement_changing_a_merged_entry_below_path_is_refused(
    make_theatre: MakeTheatre,
) -> None:
    """Req 9.2: replacing `reviewer` with a mapping that drops the merged `model` would change a
    value received from the merge key: the merge-key refusal, with or without the flag."""
    document = _document(make_theatre, SHARED)
    for expand in (False, True):
        error = _refusal(document, "reviewer", "{t: 3}", expand=expand)
        assert (error.path, error.problem) == ("reviewer", _merge_problem("reviewer.<<"))
    assert _sites(document, "reviewer", "{model: opus, t: 3}", expand=False) == ()


def test_nested_aliases_give_the_outermost_site_first(make_theatre: MakeTheatre) -> None:
    """Req 8.1: `coder.settings.inner.x` passes through `*d` and then `*e`; the refusal names the
    outermost alias `*d` and the site is `coder.settings`. At the anchor, `defaults.inner.x` is
    reached through `*e` only."""
    document = _document(make_theatre, NESTED)
    error = _refusal(document, "coder.settings.inner.x", "5", expand=False)
    assert (error.path, error.problem) == ("coder.settings.inner.x", _alias_problem("d"))
    (site,) = _sites(document, "coder.settings.inner.x", "5", expand=True)
    assert (str(site.path), _token_text(site)) == ("coder.settings", "*d")

    error = _refusal(document, "defaults.inner.x", "5", expand=False)
    assert (error.path, error.problem) == ("defaults.inner.x", _alias_problem("e"))
    (site,) = _sites(document, "defaults.inner.x", "5", expand=True)
    assert (str(site.path), _token_text(site)) == ("defaults.inner", "*e")


def test_nested_aliases_below_path_give_the_outermost_site_first(
    make_theatre: MakeTheatre,
) -> None:
    """Req 8.2: a replacement of `coder` changing `settings.inner.x` changes values through `*d`
    and `*e`; the search does not descend below `*d`, so the site is `coder.settings`."""
    document = _document(make_theatre, NESTED)
    (site,) = _sites(document, "coder", "{settings: {inner: {x: 9}, y: 2}}", expand=True)
    assert (str(site.path), _token_text(site)) == ("coder.settings", "*d")


def test_path_with_no_alias_proceeds(make_theatre: MakeTheatre) -> None:
    """Req 6.3: PATHs defined at an anchor (`defaults.model`, a new key `defaults.extra`, the
    anchored node itself) and PATHs with no alias give no site, with or without the flag."""
    document = _document(make_theatre, SHARED + "plain:\n  a: 1\n")
    cases = (
        ("defaults.model", "sonnet"),
        ("defaults.extra", "1"),
        ("defaults", "{model: x, t: 1}"),
        ("plain.a", "2"),
        ("plain.b.c", "2"),
        ("coder.name", "z"),
    )
    for path, value in cases:
        for expand in (False, True):
            assert _sites(document, path, value, expand=expand) == ()


def test_anchor_uses_index_each_anchored_node_with_its_aliases(
    make_theatre: MakeTheatre,
) -> None:
    """Req 4.6: `anchor_uses` maps the written location of every used anchored node to the
    aliases that use it, in document order: plain aliases, a merge key's alias, a list item, an
    alias inside an anchored node (once, not again at each alias place of that node), the aliases
    of an anchored include string (as a value and as the `$ref` value of an include with
    overrides, whose `$ref` mapping is also its override side) and the alias of an anchored
    `$ref` mapping. The location of an anchored include is where its anchor is written: the
    include string, or the `$ref` mapping."""
    document = _document(
        make_theatre,
        "e: &e 1\n"
        "d: &d\n"
        "  inner: *e\n"
        "a: *d\n"
        "m:\n"
        "  <<: *d\n"
        "l:\n"
        "  - *d\n"
        "unused: &u 3\n"
        "i: &i file:inc.yml\n"
        "x: *i\n"
        "r:\n"
        "  $ref: *i\n"
        "  j: 2\n"
        "f: &a {$ref: file:inc.yml, j: 3}\n"
        "g: *a\n",
        files={".troupe/inc.yml": "k: v\n"},
    )
    uses = anchor_uses(document)
    text = document.root_file.text

    def written(location: tuple[Path, int, int]) -> str:
        assert location[0] == document.root_file.real_path
        return text[location[1] : location[2]]

    def tokens(location: tuple[Path, int, int]) -> list[tuple[str, int]]:
        return [
            (text[ref.token.span.start : ref.token.span.end], ref.token.span.start)
            for ref in uses[location]
        ]

    e, d, i, f = (anchored_location(_node_at(document, key)) for key in ("e", "d", "i", "f"))
    assert (written(e), written(i), written(f)) == (
        "1",
        "file:inc.yml",
        "{$ref: file:inc.yml, j: 3}",
    )
    assert written(d).startswith("inner: *e")
    assert tokens(e) == [("*e", text.index("inner: *e") + 7)]
    assert tokens(d) == [
        ("*d", text.index("a: *d") + 3),
        ("*d", text.index("<<: *d") + 4),
        ("*d", text.index("- *d") + 2),
    ]
    assert tokens(i) == [("*i", text.index("x: *i") + 3), ("*i", text.index("$ref: *i") + 6)]
    assert tokens(f) == [("*a", text.index("g: *a") + 3)]
    assert list(uses) == [e, d, i, f]


def test_anchor_uses_is_empty_without_aliases(make_theatre: MakeTheatre) -> None:
    """Req 4.6: a configuration with an unused anchor and no alias has no anchor uses."""
    document = _document(make_theatre, "d: &d\n  a: 1\nb: 2\n")
    assert dict(anchor_uses(document)) == {}


def test_replacement_by_another_collection_kind_compares_children_by_kind(
    make_theatre: MakeTheatre,
) -> None:
    """Req 5.2, 6.3: a mapping replaced by a list (or a list by a mapping) has no intended child
    for any of its keys; without an alias below PATH the guard proceeds, and with one it refuses
    naming the alias."""
    document = _document(make_theatre, "m:\n  model: x\nl:\n  - 1\n")
    for path, value in (("m", "[a]"), ("l", "{a: 1}")):
        for expand in (False, True):
            assert _sites(document, path, value, expand=expand) == ()
    aliased = make_theatre("d: &d\n  model: x\nc:\n  s: *d\n", name="aliased")
    document = load_config(aliased / ".troupe" / "troupe.yml")
    error = _refusal(document, "c", "[a]", expand=False)
    assert (error.path, error.problem) == ("c", _alias_problem("d"))
