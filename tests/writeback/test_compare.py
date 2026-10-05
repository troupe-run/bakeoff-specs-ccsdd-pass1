"""Unit tests of the PlainTree (``troupe.config.writeback.compare``).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import math
from pathlib import Path

import pytest

from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument, IncludeKind
from troupe.config.paths import ConfigPath
from troupe.config.scalars import ScalarValue
from troupe.config.writeback.compare import (
    IncludeDirective,
    PlainMapping,
    PlainValue,
    new_to_plain,
    node_to_plain,
    plain_equal,
    root_plain,
    scalar_equal,
    splice,
)
from troupe.config.writeback.value import NewMapping, NewScalar, NewSequence


def _load(tmp_path: Path, config: str, files: dict[str, str] | None = None) -> ConfigDocument:
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    root = tmp_path / "troupe.yml"
    root.write_bytes(config.encode("utf-8"))
    return load_config(root)


def _root(tmp_path: Path, config: str, files: dict[str, str] | None = None) -> PlainMapping:
    plain = node_to_plain(_load(tmp_path, config, files).root)
    assert isinstance(plain, dict)
    return plain


def _no_text_content(text: str, directive: IncludeDirective) -> PlainValue:
    raise AssertionError(f"text_content must not be called (got {text!r} for {directive.text!r})")


def _splice(root: PlainMapping, path: str, value: PlainValue) -> PlainMapping:
    return splice(root, ConfigPath.parse(path), value, _no_text_content)


# --- scalar_equal ----------------------------------------------------------------------------

SCALARS: list[ScalarValue] = [1, 1.0, True, "1"]


@pytest.mark.parametrize("left", SCALARS)
@pytest.mark.parametrize("right", SCALARS)
def test_one_one_point_zero_true_and_string_one_are_pairwise_different(
    left: ScalarValue, right: ScalarValue
) -> None:
    """Req 14.6, 15.3: ``1``, ``1.0``, ``True`` and ``"1"`` differ by type; each equals itself."""
    assert scalar_equal(left, right) is (left is right)


@pytest.mark.parametrize(
    ("left", "right"),
    [(0, False), (None, ""), (None, 0), ("", False), (0, 0.0), (False, None)],
)
def test_falsy_values_of_different_types_differ(left: ScalarValue, right: ScalarValue) -> None:
    """Req 15.3: falsy values of different types are different values."""
    assert not scalar_equal(left, right)
    assert not scalar_equal(right, left)


def test_nan_equals_nan_and_signed_zeros_differ() -> None:
    """Req 14.6, 15.3: floats compare by ``repr``: ``nan == nan``, ``0.0 != -0.0``."""
    assert scalar_equal(math.nan, float("nan"))
    assert not scalar_equal(0.0, -0.0)
    assert scalar_equal(math.inf, math.inf)
    assert not scalar_equal(math.inf, -math.inf)
    assert scalar_equal(0.1, 0.1)
    assert not scalar_equal(0.1, 0.10000000000000002)


def test_equal_scalars_of_the_same_type_are_equal() -> None:
    """Req 14.6: same type and value are equal."""
    assert scalar_equal("a", "a")
    assert scalar_equal(None, None)
    assert scalar_equal(False, False)
    assert scalar_equal(31, 31)
    assert not scalar_equal("a", "b")
    assert not scalar_equal(True, False)


# --- node_to_plain / new_to_plain --------------------------------------------------------------


def test_loaded_nodes_become_ordered_plain_trees_with_include_directives(tmp_path: Path) -> None:
    """Req 2.8: include roots become directives with their text and expanded content."""
    config = (
        "name: demo\n"
        "port: 0x1F\n"
        "ratio: .nan\n"
        "flags: [true, ~, '1']\n"
        "team: file:agents.yml\n"
        "prompt: file:prompt.txt\n"
        "z: {b: 1, a: 2}\n"
    )
    files = {
        "agents.yml": "lead: alice\nnotes: file:notes.md\n",
        "notes.md": "n",
        "prompt.txt": "hi\r\nthere",
    }
    root = _root(tmp_path, config, files)

    assert list(root) == ["name", "port", "ratio", "flags", "team", "prompt", "z"]
    assert root["name"] == "demo"
    assert root["port"] == 31
    assert isinstance(root["ratio"], float)
    assert math.isnan(root["ratio"])
    assert root["flags"] == [True, None, "1"]
    team = root["team"]
    assert isinstance(team, IncludeDirective)
    assert team.text == "file:agents.yml"
    assert team.kind is IncludeKind.YAML
    assert team.content == {
        "lead": "alice",
        "notes": IncludeDirective("file:notes.md", "n"),
    }
    assert isinstance(team.content, dict)
    notes = team.content["notes"]
    assert isinstance(notes, IncludeDirective)
    assert notes.kind is IncludeKind.TEXT
    assert root["prompt"] == IncludeDirective("file:prompt.txt", "hi\r\nthere")
    assert list(root["z"]) == ["b", "a"]  # type: ignore[arg-type]


def test_yaml_include_of_a_text_include_keeps_only_the_outer_directive(tmp_path: Path) -> None:
    """Req 2.8: a YAML include whose content is another ``file:`` string is one YAML directive."""
    root = _root(
        tmp_path,
        "coder: file:coder.yaml\n",
        {"coder.yaml": "file:prompt.txt\n", "prompt.txt": "text"},
    )

    coder = root["coder"]
    assert isinstance(coder, IncludeDirective)
    assert coder.text == "file:coder.yaml"
    assert coder.kind is IncludeKind.YAML
    assert coder.content == "text"


def test_parsed_values_become_plain_trees_with_contentless_directives() -> None:
    """Req 2.7, 2.8: ``file:`` strings of VALUE become directives without content."""
    value = NewMapping(
        (
            ("b", NewScalar(1, "1")),
            ("a", NewSequence((NewScalar("file:x.yml", None), NewScalar("x", None)))),
            ("c", NewScalar(None, "")),
        )
    )

    plain = new_to_plain(value)

    assert plain == {"b": 1, "a": [IncludeDirective("file:x.yml", None), "x"], "c": None}
    assert isinstance(plain, dict)
    assert list(plain) == ["b", "a", "c"]
    assert new_to_plain(NewScalar("file:", None)) == IncludeDirective("file:", None)
    assert new_to_plain(NewScalar(" file:x", None)) == " file:x"


# --- plain_equal --------------------------------------------------------------------------------


@pytest.mark.parametrize("ordered", [True, False])
def test_contentless_directive_equals_a_loaded_include_with_the_same_text(
    tmp_path: Path, ordered: bool
) -> None:
    """Req 2.8, 14.6: the same ``file:`` string again equals the include; another one differs."""
    root = _root(
        tmp_path,
        "team: file:agents.yml\nprompt: file:p.txt\n",
        {"agents.yml": "a: 1\n", "p.txt": "x"},
    )
    team, prompt = root["team"], root["prompt"]

    assert plain_equal(team, new_to_plain(NewScalar("file:agents.yml", None)), ordered=ordered)
    assert plain_equal(new_to_plain(NewScalar("file:agents.yml", None)), team, ordered=ordered)
    assert plain_equal(prompt, IncludeDirective("file:p.txt", None), ordered=ordered)
    assert not plain_equal(team, IncludeDirective("file:other.yml", None), ordered=ordered)
    assert not plain_equal(team, IncludeDirective("file:./agents.yml", None), ordered=ordered)
    # Never equal to a plain value, even one equal to the content.
    assert not plain_equal(team, {"a": 1}, ordered=ordered)
    assert not plain_equal({"a": 1}, team, ordered=ordered)
    assert not plain_equal(prompt, "x", ordered=ordered)
    assert not plain_equal("file:p.txt", prompt, ordered=ordered)


def test_directives_with_known_contents_compare_their_contents() -> None:
    """Req 14.6: two directives with contents are equal only when the contents are equal."""
    assert plain_equal(
        IncludeDirective("file:a.txt", "x"), IncludeDirective("file:a.txt", "x"), ordered=True
    )
    assert not plain_equal(
        IncludeDirective("file:a.txt", "x"), IncludeDirective("file:a.txt", "y"), ordered=True
    )
    assert not plain_equal(
        IncludeDirective("file:a.yml", 1), IncludeDirective("file:a.yml", True), ordered=False
    )
    assert plain_equal(
        IncludeDirective("file:a.yml", {"a": 1, "b": 2}),
        IncludeDirective("file:a.yml", {"b": 2, "a": 1}),
        ordered=False,
    )
    assert not plain_equal(
        IncludeDirective("file:a.yml", {"a": 1, "b": 2}),
        IncludeDirective("file:a.yml", {"b": 2, "a": 1}),
        ordered=True,
    )


def test_mapping_equality_with_and_without_key_order() -> None:
    """Req 14.6, 15.3: unordered equality ignores key order; ordered equality does not."""
    first: PlainValue = {"a": 1, "b": {"c": [1, 2], "d": None}}
    reordered: PlainValue = {"b": {"d": None, "c": [1, 2]}, "a": 1}

    assert plain_equal(first, reordered, ordered=False)
    assert not plain_equal(first, reordered, ordered=True)
    assert plain_equal(first, {"a": 1, "b": {"c": [1, 2], "d": None}}, ordered=True)
    assert not plain_equal(first, {"a": 1, "b": {"c": [2, 1], "d": None}}, ordered=False)
    assert not plain_equal(first, {"a": 1}, ordered=False)
    assert not plain_equal({"a": 1}, first, ordered=False)
    assert not plain_equal({"a": 1}, {"b": 1}, ordered=False)
    assert not plain_equal({"a": 1}, {"a": 1.0}, ordered=False)
    assert not plain_equal({"a": 1}, {"a": True}, ordered=False)


def test_lists_scalars_and_kinds_compare_structurally() -> None:
    """Req 15.3: lists by index and typed items; different kinds never compare equal."""
    assert plain_equal([1, "a", None], [1, "a", None], ordered=True)
    assert not plain_equal([1, 2], [1, 2, 3], ordered=False)
    assert not plain_equal([1, 2, 3], [1, 2], ordered=False)
    assert not plain_equal([1], [1.0], ordered=False)
    assert plain_equal([math.nan], [math.nan], ordered=True)
    assert not plain_equal([0.0], [-0.0], ordered=True)
    assert not plain_equal([], {}, ordered=False)
    assert not plain_equal({}, [], ordered=False)
    assert not plain_equal(None, [], ordered=False)
    assert not plain_equal("a", ["a"], ordered=False)
    assert not plain_equal({}, None, ordered=False)
    assert plain_equal([], [], ordered=True)
    assert plain_equal({}, {}, ordered=True)


# --- splice -------------------------------------------------------------------------------------


def test_splice_replaces_a_value_without_changing_the_input() -> None:
    """Req 14.6: the intended tree has the new value at PATH; the input tree is untouched."""
    root: PlainMapping = {"db": {"host": "a", "port": 1}, "name": "x"}

    result = _splice(root, "db.port", 2)

    assert result == {"db": {"host": "a", "port": 2}, "name": "x"}
    assert list(result["db"]) == ["host", "port"]  # type: ignore[arg-type]
    assert root == {"db": {"host": "a", "port": 1}, "name": "x"}
    assert _splice(root, "name", [1, {"k": "v"}]) == {
        "db": {"host": "a", "port": 1},
        "name": [1, {"k": "v"}],
    }
    assert _splice(root, "db", "scalar") == {"db": "scalar", "name": "x"}


def test_splice_creates_missing_intermediate_mappings() -> None:
    """Req 14.6: new nested keys are appended with their missing intermediate mappings."""
    root: PlainMapping = {"db": {"host": "a"}, "name": "x"}

    result = _splice(root, "db.pool.size", 5)

    assert result == {"db": {"host": "a", "pool": {"size": 5}}, "name": "x"}
    assert list(result) == ["db", "name"]
    assert list(result["db"]) == ["host", "pool"]  # type: ignore[arg-type]
    assert _splice({}, "a.b", {"c": 1}) == {"a": {"b": {"c": 1}}}
    assert list(_splice(root, "new", 1)) == ["db", "name", "new"]


def test_splice_appends_at_an_index_equal_to_the_length() -> None:
    """Req 14.6: an index equal to the list length appends; a smaller index replaces."""
    root: PlainMapping = {"tags": ["a", "b"], "rows": [{"k": 1}]}

    assert _splice(root, "tags.2", "c") == {"tags": ["a", "b", "c"], "rows": [{"k": 1}]}
    assert _splice(root, "tags.0", "z") == {"tags": ["z", "b"], "rows": [{"k": 1}]}
    assert _splice(root, "rows.0.k", 2) == {"tags": ["a", "b"], "rows": [{"k": 2}]}
    assert _splice(root, "rows.0.j", 2) == {"tags": ["a", "b"], "rows": [{"k": 1, "j": 2}]}
    assert root == {"tags": ["a", "b"], "rows": [{"k": 1}]}


@pytest.mark.parametrize("path", ["tags.2", "tags.1.x", "tags.x", "name.x", "nothing.0", "empty.x"])
def test_splice_rejects_paths_target_resolution_refuses(path: str) -> None:
    """Req 14.6: paths outside rules 1-4 are a programming error, never silently spliced."""
    root: PlainMapping = {"tags": ["a"], "name": "x", "empty": None}

    with pytest.raises(ValueError, match="cannot splice"):
        _splice(root, path, 1)


def test_splicing_a_mapping_over_a_mapping_keeps_existing_key_order() -> None:
    """Req 5.1, 14.6: kept keys keep their order, absent keys go, new keys follow VALUE order."""
    root: PlainMapping = {"db": {"host": "a", "port": 5432, "user": "x"}}
    value = new_to_plain(
        NewMapping(
            (
                ("port", NewScalar(5432, "5432")),
                ("host", NewScalar("b", None)),
                ("pool", NewScalar(3, "3")),
            )
        )
    )

    result = _splice(root, "db", value)

    assert result == {"db": {"host": "b", "port": 5432, "pool": 3}}
    assert list(result["db"]) == ["host", "port", "pool"]  # type: ignore[arg-type]


def test_merges_reach_mappings_and_lists_at_any_depth() -> None:
    """Req 5.1, 5.2: nested mappings merge in existing order; lists merge by index."""
    root: PlainMapping = {
        "a": {"x": {"p": 1, "q": 2}, "list": [{"m": 1, "n": 2}, "old", "gone"], "keep": 1}
    }
    value: PlainValue = {
        "list": [{"n": 3, "m": 1}, "new"],
        "x": {"q": 2, "r": 3, "p": 0},
        "keep": 1,
    }

    result = _splice(root, "a", value)

    assert result == {
        "a": {"x": {"p": 0, "q": 2, "r": 3}, "list": [{"m": 1, "n": 3}, "new"], "keep": 1}
    }
    assert plain_equal(
        result,
        {"a": {"x": {"p": 0, "q": 2, "r": 3}, "list": [{"m": 1, "n": 3}, "new"], "keep": 1}},
        ordered=True,
    )
    grown = _splice({"l": [1]}, "l", [{"a": 1}, 2])
    assert grown == {"l": [{"a": 1}, 2]}
    assert _splice({"m": {"a": 1}}, "m", {}) == {"m": {}}
    assert _splice({"m": [1]}, "m", []) == {"m": []}
    assert _splice({"m": [1]}, "m", {"a": 1}) == {"m": {"a": 1}}


def test_splice_into_the_content_of_a_yaml_include(tmp_path: Path) -> None:
    """Req 2.6: a PATH inside a YAML include changes the directive's content, not its text."""
    root = _root(tmp_path, "team: file:agents.yml\n", {"agents.yml": "lead: alice\nsize: 2\n"})

    result = _splice(root, "team.lead", "bob")

    team = result["team"]
    assert isinstance(team, IncludeDirective)
    assert team.text == "file:agents.yml"
    assert team.kind is IncludeKind.YAML
    assert team.content == {"lead": "bob", "size": 2}
    assert _splice(root, "team.new.key", 1) == {
        "team": IncludeDirective("file:agents.yml", {"lead": "alice", "size": 2, "new": {"key": 1}})
    }


