"""Commit a verified-read event and resume only checkpoint-specific AI waits."""
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.modules.reasoning.contracts import OBSERVATION_CHECKPOINT_READY, OBSERVATION_CHECKPOINT_VERSION, EvidenceContractError
from digital_twin.modules.reasoning.infrastructure.observation_binding import active_observation_binding
from digital_twin.modules.ai_orchestration.infrastructure.mysql_evidence_wake import wake_checkpoint_waiters
from digital_twin.shared_kernel.events import DomainEvent


def publish_checkpoint(connection, job, artifact, material_hash, changed):
    connection.execute("SELECT control_id FROM reasoning_engine_control WHERE control_id='global' FOR UPDATE").fetchone()
    if active_observation_binding(connection) != job["binding"]:
        raise EvidenceContractError("checkpoint deployment changed before publication")
    packet = artifact["packet"]
    wake_checkpoint_waiters(connection, packet)
    if changed:
        event = DomainEvent(OBSERVATION_CHECKPOINT_READY, "reasoning",
            occurred_at=artifact.get("captureStartedAt", packet["capturedAt"]), payload={
            "checkpointVersion": OBSERVATION_CHECKPOINT_VERSION, "status": "ok",
            **{key: packet[key] for key in ("accountId", "symbol", "worldId", "sourceSnapshotId", "capturedAt")},
            "materialHash": material_hash, "reasoningBinding": job["binding"],
            "authority": "verified-facts-only"})
        insert_domain_event_with_connection(connection, event)
