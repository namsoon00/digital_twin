"""Durable collection cadence, independent of calendar event confirmation."""

from datetime import timedelta

from digital_twin.platform.application.runtime_checkpoint import checkpoint_datetime, checkpoint_iso


def collection_due(state, now):
    due_at = checkpoint_datetime(state or {}, "nextAttemptAt")
    return due_at is None or now >= due_at


def collection_attempt(previous, now, interval_seconds, failed=False):
    previous = previous or {}
    failures = min(20, int(previous.get("failureCount") or 0) + 1) if failed else 0
    # A failed source must not wait for the normal 12-hour cycle. Persist the
    # backoff so restarts cannot hammer a blocked issuer or reset its failures.
    delay = min(3600, 300 * 2 ** min(failures - 1, 4)) if failed else interval_seconds
    return {
        "lastAttemptAt": checkpoint_iso(now),
        "lastSuccessAt": previous.get("lastSuccessAt", "") if failed else checkpoint_iso(now),
        "nextAttemptAt": checkpoint_iso(now + timedelta(seconds=delay)),
        "failureCount": failures,
    }
