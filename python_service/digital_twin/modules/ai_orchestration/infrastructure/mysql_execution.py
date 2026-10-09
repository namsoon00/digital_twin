"""Small cross-worker execution state; locks are never held during a model call."""
from datetime import datetime, timedelta, timezone
import json
import uuid

from digital_twin.modules.ai_orchestration.domain.execution_resilience import (
    admit_execution, finish_execution as finish_state, recovery_wait, execution_health, stamp, instant,
)
from digital_twin.modules.ai_orchestration.infrastructure.mysql_runtime import RUNTIME
from digital_twin.modules.ai_orchestration.domain.runtime_coverage import runtime_view


EXECUTION_SCHEMA = """CREATE TABLE IF NOT EXISTS ai_control_execution_state (
    scope_key VARCHAR(64) PRIMARY KEY, state_json TEXT NOT NULL, updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
SCOPE = "shared-local-model"
PROGRESS = "central-observation-progress"


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
        with self.transaction() as connection:
            state = self._locked_execution_state(connection, SCOPE, now)
            state, ticket, wait = admit_execution(state, now, uuid.uuid4().hex)
            self._save_execution_state(connection, SCOPE, state, now)
        if wait:
            raise wait
        return ticket

    def finish_execution(self, ticket, outcome, diagnostic=None):
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            state = self._locked_execution_state(connection, SCOPE, now)
            updated = finish_state(state, ticket, now, outcome, diagnostic)
            self._save_execution_state(connection, SCOPE, updated, now)

    def execution_wait(self):
        with self.connect() as connection:
            row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
        wait = recovery_wait(json.loads(row["state_json"]) if row else {}, datetime.now(timezone.utc))
        if wait:
            raise wait

    def defer_execution(self, job, wait):
        # Frozen input identities include attempts; never decrement or reuse them.
        now = stamp(datetime.now(timezone.utc))
        with self.transaction() as connection:
            return bool(connection.execute("UPDATE ai_control_tasks SET status='pending',last_error=%s,available_at=%s,"
                "payload_json=JSON_SET(payload_json,'$.executionDeferrals',%s),updated_at=%s,lease_token='',lease_until='' "
                "WHERE task_id=%s AND status='processing' AND lease_token=%s AND lease_until>=%s",
                (wait.code, wait.retry_at, int(job.get("executionDeferrals", 0)) + 1, now,
                 job["taskId"], job["leaseToken"], now)).rowcount)

    def record_observation_progress(self, connection, job, result, now):
        if job["capability"] != "observe" or not (result.get("summary") or result.get("status") == "unchanged"):
            return
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        state = self._locked_execution_state(connection, PROGRESS, clock)
        state["lastObservationCheckAt"] = max(state.get("lastObservationCheckAt") or now, now, key=instant)
        if result.get("summary"):
            state["lastJudgmentAt"] = max(state.get("lastJudgmentAt") or now, now, key=instant)
        self._save_execution_state(connection, PROGRESS, state, clock)

    def operational_health(self, connection, now, enabled=True):
        states = {row["scope_key"]: json.loads(row["state_json"]) for row in connection.execute(
            "SELECT scope_key,state_json FROM ai_control_execution_state WHERE scope_key IN (%s,%s,%s)", (SCOPE, PROGRESS, RUNTIME)).fetchall()}
        runtime = runtime_view(states.get(RUNTIME, {}), now)
        started = instant(runtime["startedAt"])
        since = max(now - timedelta(hours=1), started) if started and started <= now else now - timedelta(hours=1)
        calls = connection.execute("SELECT workload,status,COUNT(*) AS count FROM ai_control_calls WHERE started_at>=%s "
            "AND started_at<=%s GROUP BY workload,status", (stamp(since), stamp(now))).fetchall()
        overdue = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status='pending' "
            "AND available_at<%s AND capability='observe'", (stamp(now - timedelta(minutes=20)),)).fetchone()
        expired = connection.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE status='processing' "
            "AND lease_until<%s AND capability='observe'", (stamp(now),)).fetchone()
        # Delay before the current runtime interval is not active-worker latency.
        grace = bool(started and now - started < timedelta(minutes=20))
        health = execution_health(now, states.get(SCOPE, {}), states.get(PROGRESS, {}), calls,
                                  0 if grace else int(overdue["count"]), enabled,
                                  expired_leases=0 if grace else int(expired["count"]))
        health.update(version="central-ai-operational-health-v2", runtime=runtime, callWindowStart=stamp(since),
                      callWindowMinutes=round((now - since).total_seconds() / 60, 2),
                      carriedOverdueObservationTasks=int(overdue["count"]) if grace else 0,
                      runtimeCoverage=self.runtime_report(connection, now - timedelta(hours=24), now))
        if enabled and not runtime["live"]:
            health.update(status="runtime-unconfirmed", reason="워커 생존 기록이 없거나 중단됐습니다. 중단 시각과 AI 처리 성과는 구분해 확인해야 합니다.")
        if started:
            for source, target in (("lastJudgmentAt", "judgmentSavedSinceResume"), ("lastObservationCheckAt", "observationCheckedSinceResume")):
                value = instant(health[source])
                health[target] = bool(value and started <= value <= now)
            if health["status"] == "healthy" and not health["observationCheckedSinceResume"]:
                health.update(status="awaiting-result", reason="재시작 이후 모델 호출은 완료됐지만 관찰 결과 저장은 아직 확인되지 않았습니다.")
        return health
