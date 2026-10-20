"""Mode-aware renewal through the existing collector or leased monitor queue."""
from datetime import datetime, timedelta, timezone
import hashlib

from digital_twin.modules.ai_orchestration.domain.evidence_readiness import source_refresh_requirement
from digital_twin.modules.ai_orchestration.domain.execution_resilience import instant, stamp
from digital_twin.modules.market_data.infrastructure.monitor_refresh import record_monitor_refresh


class AIObservationSourceRefresh:
    def __init__(self, store, clock=None, *, account_queue_enabled=False):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.account_queue_enabled = bool(account_queue_enabled)

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
                   "checkedAt": stamp(now), "taskId": job["taskId"],
                   "collectionMode": "account-queue" if self.account_queue_enabled else "periodic-monitor"}
        from digital_twin.modules.reasoning.contracts import CRYPTO_EVIDENCE_PROFILE
        if packet.get("profile") == CRYPTO_EVIDENCE_PROFILE:
            return {**receipt, "pipeline": "external-data-facts-typedb-observation",
                    "collectionMode": "external-data", "status": "awaiting-external-collection"}
        if not self.account_queue_enabled:
            # The ordinary monitor collects every configured account on its
            # periodic cycle. Its runner deliberately does not consume this
            # optional account queue. Do not create orphan requests or claim
            # a renewal was requested through a disabled consumer.
            return {**receipt, "status": "awaiting-periodic-collection"}
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
