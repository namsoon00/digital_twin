"""Project completed AI insights into independently deliverable notifications."""

from __future__ import annotations

from typing import Dict, Mapping

from digital_twin.modules.decisions.contracts import ai_insight_handoff, reconciliation_after_delivery
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.portfolio.contracts import utc_now_iso


AI_INSIGHT_NOTIFICATION_PROJECTION_VERSION = "ai-insight-notification-projection-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


class AIInsightNotificationProjectionService:
    """Own the AI-insight-to-notification policy boundary.

    Queue infrastructure may persist the prepared outbox job atomically, but it
    does not inspect investment decisions or construct user-facing jobs.
    """

    def prepare(self, request, context: Mapping[str, object]) -> Dict[str, object]:
        values = _mapping(context)
        reconciliation = _mapping(values.get("decisionReconciliation"))
        if _text(reconciliation.get("notificationDecision")).lower() != "send":
            return {
                "version": AI_INSIGHT_NOTIFICATION_PROJECTION_VERSION,
                "status": "web-only",
                "reason": _text(reconciliation.get("reason")),
                "notificationJob": None,
            }

        handoff = ai_insight_handoff(values)
        if handoff is None or not handoff.valid:
            errors = ", ".join(handoff.validation_errors if handoff else ("missing-handoff",))
            raise RuntimeError("AI insight notification handoff is invalid: " + errors)
        request_job_id = _text(getattr(request, "notification_job_id", ""))
        if handoff.reserved_notification_job_id != request_job_id:
            raise RuntimeError("AI insight notification reservation does not match the request.")

        draft = dict(handoff.notification_draft or {})
        job = NotificationJob.from_dict({
            **draft,
            "jobId": request_job_id,
            "context": values,
            "status": "pending",
            "updatedAt": _text(
                _mapping(values.get("notificationAiQueue")).get("completedAt")
            ) or utc_now_iso(),
        })
        return {
            "version": AI_INSIGHT_NOTIFICATION_PROJECTION_VERSION,
            "status": "notification-requested",
            "reason": _text(reconciliation.get("reason")),
            "notificationJob": job,
        }

    def reconcile(
        self,
        context: Mapping[str, object],
        delivery_outcome: Mapping[str, object],
    ) -> Dict[str, object]:
        values = _mapping(context)
        outcome = _mapping(delivery_outcome)
        return {
            "decisionReconciliation": reconciliation_after_delivery(
                _mapping(values.get("decisionReconciliation")),
                outcome,
            ),
            "aiInsightNotificationProjection": {
                "version": AI_INSIGHT_NOTIFICATION_PROJECTION_VERSION,
                "status": _text(outcome.get("status")) or "web-only",
                "queued": bool(outcome.get("queued")),
                "notificationJobId": _text(outcome.get("notificationJobId")),
                "reason": _text(outcome.get("reason")),
                "decisionOwner": "application",
                "persistenceOwner": "infrastructure",
            },
        }
