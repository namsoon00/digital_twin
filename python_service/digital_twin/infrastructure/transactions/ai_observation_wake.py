"""Durable domain-event delivery into the central AI owner's coalescing mailbox."""
import json

from digital_twin.modules.reasoning.contracts import ONTOLOGY_REASONING_COMPLETED, OBSERVATION_EVIDENCE_READY, OBSERVATION_CHECKPOINT_READY
from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.ai_orchestration.infrastructure.mysql_evidence_wake import MySQLEvidenceWakeStore


class AIObservationEvidenceWake:
    def __init__(self, settings=None):
        self.store = MySQLEvidenceWakeStore(settings)

    @staticmethod
    def pending_events(connection):
        # Read a bounded set of keys through the name/time index before loading
        # large JSON bodies. A global time scan rereads all retired event bodies
        # when this consumer has no work, blocking the central task queue.
        keys = []
        for name in (ONTOLOGY_REASONING_COMPLETED, OBSERVATION_EVIDENCE_READY, OBSERVATION_CHECKPOINT_READY):
            keys.extend(connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ e.event_id,e.occurred_at "
                "FROM domain_events e FORCE INDEX (idx_domain_events_name_time_event) WHERE e.name=%s "
                "AND NOT EXISTS (SELECT 1 FROM ai_control_evidence_events r WHERE r.event_id=e.event_id) "
                "ORDER BY e.occurred_at,e.event_id LIMIT 100", (name,)).fetchall())
        keys = sorted(keys, key=lambda row: (row["occurred_at"], row["event_id"]))[:100]
        if not keys:
            return []
        placeholders = ",".join(["%s"] * len(keys))
        rows = connection.execute("SELECT event_id,occurred_at,payload_json FROM domain_events WHERE event_id IN ("
            + placeholders + ")", tuple(row["event_id"] for row in keys)).fetchall()
        return sorted(rows, key=lambda row: (row["occurred_at"], row["event_id"]))

    @staticmethod
    def retire_receipts(connection):
        """Sweep receipt keys in bounded pages, never the full retained ledger."""
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_execution import AIExecutionPersistence
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        scope = "ai-evidence-receipt-retention"
        state = AIExecutionPersistence._locked_execution_state(connection, scope, now)
        after = state.get("afterEventId", "")
        if not isinstance(after, str):
            after = ""
        rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ event_id "
            "FROM ai_control_evidence_events FORCE INDEX(PRIMARY) WHERE event_id>%s "
            "ORDER BY event_id LIMIT 100", (after,)).fetchall()
        if rows:
            ids = [row["event_id"] for row in rows]
            connection.execute("DELETE FROM ai_control_evidence_events WHERE event_id IN ("
                + ",".join(["%s"] * len(ids)) + ") AND NOT EXISTS "
                "(SELECT 1 FROM domain_events e WHERE e.event_id=ai_control_evidence_events.event_id)", tuple(ids))
        AIExecutionPersistence._save_execution_state(connection, scope,
            {"version": "bounded-receipt-retention-v1", "afterEventId": rows[-1]["event_id"] if rows else ""}, now)

    def run_once(self, subjects):
        def deliver(connection):
            # Per-event receipts, rather than an occurred_at watermark, also
            # admit late commits. Receipt, mailbox and task timing commit together.
            rows = self.pending_events(connection)
            for event in rows:
                inserted = connection.execute("INSERT IGNORE INTO ai_control_evidence_events (event_id,processed_at) VALUES (%s,%s)",
                                              (event["event_id"], stamp())).rowcount
                if inserted:
                    for target in evidence_wake_targets(json.loads(event["payload_json"]), subjects):
                        self.store.record(connection, event, target)
            woken = self.store.wake_pending(connection, subjects)
            # Receipts can retire only after their source event has retired.
            self.retire_receipts(connection)
            return {"eventsConsumed": len(rows), "tasksWoken": woken}
        return self.store.transaction_with_deadlock_retry("ai-evidence-wake", deliver)
