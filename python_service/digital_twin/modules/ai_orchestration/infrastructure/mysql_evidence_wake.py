"""Coalesced evidence mailbox; unstarted work wakes without disturbing retry leases."""
import json
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.modules.ai_orchestration.domain.planning import identity


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_control_evidence_events (
    event_id VARCHAR(191) PRIMARY KEY, processed_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS ai_control_evidence_wakes (
    scope_id VARCHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL,
    world_id VARCHAR(191) NOT NULL, snapshot_id VARCHAR(191) NOT NULL, event_id VARCHAR(191) NOT NULL,
    occurred_at VARCHAR(40) NOT NULL, pending TINYINT NOT NULL DEFAULT 1,
    INDEX ai_evidence_wake_pending(pending,account_id,symbol)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
)


class MySQLEvidenceWakeStore(MySQLOperationalConnection):
    def __init__(self, settings=None):
        super().__init__(settings)
        with self.connect() as connection:
            for sql in SCHEMA:
                connection.execute(sql)

    @staticmethod
    def record(connection, event, target):
        key = identity(target["accountId"], target["symbol"], target["worldId"])
        row = connection.execute("SELECT snapshot_id,event_id,occurred_at FROM ai_control_evidence_wakes WHERE scope_id=%s FOR UPDATE", (key,)).fetchone()
        if row and (row["occurred_at"], row["event_id"]) >= (event["occurred_at"], event["event_id"]):
            return
        if row:
            connection.execute("UPDATE ai_control_evidence_wakes SET pending=IF(snapshot_id<>%s,1,pending),"
                "snapshot_id=%s,event_id=%s,occurred_at=%s WHERE scope_id=%s",
                (target["sourceSnapshotId"], target["sourceSnapshotId"], event["event_id"], event["occurred_at"], key))
        else:
            connection.execute("INSERT INTO ai_control_evidence_wakes "
                "(scope_id,account_id,symbol,world_id,snapshot_id,event_id,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (key, target["accountId"], target["symbol"], target["worldId"], target["sourceSnapshotId"], event["event_id"], event["occurred_at"]))

    @staticmethod
    def wake_pending(connection, subjects):
        woken = 0
        for subject in subjects:
            key = identity(subject["accountId"], subject["symbol"], subject["worldId"])
            row = connection.execute("SELECT * FROM ai_control_evidence_wakes WHERE scope_id=%s AND pending=1 FOR UPDATE", (key,)).fetchone()
            if not row:
                continue
            scope = (subject["accountId"], subject["symbol"], subject["worldId"])
            tasks = connection.execute("SELECT task_id,available_at FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s AND capability='observe' "
                "AND status='pending' AND attempts=0 FOR UPDATE", scope).fetchall()
            if not tasks:
                continue  # A processing/retrying task leaves this wake for its successor.
            failed = connection.execute("SELECT task_id FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s AND capability='observe' AND status='failed' "
                "ORDER BY updated_at DESC,task_id DESC LIMIT 1", scope).fetchone()
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            recovery_id = identity(failed["task_id"], "recovery") if failed else ""
            # Terminal recovery is a new, unattempted task with a deliberate
            # delay. Its stable parent identity also protects pre-upgrade jobs.
            tasks = [task for task in tasks if task["task_id"] != recovery_id or task["available_at"] <= now]
            if not tasks:
                continue
            recent = connection.execute("SELECT MAX(updated_at) AS last_at FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s AND capability='observe' AND status='completed'", scope).fetchone()
            due = datetime.now(timezone.utc)
            if recent and recent["last_at"]:
                due = max(due, datetime.fromisoformat(recent["last_at"].replace("Z", "+00:00")) + timedelta(minutes=15))
            due_at = due.isoformat().replace("+00:00", "Z")
            receipt = json.dumps({"eventId": row["event_id"], "sourceSnapshotId": row["snapshot_id"],
                                  "occurredAt": row["occurred_at"], "reason": "committed-typedb-evidence"})
            for task in tasks:
                connection.execute("UPDATE ai_control_tasks SET available_at=LEAST(available_at,%s),priority=GREATEST(priority,3),"
                    "payload_json=JSON_SET(payload_json,'$.evidenceWake',CAST(%s AS JSON)) WHERE task_id=%s",
                    (due_at, receipt, task["task_id"]))
            connection.execute("UPDATE ai_control_evidence_wakes SET pending=0 WHERE scope_id=%s", (key,))
            woken += len(tasks)
        return woken
