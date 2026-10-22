"""Small, receipt-confirmed input for the outcomes observer."""
import json
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection


class NotificationTimingReceiptSource(MySQLOperationalConnection):
    def after(self, account_id, cursor, limit=100):
        completed, identity = cursor
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT attempt_id, job_id, completed_at, JSON_EXTRACT(metadata_json, '$.notificationTiming') AS contract_json "
                "FROM notification_delivery_attempts WHERE status='delivered' "
                "AND (completed_at > %s OR (completed_at=%s AND attempt_id>%s)) "
                "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.notificationTiming.accountId'))=%s "
                "ORDER BY completed_at, attempt_id LIMIT %s",
                (completed, completed, identity, account_id, min(100, max(1, limit))),
            ).fetchall()
        return [{"receiptId": r["attempt_id"], "jobId": r["job_id"], "deliveredAt": r["completed_at"],
                 "contract": json.loads(r["contract_json"])} for r in rows]