def test_a_yaml_include_directive_is_replaced_never_merged(tmp_path: Path) -> None:
    """Req 2.5, 5.8: a value placed over a YAML include root replaces it (and so differs)."""
    root = _root(tmp_path, "team: file:agents.yml\n", {"agents.yml": "lead: alice\n"})

    replaced = _splice(root, "team", {"lead": "alice"})
    assert replaced == {"team": {"lead": "alice"}}
    assert not plain_equal(replaced, root, ordered=False)
    again = _splice(root, "team", IncludeDirective("file:agents.yml", None))
    assert plain_equal(again, root, ordered=True)
    nested = _splice({"a": dict(root)}, "a", {"team": {"lead": "alice"}})
    assert nested == {"a": {"team": {"lead": "alice"}}}


def _content(text: str, directive: IncludeDirective) -> PlainValue:
    return f"<{directive.text}>{text.replace(chr(10), chr(13) + chr(10))}"


def test_a_string_over_a_text_include_keeps_the_directive_and_sets_its_content(
    tmp_path: Path,
) -> None:
    """Req 2.2, 2.3, 14.6: a string over a text include keeps it; the callback gives the content."""
    root = _root(tmp_path, "cfg:\n  t: file:t.txt\n  u: 1\n", {"t.txt": "old\r\n"})
    value = new_to_plain(NewMapping((("t", NewScalar("new\n", None)), ("u", NewScalar(1, "1")))))

    result = splice(root, ConfigPath.parse("cfg"), value, _content)

    t = result["cfg"]["t"]  # type: ignore[index, call-overload]
    assert isinstance(t, IncludeDirective)
    assert t.text == "file:t.txt"
    assert t.kind is IncludeKind.TEXT
    assert t.content == "<file:t.txt>new\r\n"
    assert result["cfg"]["u"] == 1  # type: ignore[index, call-overload]


