"""Central work ledger; completion and successor scheduling share one transaction."""
from digital_twin.modules.reasoning.contracts import OBSERVATION_QUOTE_KINDS

import json
import gzip
from copy import deepcopy
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_schema_tuning import MySQLIndexDefinition, ensure_mysql_indexes
from digital_twin.modules.ai_orchestration.domain.planning import bounded, identity, stamp
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait, admission_wait, budget_state, budgets_enabled
from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays
from digital_twin.modules.ai_orchestration.domain.retrieval import RETRIEVAL_PROMPT_VERSION, validate_trace
from ..domain import working_retrieval, scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval, quality_retrieval, completion_retrieval, business_retrieval, judgment_policy, retrieval_view, evidence_readiness, evidence_purpose
from digital_twin.modules.reasoning.contracts import content_hash
from digital_twin.modules.ai_orchestration.infrastructure.mysql_execution import AIExecutionPersistence, EXECUTION_SCHEMA
from digital_twin.modules.ai_orchestration.domain.execution_resilience import safe_diagnostic
from digital_twin.modules.ai_orchestration.infrastructure.mysql_runtime import AIRuntimePersistence, RUNTIME_SCHEMA
from digital_twin.modules.ai_orchestration.domain.observation_recovery import same_regular_observation
from ..domain.execution_lanes import MARKET, OBSERVATIONS, enabled as lanes_enabled
from ..domain.task_dispatch import SCOPE as DISPATCH_SCOPE, dispatched
from .mysql_task_dispatch import AITaskDispatchPersistence
from .mysql_diagnostic_progress import DiagnosticProgressPersistence, SCHEMA as DIAGNOSTIC_SCHEMA
from .mysql_token_usage import AITokenUsagePersistence
from ..domain.token_budget import usage_counts, RESERVATION, execution_pool


STATUS_INDEXES = (
    MySQLIndexDefinition("ai_control_tasks", "ai_control_recent", "`updated_at`, `task_id`"),
    MySQLIndexDefinition("ai_control_tasks", "ai_control_account_recent", "`account_id`, `updated_at`, `task_id`"),
)

