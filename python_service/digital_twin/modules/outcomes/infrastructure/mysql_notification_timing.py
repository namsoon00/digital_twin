"""Durable observations independent of investment decision/qualification rows."""
import json
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection


class MySQLNotificationTimingStore(MySQLOperationalConnection):
    def cursor(self, account_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT delivered_at, receipt_id FROM investment_notification_timing WHERE account_id=%s "
                "ORDER BY delivered_at DESC, receipt_id DESC LIMIT 1", (account_id,),
            ).fetchone()
        return (row["delivered_at"], row["receipt_id"]) if row else ("", "")

    def register(self, row, now):
        contract = row["contract"]
        with self.transaction() as connection:
            previous = connection.execute("SELECT receipt_id FROM investment_notification_timing WHERE job_id=%s LIMIT 1", (row["jobId"],)).fetchone()
            state = "duplicate-receipt" if previous and previous["receipt_id"] != row["receiptId"] else "awaiting-entry"
            payload = {**row, "state": state, "nextCheckAt": row["deliveredAt"], "outcomes": {}}
            connection.execute(
                "INSERT IGNORE INTO investment_notification_timing "
                "(receipt_id,job_id,account_id,symbol,delivered_at,state,next_check_at,payload_json,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row["receiptId"], row["jobId"], contract["accountId"], contract["symbol"], row["deliveredAt"],
                 state, row["deliveredAt"], json.dumps(payload, ensure_ascii=False), now),
            )

    def pending(self, account_id, now):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json,updated_at FROM investment_notification_timing WHERE account_id=%s "
                "AND state IN ('awaiting-entry','awaiting-outcomes') AND next_check_at<=%s "
                "ORDER BY next_check_at,receipt_id LIMIT 100", (account_id, now),
            ).fetchall()
        return [{**json.loads(row["payload_json"]), "_expectedPayload": row["payload_json"]} for row in rows]

    def repairable_entries(self, account_id, receipt_ids):
        ids = list(dict.fromkeys(receipt_ids))
        if not ids:
            return []
        if len(ids) > 100:
            raise ValueError("Repair at most 100 explicit receipts per call")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_notification_timing WHERE account_id=%s "
                "AND state='unmeasurable' AND receipt_id IN (" + ",".join(["%s"] * len(ids)) + ")",
                (account_id, *ids),
            ).fetchall()
        candidates = [{**json.loads(row["payload_json"]), "_expectedPayload": row["payload_json"]} for row in rows]
        return [row for row in candidates if not row.get("entry")
                and row.get("reason") == "post-delivery-quote-unavailable"]

    def save(self, row, now):
        payload = {key: value for key, value in row.items() if key != "_expectedPayload"}
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE investment_notification_timing SET state=%s,next_check_at=%s,payload_json=%s,updated_at=%s "
                "WHERE receipt_id=%s AND account_id=%s AND payload_json=%s",
                (row["state"], row.get("nextCheckAt") or "", json.dumps(payload, ensure_ascii=False), now,
                 row["receiptId"], row["contract"]["accountId"], row["_expectedPayload"]),
            )
            return result.rowcount == 1

    def report_rows(self, account_id="", symbol="", limit=100):
        clauses, params = ["state != 'duplicate-receipt'"], []
        for field, value in (("account_id", account_id), ("symbol", symbol)):
            if value:
                clauses.append(field + "=%s")
                params.append(value)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_notification_timing WHERE " + " AND ".join(clauses)
                + " ORDER BY delivered_at DESC LIMIT %s", (*params, min(200, max(1, int(limit)))),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