def test_text_include_content_equal_to_the_file_is_unchanged(tmp_path: Path) -> None:
    """Req 14.6: content equal to the file's text leaves the intended tree unchanged."""
    root = _root(tmp_path, "cfg: {t: file:t.txt, l: [file:t.txt]}\n", {"t.txt": "new\r\n"})
    calls: list[tuple[str, str]] = []

    def to_crlf(text: str, directive: IncludeDirective) -> PlainValue:
        calls.append((text, directive.text))
        return text.replace("\n", "\r\n")

    at_path = splice(root, ConfigPath.parse("cfg.t"), "new\n", to_crlf)
    merged = splice(root, ConfigPath.parse("cfg"), {"t": "new\n", "l": ["new\n"]}, to_crlf)

    assert plain_equal(at_path, root, ordered=True)
    assert plain_equal(merged, root, ordered=True)
    assert calls == [("new\n", "file:t.txt")] * 3
    changed = splice(root, ConfigPath.parse("cfg.t"), "other", to_crlf)
    assert not plain_equal(changed, root, ordered=False)
    assert changed["cfg"]["t"] == IncludeDirective("file:t.txt", "other")  # type: ignore[index, call-overload]


def test_non_strings_and_directives_over_a_text_include_replace_it(tmp_path: Path) -> None:
    """Req 2.4, 2.8: only a string keeps a text include; other values replace the directive."""
    root = _root(tmp_path, "t: file:t.txt\n", {"t.txt": "x"})

    assert _splice(root, "t", 5) == {"t": 5}
    assert _splice(root, "t", ["x"]) == {"t": ["x"]}
    again = _splice(root, "t", IncludeDirective("file:t.txt", None))
    assert plain_equal(again, root, ordered=True)
    other = _splice(root, "t", IncludeDirective("file:u.txt", None))
    assert not plain_equal(other, root, ordered=True)


