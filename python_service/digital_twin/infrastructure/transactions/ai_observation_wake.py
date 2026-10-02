"""Durable domain-event delivery into the central AI owner's coalescing mailbox."""
import json

from digital_twin.modules.reasoning.contracts import ONTOLOGY_REASONING_COMPLETED, OBSERVATION_EVIDENCE_READY
from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.ai_orchestration.infrastructure.mysql_evidence_wake import MySQLEvidenceWakeStore


class AIObservationEvidenceWake:
    def __init__(self, settings=None):
        self.store = MySQLEvidenceWakeStore(settings)

    def run_once(self, subjects):
        def deliver(connection):
            # Per-event receipts, rather than an occurred_at watermark, also
            # admit late commits. Receipt, mailbox and task timing commit together.
            rows = connection.execute("SELECT e.event_id,e.occurred_at,e.payload_json FROM domain_events e "
                "WHERE e.name IN (%s,%s) AND JSON_CONTAINS_PATH(e.payload_json,'one','$.projectionOutcomes[0].worldId') "
                "AND NOT EXISTS (SELECT 1 FROM ai_control_evidence_events r WHERE r.event_id=e.event_id) "
                "ORDER BY e.occurred_at,e.event_id LIMIT 100", (ONTOLOGY_REASONING_COMPLETED, OBSERVATION_EVIDENCE_READY)).fetchall()
            for event in rows:
                inserted = connection.execute("INSERT IGNORE INTO ai_control_evidence_events (event_id,processed_at) VALUES (%s,%s)",
                                              (event["event_id"], stamp())).rowcount
                if inserted:
                    for target in evidence_wake_targets(json.loads(event["payload_json"]), subjects):
                        self.store.record(connection, event, target)
            woken = self.store.wake_pending(connection, subjects)
            # Receipts can retire only after their source event has retired.
            retired = connection.execute("SELECT r.event_id FROM ai_control_evidence_events r "
                "WHERE NOT EXISTS (SELECT 1 FROM domain_events e WHERE e.event_id=r.event_id) LIMIT 100").fetchall()
            for row in retired:
                connection.execute("DELETE FROM ai_control_evidence_events WHERE event_id=%s", (row["event_id"],))
            return {"eventsConsumed": len(rows), "tasksWoken": woken}
        return self.store.transaction_with_deadlock_retry("ai-evidence-wake", deliver)
