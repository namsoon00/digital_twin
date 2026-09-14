import json
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection


class MySQLInformationFollowups(MySQLOperationalConnection):
    def __init__(self, settings, notification_writer=None):
        super().__init__(settings)
        self.notification_writer = notification_writer

    @staticmethod
    def decode(row):
        if not row:
            return None
        result = json.loads(row["payload_json"])
        result["rowVersion"] = row["updated_at"]
        result["status"] = row["status"]
        return result

    def get(self, identity):
        with self.connect() as connection:
            return self.decode(connection.execute("SELECT * FROM information_followups WHERE tracking_id = %s", (identity,)).fetchone())

    def latest(self, kind, source_id):
        with self.connect() as connection:
            return self.decode(connection.execute(
                "SELECT * FROM information_followups WHERE source_kind = %s AND source_id = %s ORDER BY created_at DESC LIMIT 1",
                (kind, source_id),
            ).fetchone())

    def register(self, row):
        with self.transaction() as connection:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat().replace('+00:00', 'Z')
            connection.execute("DELETE FROM information_followups WHERE status != 'active' AND updated_at < %s LIMIT 100", (cutoff,))
            cursor = connection.execute(
                "INSERT IGNORE INTO information_followups (tracking_id, source_kind, source_id, source_hash, status, next_check_at, payload_json, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row["trackingId"], row["sourceKind"], row["sourceId"], row["sourceHash"], row["status"], row["nextCheckAt"], json.dumps(row, ensure_ascii=False), row["createdAt"], row["updatedAt"]),
            )
            inserted = bool(cursor.rowcount)
            if inserted:
                connection.execute("UPDATE information_followups SET status = 'superseded', updated_at = %s WHERE source_kind = %s AND source_id = %s AND tracking_id != %s AND status = 'active'",
                    (row["updatedAt"], row["sourceKind"], row["sourceId"], row["trackingId"]))
            return inserted

    def due(self, now, limit=25):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM information_followups WHERE status = 'active' AND next_check_at <= %s ORDER BY next_check_at, tracking_id LIMIT %s", (now, limit)).fetchall()
        return [self.decode(row) for row in rows]

    def summary(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count, MAX(updated_at) AS last_checked_at FROM information_followups GROUP BY status").fetchall()
        return {"states": [{"status": row["status"], "count": row["count"], "lastCheckedAt": row["last_checked_at"]} for row in rows]}

    def complete(self, row, event, phases):
        with self.transaction() as connection:
            current = connection.execute("SELECT updated_at, status FROM information_followups WHERE tracking_id = %s FOR UPDATE", (row["trackingId"],)).fetchone()
            if not current or current["status"] != "active" or current["updated_at"] != row.get("rowVersion"):
                return False
            if phases:
                insert_domain_event_with_connection(connection, event)
                if self.notification_writer:
                    row["delivery"] = self.notification_writer(connection, row, event, phases)
            saved = {key: value for key, value in row.items() if key != "rowVersion"}
            connection.execute("UPDATE information_followups SET status = %s, next_check_at = %s, payload_json = %s, updated_at = %s WHERE tracking_id = %s",
                (row["status"], row["nextCheckAt"], json.dumps(saved, ensure_ascii=False), row["updatedAt"], row["trackingId"]))
        return True
