"""Immutable source packets shared by versioned investment reasoning engines."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Dict, Iterable, Mapping

from .ontology_projection_input import (
    compact_monitor_state_for_ontology,
    frozen_monitor_state_for_reasoning,
    reasoning_snapshot_symbols,
)


REASONING_SOURCE_SNAPSHOT_VERSION = "reasoning-source-snapshot-v1"


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def reasoning_source_snapshot_id(account_id: object, generated_at: object) -> str:
    identity = {
        "accountId": _text(account_id),
        "generatedAt": _text(generated_at),
        "contractVersion": REASONING_SOURCE_SNAPSHOT_VERSION,
    }
    return "reasoning-source:" + _hash(identity)[:40]


def build_reasoning_source_snapshot(
    account_id: object,
    state: Mapping[str, object],
    *,
    previous_state: Mapping[str, object] = None,
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Freeze the exact bounded facts that V1 and V2 may replay later."""

    source = deepcopy(dict(state or {}))
    clean_account_id = _text(account_id) or _text(source.get("accountId"))
    generated_at = _text(source.get("generatedAt"))
    source["accountId"] = clean_account_id
    selected_symbols = sorted({
        _text(value).upper()
        for value in (target_symbols or reasoning_snapshot_symbols(source))
        if _text(value)
    })
    metadata = dict(source.get("metadata") or {})
    if isinstance(previous_state, Mapping) and previous_state:
        metadata["previousMonitorState"] = compact_monitor_state_for_ontology(
            previous_state,
            settings=settings,
        )
    source["metadata"] = metadata
    payload = frozen_monitor_state_for_reasoning(
        source,
        target_symbols=selected_symbols,
        settings=settings,
    )
    snapshot_id = reasoning_source_snapshot_id(clean_account_id, generated_at)
    replay = dict((payload.get("metadata") or {}).get("reasoningSnapshotReplay") or {})
    replay.update({
        "contractVersion": REASONING_SOURCE_SNAPSHOT_VERSION,
        "snapshotId": snapshot_id,
        "snapshotGeneratedAt": generated_at,
        "immutableInput": True,
    })
    payload.setdefault("metadata", {})["reasoningSnapshotReplay"] = replay
    fingerprint = _hash(payload)
    return {
        "snapshotId": snapshot_id,
        "accountId": clean_account_id,
        "accountLabel": _text(source.get("accountLabel")),
        "provider": _text(source.get("provider")),
        "mode": _text(source.get("mode")),
        "status": _text(source.get("status")),
        "generatedAt": generated_at,
        "contractVersion": REASONING_SOURCE_SNAPSHOT_VERSION,
        "fingerprint": fingerprint,
        "symbols": selected_symbols,
        "payload": payload,
    }

