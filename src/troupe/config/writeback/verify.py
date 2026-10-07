"""WriteVerifier: re-load the configuration with every patch in memory and compare.

:func:`verify_patch` proves, before any file is written, that the patched texts of all files
together load as the intended configuration (13.6, 14.1). Every file of the loaded document is
overridden with its loaded text and every patched file with its new text; targets of includes
written by this set are not overridden and are read (never written) from disk by the loader,
exactly as a later ``show`` would read them. Any load error (including a missing, unreadable or
unloadable target of a new include and an include cycle) and any difference from the intended
configuration (including key order and include directives) is a :class:`WriteRefusedError`
(exit 3) naming the first patched file and PATH (14.2, 14.8). The intended configuration comes from
the setter: for a file included at several places it already holds the change at every place
(config-include-overrides 11.2), so a shared file is not refused merely for being shared.

Since config-remote-schema (step 7) every loaded source is overridden by its identity
(:attr:`~troupe.config.model.SourceFile.key`, the URL of a remote source), so the read-back reuses
the remote content fetched when the configuration was loaded and fetches only a remote include
this set newly writes (10.3, 9.4); such an include that cannot be fetched keeps the read-back
refusal above, whose problem names the URL. The read-back also validates the schema: a
:class:`SchemaViolationError` becomes a refusal (exit 3) naming the schema file, PATH, the failing
key path (none at the root) and the validator's description (7.1).
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from troupe.config.errors import ConfigLoadError, SchemaViolationError, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.writeback.compare import plain_equal, root_plain
from troupe.config.writeback.planner import FilePatch
from troupe.config.writeback.target import SetRequest

SET_MAKES_INVALID: Final = "the set would make the configuration invalid: {failure}"
"""The problem of a set whose read-back does not validate against the schema (7.1); ``failure`` is
``<key path>: <detail>``, or the detail alone for a failure at the root."""


def verify_patch(
    config_file: Path,
    document: ConfigDocument,
    patches: Sequence[FilePatch],
    request: SetRequest,
) -> ConfigDocument:
    """Re-load ``config_file`` with all ``patches`` applied in memory and return the result.

    Raises :class:`WriteRefusedError` naming the first patched file and PATH when the patched
    configuration cannot be loaded (the problem carries the loader's message, which names the
    failing file and, for includes, the include target) or when it differs from
    ``request.intended`` (ordered comparison of the root mapping, a mapping-form root include
    unwrapped to its merged content; further patched files are listed in the problem). Raises
    :class:`WriteRefusedError` naming the schema file and PATH when the patched configuration
    does not validate against the schema (7.1).
    """
    if not patches:  # pragma: no cover - plan_patch never returns an empty plan
        raise ValueError("verify_patch needs at least one patch")
    overrides: dict[Path | str, str] = {file.key: file.text for file in document.files}
    overrides.update((patch.file.key, patch.new_text) for patch in patches)
    first = patches[0].file.shown
    path = str(request.path)
    try:
        after = load_config(config_file, overrides)
    except SchemaViolationError as error:
        failure = error.detail if error.key_path is None else f"{error.key_path}: {error.detail}"
        raise WriteRefusedError(
            SET_MAKES_INVALID.format(failure=failure), file=error.file, path=path
        ) from error
    except ConfigLoadError as error:
        raise WriteRefusedError(
            f"written value cannot be read back: {error}", file=first, path=path
        ) from error
    if not plain_equal(root_plain(after), request.intended, ordered=True):
        problem = "written configuration differs from the intended configuration"
        others = [str(patch.file.shown) for patch in patches[1:]]
        if others:
            problem += f" (also patched: {', '.join(others)})"
        raise WriteRefusedError(problem, file=first, path=path)
    return after
