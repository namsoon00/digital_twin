"""Heartbeat coverage is observed availability, never inferred historical uptime."""
from datetime import timedelta
from .execution_resilience import instant, stamp

PULSE_GAP_SECONDS = 150


def runtime_coverage(rows, start, end):
    spans = []
    for row in rows:
        left, right = instant(row.get("started_at")), instant(row.get("last_seen_at"))
        if left and right and right >= left and right >= start and left <= end:
            spans.append((max(start, left), min(end, right)))
    merged = []
    for left, right in sorted(spans):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    seconds = sum((right - left).total_seconds() for left, right in merged)
    return {"version": "ai-runtime-coverage-v1", "windowStart": stamp(start), "windowEnd": stamp(end),
            "observedActiveSeconds": seconds, "unobservedSeconds": max(0, (end - start).total_seconds() - seconds),
            "intervalCount": len(merged), "exactDowntimeSeconds": None,
            "basis": "heartbeat-coverage", "maxPulseGapSeconds": PULSE_GAP_SECONDS}


def runtime_view(state, now):
    seen, started = instant(state.get("lastSeenAt")), instant(state.get("startedAt"))
    live = bool(seen and started and started <= seen <= now and now - seen <= timedelta(seconds=PULSE_GAP_SECONDS))
    return {"status": "observed" if live else "unconfirmed", "startedAt": state.get("startedAt", ""),
            "lastSeenAt": state.get("lastSeenAt", ""), "previousLastSeenAt": state.get("previousLastSeenAt", ""),
            "shutdownAt": None, "live": live}
