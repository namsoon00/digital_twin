"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any, Dict
from digital_twin.domain.ontology_projection_input import (
    compact_monitor_state_for_ontology,
    compact_monitor_state_for_reasoning_base,
    compact_monitor_state_for_reasoning_symbol,
    reasoning_snapshot_symbols,
)
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.settings import utc_now


def insert_verified_source_snapshot(
    connection: BoundWriteConnection,
    source_snapshot: Any,
    updated_at: Any,
):
    return connection.execute(
        """
            INSERT IGNORE INTO verified_reasoning_source_snapshots (
                snapshot_id, account_id, account_label, provider, mode, status,
                generated_at, contract_version, fingerprint, symbols_json,
                payload_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
        (
            source_snapshot["snapshotId"],
            source_snapshot["accountId"],
            source_snapshot["accountLabel"],
            source_snapshot["provider"],
            source_snapshot["mode"],
            source_snapshot["status"],
            source_snapshot["generatedAt"],
            source_snapshot["contractVersion"],
            source_snapshot["fingerprint"],
            json_dumps(source_snapshot["symbols"]),
            json_dumps(source_snapshot["payload"]),
            updated_at,
            updated_at,
        ),
    )


def upsert_reasoning_snapshot_inputs(
    connection: BoundWriteConnection,
    account_id: str,
    state: Dict[str, object],
    generated_at: str,
    stamp: str,
    previous_state: Dict[str, object],
    *,
    _runtime_settings: Any,
):
    """Persist target-scoped TypeDB input beside its verified source row.

    The durable mailbox event is inserted in the same monitoring-cycle
    transaction.  Writing this cache here gives the reasoning worker a
    small, revision-aligned input without asking it to decode the source
    provider archive after it has already claimed live queue work.
    """

    current = dict(state or {}) if isinstance(state, dict) else {}
    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
        return
    updated_at = str(stamp or utc_now())
    source_generated_at = str(generated_at or current.get("generatedAt") or updated_at)
    base = compact_monitor_state_for_reasoning_base(
        current,
        settings=_runtime_settings,
    )
    base["accountId"] = str(base.get("accountId") or normalized_account_id)
    base["generatedAt"] = str(base.get("generatedAt") or source_generated_at)
    compact_previous = (
        compact_monitor_state_for_ontology(
            previous_state,
            settings=_runtime_settings,
        )
        if isinstance(previous_state, dict)
        else {}
    )
    previous_generated_at = str(compact_previous.get("generatedAt") or "")
    if (
        compact_previous
        and previous_generated_at
        and previous_generated_at < source_generated_at
    ):
        metadata = dict(base.get("metadata") or {})
        metadata["previousMonitorState"] = compact_previous
        base["metadata"] = metadata
    inputs = [("", base)]
    for symbol in sorted(reasoning_snapshot_symbols(current)):
        inputs.append(
            (
                symbol,
                compact_monitor_state_for_reasoning_symbol(
                    current,
                    symbol,
                    settings=_runtime_settings,
                ),
            )
        )
    for symbol, payload in inputs:
        connection.execute(
            """
                INSERT INTO monitor_snapshot_reasoning_inputs (
                    account_id, generated_at, symbol, payload_json, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE generated_at = VALUES(generated_at),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
            (
                normalized_account_id,
                source_generated_at,
                str(symbol or ""),
                json_dumps(payload),
                updated_at,
            ),
        )
    cached_symbols = [str(symbol or "") for symbol, _payload in inputs]
    placeholders = ", ".join(["%s"] * len(cached_symbols))
    connection.execute(
        "DELETE FROM monitor_snapshot_reasoning_inputs "
        "WHERE account_id = %s AND symbol NOT IN (" + placeholders + ")",
        tuple([normalized_account_id] + cached_symbols),
    )
