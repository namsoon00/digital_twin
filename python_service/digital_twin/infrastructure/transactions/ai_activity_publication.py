"""Bridge committed AI operational events into the existing delivery outbox."""
import json
from digital_twin.modules.ai_orchestration.contracts import ai_activity_notifications_enabled
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.application.ai_activity_message import render_ai_activity


class AIActivityPublication:
    def __init__(self, control, notifications, settings):
        self.control, self.notifications, self.settings = control, notifications, settings

    def run_once(self):
        if not ai_activity_notifications_enabled(self.settings):
            return {"status": "disabled", "queued": 0}
        count = 0
        with self.control.transaction() as connection:
            rows = connection.execute("SELECT event_id,payload_json FROM ai_activity_events FORCE INDEX(ai_activity_pending) "
                "WHERE published_at='' ORDER BY created_at,event_id LIMIT 50 FOR UPDATE SKIP LOCKED").fetchall()
            for row in rows:
                value = json.loads(row["payload_json"])
                body = render_ai_activity(value)
                job_id = "ai-activity-" + row["event_id"][:36]
                job = NotificationJob(job_id=job_id, account_id=value["accountId"], account_label="",
                    message_type="aiActivity", text=body, dedupe_key=job_id,
                    source_event_id=row["event_id"], source_event_name=value["eventName"], context={
                        "messageType": "aiActivity", "accountId": value["accountId"], "symbol": value["symbol"],
                        "name": value["name"], "aiActivity": value,
                        "notificationContent": {"kind": "report", "body": body}})
                accepted = self.notifications.enqueue_with_connection(connection, job)
                if not accepted:
                    existing = connection.execute("SELECT job_id FROM notification_jobs WHERE job_id=%s", (job_id,)).fetchone()
                    if not existing:
                        raise RuntimeError("AI activity notification was not persisted")
                connection.execute("UPDATE ai_activity_events SET published_at=%s WHERE event_id=%s", (stamp(), row["event_id"]))
                count += bool(accepted)
        return {"status": "completed", "queued": count}
