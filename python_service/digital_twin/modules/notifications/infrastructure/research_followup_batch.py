"""Coalesce inside the case-event transaction, before a sender can claim the job."""
from ..domain.research_followup_batch import batch_identity, merge_followup
from ..domain.notifications import NotificationJob
from ..application.research_progress_message import render_research_progress


def enqueue_followup_batch(store, connection, event):
    key = batch_identity(event)
    job_id = "research-batch-" + key[:39]
    row = connection.execute("SELECT status,attempts,text,payload_json FROM notification_jobs "
                             "WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
    if row:
        job = store.job_from_row(row)
        # A claimed/sent/partially delivered body is immutable. Individual case
        # events remain available even if a late duplicate cannot join the batch.
        if row["status"] != "pending" or row["attempts"] or job.context.get("transportDelivery"):
            return {"status": "suppressed", "reason": "research-batch-sealed", "jobId": job_id}
        previous = job.context.get("researchProgress") or {}
        batch = merge_followup(event, previous)
        if batch == previous:
            return {"status": "suppressed", "reason": "research-batch-duplicate", "jobId": job_id}
        job.context["researchProgress"] = batch
        job.text = render_research_progress(batch)
        job.context["notificationContent"] = {"kind": "research-progress", "body": job.text}
        store.upsert_job_with_connection(connection, job)
        return {"status": "coalesced", "jobId": job_id}
    batch = merge_followup(event)
    body = render_research_progress(batch)
    job = NotificationJob(job_id=job_id, account_id=event["accountId"], account_label="",
        message_type="researchProgress", text=body, dedupe_key=job_id,
        source_event_id=event["eventId"], source_event_name="ai_research.progress_recorded", context={
            "accountId": event["accountId"], "symbol": event["symbol"], "name": event.get("name", ""),
            "messageType": "researchProgress", "researchProgress": batch,
            "notificationContent": {"kind": "research-progress", "body": body}})
    accepted = store.enqueue_with_connection(connection, job)
    return {"status": "queued" if accepted else "suppressed", "jobId": job_id}
