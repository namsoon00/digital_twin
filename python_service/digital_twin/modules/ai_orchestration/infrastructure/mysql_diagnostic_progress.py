"""Durable recovery cursors over exact observe/next/wait/recovery task lineage."""
import json
from ..domain.planning import identity, stamp
from ..domain.diagnostic_progress import successor_key


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_observation_diagnostic_followups (
    root_job_id VARCHAR(191) PRIMARY KEY, account_id VARCHAR(191) NOT NULL,
    symbol VARCHAR(64) NOT NULL, world_id VARCHAR(191) NOT NULL,
    root_task_id VARCHAR(64) NOT NULL, cursor_task_id VARCHAR(64) NOT NULL,
    closed TINYINT NOT NULL DEFAULT 0, state_json TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
    INDEX diagnostic_followups_open(closed,updated_at),
    INDEX diagnostic_followups_subject(account_id,symbol,closed)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_observation_diagnostic_events (
    event_id VARCHAR(64) PRIMARY KEY, root_job_id VARCHAR(191) NOT NULL,
    payload_json TEXT NOT NULL, created_at VARCHAR(40) NOT NULL,
    INDEX diagnostic_events_root(root_job_id,created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value or {}


class DiagnosticProgressPersistence:
    @staticmethod
    def diagnostic_task(connection, task_id, scope):
        row = connection.execute("SELECT task_id,account_id,symbol,status,available_at,updated_at,last_error,payload_json,result_json "
            "FROM ai_control_tasks WHERE task_id=%s AND capability='observe'", (task_id,)).fetchone()
        if not row:
            return None
        payload = decode(row["payload_json"])
        if (row["account_id"], row["symbol"], payload.get("worldId")) != (scope["account_id"], scope["symbol"], scope["world_id"]):
            return None
        return {"taskId": row["task_id"], "status": row["status"], "availableAt": row["available_at"],
                "updatedAt": row["updated_at"], "lastError": row["last_error"], "result": decode(row["result_json"])}

    def diagnostic_chain(self, connection, scope, target="", limit=24):
        task = self.diagnostic_task(connection, scope["cursor_task_id"], scope)
        deliveries = {}
        visited = set()
        for _ in range(limit):
            publication = ((task or {}).get("result") or {}).get("publication") or {}
            if publication.get("status") == "queued" and publication.get("jobId"):
                deliveries[publication["jobId"]] = task["taskId"]
            if task:
                task["deliveries"] = dict(deliveries)
            if not task or task["taskId"] == target:
                return task
            visited.add(task["taskId"])
            suffix = successor_key(task)
            result = task.get("result") or {}
            child_id = (result.get("replacementTaskId") if result.get("status") == "superseded"
                        else identity(task["taskId"], suffix) if suffix else "")
            child = self.diagnostic_task(connection, child_id, scope) if child_id and child_id not in visited else None
            if not child:
                return task if not target else None
            if not target and child["status"] == "pending" and not child.get("lastError"):
                task["nextCheckAt"] = child["availableAt"]
                return task
            task = child
        if task:
            task["deliveries"] = deliveries
            task["lineageIncomplete"] = True
        return task if not target else None

    def diagnostic_ancestor(self, connection, task):
        rows = connection.execute("SELECT * FROM ai_observation_diagnostic_followups "
            "WHERE account_id=%s AND symbol=%s AND world_id=%s AND closed=0 ORDER BY created_at DESC LIMIT 12",
            (task["accountId"], task["symbol"], task["worldId"])).fetchall()
        for scope in rows:
            found = self.diagnostic_chain(connection, scope, task["taskId"])
            if found and found["taskId"] == task["taskId"] and task["taskId"] != scope["root_task_id"]:
                return scope
        return None

    @staticmethod
    def register_diagnostic_followup(connection, job_id, task, result):
        world = (result.get("input") or {}).get("worldId") or task.get("worldId")
        if not world or not all(task.get(k) for k in ("accountId", "symbol", "taskId")):
            return
        state = {"name": task.get("name") or task["symbol"], "revision": 0, "fingerprint": ""}
        connection.execute("INSERT IGNORE INTO ai_observation_diagnostic_followups "
            "(root_job_id,account_id,symbol,world_id,root_task_id,cursor_task_id,state_json,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (job_id, task["accountId"], task["symbol"], world,
                task["taskId"], task["taskId"], json.dumps(state, ensure_ascii=False), stamp(), stamp()))

    @staticmethod
    def save_diagnostic_progress(connection, scope, state, cursor, event=None):
        if event:
            connection.execute("INSERT INTO ai_observation_diagnostic_events (event_id,root_job_id,payload_json,created_at) VALUES (%s,%s,%s,%s)",
                (event["eventId"], scope["root_job_id"], json.dumps(event, ensure_ascii=False), stamp()))
        connection.execute("UPDATE ai_observation_diagnostic_followups SET cursor_task_id=%s,closed=%s,state_json=%s,updated_at=%s WHERE root_job_id=%s",
            (cursor, int(state.get("closed", False)), json.dumps(state, ensure_ascii=False), stamp(), scope["root_job_id"]))
