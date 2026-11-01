"""Transactional, idempotent activity outbox owned by central AI."""
import json
from ..domain import activity
from ..domain.planning import stamp, identity

SCHEMA = """CREATE TABLE IF NOT EXISTS ai_activity_events (
    event_id VARCHAR(64) PRIMARY KEY, payload_json TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL, published_at VARCHAR(40) NOT NULL DEFAULT '',
    INDEX ai_activity_pending(published_at,created_at,event_id),
    INDEX ai_activity_created(created_at,event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def append_activity(connection, settings, stage, source_id, subject=None, **details):
    if not activity.enabled(settings):
        return None
    value = activity.event(stage, source_id, subject, details)
    connection.execute("INSERT IGNORE INTO ai_activity_events (event_id,payload_json,created_at) VALUES (%s,%s,%s)",
                       (value["eventId"], json.dumps(value, ensure_ascii=False), value["at"]))
    return value


class AIActivityPersistence:
    def activity(self, connection, stage, source_id, subject=None, **details):
        return append_activity(connection, self.runtime_settings, stage, source_id, subject, **details)

    def record_activity(self, job, stage, **details):
        if not activity.enabled(self.runtime_settings):
            return
        with self.transaction() as connection:
            owned = connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s AND status='processing' "
                "AND lease_token=%s AND lease_until>=%s FOR UPDATE", (job["taskId"], job["leaseToken"], stamp())).fetchone()
            if owned:
                self.activity(connection, stage, identity(job["taskId"], job["attempts"], stage), job,
                              attempt=job["attempts"], capability=job["capability"], **details)

    def activity_subject(self, connection, task_id):
        if not task_id or not activity.enabled(self.runtime_settings):
            return {}
        row = connection.execute("SELECT task_id,account_id,symbol,capability,"
            "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.name')) AS name FROM ai_control_tasks WHERE task_id=%s",
            (task_id,)).fetchone()
        return {"taskId": task_id, "accountId": row["account_id"], "symbol": row["symbol"],
                "name": row["name"] or row["symbol"]} if row else {}
