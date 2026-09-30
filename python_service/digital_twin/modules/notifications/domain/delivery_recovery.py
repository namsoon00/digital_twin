"""Policy for recovering notification transport failures without stale sends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Dict, Mapping, Optional


DELIVERY_RECOVERY_POLICY_VERSION = "notification-delivery-recovery-v1"

_CIRCUIT_UNTIL = re.compile(
    r"circuit open until\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
    re.IGNORECASE,
)
_TRANSIENT_MARKERS = (
    "circuit open",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "remote end closed",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)

_RECOVERY_TTL_MINUTES = {
    "investmentInsight": 180,
    "newsDigest": 360,
    "investmentCalendarReminder": 720,
    "marketObservation": 30,
    "externalDataConnection": 30,
    "monitorConnection": 30,
    "monitorHeartbeat": 15,
    "workHandoff": 1440,
}


def _parse_time(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def transient_delivery_failure(error: object) -> bool:
    text = str(error or "").strip().lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def notification_failure_retry_at(
    error: object,
    attempts: int,
    *,
    now: datetime = None,
    retry_after_seconds: int = 0,
) -> str:
    """Return the next safe retry clock for a transient transport failure."""

    text = str(error or "").strip()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    server_floor = current + timedelta(seconds=max(0, int(retry_after_seconds or 0)))
    match = _CIRCUIT_UNTIL.search(text)
    if match:
        parsed = _parse_time(match.group(1))
        if parsed and parsed > current:
            return max(parsed, server_floor).isoformat().replace("+00:00", "Z")
    if not transient_delivery_failure(text):
        return server_floor.isoformat().replace("+00:00", "Z") if retry_after_seconds else ""
    delay_seconds = min(900, 15 * (2 ** max(0, min(6, int(attempts or 1) - 1))))
    return max(server_floor, current + timedelta(seconds=delay_seconds)).isoformat().replace("+00:00", "Z")


def terminal_delivery_recovery_decision(
    job: Mapping[str, object],
    *,
    now: datetime = None,
) -> Dict[str, object]:
    """Decide whether an exhausted failure may be retried without stale delivery."""

    values = dict(job or {})
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created_at = _parse_time(values.get("createdAt") or values.get("created_at"))
    age_minutes = (
        max(0.0, (current - created_at).total_seconds() / 60.0)
        if created_at
        else float("inf")
    )
    message_type = str(values.get("messageType") or values.get("message_type") or "notification")
    ttl_minutes = int(_RECOVERY_TTL_MINUTES.get(message_type, 60))
    context = values.get("context") if isinstance(values.get("context"), Mapping) else {}
    recovery = context.get("deliveryRecovery") if isinstance(context.get("deliveryRecovery"), Mapping) else {}
    recovery_count = int(recovery.get("count") or 0)
    error = str(values.get("lastError") or values.get("last_error") or "")
    retry_at = notification_failure_retry_at(error, int(values.get("attempts") or 0), now=current,
        retry_after_seconds=int(context.get("deliveryRetryAfterSeconds") or 0))
    if not transient_delivery_failure(error):
        action, reason = "retain-failed", "non-transient-delivery-failure"
    elif age_minutes > ttl_minutes:
        action, reason = "supersede", "delivery-value-window-expired"
    elif recovery_count >= 1:
        action, reason = "retain-failed", "delivery-recovery-budget-exhausted"
    else:
        action, reason = "retry", "transient-delivery-recovery"
    return {
        "version": DELIVERY_RECOVERY_POLICY_VERSION,
        "action": action,
        "reasonCode": reason,
        "messageType": message_type,
        "ageMinutes": round(age_minutes, 2) if age_minutes != float("inf") else None,
        "ttlMinutes": ttl_minutes,
        "recoveryCount": recovery_count,
        "retryAt": retry_at,
    }
