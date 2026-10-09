"""Resume current observations without inventing checks during an unobserved gap."""
from .execution_resilience import instant


REGULAR_KEYS = {"accountId", "symbol", "name", "worldId", "marketWorldId", "capability",
                "taskId", "availableAt", "priority", "watchQuestions"}


def same_regular_observation(left, right):
    # Research, case/evidence wakes and previously attempted work retain their own audit.
    return (set(left) <= REGULAR_KEYS and set(right) <= REGULAR_KEYS
            and left.get("capability") == right.get("capability") == "observe"
            and all(left.get(key) == right.get(key) for key in ("accountId", "symbol", "worldId"))
            and left.get("watchQuestions", []) == right.get("watchQuestions", []))


def observation_resume(job, packet):
    scheduled, claimed = instant(job.get("scheduledAt")), instant(job.get("claimedAt"))
    return {"version": "current-observation-resume-v1", "mode": "latest-available-snapshot",
            "scheduledAt": job.get("scheduledAt", ""), "claimedAt": job.get("claimedAt", ""),
            "capturedAt": packet.get("capturedAt", ""), "sourceSnapshotId": packet.get("sourceSnapshotId", ""),
            "scheduleDelaySeconds": max(0, (claimed - scheduled).total_seconds()) if scheduled and claimed else None,
            "historicalChecksReplayed": False}
