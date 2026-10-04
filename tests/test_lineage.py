"""Lineage values, the batch stamp and the timestamp format.

config-lineage 1.2-1.6, 3.4, 4.3, 5.1, 5.3.

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from troupe.config.lineage import (
    FILE_CONTEXT,
    IMPORT_CONTEXT,
    UNKNOWN_ACTOR,
    Actor,
    ChangeContext,
    Lineage,
    cli_context,
    format_utc,
    stamp,
)

AT_FORMAT = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


def test_format_utc_writes_six_fractional_digits_and_z() -> None:
    """Req 1.4, 5.1: ``at`` is ``YYYY-MM-DDTHH:MM:SS.ffffffZ``, six digits always."""
    whole_second = datetime(2026, 10, 4, 9, 5, 7, tzinfo=UTC)
    tiny_fraction = datetime(2026, 1, 2, 3, 4, 5, 12, tzinfo=UTC)

    assert format_utc(whole_second) == "2026-10-04T09:05:07.000000Z"
    assert format_utc(tiny_fraction) == "2026-01-02T03:04:05.000012Z"


def test_format_utc_converts_a_non_utc_aware_time_to_utc() -> None:
    """Req 5.3: a time in another zone is expressed in UTC, across a date boundary."""
    tokyo = timezone(timedelta(hours=9))
    moment = datetime(2026, 10, 4, 3, 30, 0, 250000, tzinfo=tokyo)

    assert format_utc(moment) == "2026-10-03T18:30:00.250000Z"


def test_format_utc_refuses_a_naive_time() -> None:
    """Req 5.3: a naive time has no known zone, so it cannot be expressed in UTC."""
    with pytest.raises(ValueError, match="naive"):
        format_utc(datetime(2026, 10, 4, 9, 0, 0))


def test_stamp_keeps_the_context_and_records_the_current_utc_time() -> None:
    """Req 1.4, 1.6, 5.1, 5.3: keeps actor/via/reason, adds a change string and the time now."""
    context = cli_context(Actor(kind="agent", id="planner:v2"), "rotate the key")

    before = format_utc(datetime.now(UTC))
    lineage = stamp(context)
    after = format_utc(datetime.now(UTC))

    assert lineage.actor == Actor(kind="agent", id="planner:v2")
    assert lineage.via == "cli"
    assert lineage.reason == "rotate the key"
    assert isinstance(lineage.change, str)
    assert lineage.change != ""
    assert AT_FORMAT.fullmatch(lineage.at)
    assert before <= lineage.at <= after


def test_stamp_keeps_an_empty_reason_and_a_null_id() -> None:
    """Req 1.2, 1.5: an empty reason stays empty (not null) and a null actor id stays null."""
    lineage = stamp(cli_context(Actor(kind="human", id=None), ""))

    assert lineage.actor == Actor(kind="human", id=None)
    assert lineage.reason == ""


def test_many_stamps_never_share_a_change_id() -> None:
    """Req 4.3: every stamp has a fresh change id, even for the same context in quick succession."""
    changes = {stamp(FILE_CONTEXT).change for _ in range(2000)}

    assert len(changes) == 2000


def test_import_and_file_contexts_carry_the_unknown_actor_and_no_reason() -> None:
    """Req 1.3, 3.4: imports and file edits are attributed to nobody, with their via, no reason."""
    nobody = Actor(kind="unknown", id=None)

    assert nobody == UNKNOWN_ACTOR
    assert ChangeContext(actor=nobody, via="import", reason=None) == IMPORT_CONTEXT
    assert ChangeContext(actor=nobody, via="file", reason=None) == FILE_CONTEXT


def test_stamped_import_and_file_lineage_keep_the_unknown_actor() -> None:
    """Req 3.4: stamping does not attribute an import or a file change to anyone."""
    for context, via in ((IMPORT_CONTEXT, "import"), (FILE_CONTEXT, "file")):
        lineage = stamp(context)
        assert (lineage.actor, lineage.via, lineage.reason) == (UNKNOWN_ACTOR, via, None)


def test_cli_context_uses_the_given_actor_and_reason() -> None:
    """Req 1.3, 3.4: a cli context carries the command's own actor and its reason."""
    actor = Actor(kind="human", id="alice")

    assert cli_context(actor, None) == ChangeContext(actor=actor, via="cli", reason=None)
    assert cli_context(actor, "why") == ChangeContext(actor=actor, via="cli", reason="why")


def test_change_context_reason_defaults_to_none() -> None:
    """Req 1.5: a context built without a reason has a null reason."""
    assert ChangeContext(actor=UNKNOWN_ACTOR, via="file").reason is None


def test_lineage_values_are_immutable() -> None:
    """Req 4.3: a stamped lineage cannot be altered, so a batch cannot be relabelled."""
    lineage = stamp(IMPORT_CONTEXT)

    with pytest.raises(AttributeError):
        lineage.change = "other"  # type: ignore[misc]
    assert isinstance(lineage, Lineage)
