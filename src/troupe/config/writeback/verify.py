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
"""

from collections.abc import Sequence
from pathlib import Path

from troupe.config.errors import ConfigLoadError, WriteRefusedError
from troupe.config.loader import load_config
from troupe.config.model import ConfigDocument
from troupe.config.writeback.compare import plain_equal, root_plain
from troupe.config.writeback.planner import FilePatch
from troupe.config.writeback.target import SetRequest


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
    unwrapped to its merged content; further patched files are listed in the problem).
    """
    if not patches:  # pragma: no cover - plan_patch never returns an empty plan
        raise ValueError("verify_patch needs at least one patch")
    overrides: dict[Path | str, str] = {file.key: file.text for file in document.files}
    overrides.update((patch.file.key, patch.new_text) for patch in patches)
    first = patches[0].file.path
    path = str(request.path)
    try:
        after = load_config(config_file, overrides)
    except ConfigLoadError as error:
        raise WriteRefusedError(
            f"written value cannot be read back: {error}", file=first, path=path
        ) from error
    if not plain_equal(root_plain(after), request.intended, ordered=True):
        problem = "written configuration differs from the intended configuration"
        others = [str(patch.file.path) for patch in patches[1:]]
        if others:
            problem += f" (also patched: {', '.join(others)})"
        raise WriteRefusedError(problem, file=first, path=path)
    return after
