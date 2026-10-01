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
