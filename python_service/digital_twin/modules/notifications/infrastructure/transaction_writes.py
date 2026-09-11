"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any, Callable, Dict, Iterable
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.settings import utc_now
from digital_twin.modules.portfolio.contracts import AlertEvent
from digital_twin.infrastructure.mysql_operational_helpers import _sent_key_hash


def set_ai_delivery_status(
    connection: BoundWriteConnection,
    notification_job_id: str,
    status: str,
    error: str,
    context_updates: Dict[str, object],
    replacement_context: Dict[str, object],
    only_if_statuses: Any,
    attempts_delta: int,
    *,
    _bound__clean: Callable[..., Any],
    _bound__compact_notification_payload: Callable[..., Any],
    _bound__notification_job_from_row: Callable[..., Any],
):
    row = connection.execute(
        "SELECT status, text, payload_json FROM notification_jobs WHERE job_id = %s FOR UPDATE",
        (_bound__clean(notification_job_id),),
    ).fetchone()
    if not row:
        return False
    if only_if_statuses and _bound__clean(row.get("status")) not in set(
        only_if_statuses
    ):
        return False
    job = _bound__notification_job_from_row(row)
    context = (
        dict(replacement_context)
        if isinstance(replacement_context, dict)
        else dict(job.context or {})
    )
    for key, value in dict(context_updates or {}).items():
        if isinstance(value, dict) and isinstance(context.get(key), dict):
            context[key] = {**dict(context.get(key) or {}), **value}
        else:
            context[key] = value
    job.context = context
    job.status = _bound__clean(status) or job.status
    job.attempts = max(0, int(job.attempts or 0) + int(attempts_delta or 0))
    job.updated_at = utc_now()
    job.last_error = _bound__clean(error)
    cursor = connection.execute(
        """
            UPDATE notification_jobs
            SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                processing_started_at = '', retry_at = '', payload_json = %s
            WHERE job_id = %s
            """,
        (
            job.status,
            job.attempts,
            job.updated_at,
            job.last_error,
            json_dumps(_bound__compact_notification_payload(job)),
            job.job_id,
        ),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def mark_monitor_alerts_sent(
    connection: BoundWriteConnection,
    events: Iterable[AlertEvent],
    stamp: str,
    *,
    _sent_entries: Callable[..., Any],
):
    entries = _sent_entries(events, stamp)
    for key, sent_at in entries.items():
        connection.execute(
            """
                INSERT INTO monitor_sent (sent_key_hash, sent_key, sent_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE sent_at = VALUES(sent_at)
                """,
            (_sent_key_hash(key), key, sent_at),
        )
    return entries
