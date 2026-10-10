"""Atomic case-event to notification-outbox adapter; transport stays in its worker."""
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
from digital_twin.shared_kernel.parsing import parse_assignments


class ResearchProgressPublication:
    def __init__(self, notifications, settings=None):
        self.notifications = notifications
        self.settings = dict(settings or {})

    def publish(self, connection, event):
        if not parse_assignments(self.settings.get("alertRules", ""), {"researchProgress": 1}).get("researchProgress", 1):
            return {"status": "disabled"}
        body = render_research_progress(event)
        job_id = "research-" + event["eventId"][:39]
        job = NotificationJob(job_id=job_id, account_id=event["accountId"], account_label="",
            message_type="researchProgress", text=body, dedupe_key=job_id,
            source_event_id=event["eventId"], source_event_name="ai_research.progress_recorded", context={
                "accountId": event["accountId"], "symbol": event["symbol"],
                "messageType": "researchProgress", "researchProgress": event,
                "notificationContent": {"kind": "research-progress", "body": body}})
        accepted = self.notifications.enqueue_with_connection(connection, job)
        return {"status": "queued" if accepted else "suppressed", "jobId": job_id}
