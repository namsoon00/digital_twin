"""Lease-fenced, coalesced source renewal through the existing monitor queue."""
from datetime import datetime, timedelta, timezone
import hashlib

from digital_twin.modules.ai_orchestration.domain.evidence_readiness import source_refresh_requirement
from digital_twin.modules.ai_orchestration.domain.execution_resilience import instant, stamp
from digital_twin.modules.market_data.infrastructure.monitor_refresh import record_monitor_refresh


class AIObservationSourceRefresh:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self, job, packet):
        requirement = source_refresh_requirement(packet)
        if not requirement:
            return {}
        if any(packet.get(key) != job.get(key) for key in ("accountId", "symbol", "worldId")):
            raise ValueError("source refresh ownership mismatch")
        now = self.clock()
        # The existing execution-state key is VARCHAR(64). Keep a namespaced
        # 160-bit account identity inside that contract; no schema expansion.
        scope = "source-renewal:" + hashlib.sha256(job["accountId"].encode()).hexdigest()[:40]
        receipt = {**requirement, "requested": False, "pipeline": "monitor-snapshot-typedb-observation",
                   "checkedAt": stamp(now), "taskId": job["taskId"]}
        try:
            with self.store.transaction() as connection:
                owned = connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s "
                    "AND account_id=%s AND symbol=%s AND status='processing' AND attempts=%s "
                    "AND lease_token=%s AND lease_until>=%s FOR UPDATE", (job["taskId"], job["accountId"],
                    job["symbol"], job["attempts"], job["leaseToken"], stamp(now))).fetchone()
                if not owned:
                    return {**receipt, "status": "lease-lost"}
                state = self.store._locked_execution_state(connection, scope, now)
                due = instant(state.get("nextAllowedAt"))
                if due and now < due:
                    return {**receipt, "status": "coalesced", "retryAt": state["nextAllowedAt"],
                            "requestedAt": state.get("requestedAt", "")}
                # One account refresh covers all its symbols. Keep the existing
                # monitor lease, provider caches, native projection and wakeup
                # contracts; do not inject current quotes into the frozen input.
                record_monitor_refresh(connection.execute, job["accountId"], stamp(now), 5)
                self.store._save_execution_state(connection, scope, {
                    "requestedAt": stamp(now), "nextAllowedAt": stamp(now + timedelta(minutes=5)),
                    "sourceSnapshotId": packet["sourceSnapshotId"], "taskId": job["taskId"]}, now)
            return {**receipt, "status": "requested", "requested": True, "requestedAt": stamp(now)}
        except Exception as error:
            # Renewal failure is operational and cannot change evidence or
            # turn an otherwise useful filing observation into a false answer.
            return {**receipt, "status": "unavailable", "errorKind": type(error).__name__}
