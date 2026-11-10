"""Prepare read-only source context while the native writer is released."""

from typing import Dict
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from .ports import PrepareContextInputs


def prepare_runtime_context(
    _inputs: PrepareContextInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
    progress_callback=None,
) -> Dict[str, object]:
    """Prepare exact-source MySQL inputs before reacquiring the native writer.

    Only a release with frozen TBox metadata can use this path. The normal
    context reader verifies dependency revisions on reuse; cache expiry or a
    concurrent repair simply causes a fresh authoritative read under the writer.
    This stage neither reads nor writes the active TypeDB Manifest.
    """
    if _inputs.frozen_tbox_context is None or not _inputs.runtime_context_cache_enabled():
        return {"status": "not-configured"}
    _inputs.runtime_context(
        snapshot,
        active_tbox=_inputs.frozen_tbox_context,
        target_symbols=target_symbols,
        progress_callback=progress_callback,
    )
    return {"status": "prepared", "sourceAsOf": str(snapshot.generated_at or "")}
