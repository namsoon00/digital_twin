"""Publish evidence readiness inside the fenced V2 completion transaction."""
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.modules.reasoning.contracts import completed_observation_evidence_event


def publish_observation_evidence_ready(connection, job_id, result):
    job = connection.execute("SELECT job_id,deployment_id,source_event_id FROM reasoning_engine_jobs WHERE job_id=%s", (job_id,)).fetchone()
    # Read current ownership, and keep promotion from racing this commit.
    control = connection.execute("SELECT active_deployment_id,delivery_deployment_id FROM reasoning_engine_control WHERE control_id='global' FOR UPDATE").fetchone() or {}
    delivery = control.get("delivery_deployment_id") or control.get("active_deployment_id")
    if not job or job["deployment_id"] != delivery:
        return
    event = completed_observation_evidence_event(job, result)
    if event:
        insert_domain_event_with_connection(connection, event)
