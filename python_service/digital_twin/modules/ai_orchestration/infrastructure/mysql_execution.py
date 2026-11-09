"""Small cross-worker execution state; locks are never held during a model call."""
from datetime import datetime, timedelta, timezone
import json
import uuid

from digital_twin.modules.ai_orchestration.domain.execution_resilience import (
    admit_execution, finish_execution as finish_state, recovery_wait, execution_health, stamp, instant,
)
from digital_twin.modules.ai_orchestration.infrastructure.mysql_runtime import RUNTIME
from digital_twin.modules.ai_orchestration.domain.runtime_coverage import runtime_view
from ..domain.execution_lanes import MARKET, OBSERVATIONS, enabled as lanes_enabled
from .mysql_commit_confirmation import confirmed_transaction


EXECUTION_SCHEMA = """CREATE TABLE IF NOT EXISTS ai_control_execution_state (
    scope_key VARCHAR(64) PRIMARY KEY, state_json TEXT NOT NULL, updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
SCOPE = "shared-local-model"
PROGRESS = "central-observation-progress"
MARKET_PROGRESS = "market-observation-progress"


class AIExecutionPersistence:
    @staticmethod
    def _locked_execution_state(connection, scope, now):
        # Take the exclusive row lock immediately. INSERT IGNORE can acquire
        # shared duplicate-key locks which deadlock when both workers upgrade.
        connection.execute("INSERT INTO ai_control_execution_state (scope_key,state_json,updated_at) VALUES (%s,'{}',%s) "
                           "ON DUPLICATE KEY UPDATE scope_key=VALUES(scope_key)", (scope, stamp(now)))
        row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s FOR UPDATE", (scope,)).fetchone()
        return json.loads(row["state_json"])

    @staticmethod
    def _save_execution_state(connection, scope, state, now):
        connection.execute("UPDATE ai_control_execution_state SET state_json=%s,updated_at=%s WHERE scope_key=%s",
                           (json.dumps(state, ensure_ascii=False, allow_nan=False), stamp(now), scope))

    def acquire_execution(self):
        now = datetime.now(timezone.utc)
        confirmation = {}
        def persisted(connection):
            row = connection.execute("SELECT state_json,updated_at FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
            return bool(confirmation and row and row["updated_at"] == stamp(now)
                        and json.loads(row["state_json"]) == confirmation["state"])
        with confirmed_transaction(self, persisted) as connection:
            state = self._locked_execution_state(connection, SCOPE, now)
            state, ticket, wait = admit_execution(state, now, uuid.uuid4().hex)
            self._save_execution_state(connection, SCOPE, state, now)
            confirmation["state"] = state
        if wait:
            raise wait
        return ticket

    def finish_execution(self, ticket, outcome, diagnostic=None):
        now = datetime.now(timezone.utc)
        confirmation = {}
        def persisted(connection):
            row = connection.execute("SELECT state_json,updated_at FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
            return bool(confirmation and row and row["updated_at"] == stamp(now)
                        and json.loads(row["state_json"]) == confirmation["state"])
        with confirmed_transaction(self, persisted) as connection:
            state = self._locked_execution_state(connection, SCOPE, now)
            updated = finish_state(state, ticket, now, outcome, diagnostic)
            self._save_execution_state(connection, SCOPE, updated, now)
            confirmation["state"] = updated

    def execution_wait(self):
        with self.connect() as connection:
            row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
        wait = recovery_wait(json.loads(row["state_json"]) if row else {}, datetime.now(timezone.utc))
        if wait:
            raise wait

    def park_model_waiters(self):
        """Move due model work behind the provider gate without claiming it.

        Independent source collection remains eligible. Only this wait reason
        may be awakened on provider recovery; later evidence/error waits win.
        No attempt, model budget, input, result or source clock is manufactured.
        """
        now = datetime.now(timezone.utc)
        clock = stamp(now)
        def update(connection):
            state = self._locked_execution_state(connection, SCOPE, now)
            wait = recovery_wait(state, now)
            if wait:
                where = ("status='pending' AND available_at<=%s "
                         "AND capability IN ('observe','market-observe','research-plan','portfolio-report')")
                params = (clock,)
            else:
                where = "status='pending' AND last_error='ai-execution:recovery-wait'"
                params = ()
            rows = connection.execute("SELECT task_id FROM ai_control_tasks FORCE INDEX(ai_control_due) WHERE "
                + where + " ORDER BY available_at,task_id LIMIT 50 FOR UPDATE SKIP LOCKED", params).fetchall()
            for row in rows:
                if wait:
                    connection.execute("UPDATE ai_control_tasks SET "
                        "payload_json=JSON_SET(payload_json,'$.executionWait',CAST(%s AS JSON),"
                        "'$.providerParking',COALESCE(JSON_EXTRACT(payload_json,'$.providerParking'),"
                        "JSON_OBJECT('priorError',last_error,'scheduledAt',available_at))),"
                        "available_at=%s,last_error=%s,updated_at=%s "
                        "WHERE task_id=%s AND status='pending' AND available_at<=%s",
                        (json.dumps(wait.result()), wait.retry_at, wait.code, clock, row["task_id"], clock))
                else:
                    connection.execute("UPDATE ai_control_tasks SET available_at=LEAST(available_at,%s),"
                        "last_error=COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.providerParking.priorError')),''),"
                        "updated_at=%s,payload_json=JSON_REMOVE(payload_json,'$.executionWait','$.providerParking') "
                        "WHERE task_id=%s AND status='pending' AND last_error='ai-execution:recovery-wait'",
                        (clock, clock, row["task_id"]))
            return wait
        return self.transaction_with_deadlock_retry("ai-provider-wait", update)

    def worker_preflight(self):
        """No full service/graph/research construction for an idle quota wait."""
        from ..domain.planning import enabled
        if not enabled(self.runtime_settings):
            return {"status": "paused"}
        wait = self.park_model_waiters()
        if not wait:
            return None
        clock = stamp(datetime.now(timezone.utc))
        with self.connect() as connection:
            source = connection.execute("SELECT task_id FROM ai_control_tasks FORCE INDEX(ai_control_due) "
                "WHERE status='pending' AND available_at<=%s AND capability='research' LIMIT 1", (clock,)).fetchone()
            expired = connection.execute("SELECT task_id FROM ai_control_tasks FORCE INDEX(ai_control_due) "
                "WHERE status='processing' AND lease_until<%s AND capability='research' LIMIT 1", (clock,)).fetchone()
        return None if source or expired else wait.result()

    def defer_execution(self, job, wait):
        # Frozen input identities include attempts; never decrement or reuse them.
        now = stamp(datetime.now(timezone.utc))
        with self.transaction() as connection:
            changed = connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "payload_json=JSON_SET(payload_json,'$.executionDeferrals',%s,'$.executionWait',CAST(%s AS JSON)),updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, wait.retry_at, int(job.get("executionDeferrals", 0)) + 1, json.dumps(wait.result()), now,
                 job["taskId"], job["leaseToken"], now)).rowcount
            if changed:
                from ..domain.planning import identity
                self.activity(connection, "task-deferred", identity(job["taskId"], job["attempts"]), job,
                              status="recovery-wait", reasonCode=wait.category, nextCheckAt=wait.retry_at)
            return bool(changed)

    def request_execution_recheck(self):
        """Explicit operator request: advance one fenced probe, never erase failures."""
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            state = self._locked_execution_state(connection, SCOPE, now)
            if instant(state.get("probeUntil")) and instant(state["probeUntil"]) > now:
                return {"status": "probe-running"}
            if not state.get("blockedUntil"):
                return {"status": "ready"}
            previous = state["blockedUntil"]
            state.update(blockedUntil=stamp(now), operatorRecheckAt=stamp(now), previousBlockedUntil=previous)
            self._save_execution_state(connection, SCOPE, state, now)
            self.activity(connection, "resume-requested", stamp(now), status="pending", reasonCode=state.get("lastFailureCategory", ""))
        return {"status": "probe-requested", "previousRetryAt": previous}

    def record_observation_progress(self, connection, job, result, now):
        if job["capability"] not in OBSERVATIONS or not (result.get("summary") or result.get("status") in {"unchanged", "awaiting-evidence", "unchanged-wait"}):
            return
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        scope = MARKET_PROGRESS if job["capability"] == MARKET else PROGRESS
        state = self._locked_execution_state(connection, scope, clock)
        state["lastObservationCheckAt"] = max(state.get("lastObservationCheckAt") or now, now, key=instant)
        if result.get("summary"):
            state["lastJudgmentAt"] = max(state.get("lastJudgmentAt") or now, now, key=instant)
        self._save_execution_state(connection, scope, state, clock)

    def operational_health(self, connection, now, enabled=True):
        lane = MARKET if lanes_enabled(self.runtime_settings) else "observe"
        progress_scope = MARKET_PROGRESS if lane == MARKET else PROGRESS
        states = {row["scope_key"]: json.loads(row["state_json"]) for row in connection.execute(
            "SELECT scope_key,state_json FROM ai_control_execution_state WHERE scope_key IN (%s,%s,%s)", (SCOPE, progress_scope, RUNTIME)).fetchall()}
        runtime = runtime_view(states.get(RUNTIME, {}), now)
        started = instant(runtime["startedAt"])
        since = max(now - timedelta(hours=1), started) if started and started <= now else now - timedelta(hours=1)
        calls = connection.execute("SELECT c.workload,c.status,COUNT(*) AS count FROM ai_control_calls c "
            "JOIN ai_control_tasks t ON t.task_id=c.task_id WHERE c.started_at>=%s "
            "AND c.started_at<=%s AND t.capability=%s GROUP BY c.workload,c.status", (stamp(since), stamp(now), lane)).fetchall()
        overdue = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status='pending' "
            "AND available_at<%s AND capability=%s", (stamp(now - timedelta(minutes=20)), lane)).fetchone()
        expired = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status='processing' "
            "AND lease_until<%s AND capability=%s", (stamp(now), lane)).fetchone()
        # Delay before the current runtime interval is not active-worker latency.
        grace = bool(started and now - started < timedelta(minutes=20))
        health = execution_health(now, states.get(SCOPE, {}), states.get(progress_scope, {}), calls,
                                  0 if grace else int(overdue["count"]), enabled,
                                  expired_leases=0 if grace else int(expired["count"]))
        health.update(version="central-ai-operational-health-v3-isolated-market", executionLane=lane, runtime=runtime, callWindowStart=stamp(since),
                      callWindowMinutes=round((now - since).total_seconds() / 60, 2),
                      carriedOverdueObservationTasks=int(overdue["count"]) if grace else 0,
                      runtimeCoverage=self.runtime_report(connection, now - timedelta(hours=24), now))
        activity = states.get(RUNTIME, {})
        health["workerActivity"] = {key: activity.get(key, "") for key in ("activity", "activityAt")}
        activity_at = instant(activity.get("activityAt"))
        if (enabled and runtime["live"] and activity.get("activity") == "resource-deferred"
                and activity_at and timedelta(0) <= now - activity_at <= timedelta(minutes=2)
                and not recovery_wait(states.get(SCOPE, {}), now)):
            health.update(status="resource-wait", reason="워커는 실행 중이며 메모리 또는 실행 자리 확보를 기다립니다. 새 판단을 완료한 상태는 아닙니다.")
        if (enabled and runtime["live"] and activity.get("activity") == "unavailable"
                and activity_at and timedelta(0) <= now - activity_at <= timedelta(minutes=2)):
            health.update(status="degraded", reason="최근 작업 실행 중 오류가 발생했습니다. 워커 생존과 판단 완료를 구분해 확인해야 합니다.")
        if enabled and not runtime["live"]:
            health.update(status="runtime-unconfirmed", reason="워커 생존 기록이 없거나 중단됐습니다. 중단 시각과 AI 처리 성과는 구분해 확인해야 합니다.")
        if started:
            for source, target in (("lastJudgmentAt", "judgmentSavedSinceResume"), ("lastObservationCheckAt", "observationCheckedSinceResume")):
                value = instant(health[source])
                health[target] = bool(value and started <= value <= now)
            if health["status"] == "healthy" and not health["observationCheckedSinceResume"]:
                health.update(status="awaiting-result", reason="재시작 이후 모델 호출은 완료됐지만 관찰 결과 저장은 아직 확인되지 않았습니다.")
        return health
