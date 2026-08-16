"""Notification lifecycle read model used by web diagnostics."""

from typing import Dict, List


class NotificationTraceQueryService:
    def __init__(self, store):
        self.store = store

    def trace_for_job(self, job_id: str) -> Dict[str, object]:
        lifecycle = (
            list(self.store.lifecycle_for_job(job_id) or [])
            if hasattr(self.store, "lifecycle_for_job")
            else []
        )
        attempts = (
            list(self.store.delivery_attempts_for_job(job_id) or [])
            if hasattr(self.store, "delivery_attempts_for_job")
            else []
        )
        timeline: List[Dict[str, object]] = []
        for event in lifecycle:
            timeline.append({
                "kind": "lifecycle",
                "id": str(event.get("eventId") or ""),
                "at": str(event.get("createdAt") or ""),
                "stage": str(event.get("stage") or ""),
                "outcome": str(event.get("outcome") or ""),
                "reason": str(event.get("reason") or ""),
                "metadata": dict(event.get("metadata") or {}),
            })
        for attempt in attempts:
            timeline.append({
                "kind": "deliveryAttemptStarted",
                "id": str(attempt.get("attemptId") or "") + ":started",
                "at": str(attempt.get("startedAt") or ""),
                "stage": "dispatching",
                "outcome": "started",
                "reason": "",
                "metadata": {
                    "channel": str(attempt.get("channel") or ""),
                    "audience": str(attempt.get("audience") or ""),
                    **dict(attempt.get("metadata") or {}),
                },
            })
            if str(attempt.get("completedAt") or ""):
                timeline.append({
                    "kind": "deliveryAttemptCompleted",
                    "id": str(attempt.get("attemptId") or "") + ":completed",
                    "at": str(attempt.get("completedAt") or ""),
                    "stage": "delivery_result",
                    "outcome": str(attempt.get("status") or ""),
                    "reason": str(attempt.get("reason") or ""),
                    "metadata": {
                        "channel": str(attempt.get("channel") or ""),
                        "audience": str(attempt.get("audience") or ""),
                        "provider": str(attempt.get("provider") or ""),
                        **dict(attempt.get("metadata") or {}),
                    },
                })
        timeline.sort(key=lambda item: (item.get("at") or "", item.get("id") or ""))
        for sequence, item in enumerate(timeline, start=1):
            item["sequence"] = sequence
        return {
            "contractVersion": "notification-trace-v1",
            "jobId": str(job_id or ""),
            "lifecycle": lifecycle,
            "deliveryAttempts": attempts,
            "timeline": timeline,
        }