def test_a_string_over_a_yaml_include_with_string_content_replaces_it(tmp_path: Path) -> None:
    """Req 2.5: a YAML include root is never kept by the text-include rule."""
    root = _root(
        tmp_path,
        "a: file:a.yml\nb: file:b.yml\n",
        {"a.yml": "hello\n", "b.yml": "file:p.txt\n", "p.txt": "text"},
    )

    assert _splice(root, "a", "hello") == {"a": "hello", "b": root["b"]}
    assert _splice(root, "b", "text") == {"a": root["a"], "b": "text"}


# --- mapping-form includes (config-include-overrides) -------------------------------------------


def _ref(text: str, *entries: tuple[str, NewMapping | NewScalar | NewSequence]) -> NewMapping:
    return NewMapping(((REF, NewScalar(text, None)), *entries))


REF = "$ref"
LOADED = IncludeDirective("file:x.yaml", {"a": 1, "m": {"x": 1, "y": 2}, "l": [1, 2]})


def test_value_ref_mapping_becomes_a_directive_with_overrides() -> None:
    """Req 8.2: a VALUE mapping whose `$ref` is a `file:` string becomes a contentless directive
    whose overrides are the other entries in VALUE order; it has no include kind."""
    value = _ref(
        "file:x.yaml",
        ("m", NewMapping((("y", NewScalar(2, "2")), ("x", NewScalar(1, "1"))))),
        ("a", NewScalar("file:a.txt", None)),
    )
    plain = new_to_plain(value)
    assert isinstance(plain, IncludeDirective)
    assert plain.text == "file:x.yaml"
    assert plain.content is None
    assert plain.kind is None
    assert plain.overrides == {"m": {"y": 2, "x": 1}, "a": IncludeDirective("file:a.txt", None)}
    assert plain.overrides is not None
    assert list(plain.overrides) == ["m", "a"]
    m = plain.overrides["m"]
    assert isinstance(m, dict)
    assert list(m) == ["y", "x"]
    bare = new_to_plain(_ref("file:x.yaml"))
    assert bare == IncludeDirective("file:x.yaml", None)
    assert isinstance(bare, IncludeDirective)
    assert bare.overrides == {}


