"""Central work ledger; completion and successor scheduling share one transaction."""
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.modules.ai_orchestration.domain.planning import bounded, identity, stamp


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_control_tasks (
    task_id VARCHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL,
    capability VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, priority INT NOT NULL DEFAULT 0,
    available_at VARCHAR(40) NOT NULL, lease_token VARCHAR(64) NOT NULL DEFAULT '',
    lease_until VARCHAR(40) NOT NULL DEFAULT '', attempts INT NOT NULL DEFAULT 0,
    payload_json LONGTEXT NOT NULL, result_json LONGTEXT NOT NULL, last_error VARCHAR(100) NOT NULL DEFAULT '',
    created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
    INDEX ai_control_due(status, available_at), INDEX ai_control_subject(account_id, symbol, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_control_calls (
    call_id VARCHAR(64) PRIMARY KEY, workload VARCHAR(64) NOT NULL, status VARCHAR(24) NOT NULL,
    task_id VARCHAR(64) NOT NULL DEFAULT '', prompt_hash VARCHAR(64) NOT NULL,
    started_at VARCHAR(40) NOT NULL, completed_at VARCHAR(40) NOT NULL DEFAULT '',
    error_kind VARCHAR(100) NOT NULL DEFAULT '', INDEX ai_calls_started(started_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_control_budget (
    day_key VARCHAR(32) PRIMARY KEY, used_count INT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


class MySQLAIControlStore(MySQLOperationalConnection):
    def __init__(self, settings=None):
        super().__init__(settings)
        with self.connect() as connection:
            for sql in SCHEMA:
                connection.execute(sql)

    @staticmethod
    def insert(connection, job):
        now = stamp()
        connection.execute(
            "INSERT IGNORE INTO ai_control_tasks (task_id,account_id,symbol,capability,status,priority,available_at,payload_json,result_json,created_at,updated_at) VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,'{}',%s,%s)",
            (job["taskId"], job["accountId"], job["symbol"], job["capability"], int(job.get("priority", 0)),
             job.get("availableAt", now), json.dumps(job, ensure_ascii=False), now, now),
        )

    def seed(self, subject):
        if not all(subject.get(key) for key in ("accountId", "symbol", "worldId")):
            return
        # A persistent root identity prevents periodic worker ticks from creating new work.
        job = {**subject, "capability": "observe", "taskId": identity("watch-v1", subject["accountId"], subject["symbol"])}
        with self.transaction() as connection:
            self.insert(connection, job)
            latest = connection.execute("SELECT task_id,status,result_json FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability='observe' ORDER BY updated_at DESC,task_id DESC LIMIT 1 FOR UPDATE", (subject["accountId"], subject["symbol"])).fetchone()
            if latest and latest["status"] == "completed" and json.loads(latest["result_json"]).get("status") == "retired":
                self.insert(connection, {**job, "taskId": identity(latest["task_id"], subject["worldId"], "rejoin")})

    def claim(self):
        now = stamp()
        day = now[:10]
        maximum = bounded(self.runtime_settings.get("aiControlDailyTaskBudget"), 48, 0, 200)
        with self.transaction() as connection:
            connection.execute("INSERT IGNORE INTO ai_control_budget (day_key,used_count) VALUES (%s,0)", (day,))
            budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
            if int(budget["used_count"]) >= maximum:
                return None
            row = connection.execute(
                "SELECT * FROM ai_control_tasks WHERE (status='pending' AND available_at<=%s) OR (status='processing' AND lease_until<%s) ORDER BY priority DESC,available_at,task_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (now, now),
            ).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
            connection.execute("UPDATE ai_control_tasks SET status='processing',lease_token=%s,lease_until=%s,attempts=attempts+1,updated_at=%s WHERE task_id=%s", (token, until, now, row["task_id"]))
            connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            return {**json.loads(row["payload_json"]), "leaseToken": token, "attempts": int(row["attempts"]) + 1}

    def heartbeat(self, job):
        until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            return bool(connection.execute("UPDATE ai_control_tasks SET lease_until=%s WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s", (until, job["taskId"], job["leaseToken"], stamp())).rowcount)

    @contextmanager
    def keep_alive(self, job):
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(45):
                try:
                    if not self.heartbeat(job):
                        return
                except Exception:
                    return  # Completion still checks the lease; lost ownership cannot publish.
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)

    def complete(self, job, result, children):
        now = stamp()
        with self.transaction() as connection:
            cursor = connection.execute("UPDATE ai_control_tasks SET status='completed',result_json=%s,updated_at=%s,lease_token='',lease_until='' WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                                        (json.dumps(result, ensure_ascii=False), now, job["taskId"], job["leaseToken"], now))
            if not cursor.rowcount:
                return False
            for child in children:
                self.insert(connection, child)
            return True

    def fail(self, job, error_kind):
        terminal = job["attempts"] >= 3
        due = (datetime.now(timezone.utc) + timedelta(minutes=30 * min(job["attempts"], 3))).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status=%s,last_error=%s,available_at=%s,updated_at=%s,lease_token='',lease_until='' WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                ("failed" if terminal else "pending", error_kind[:100], due, stamp(), job["taskId"], job["leaseToken"], stamp())).rowcount
            if changed and terminal and job["capability"] == "observe":
                next_due = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
                self.insert(connection, {**{k: job[k] for k in ("accountId", "symbol", "name", "worldId")},
                    "capability": "observe", "taskId": identity(job["taskId"], "recovery"), "availableAt": next_due})

    def memory(self, account_id, symbol):
        with self.connect() as connection:
            rows = connection.execute("SELECT result_json,updated_at FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability='observe' AND status='completed' AND JSON_EXTRACT(result_json,'$.summary') IS NOT NULL ORDER BY updated_at DESC LIMIT 3", (account_id, symbol)).fetchall()
        result = []
        for row in rows:
            saved = json.loads(row["result_json"])
            previous = saved.pop("input", {})
            keys = ("id", "label", "symbol", "currentPrice", "changeRate", "ma20", "ma60", "volumeRatio", "profitLossRate", "sourceAsOf", "asOf", "sourceSnapshotId", "source", "freshnessStatus")
            saved["previousFacts"] = [{key: fact[key] for key in keys if key in fact} for fact in previous.get("facts", [])[:20]]
            result.append({**saved, "completedAt": row["updated_at"]})
        return result

    def begin_call(self, workload, prompt_hash, task_id=""):
        call_id = uuid.uuid4().hex
        with self.transaction() as connection:
            if task_id:
                day = "calls:" + stamp()[:10]
                maximum = bounded(self.runtime_settings.get("aiControlDailyCallBudget"), 24, 0, 300)
                connection.execute("INSERT IGNORE INTO ai_control_budget (day_key,used_count) VALUES (%s,0)", (day,))
                row = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
                if int(row["used_count"]) >= maximum:
                    raise RuntimeError("central AI daily call budget exhausted")
                connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            connection.execute("INSERT INTO ai_control_calls (call_id,workload,status,task_id,prompt_hash,started_at) VALUES (%s,%s,'running',%s,%s,%s)", (call_id, workload[:64], task_id[:64], prompt_hash, stamp()))
        return call_id

    def finish_call(self, call_id, error_kind=""):
        with self.connect() as connection:
            connection.execute("UPDATE ai_control_calls SET status=%s,completed_at=%s,error_kind=%s WHERE call_id=%s", ("failed" if error_kind else "completed", stamp(), error_kind[:100], call_id))

    def status(self, account_id=""):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ai_control_tasks WHERE (%s='' OR account_id=%s) ORDER BY updated_at DESC LIMIT 60", (account_id, account_id)).fetchall()
            calls = connection.execute("SELECT workload,status,COUNT(*) AS count FROM ai_control_calls WHERE started_at>=%s GROUP BY workload,status", (stamp()[:10],)).fetchall()
            budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", (stamp()[:10],)).fetchone()
            active = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status IN ('pending','processing') AND (%s='' OR account_id=%s)", (account_id, account_id)).fetchone()
        return {"tasks": [{"taskId": row["task_id"], "accountId": row["account_id"], "symbol": row["symbol"],
                           "name": json.loads(row["payload_json"]).get("name", row["symbol"]),
                           "capability": row["capability"], "status": row["status"], "nextCheckAt": row["available_at"],
                           "attempts": row["attempts"], "lastError": row["last_error"], "updatedAt": row["updated_at"],
                           "result": json.loads(row["result_json"])} for row in rows],
                "callsToday": list(calls), "tasksStartedToday": int((budget or {}).get("used_count", 0)),
                "activeTaskCount": int(active["count"]),
                "dailyCallBudget": bounded(self.runtime_settings.get("aiControlDailyCallBudget"), 24, 0, 300),
                "dailyTaskBudget": bounded(self.runtime_settings.get("aiControlDailyTaskBudget"), 48, 0, 200)}
