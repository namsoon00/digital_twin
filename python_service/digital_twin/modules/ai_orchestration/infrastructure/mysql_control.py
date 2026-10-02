"""Central work ledger; completion and successor scheduling share one transaction."""
import json
import gzip
from copy import deepcopy
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.modules.ai_orchestration.domain.planning import bounded, identity, stamp
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait, admission_wait, budget_state, budgets_enabled
from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_control_call_metrics (
    call_id VARCHAR(64) PRIMARY KEY, metrics_json TEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
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
    """CREATE TABLE IF NOT EXISTS ai_control_inputs (
    input_id VARCHAR(64) PRIMARY KEY, task_id VARCHAR(64) NOT NULL, attempt INT NOT NULL,
    protocol_version VARCHAR(64) NOT NULL, input_hash VARCHAR(64) NOT NULL, prompt_hash VARCHAR(64) NOT NULL,
    artifact_gzip LONGBLOB NOT NULL, created_at VARCHAR(40) NOT NULL,
    INDEX ai_input_task(task_id, attempt)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_control_input_calls (
    call_id VARCHAR(64) PRIMARY KEY, input_id VARCHAR(64) NOT NULL,
    INDEX ai_input_call(input_id)
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
        with self.transaction() as connection:
            connection.execute("INSERT IGNORE INTO ai_control_budget (day_key,used_count) VALUES (%s,0)", (day,))
            budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
            row = connection.execute(
                "SELECT * FROM ai_control_tasks WHERE (status='pending' AND (available_at<=%s OR last_error='ai-call-budget-exhausted')) OR (status='processing' AND lease_until<%s) ORDER BY priority DESC,available_at,task_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (now, now),
            ).fetchone()
            if not row:
                return None
            calls = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + day,)).fetchone()
            state = budget_state(self.runtime_settings, budget["used_count"], (calls or {}).get("used_count", 0),
                                 datetime.fromisoformat(now.replace("Z", "+00:00")))
            wait = admission_wait(state, row["capability"])
            if wait:
                raise wait
            token = uuid.uuid4().hex
            until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
            connection.execute("UPDATE ai_control_tasks SET status='processing',lease_token=%s,lease_until=%s,attempts=attempts+1,updated_at=%s WHERE task_id=%s", (token, until, now, row["task_id"]))
            connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            return {**json.loads(row["payload_json"]), "leaseToken": token, "attempts": int(row["attempts"]) + 1,
                    "previousError": row["last_error"]}

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
        def write(connection):
            # Retry only a rolled-back transaction, with independent mutable
            # results each time. No author/reviewer call belongs to this unit.
            saved_result, saved_children = deepcopy(result), deepcopy(children)
            if job.get("previousError"):
                saved_result["recovery"] = {"previousError": job["previousError"], "completedAttempt": job["attempts"]}
            now = stamp()
            cursor = connection.execute("UPDATE ai_control_tasks SET status='completed',last_error='',result_json=%s,updated_at=%s,lease_token='',lease_until='' WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                                        (json.dumps(saved_result, ensure_ascii=False), now, job["taskId"], job["leaseToken"], now))
            if not cursor.rowcount:
                return None
            development = getattr(self, "development_writer", None)
            if development is not None and job["capability"] == "observe" and saved_result.get("summary"):
                saved_result["development"] = development(connection, job, saved_result)
            agenda = getattr(self, "agenda_writer", None)
            if agenda is not None:
                saved_result["brain"] = agenda(connection, job, saved_result, saved_children)
            writer = getattr(self, "outbox_writer", None)
            if writer is not None and job["capability"] == "observe" and saved_result.get("summary"):
                saved_result["publication"] = writer(connection, job, saved_result)
            connection.execute("UPDATE ai_control_tasks SET result_json=%s WHERE task_id=%s",
                               (json.dumps(saved_result, ensure_ascii=False), job["taskId"]))
            for child in saved_children:
                self.insert(connection, child)
            return saved_result, saved_children

        committed = self.transaction_with_deadlock_retry("ai-control-complete", write)
        if committed is None:
            return False
        result.clear()
        result.update(committed[0])
        children[:] = committed[1]
        return True

    def fail(self, job, error_kind):
        failures = job["attempts"] - int(job.get("budgetDeferrals", 0))
        terminal = failures >= 3
        delay, recovery_delay = retry_delays(job["capability"], error_kind, failures)
        now = datetime.now(timezone.utc)
        due = (now + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status=%s,last_error=%s,available_at=%s,updated_at=%s,lease_token='',lease_until='' WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                ("failed" if terminal else "pending", error_kind[:100], due, stamp(), job["taskId"], job["leaseToken"], stamp())).rowcount
            failure = getattr(self, "agenda_failure", None)
            if changed and terminal and failure is not None:
                failure(connection, job, error_kind[:100])
            if changed and terminal and job["capability"] == "observe":
                next_due = (now + timedelta(seconds=recovery_delay)).isoformat().replace("+00:00", "Z")
                self.insert(connection, {**{k: job[k] for k in ("accountId", "symbol", "name", "worldId")},
                    "capability": "observe", "taskId": identity(job["taskId"], "recovery"), "availableAt": next_due})

    def defer_budget(self, job, wait):
        # Keep attempt identities immutable for frozen input/call audit. Budget
        # waits are separately counted so they cannot exhaust failure retries.
        with self.transaction() as connection:
            return bool(connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "payload_json=JSON_SET(payload_json,'$.budgetDeferrals',%s),updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, wait.reset_at, int(job.get("budgetDeferrals", 0)) + 1, stamp(),
                 job["taskId"], job["leaseToken"], stamp())).rowcount)

    def memory(self, account_id, symbol):
        with self.connect() as connection:
            rows = connection.execute("SELECT result_json,updated_at FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability='observe' AND status='completed' AND JSON_EXTRACT(result_json,'$.summary') IS NOT NULL ORDER BY updated_at DESC LIMIT 3", (account_id, symbol)).fetchall()
            condition_state = connection.execute("SELECT result_json FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                "AND capability='observe' AND status='completed' AND JSON_EXTRACT(result_json,'$.followUpEvaluations') IS NOT NULL "
                "ORDER BY updated_at DESC,task_id DESC LIMIT 1", (account_id, symbol)).fetchone()
        result = []
        for row in rows:
            saved = json.loads(row["result_json"])
            previous = saved.pop("input", {})
            saved.pop("comparisonFacts", None)
            keys = ("id", "label", "symbol", "currentPrice", "changeRate", "ma20", "ma60", "volumeRatio", "profitLossRate", "sourceAsOf", "asOf", "sourceSnapshotId", "source", "freshnessStatus")
            saved["previousFacts"] = [{key: fact[key] for key in keys if key in fact} for fact in previous.get("facts", [])[:20]]
            saved.update({key: previous[key] for key in ("accountId", "symbol", "worldId") if key in previous})
            result.append({**saved, "completedAt": row["updated_at"]})
        if result and condition_state:
            result[0]["followUpEvaluations"] = json.loads(condition_state["result_json"])["followUpEvaluations"]
        return result

    def save_execution_input(self, job, envelope):
        digest = validate_execution_input(envelope)
        packet = envelope["current"]
        if any(packet.get(key) != job[key] for key in ("accountId", "symbol", "worldId", "taskId")):
            raise ValueError("execution input task ownership mismatch")
        input_id = identity(job["taskId"], job["attempts"], digest)
        artifact = gzip.compress(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode(), mtime=0)
        with self.transaction() as connection:
            owned = connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s AND status='processing' "
                "AND lease_token=%s AND lease_until>=%s FOR UPDATE", (job["taskId"], job["leaseToken"], stamp())).fetchone()
            if not owned:
                return ""
            connection.execute("INSERT IGNORE INTO ai_control_inputs "
                "(input_id,task_id,attempt,protocol_version,input_hash,prompt_hash,artifact_gzip,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (input_id, job["taskId"], job["attempts"], envelope["protocolVersion"], digest, envelope["promptHash"], artifact, stamp()))
        return input_id

    def begin_call(self, workload, prompt_hash, task_id="", input_id=""):
        call_id = uuid.uuid4().hex
        now = stamp()
        with self.transaction() as connection:
            if workload == "independent-observation":
                frozen = connection.execute("SELECT i.task_id,i.prompt_hash FROM ai_control_inputs i "
                    "JOIN ai_control_tasks t ON t.task_id=i.task_id AND t.attempts=i.attempt "
                    "WHERE i.input_id=%s AND t.status='processing' AND t.lease_until>=%s", (input_id, stamp())).fetchone()
                if not frozen or frozen["task_id"] != task_id or frozen["prompt_hash"] != prompt_hash:
                    raise ValueError("independent AI call requires exact persisted input")
            if task_id:
                day = "calls:" + now[:10]
                maximum = bounded(self.runtime_settings.get("aiControlDailyCallBudget"), 24, 0, 300)
                connection.execute("INSERT IGNORE INTO ai_control_budget (day_key,used_count) VALUES (%s,0)", (day,))
                row = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
                if budgets_enabled(self.runtime_settings) and int(row["used_count"]) >= maximum:
                    reset = budget_state(self.runtime_settings, 0, row["used_count"], datetime.fromisoformat(now.replace("Z", "+00:00")))["budgetResetAt"]
                    raise AIControlBudgetWait("call", reset)
                connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            connection.execute("INSERT INTO ai_control_calls (call_id,workload,status,task_id,prompt_hash,started_at) VALUES (%s,%s,'running',%s,%s,%s)", (call_id, workload[:64], task_id[:64], prompt_hash, now))
            if input_id:
                connection.execute("INSERT INTO ai_control_input_calls (call_id,input_id) VALUES (%s,%s)", (call_id, input_id))
        return call_id

    def retrieval_round_budget(self):
        from digital_twin.modules.ai_orchestration.domain.retrieval import MAX_ROUNDS
        if not budgets_enabled(self.runtime_settings):
            return MAX_ROUNDS
        with self.connect() as connection:
            row = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + stamp()[:10],)).fetchone()
        maximum = bounded(self.runtime_settings.get("aiControlDailyCallBudget"), 24, 0, 300)
        return max(0, min(MAX_ROUNDS, maximum - int((row or {}).get("used_count", 0)) - 2))

    def finish_call(self, call_id, error_kind="", metrics=None):
        with self.transaction() as connection:
            if metrics:
                safe = {key: metrics[key] for key in ("stage", "configuredTimeoutSeconds", "promptBytes",
                    "capacityWaitMs", "modelProcessMs", "totalMs", "returnCode", "terminationReason") if key in metrics}
                connection.execute("INSERT INTO ai_control_call_metrics (call_id,metrics_json) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE metrics_json=VALUES(metrics_json)", (call_id, json.dumps(safe)))
            connection.execute("UPDATE ai_control_calls SET status=%s,completed_at=%s,error_kind=%s WHERE call_id=%s", ("failed" if error_kind else "completed", stamp(), error_kind[:100], call_id))

    def review_proof(self, input_id):
        with self.connect() as connection:
            return self.review_proof_with_connection(connection, input_id)

    @staticmethod
    def review_proof_with_connection(connection, input_id):
        from digital_twin.modules.ai_orchestration.domain.execution_input import REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION
        from digital_twin.modules.ai_orchestration.domain.insight_contract import narrative_digest
        row = connection.execute("SELECT i.task_id,i.artifact_gzip FROM ai_control_inputs i "
            "JOIN ai_control_input_calls l ON l.input_id=i.input_id "
            "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
            "WHERE i.input_id=%s AND c.status='completed' LIMIT 1", (input_id,)).fetchone()
        if not row:
            return {}
        envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
        validate_execution_input(envelope)
        if envelope["promptVersion"] not in {REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION}:
            return {}
        return {"taskId": row["task_id"], "draftHash": narrative_digest({**envelope["draft"], "input": envelope["current"]})}

    def status(self, account_id=""):
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM ai_control_tasks WHERE (%s='' OR account_id=%s) ORDER BY updated_at DESC LIMIT 60", (account_id, account_id)).fetchall()
            calls = connection.execute("SELECT workload,status,COUNT(*) AS count FROM ai_control_calls WHERE started_at>=%s GROUP BY workload,status", (day,)).fetchall()
            budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", (day,)).fetchone()
            call_budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + day,)).fetchone()
            active = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status IN ('pending','processing') AND (%s='' OR account_id=%s)", (account_id, account_id)).fetchone()
        state = budget_state(self.runtime_settings, (budget or {}).get("used_count", 0), (call_budget or {}).get("used_count", 0), now)
        wait = admission_wait(state)
        return {"tasks": [{"taskId": row["task_id"], "accountId": row["account_id"], "symbol": row["symbol"],
                           "name": json.loads(row["payload_json"]).get("name", row["symbol"]),
                           "capability": row["capability"], "status": row["status"], "nextCheckAt": row["available_at"],
                           "attempts": row["attempts"], "lastError": row["last_error"], "updatedAt": row["updated_at"],
                           "result": json.loads(row["result_json"])} for row in rows],
                "callsToday": list(calls), **state,
                "observationScheduling": wait.result() if wait else {"status": "ready"},
                "activeTaskCount": int(active["count"])}