def test_value_ref_that_is_not_an_include_string_stays_a_plain_mapping() -> None:
    """Req 8.2: a `$ref` whose value is not a `file:` string is an ordinary mapping entry (the
    read-back load refuses it)."""
    assert new_to_plain(_ref("x.yaml", ("k", NewScalar(1, "1")))) == {"$ref": "x.yaml", "k": 1}
    number = NewMapping(((REF, NewScalar(5, "5")),))
    assert new_to_plain(number) == {"$ref": 5}
    nested = NewMapping(((REF, NewMapping((("k", NewScalar("file:x", None)),))),))
    assert new_to_plain(nested) == {"$ref": {"k": IncludeDirective("file:x", None)}}


def _with_overrides(overrides: PlainMapping, text: str = "file:x.yaml") -> IncludeDirective:
    return IncludeDirective(text, None, overrides=overrides)


@pytest.mark.parametrize("ordered", [True, False])
def test_directive_with_overrides_equals_a_loaded_include_containing_them(ordered: bool) -> None:
    """Req 8.2, 9.2: a directive with overrides equals a loaded include with the same text whose
    content contains the overrides (in either argument order); extra content keys and their order
    do not matter."""
    contained: list[PlainMapping] = [
        {},
        {"a": 1},
        {"l": [1, 2], "a": 1},
        {"m": {"y": 2}},
        {"m": {"y": 2, "x": 1}, "a": 1},
    ]
    for overrides in contained:
        directive = _with_overrides(overrides)
        assert plain_equal(directive, LOADED, ordered=ordered)
        assert plain_equal(LOADED, directive, ordered=ordered)
        assert plain_equal({"k": directive}, {"k": LOADED}, ordered=ordered)


