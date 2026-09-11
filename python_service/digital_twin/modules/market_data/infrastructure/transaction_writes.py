"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any, Dict, Iterable
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.settings import utc_now


def mark_reasoning_anchor_pending(
    connection: BoundWriteConnection,
    account_id: str,
    event_id: str,
    candidates: Iterable[Dict[str, object]],
    stamp: str,
):
    count = 0
    for candidate in candidates or []:
        item = dict(candidate or {}) if isinstance(candidate, dict) else {}
        observation = (
            item.get("marketObservation")
            if isinstance(item.get("marketObservation"), dict)
            else {}
        )
        symbol = str(item.get("symbol") or "").upper().strip()
        try:
            pending_price = float(observation.get("currentPrice") or 0)
            completed_price = float(
                observation.get("reasoningBaselinePrice")
                or observation.get("baselinePrice")
                or 0
            )
        except (TypeError, ValueError):
            continue
        if not symbol or pending_price <= 0:
            continue
        connection.execute(
            """
                INSERT INTO market_observation_reasoning_anchors (
                    account_id, symbol, completed_price, completed_at,
                    pending_price, pending_event_id, pending_at, updated_at
                ) VALUES (%s, %s, %s, '', %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    completed_price = CASE
                        WHEN completed_price > 0 THEN completed_price
                        ELSE VALUES(completed_price)
                    END,
                    pending_price = VALUES(pending_price),
                    pending_event_id = VALUES(pending_event_id),
                    pending_at = VALUES(pending_at),
                    updated_at = VALUES(updated_at)
                """,
            (
                str(account_id or ""),
                symbol,
                completed_price,
                pending_price,
                str(event_id or ""),
                str(stamp or utc_now()),
                str(stamp or utc_now()),
            ),
        )
        count += 1
    return count


def upsert_monitor_snapshot(
    connection: BoundWriteConnection,
    account_id: Any,
    generated_at: Any,
    state: Any,
    updated_at: Any,
):
    return connection.execute(
        """
            INSERT INTO monitor_snapshots (
                account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_label = VALUES(account_label), provider = VALUES(provider),
                mode = VALUES(mode), status = VALUES(status), generated_at = VALUES(generated_at),
                payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
            """,
        (
            account_id,
            str(state.get("accountLabel") or ""),
            str(state.get("provider") or ""),
            str(state.get("mode") or ""),
            str(state.get("status") or ""),
            generated_at,
            json_dumps(state),
            updated_at,
        ),
    )


def upsert_monitor_snapshot_history(
    connection: BoundWriteConnection,
    account_id: Any,
    generated_at: Any,
    state: Any,
    temporal_projection: Any,
    updated_at: Any,
):
    return connection.execute(
        """
            INSERT INTO monitor_snapshot_history (
                account_id, generated_at, payload_json, projection_payload_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json),
                projection_payload_json = VALUES(projection_payload_json), created_at = VALUES(created_at)
            """,
        (
            account_id,
            generated_at,
            json_dumps(state),
            json_dumps(temporal_projection),
            updated_at,
        ),
    )
