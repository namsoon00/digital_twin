"""Account/subject-scoped hypothesis memory, separate from decision and graph stores."""

from __future__ import annotations

from digital_twin.domain.hypothesis_lifecycle import HYPOTHESIS_LIFECYCLE_KEY_PREFIX
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Dict
from typing import List
from digital_twin.modules.reasoning.application.projection_input.ports import (
    HypothesisInputs,
)


def hypothesis_proposal_context(
    _inputs: HypothesisInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
) -> List[Dict[str, object]]:
    if not _inputs.hypothesis_proposal_store or not hasattr(
        _inputs.hypothesis_proposal_store, "list_hypothesis_proposals"
    ):
        return []
    symbols = {
        str(getattr(position, "symbol", "") or "").upper().strip()
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if str(getattr(position, "symbol", "") or "").strip()
    }
    requested = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }
    if requested:
        symbols.intersection_update(requested)
    try:
        rows = _inputs.hypothesis_proposal_store.list_hypothesis_proposals("", "", 200)
    except Exception:  # noqa: BLE001 - proposal memory must not block ABox projection.
        return []
    return [
        dict(item)
        for item in rows or []
        if isinstance(item, dict)
        and str(item.get("accountId") or "") == str(snapshot.account_id or "")
        and str(item.get("symbol") or "").upper().strip() in symbols
    ]


def hypothesis_lifecycle_context(
    _inputs: HypothesisInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
) -> List[Dict[str, object]]:
    if not _inputs.hypothesis_lifecycle_store:
        return []
    symbols = {
        str(getattr(position, "symbol", "") or "").upper().strip()
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
    }
    requested = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }
    if requested:
        symbols.intersection_update(requested)
    if not symbols:
        return []
    try:
        if hasattr(_inputs.hypothesis_lifecycle_store, "current_summary_for_subjects"):
            try:
                records = (
                    _inputs.hypothesis_lifecycle_store.current_summary_for_subjects(
                        snapshot.account_id,
                        symbols,
                        lifecycle_key_prefix=HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
                    )
                )
            except TypeError:
                records = (
                    _inputs.hypothesis_lifecycle_store.current_summary_for_subjects(
                        snapshot.account_id, symbols
                    )
                )
        elif hasattr(_inputs.hypothesis_lifecycle_store, "current_for_subjects"):
            try:
                records = _inputs.hypothesis_lifecycle_store.current_for_subjects(
                    snapshot.account_id,
                    symbols,
                    lifecycle_key_prefix=HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
                )
            except TypeError:
                records = _inputs.hypothesis_lifecycle_store.current_for_subjects(
                    snapshot.account_id, symbols
                )
        else:
            return []
    except (
        Exception
    ):  # noqa: BLE001 - lifecycle audit must not block a factual ABox projection.
        return []
    return [
        item.to_dict() for item in (records or {}).values() if hasattr(item, "to_dict")
    ]