@pytest.mark.parametrize(
    "overrides",
    [
        {"missing": 1},
        {"a": 2},
        {"a": 1.0},
        {"a": "1"},
        {"m": {"x": 2}},
        {"m": {"z": 1}},
        {"m": 5},
        {"a": {"x": 1}},
        {"l": [1]},
        {"l": [1, 2, 3]},
        {"l": {"0": 1}},
    ],
)
def test_missing_or_differing_override_makes_the_directive_unequal(overrides: PlainMapping) -> None:
    """Req 8.2, 9.2: an override key missing from the loaded content, or an override value that
    differs from it (by type and value; mappings by containment), makes the directives unequal."""
    directive = _with_overrides(overrides)
    assert not plain_equal(directive, LOADED, ordered=True)
    assert not plain_equal(LOADED, directive, ordered=False)


def test_override_containment_rules_beyond_plain_mappings() -> None:
    """Req 8.2: texts must match; a loaded include without mapping content contains no overrides
    (not even none); a mapping override is contained in a nested include's mapping content; a
    directive override compares as a directive; a directive never equals a non-directive."""
    assert not plain_equal(_with_overrides({}, "file:y.yaml"), LOADED, ordered=True)
    assert not plain_equal(_with_overrides({}), {"a": 1}, ordered=True)
    assert not plain_equal(_with_overrides({}), IncludeDirective("file:x.yaml", [1]), ordered=True)
    assert not plain_equal(_with_overrides({}), IncludeDirective("file:x.yaml", 1), ordered=True)
    nested = IncludeDirective(
        "file:x.yaml", {"m": IncludeDirective("file:m.yaml", {"y": 2, "w": 3}), "z": 1}
    )
    assert plain_equal(_with_overrides({"m": {"y": 2}}), nested, ordered=True)
    assert not plain_equal(_with_overrides({"m": {"y": 3}}), nested, ordered=True)
    as_directive = _with_overrides({"m": IncludeDirective("file:m.yaml", None)})
    assert plain_equal(as_directive, nested, ordered=True)
    assert not plain_equal(
        _with_overrides({"m": IncludeDirective("file:n.yaml", None)}), nested, ordered=True
    )
    inner = _with_overrides({"y": 2}, "file:m.yaml")
    assert plain_equal(_with_overrides({"m": inner}), nested, ordered=True)