SCHEMA = (
    *DIAGNOSTIC_SCHEMA,
    EXECUTION_SCHEMA,
    RUNTIME_SCHEMA,
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
    INDEX ai_control_due(status, available_at), INDEX ai_control_subject(account_id, symbol, updated_at),
    INDEX ai_control_recent(updated_at, task_id), INDEX ai_control_account_recent(account_id, updated_at, task_id)
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
    """CREATE TABLE IF NOT EXISTS ai_control_retrieval_rounds (
    input_id VARCHAR(64) PRIMARY KEY, task_id VARCHAR(64) NOT NULL, attempt INT NOT NULL,
    round_status VARCHAR(24) NOT NULL, artifact_hash VARCHAR(64) NOT NULL,
    artifact_gzip LONGBLOB NOT NULL, created_at VARCHAR(40) NOT NULL,
    INDEX ai_retrieval_task(task_id, attempt),
    FOREIGN KEY (input_id) REFERENCES ai_control_inputs(input_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_control_input_calls (
    call_id VARCHAR(64) PRIMARY KEY, input_id VARCHAR(64) NOT NULL,
    INDEX ai_input_call(input_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


class MySQLAIControlStore(AITokenUsagePersistence, DiagnosticProgressPersistence, AITaskDispatchPersistence, AIRuntimePersistence, AIExecutionPersistence, MySQLOperationalConnection):
    def __init__(self, settings=None):
        super().__init__(settings)
        with self.connect() as connection:
            for sql in SCHEMA:
                connection.execute(sql)
            ensure_mysql_indexes(connection, {"ai_control_tasks": STATUS_INDEXES})

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
            if lanes_enabled(self.runtime_settings):
                market_job = {**subject, "capability": MARKET, "taskId": identity("market-observe-v1", subject["accountId"], subject["symbol"], subject["worldId"])}
                self.insert(connection, market_job)
                prior = connection.execute("SELECT task_id,status,JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.status')) AS result_status FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability=%s ORDER BY updated_at DESC,task_id DESC LIMIT 1 FOR UPDATE", (subject["accountId"], subject["symbol"], MARKET)).fetchone()
                if prior and prior["status"] == "completed" and prior["result_status"] == "retired":
                    self.insert(connection, {**market_job, "taskId": identity(prior["task_id"], subject["worldId"], "rejoin")})
            latest = connection.execute("SELECT task_id,status,result_json FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability='observe' ORDER BY updated_at DESC,task_id DESC LIMIT 1 FOR UPDATE", (subject["accountId"], subject["symbol"])).fetchone()
            if latest and latest["status"] == "completed" and json.loads(latest["result_json"]).get("status") == "retired":
                self.insert(connection, {**job, "taskId": identity(latest["task_id"], subject["worldId"], "rejoin")})

    def claim(self):
        now = stamp()
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        day = now[:10]
        with self.transaction() as connection:
            # Claiming a task also performs retirement, repeat suppression,
            # cached source checks and follow-up accounting without a model.
            # The locked begin_call reservation remains the model admission gate.
            self.wake_token_waiters(connection, clock)
            cursor = self._locked_execution_state(connection, DISPATCH_SCOPE, clock)
            connection.execute("INSERT INTO ai_control_budget (day_key,used_count) VALUES (%s,0) "
                               "ON DUPLICATE KEY UPDATE day_key=VALUES(day_key)", (day,))
            budget = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
            calls = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + day,)).fetchone()
            state = budget_state(self.runtime_settings, budget["used_count"], (calls or {}).get("used_count", 0), clock)
            row, slot, lane = self.select_dispatch(connection, cursor, state, clock)
            if not row:
                return None
            token = uuid.uuid4().hex
            until = (clock + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
            cursor, receipt = dispatched(cursor, slot, lane, row["task_id"], row["capability"], now,
                                         row["lease_until"] if lane == "recovery" else "")
            connection.execute("UPDATE ai_control_tasks SET status='processing',lease_token=%s,lease_until=%s,"
                "attempts=attempts+1,updated_at=%s,payload_json=JSON_SET(payload_json,'$.taskDispatch',CAST(%s AS JSON)) "
                "WHERE task_id=%s", (token, until, now, json.dumps(receipt), row["task_id"]))
            connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            self._save_execution_state(connection, DISPATCH_SCOPE, cursor, clock)
            return {**json.loads(row["payload_json"]), "leaseToken": token, "attempts": int(row["attempts"]) + 1,
                    "previousError": row["last_error"], "scheduledAt": row["available_at"], "claimedAt": now,
                    "claimedPriority": int(row["priority"]), "taskDispatch": receipt}

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
                try:
                    self.record_runtime_pulse()
                except Exception:
                    pass  # Missing telemetry remains unknown; do not abandon a valid job lease.
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
            if job["capability"] in OBSERVATIONS and (saved_result.get("quality", {}).get("status") in {"accepted", "observation-only"}
                                                    or saved_result.get("status") == "unchanged"):
                saved_result.setdefault("resumption", {})["coalescedTaskCount"] = self.coalesce_observations(connection, job, now)
            development = getattr(self, "development_writer", None)
            if development is not None and job["capability"] in OBSERVATIONS and saved_result.get("summary"):
                saved_result["development"] = development(connection, job, saved_result)
            agenda = getattr(self, "agenda_writer", None)
            if agenda is not None:
                saved_result["brain"] = agenda(connection, job, saved_result, saved_children)
            knowledge = getattr(self, "knowledge_writer", None)
            if knowledge is not None and job["capability"] in OBSERVATIONS and (saved_result.get("summary") or saved_result.get("retrievalInputIds")):
                saved_result["knowledge"] = knowledge(connection, job, saved_result)
            writer = getattr(self, "outbox_writer", None)
            if writer is not None and job["capability"] in OBSERVATIONS and saved_result.get("summary"):
                saved_result["publication"] = writer(connection, job, saved_result)
            connection.execute("UPDATE ai_control_tasks SET result_json=%s WHERE task_id=%s",
                               (json.dumps(saved_result, ensure_ascii=False), job["taskId"]))
            for child in saved_children:
                self.insert(connection, child)
            self.record_observation_progress(connection, job, saved_result, now)
            return saved_result, saved_children

        committed = self.transaction_with_deadlock_retry("ai-control-complete", write)
        if committed is None:
            return False
        result.clear()
        result.update(committed[0])
        children[:] = committed[1]
        return True

    @staticmethod
    def coalesce_observations(connection, job, now):
        if not job.get("claimedAt") or job.get("claimedPriority", 0) != 0:
            return 0
        original = {k: v for k, v in job.items() if k not in
                    {"leaseToken", "attempts", "previousError", "scheduledAt", "claimedAt", "claimedPriority", "taskDispatch"}}
        if not same_regular_observation(original, original):
            return 0
        rows = connection.execute("SELECT task_id,payload_json FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
            "AND capability=%s AND status='pending' AND attempts=0 AND priority=0 AND available_at<=%s AND created_at<=%s "
            "ORDER BY available_at,task_id LIMIT 100 FOR UPDATE SKIP LOCKED",
            (job["accountId"], job["symbol"], job["capability"], job["claimedAt"], job["claimedAt"])).fetchall()
        count = 0
        for row in rows:
            if not same_regular_observation(original, json.loads(row["payload_json"])):
                continue
            result = {"status": "superseded", "reason": "같은 확인 질문을 현재 자료로 함께 확인했습니다.",
                      "replacementTaskId": job["taskId"], "historicalChecksReplayed": False}
            count += connection.execute("UPDATE ai_control_tasks SET status='completed',result_json=%s,updated_at=%s "
                "WHERE task_id=%s AND status='pending' AND attempts=0",
                (json.dumps(result, ensure_ascii=False), now, row["task_id"])).rowcount
        return count

    def fail(self, job, error_kind, terminal=False, result=None):
        capacity_wait = error_kind == "LocalAICapacityUnavailable" and not terminal
        capacity_deferrals = int(job.get("capacityDeferrals", 0))
        failures = (job["attempts"] - int(job.get("budgetDeferrals", 0)) - capacity_deferrals
                    - int(job.get("executionDeferrals", 0)) - int(job.get("researchDeferrals", 0))
                    - int(job.get("evidenceDeferrals", 0)))
        terminal = terminal or (not capacity_wait and failures >= 3)
        status = "failed" if terminal else "pending"
        delay, recovery_delay = retry_delays(job["capability"], error_kind,
                                             capacity_deferrals + 1 if capacity_wait else failures)
        now = datetime.now(timezone.utc)
        due = (now + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status=%s,last_error=%s,available_at=%s,updated_at=%s,"
                "result_json=COALESCE(%s,result_json),payload_json=JSON_SET(payload_json,'$.capacityDeferrals',%s),lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (status, error_kind[:100], due, stamp(), json.dumps(result, ensure_ascii=False, allow_nan=False) if result is not None else None,
                 capacity_deferrals + int(capacity_wait), job["taskId"], job["leaseToken"], stamp())).rowcount
            failure = getattr(self, "agenda_failure", None)
            if changed and terminal and failure is not None:
                failure(connection, job, error_kind[:100])
            if changed and terminal and job["capability"] in OBSERVATIONS:
                next_due = (now + timedelta(seconds=recovery_delay)).isoformat().replace("+00:00", "Z")
                self.insert(connection, {**{k: job[k] for k in ("accountId", "symbol", "name", "worldId")},
                    "capability": job["capability"], "taskId": identity(job["taskId"], "recovery"), "availableAt": next_due})
        return {"status": status if changed else "lease-lost"}

    def defer_evidence(self, job, wait):
        # Keep the same task and first wait clock. Attempts remain monotonic for
        # frozen input identities, but unavailable graphs cannot exhaust retries.
        if job.get("capability") not in OBSERVATIONS:
            raise ValueError("only observations can wait for a graph generation")
        now = datetime.now(timezone.utc)
        count = int(job.get("evidenceDeferrals", 0)) + 1
        retry_at = (now + timedelta(seconds=min(900, 60 * 2 ** min(count - 1, 4)))).isoformat().replace("+00:00", "Z")
        started_at = job.get("evidenceWaitStartedAt") or now.isoformat().replace("+00:00", "Z")
        producer = {}
        reader = getattr(self, "projection_status", None)
        if reader is not None and wait.code == "evidence-contract:crypto-quote-projection-not-ready":
            try:
                producer = reader(job)
            except Exception:
                producer = {"status": "unknown", "reasonCode": "projection-status-unavailable"}
        blocked = producer.get("status") == "blocked"
        if blocked:
            retry_at = (now + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
        result = {"status": "awaiting-evidence", "processingOutcome": {
            "status": "projection-blocked" if blocked else "data-wait", "stage": "evidence-capture", "reasonCode": wait.code,
            "conditions": wait.conditions, "metadataStatus": wait.metadata_status,
            "producer": producer, "resumeOn": ["committed-typedb-evidence"],
            "startedAt": started_at, "retryAt": retry_at, "deferrals": count}}
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "result_json=%s,payload_json=JSON_SET(payload_json,'$.evidenceDeferrals',%s,'$.evidenceWaitStartedAt',%s),"
                "updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, retry_at, json.dumps(result, ensure_ascii=False), count, started_at, stamp(),
                 job["taskId"], job["leaseToken"], stamp())).rowcount
            if changed:
                self.record_observation_progress(connection, job, result, stamp())
            return bool(changed)

    def defer_research(self, job, wait):
        if job.get("capability") != "research":
            raise ValueError("only source research can wait for its collection cooldown")
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "result_json=%s,payload_json=JSON_SET(payload_json,'$.researchDeferrals',%s),updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, wait.retry_at, json.dumps(wait.result(), ensure_ascii=False),
                 int(job.get("researchDeferrals", 0)) + 1, stamp(), job["taskId"], job["leaseToken"], stamp())).rowcount
            agenda = getattr(self, "agenda_writer", None)
            if changed and agenda:
                agenda(connection, job, wait.result(), [])
            return bool(changed)

    def defer_budget(self, job, wait):
        # Keep attempt identities immutable for frozen input/call audit. Budget
        # waits are separately counted so they cannot exhaust failure retries.
        with self.transaction() as connection:
            return bool(connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "payload_json=JSON_SET(payload_json,'$.budgetDeferrals',%s),updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, wait.reset_at, int(job.get("budgetDeferrals", 0)) + 1, stamp(),
                 job["taskId"], job["leaseToken"], stamp())).rowcount)

    def last_processing_outcome(self, account_id, symbol, capability="observe"):
        with self.connect() as connection:
            rows = connection.execute("SELECT JSON_EXTRACT(result_json,'$.processingOutcome') AS outcome_json "
                "FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability=%s AND status IN ('completed','failed') "
                "ORDER BY updated_at DESC,task_id DESC LIMIT 8", (account_id, symbol, capability)).fetchall()
        return next((json.loads(row["outcome_json"]) for row in rows if row["outcome_json"]), {})

    def memory(self, account_id, symbol, capability="observe"):
        with self.connect() as connection:
            rows = connection.execute("SELECT result_json,updated_at FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability=%s AND status='completed' AND JSON_EXTRACT(result_json,'$.summary') IS NOT NULL ORDER BY updated_at DESC LIMIT 3", (account_id, symbol, capability)).fetchall()
            condition_state = connection.execute("SELECT result_json FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                "AND capability=%s AND status='completed' AND JSON_EXTRACT(result_json,'$.followUpEvaluations') IS NOT NULL "
                "ORDER BY updated_at DESC,task_id DESC LIMIT 1", (account_id, symbol, capability)).fetchone()
        result = []
        for row in rows:
            saved = json.loads(row["result_json"])
            previous = saved.pop("input", {})
            saved.pop("comparisonFacts", None)
            keys = ("id", "kind", "label", "symbol", "currency", "currentPrice", "changeRate", "ma20", "ma60", "volumeRatio",
                    "profitLossRate", "seriesId", "value", "unit", "observationDate", "sourceAsOf", "asOf", "sourceSnapshotId",
                    "source", "sourceUrl", "sourceRevision", "freshnessStatus", "judgementEvidenceUsable")
            # Retain the required quote/macro baseline before optional detailed
            # reads, which can otherwise displace every dated macro comparison.
            facts = sorted(previous.get("facts", []), key=lambda fact:
                0 if fact.get("kind") in OBSERVATION_QUOTE_KINDS else 1 if fact.get("evidenceCategory") == "macro" else 2)
            saved["previousFacts"] = [{key: fact[key] for key in keys if key in fact} for fact in facts[:20]]
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
        if packet.get("executionLanePolicy") and packet.get("executionLane") != (MARKET if job["capability"] == MARKET else "question-research"):
            raise ValueError("execution input lane ownership mismatch")
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

    def save_retrieval_round(self, job, step):
        raw = json.dumps(step, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > 256 * 1024:
            raise ValueError("retrieval round exceeds audit budget")
        digest = content_hash(step)
        with self.transaction() as connection:
            owned = connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s AND status='processing' "
                "AND attempts=%s AND lease_token=%s AND lease_until>=%s FOR UPDATE",
                (job["taskId"], job["attempts"], job["leaseToken"], stamp())).fetchone()
            if not owned:
                return False
            frozen = connection.execute("SELECT artifact_gzip FROM ai_control_inputs "
                "WHERE input_id=%s AND task_id=%s AND attempt=%s", (step["inputId"], job["taskId"], job["attempts"])).fetchone()
            if not frozen:
                raise ValueError("retrieval audit requires owned frozen input")
            envelope = json.loads(gzip.decompress(frozen["artifact_gzip"]))
            validate_execution_input(envelope)
            if envelope["promptVersion"] not in {business_retrieval.VERSION, completion_retrieval.VERSION, quality_retrieval.VERSION, compact_retrieval.VERSION, *purpose_retrieval.SUPPORTED_VERSIONS, bounded_retrieval.VERSION, lane_retrieval.VERSION, RETRIEVAL_PROMPT_VERSION, working_retrieval.VERSION, scoped_retrieval.VERSION, indexed_retrieval.VERSION, question_retrieval.VERSION}:
                raise ValueError("retrieval audit requires retrieval input")
            validator = (business_retrieval.validate_trace if envelope["promptVersion"] == business_retrieval.VERSION else
                         completion_retrieval.validate_trace if envelope["promptVersion"] == completion_retrieval.VERSION else
                         quality_retrieval.validate_trace if envelope["promptVersion"] == quality_retrieval.VERSION else
                         compact_retrieval.validate_trace if envelope["promptVersion"] == compact_retrieval.VERSION else
                         purpose_retrieval.validate_trace if envelope["promptVersion"] in purpose_retrieval.SUPPORTED_VERSIONS else
                         bounded_retrieval.validate_trace if envelope["promptVersion"] == bounded_retrieval.VERSION else
                         lane_retrieval.validate_trace if envelope["promptVersion"] == lane_retrieval.VERSION else
                         question_retrieval.validate_trace if envelope["promptVersion"] == question_retrieval.VERSION else
                         indexed_retrieval.validate_trace if envelope["promptVersion"] == indexed_retrieval.VERSION else
                         scoped_retrieval.validate_trace if envelope["promptVersion"] == scoped_retrieval.VERSION else
                         working_retrieval.validate_trace if envelope["promptVersion"] == working_retrieval.VERSION else validate_trace)
            full_trace = [*envelope["retrievalContext"].get("trace", []), step]
            validator(full_trace)
            if envelope["promptVersion"] in {compact_retrieval.VERSION, quality_retrieval.VERSION, completion_retrieval.VERSION, business_retrieval.VERSION}:
                retrieval_view.paths(envelope["current"], {**envelope["retrievalContext"], "trace": full_trace})
            connection.execute("INSERT IGNORE INTO ai_control_retrieval_rounds "
                "(input_id,task_id,attempt,round_status,artifact_hash,artifact_gzip,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (step["inputId"], job["taskId"], job["attempts"], step["status"], digest, gzip.compress(raw, mtime=0), stamp()))
            saved = connection.execute("SELECT artifact_hash FROM ai_control_retrieval_rounds WHERE input_id=%s", (step["inputId"],)).fetchone()
            if saved["artifact_hash"] != digest:
                raise ValueError("retrieval audit is immutable")
        return True

    def begin_call(self, workload, prompt_hash, task_id="", input_id=""):
        call_id = uuid.uuid4().hex
        now = stamp()
        workload = workload[:64]
        with self.transaction() as connection:
            prompt_version = ""
            if workload == "independent-observation":
                frozen = connection.execute("SELECT i.task_id,i.prompt_hash,i.artifact_gzip FROM ai_control_inputs i "
                    "JOIN ai_control_tasks t ON t.task_id=i.task_id AND t.attempts=i.attempt "
                    "WHERE i.input_id=%s AND t.status='processing' AND t.lease_until>=%s", (input_id, stamp())).fetchone()
                if not frozen or frozen["task_id"] != task_id or frozen["prompt_hash"] != prompt_hash:
                    raise ValueError("independent AI call requires exact persisted input")
                prompt_version = json.loads(gzip.decompress(frozen["artifact_gzip"]))["promptVersion"]
            if task_id:
                day = "calls:" + now[:10]
                maximum = bounded(self.runtime_settings.get("aiControlDailyCallBudget"), 24, 0, 300)
                connection.execute("INSERT IGNORE INTO ai_control_budget (day_key,used_count) VALUES (%s,0)", (day,))
                row = connection.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s FOR UPDATE", (day,)).fetchone()
                if budgets_enabled(self.runtime_settings) and int(row["used_count"]) >= maximum:
                    reset = budget_state(self.runtime_settings, 0, row["used_count"], datetime.fromisoformat(now.replace("Z", "+00:00")))["budgetResetAt"]
                    raise AIControlBudgetWait("call", reset)
                connection.execute("UPDATE ai_control_budget SET used_count=used_count+1 WHERE day_key=%s", (day,))
            pool = execution_pool(workload, prompt_version)
            self.reserve_tokens(connection, workload, now, pool)
            connection.execute("INSERT INTO ai_control_calls (call_id,workload,status,task_id,prompt_hash,started_at) VALUES (%s,%s,'running',%s,%s,%s)", (call_id, workload, task_id[:64], prompt_hash, now))
            connection.execute("INSERT INTO ai_control_call_metrics (call_id,metrics_json) VALUES (%s,%s)",
                               (call_id, json.dumps({"tokenReservation": RESERVATION, "tokenPool": pool,
                                                    "promptVersion": prompt_version})))
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
            call = connection.execute("SELECT workload,started_at,status FROM ai_control_calls WHERE call_id=%s FOR UPDATE", (call_id,)).fetchone()
            if not call or call["status"] != "running":
                return
            saved = connection.execute("SELECT metrics_json FROM ai_control_call_metrics WHERE call_id=%s", (call_id,)).fetchone()
            admission = json.loads(saved["metrics_json"]) if saved else {}
            reserved = admission.get("tokenReservation")
            usage = usage_counts((metrics or {}).get("usage"))
            not_started = (metrics or {}).get("processNotStarted") is True
            if reserved == RESERVATION:
                self.settle_tokens(connection, call, usage, stamp(), not_started, admission.get("tokenPool"))
            metrics = {**(metrics or {}), **admission, "usage": usage, "usageStatus": "not-started" if not_started else "reported" if usage is not None else "unknown"}
            if metrics:
                safe = {key: metrics[key] for key in ("stage", "configuredTimeoutSeconds", "promptBytes",
                    "capacityWaitMs", "modelProcessMs", "totalMs", "returnCode", "terminationReason", "failure", "usage", "usageStatus",
                    "tokenReservation", "tokenPool", "promptVersion") if key in metrics}
                if "failure" in safe:
                    safe["failure"] = safe_diagnostic(safe["failure"])
                connection.execute("INSERT INTO ai_control_call_metrics (call_id,metrics_json) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE metrics_json=VALUES(metrics_json)", (call_id, json.dumps(safe)))
            connection.execute("UPDATE ai_control_calls SET status=%s,completed_at=%s,error_kind=%s WHERE call_id=%s", ("failed" if error_kind else "completed", stamp(), error_kind[:100], call_id))

    def review_proof(self, input_id):
        with self.connect() as connection:
            return self.review_proof_with_connection(connection, input_id)

    @staticmethod
    def review_proof_with_connection(connection, input_id):
        from digital_twin.modules.ai_orchestration.domain.execution_input import (
            LANE_REVIEW_PROMPT_VERSION, REVIEW_PROMPT_VERSION, CONTINUITY_REVIEW_PROMPT_VERSION, CITABLE_REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION,
        )
        from digital_twin.modules.ai_orchestration.domain.insight_contract import narrative_digest
        row = connection.execute("SELECT i.task_id,i.artifact_gzip FROM ai_control_inputs i "
            "JOIN ai_control_input_calls l ON l.input_id=i.input_id "
            "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
            "WHERE i.input_id=%s AND c.status='completed' LIMIT 1", (input_id,)).fetchone()
        if not row:
            return {}
        envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
        validate_execution_input(envelope)
        if envelope["promptVersion"] not in {evidence_purpose.review_version(envelope["current"]), evidence_purpose.REVIEW_VERSION, evidence_readiness.REVIEW_VERSION, LANE_REVIEW_PROMPT_VERSION, REVIEW_PROMPT_VERSION, CONTINUITY_REVIEW_PROMPT_VERSION, CITABLE_REVIEW_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION}:
            return {}
        return {"taskId": row["task_id"], "draftHash": narrative_digest({**envelope["draft"], "input": envelope["current"]})}

    def status(self, account_id=""):
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        with self.connect() as connection:
            # Sort only a covering index, then fetch at most 60 payloads by PK.
            # The account/subject index cannot serve cross-symbol recency order.
            index = "ai_control_account_recent" if account_id else "ai_control_recent"
            where, params = (" WHERE account_id=%s", (account_id,)) if account_id else ("", ())
            recent = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ task_id FROM ai_control_tasks FORCE INDEX ("
                + index + ")" + where + " ORDER BY updated_at DESC,task_id DESC LIMIT 60", params).fetchall()
            rows = []
            if recent:
                ids = [row["task_id"] for row in recent]
                fetched = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ task_id,account_id,symbol,"
                    "capability,status,available_at,attempts,last_error,updated_at,result_json,"
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.name')) AS name "
                    "FROM ai_control_tasks FORCE INDEX (PRIMARY) WHERE task_id IN (" + ",".join(["%s"] * len(ids)) + ")" + where.replace(" WHERE ", " AND "),
                    tuple(ids) + params).fetchall()
                by_id = {row["task_id"]: row for row in fetched}
                rows = [by_id[task_id] for task_id in ids if task_id in by_id]
            calls = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ workload,status,COUNT(*) AS count FROM ai_control_calls WHERE started_at>=%s GROUP BY workload,status", (day,)).fetchall()
            budget = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ used_count FROM ai_control_budget WHERE day_key=%s", (day,)).fetchone()
            call_budget = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + day,)).fetchone()
            active = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS count FROM ai_control_tasks WHERE status IN ('pending','processing') AND (%s='' OR account_id=%s)", (account_id, account_id)).fetchone()
            from digital_twin.modules.ai_orchestration.domain.planning import enabled
            health = self.operational_health(connection, now, enabled(self.runtime_settings))
            scheduling = self.task_scheduling(connection, now, account_id)
            from .mysql_lane_status import lane_status
            lanes = lane_status(connection, now, account_id) if lanes_enabled(self.runtime_settings) else None
            tokens = self.token_usage(connection, now)
        state = budget_state(self.runtime_settings, (budget or {}).get("used_count", 0), (call_budget or {}).get("used_count", 0), now)
        wait = admission_wait(state)
        if tokens["exhausted"]:
            wait = AIControlBudgetWait("token", tokens["resetAt"])
        return {"tasks": [{"taskId": row["task_id"], "accountId": row["account_id"], "symbol": row["symbol"],
                           "name": row["name"] if row["name"] is not None else row["symbol"],
                           "capability": row["capability"], "status": row["status"], "nextCheckAt": row["available_at"],
                           "attempts": row["attempts"], "lastError": row["last_error"], "updatedAt": row["updated_at"],
                           "result": json.loads(row["result_json"])} for row in rows],
                "callsToday": list(calls), "tokenUsageToday": tokens, **state,
                "observationScheduling": {**wait.result(), "scope": "model-calls-only", "nonModelChecksAllowed": True} if wait else {"status": "ready"},
                "activeTaskCount": int(active["count"]), "executionHealth": health, "taskScheduling": scheduling,
                "executionLanes": lanes}
