"""Persist bounded heartbeat intervals without treating missing pulses as AI errors."""
from datetime import datetime, timedelta, timezone
import uuid

from digital_twin.modules.ai_orchestration.domain.execution_resilience import instant, stamp
from digital_twin.modules.ai_orchestration.domain.runtime_coverage import PULSE_GAP_SECONDS, runtime_coverage
from ..domain.activity import enabled as activity_enabled

RUNTIME = "central-observation-runtime"
WORKER_ID = uuid.uuid4().hex
RUNTIME_SCHEMA = """CREATE TABLE IF NOT EXISTS ai_control_runtime_intervals (
    interval_id VARCHAR(64) PRIMARY KEY, worker_id VARCHAR(64) NOT NULL,
    started_at VARCHAR(40) NOT NULL, last_seen_at VARCHAR(40) NOT NULL,
    INDEX ai_runtime_seen(last_seen_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


class AIRuntimePersistence:
    def record_runtime_pulse(self, *, now=None, worker_id=None, activity=None):
        now, worker = now or datetime.now(timezone.utc), worker_id or WORKER_ID
        with self.transaction() as connection:
            state = self._locked_execution_state(connection, RUNTIME, now)
            last = instant(state.get("lastSeenAt"))
            if last and now < last:
                return  # A delayed heartbeat may not move the coverage clock backwards.
            if (state.get("workerId") != worker or not last or
                    now - last > timedelta(seconds=PULSE_GAP_SECONDS)):
                state = {"workerId": worker, "intervalId": uuid.uuid4().hex, "startedAt": stamp(now),
                         "previousLastSeenAt": state.get("lastSeenAt", "")}
                connection.execute("INSERT INTO ai_control_runtime_intervals "
                    "(interval_id,worker_id,started_at,last_seen_at) VALUES (%s,%s,%s,%s)",
                    (state["intervalId"], worker, stamp(now), stamp(now)))
            else:
                connection.execute("UPDATE ai_control_runtime_intervals SET last_seen_at=%s WHERE interval_id=%s",
                                   (stamp(now), state["intervalId"]))
            state["lastSeenAt"] = stamp(now)
            if activity_enabled(self.runtime_settings) and activity in {
                    "resource-deferred", "recovery-wait", "budget-wait", "idle", "paused", "unavailable"}:
                details = {"status": activity}
                if activity == "recovery-wait":
                    import json
                    row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key='shared-local-model'").fetchone()
                    provider = json.loads(row["state_json"]) if row else {}
                    details.update(reasonCode=provider.get("lastFailureCategory", ""), nextCheckAt=provider.get("blockedUntil", ""))
                if state.get("notifiedActivity") != details:
                    state["notifiedActivity"] = details
                    self.activity(connection, "worker-state", stamp(now), **details)
            if activity in {"checking", "running", "resource-deferred", "recovery-wait", "budget-wait",
                            "idle", "paused", "completed", "unchanged", "unchanged-wait", "awaiting-evidence",
                            "deferred", "failed", "retired", "lease-lost", "unavailable"}:
                state.update(activity=activity, activityAt=stamp(now))
            self._save_execution_state(connection, RUNTIME, state, now)

    @staticmethod
    def runtime_report(connection, start, end):
        rows = connection.execute("SELECT started_at,last_seen_at FROM ai_control_runtime_intervals "
            "WHERE last_seen_at>=%s AND started_at<=%s ORDER BY last_seen_at LIMIT 4097",
            (stamp(start), stamp(end))).fetchall()
        report = runtime_coverage(rows[:4096], start, end)
        report["truncated"] = len(rows) > 4096
        if report["truncated"]:
            report.update(observedActiveSeconds=None, unobservedSeconds=None)
        return report