def test_directive_with_overrides_against_a_contentless_directive_compares_texts() -> None:
    """Req 8.2: without content on either side only the texts are compared (as before)."""
    assert plain_equal(
        _with_overrides({"a": 1}), IncludeDirective("file:x.yaml", None), ordered=True
    )
    assert plain_equal(_with_overrides({"a": 1}), _with_overrides({"b": 2}), ordered=True)
    assert not plain_equal(_with_overrides({"a": 1}), _with_overrides({}, "file:y"), ordered=True)


def test_directives_without_overrides_keep_their_equality() -> None:
    """Req 8.2: directives without overrides compare as before: a contentless directive equals any
    loaded include with its text; two contents must be equal (ordered when asked)."""
    assert plain_equal(IncludeDirective("file:x.yaml", None), LOADED, ordered=True)
    assert not plain_equal(IncludeDirective("file:x.yaml", {"a": 1}), LOADED, ordered=False)
    reordered = IncludeDirective("file:x.yaml", {"l": [1, 2], "a": 1, "m": {"x": 1, "y": 2}})
    assert plain_equal(reordered, LOADED, ordered=False)
    assert not plain_equal(reordered, LOADED, ordered=True)
    with_overrides = IncludeDirective("file:x.yaml", {"a": 1}, overrides={"a": 1})
    assert with_overrides == IncludeDirective("file:x.yaml", {"a": 1})
    assert not plain_equal(with_overrides, LOADED, ordered=True)


def test_root_plain_unwraps_a_mapping_form_root_include(tmp_path: Path) -> None:
    """Req 9.2: the root plain tree of a file whose root is a `$ref` mapping is the merged mapping
    of the include (not a directive); nested includes stay directives."""
    document = _load(
        tmp_path,
        "$ref: file:base.yaml\nb: 2\nc: file:c.yaml\n",
        {"base.yaml": "a: 1\nb: 1\n", "c.yaml": "k: v\n"},
    )
    assert isinstance(node_to_plain(document.root), IncludeDirective)
    plain = root_plain(document)
    assert plain == {"a": 1, "b": 2, "c": IncludeDirective("file:c.yaml", {"k": "v"})}
    assert list(plain) == ["a", "b", "c"]


def test_root_plain_of_a_plain_root_is_its_plain_tree(tmp_path: Path) -> None:
    """Req 9.2: a root without an include gives the same tree as node_to_plain."""
    document = _load(tmp_path, "a: 1\nx: file:x.yaml\n", {"x.yaml": "k: v\n"})
    assert root_plain(document) == node_to_plain(document.root)
    assert root_plain(document) == {"a": 1, "x": IncludeDirective("file:x.yaml", {"k": "v"})}


def test_include_override_absorbed_by_the_merge_is_contained() -> None:
    """Req 8.2, 1.7: an override that is itself an include, at a key where the included file holds
    a mapping, is merged into that mapping (the loaded value is a plain mapping): a scalar-form
    include override accepts the merged mapping; a `$ref` override needs its overrides there."""
    merged = IncludeDirective("file:a.yaml", {"m": {"p": 1, "q": 2, "r": 3}, "s": 1})
    scalar_form = _with_overrides({"m": IncludeDirective("file:m.yaml", None)}, "file:a.yaml")
    assert plain_equal(scalar_form, merged, ordered=True)
    assert plain_equal(merged, scalar_form, ordered=False)
    ref_form = _with_overrides({"m": _with_overrides({"r": 3}, "file:m.yaml")}, "file:a.yaml")
    assert plain_equal(ref_form, merged, ordered=True)
    assert plain_equal(merged, ref_form, ordered=False)
    differing = _with_overrides({"m": _with_overrides({"r": 4}, "file:m.yaml")}, "file:a.yaml")
    assert not plain_equal(differing, merged, ordered=True)
    missing = _with_overrides({"m": _with_overrides({"t": 3}, "file:m.yaml")}, "file:a.yaml")
    assert not plain_equal(missing, merged, ordered=True)
    over_scalar = _with_overrides({"s": IncludeDirective("file:m.yaml", None)}, "file:a.yaml")
    assert not plain_equal(over_scalar, merged, ordered=True)
